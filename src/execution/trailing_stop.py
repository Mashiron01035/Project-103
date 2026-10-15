"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : trailing_stop
    Project     : Project-103
    Description : Trailing stop and break-even stop management module.
                  Handles trailing stop logic and break-even with re-entry for positions.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, List, Optional

from src.utils.datetime_utils import utcnow
from src.utils.log_tags import P  # Position tags

logger = logging.getLogger(__name__)


# ==================== Break-Even Stop ====================


class BreakEvenState(Enum):
    """Break-even stop states."""

    INACTIVE = "INACTIVE"  # Not yet activated
    MONITORING = "MONITORING"  # Watching for activation threshold
    ACTIVE = "ACTIVE"  # BE stop is set
    TRIGGERED = "TRIGGERED"  # BE stop was hit
    REENTRY_PENDING = "REENTRY_PENDING"  # Waiting for re-entry


@dataclass
class BreakEvenConfig:
    """Break-even stop configuration."""

    # Enable/disable
    enabled: bool = True

    # Activation threshold - when to move stop to break-even
    activation_profit_percent: float = 0.5  # Activate at 0.5% profit

    # Lock profit based on TP1 distance (matches backtest logic)
    # 0.5 = lock 50% of distance between entry and TP1
    lock_profit_ratio: float = 0.5  # Lock 50% of TP1 distance at BE

    # Fallback: fixed percent lock when no TP prices available
    lock_profit_percent: float = 0.8  # Lock 0.8% profit at BE (fallback)

    # Commission settings (to calculate true break-even)
    maker_fee_percent: float = 0.02  # 0.02% maker fee
    taker_fee_percent: float = 0.055  # 0.055% taker fee
    use_maker_for_entry: bool = False  # Usually entry is taker
    use_maker_for_exit: bool = True  # BE stop can be limit order (maker)

    # Buffer above break-even (extra safety margin)
    buffer_percent: float = 0.01  # 0.01% buffer above BE

    # Re-entry settings
    enable_reentry: bool = True
    reentry_price_offset_percent: float = 0.3  # Re-enter 0.3% below original entry (for LONG)
    reentry_order_type: str = "LIMIT"  # LIMIT or STOP_LIMIT
    reentry_expiry_seconds: int = 3600  # 1 hour expiry for re-entry order
    max_reentries: int = 2  # Maximum re-entries per signal

    # Re-entry improvements (B)
    reentry_require_better_price: bool = True  # Only re-enter at better price than previous entry
    reentry_min_improvement_percent: float = 0.3  # Minimum 0.3% improvement from original entry
    reentry_use_limit_order: bool = True  # Use limit order (not market) for better fills

    # Signal validity check
    min_signal_time_remaining_seconds: int = 300  # At least 5 min signal validity for re-entry
    ignore_signal_expiry_for_reentry: bool = True  # Allow re-entry even if signal expired

    def get_total_fees_percent(self) -> float:
        """Calculate total round-trip fees."""
        entry_fee = self.maker_fee_percent if self.use_maker_for_entry else self.taker_fee_percent
        exit_fee = self.maker_fee_percent if self.use_maker_for_exit else self.taker_fee_percent
        return entry_fee + exit_fee


@dataclass
class BreakEvenStatus:
    """Break-even stop status for a position."""

    position_id: str
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    quantity: float

    # State
    state: BreakEvenState = BreakEvenState.INACTIVE
    break_even_price: Optional[float] = None  # True BE price (entry + fees)
    stop_price: Optional[float] = None  # Actual stop price (BE + buffer)

    # Tracking
    highest_price: Optional[float] = None  # For LONG
    lowest_price: Optional[float] = None  # For SHORT
    max_profit_percent: float = 0.0
    activated_at: Optional[datetime] = None
    triggered_at: Optional[datetime] = None

    # Re-entry
    reentry_count: int = 0
    reentry_order_id: Optional[str] = None
    reentry_price: Optional[float] = None
    reentry_expires_at: Optional[datetime] = None

    # Original signal info (for re-entry validation)
    original_signal_id: Optional[str] = None
    original_stop_loss: Optional[float] = None
    original_take_profit: Optional[List[float]] = None
    signal_expires_at: Optional[datetime] = None

    # Breakeven parameters from the signal metadata. The backtest always read them,
    # live did not, so breakeven was applied to every position the same way.
    activation_percent: Optional[float] = None  # None -> no percentage trigger
    lock_profit_ratio: Optional[float] = None  # None -> value from the config

    # Timestamp when position was registered (for stale data protection)
    registered_at: datetime = field(default_factory=utcnow)

    # Deprecated: replaced by time-based stale data protection
    first_update_done: bool = False


@dataclass
class ReentryOrder:
    """Re-entry order details."""

    position_id: str
    symbol: str
    side: str  # BUY or SELL
    order_type: str  # LIMIT, STOP_LIMIT
    price: float
    quantity: float
    stop_loss: float
    take_profit: List[float]
    expires_at: datetime
    original_signal_id: str
    reentry_number: int  # 1st, 2nd, etc.


class BreakEvenManager:
    """
    Break-even stop manager with re-entry capability.

    Flow:
    1. Position opened -> Start monitoring
    2. Price reaches activation threshold -> Set BE stop
    3. BE stop triggered -> Check if re-entry makes sense
    4. If yes -> Place limit order for re-entry
    5. Re-entry filled -> Back to step 1
    """

    def __init__(self, config: BreakEvenConfig):
        """
        Initialize break-even manager.

        Args:
            config: Break-even configuration
        """
        self.config = config
        self.positions: Dict[str, BreakEvenStatus] = {}
        self.pending_reentries: Dict[str, ReentryOrder] = {}

        # Callbacks
        self.on_be_activated: Optional[Callable[[BreakEvenStatus], None]] = None
        self.on_be_triggered: Optional[Callable[[BreakEvenStatus], None]] = None
        self.on_reentry_created: Optional[Callable[[ReentryOrder], None]] = None

        logger.info(
            f"BreakEvenManager initialized (activation: {config.activation_profit_percent}%, "
            f"fees: {config.get_total_fees_percent():.3f}%, reentry: {config.enable_reentry})"
        )

    def register_position(
        self,
        position_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        signal_id: Optional[str] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[List[float]] = None,
        signal_expiry: Optional[datetime] = None,
        activation_percent: Optional[float] = None,
        lock_profit_ratio: Optional[float] = None,
    ) -> BreakEvenStatus:
        """
        Register a new position for break-even monitoring.

        Args:
            position_id: Position ID
            symbol: Trading symbol
            side: LONG or SHORT
            entry_price: Entry price
            quantity: Position quantity
            signal_id: Original signal ID (for re-entry)
            stop_loss: Original stop loss
            take_profit: Original take profit levels
            signal_expiry: When the signal expires

        Returns:
            BreakEvenStatus: Initial status
        """
        # Calculate break-even price with locked profit
        total_fees = self.config.get_total_fees_percent() / 100
        buffer = self.config.buffer_percent / 100

        # Use TP1-distance-based lock if TP prices available (matches backtest)
        ratio = (
            lock_profit_ratio if lock_profit_ratio is not None else self.config.lock_profit_ratio
        )

        if take_profit and len(take_profit) > 0:
            tp1 = take_profit[0]
            tp1_distance = abs(tp1 - entry_price)
            lock_distance = tp1_distance * ratio

            if side == "LONG":
                be_price = entry_price * (1 + total_fees)
                stop_price = entry_price + lock_distance + (entry_price * buffer)
            else:
                be_price = entry_price * (1 - total_fees)
                stop_price = entry_price - lock_distance - (entry_price * buffer)
        else:
            # Fallback to fixed percent when no TPs
            lock_profit = self.config.lock_profit_percent / 100

            if side == "LONG":
                be_price = entry_price * (1 + total_fees)
                stop_price = entry_price * (1 + lock_profit + buffer)
            else:
                be_price = entry_price * (1 - total_fees)
                stop_price = entry_price * (1 - lock_profit - buffer)

        status = BreakEvenStatus(
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            state=BreakEvenState.MONITORING,
            break_even_price=be_price,
            stop_price=stop_price,
            highest_price=entry_price if side == "LONG" else None,
            lowest_price=entry_price if side == "SHORT" else None,
            original_signal_id=signal_id,
            original_stop_loss=stop_loss,
            original_take_profit=take_profit,
            signal_expires_at=signal_expiry,
            registered_at=utcnow(),
            activation_percent=activation_percent,
            lock_profit_ratio=lock_profit_ratio,
        )

        self.positions[position_id] = status

        if take_profit and len(take_profit) > 0:
            lock_info = f"lock {self.config.lock_profit_ratio*100:.0f}% of TP1 distance"
        else:
            lock_info = f"lock {self.config.lock_profit_percent}%"
        logger.info(
            f"{P.BE} {side} {symbol} @ {entry_price:.4f}, stop={stop_price:.4f} ({lock_info})",
            extra={"log_type": "trade"},
        )

        return status

    def update_price(
        self,
        position_id: str,
        current_price: float,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Optional[BreakEvenStatus]:
        """
        Update price and check BE conditions.

        Uses candle high/low for wick detection (matches backtest behavior):
        - LONG activation: candle_high for profit calculation (catches upward wicks)
        - LONG trigger: candle_low for stop check (catches downward wicks)
        - SHORT activation: candle_low for profit calculation
        - SHORT trigger: candle_high for stop check

        Args:
            position_id: Position ID
            current_price: Current market price (candle close)
            candle_high: Candle high for wick detection. Falls back to current_price.
            candle_low: Candle low for wick detection. Falls back to current_price.

        Returns:
            Updated status or None if position not found
        """
        if position_id not in self.positions:
            return None

        status = self.positions[position_id]

        # Skip if not monitoring or already triggered
        if status.state not in [BreakEvenState.MONITORING, BreakEvenState.ACTIVE]:
            return status

        # Track highest/lowest from CLOSE prices only (not candle extremes).
        # Candle high/low may contain stale data from before the position was
        # opened (e.g., a 15m candle wick that happened 10 minutes ago).
        # Close price is always the most recent tick, so it's safe to use.
        if status.side == "LONG":
            if status.highest_price is None or current_price > status.highest_price:
                status.highest_price = current_price
            profit_percent = (
                (status.highest_price - status.entry_price) / status.entry_price
            ) * 100
        else:
            if status.lowest_price is None or current_price < status.lowest_price:
                status.lowest_price = current_price
            profit_percent = ((status.entry_price - status.lowest_price) / status.entry_price) * 100

        # Update max profit
        if profit_percent > status.max_profit_percent:
            status.max_profit_percent = profit_percent

        # Check activation (based on close-price profit, not candle extremes)
        if status.state == BreakEvenState.MONITORING:
            # The threshold comes from the signal. If the strategy did not set it,
            # the percentage breakeven is NOT enabled — exactly as in the backtest,
            # where breakeven without breakeven_activation_percent waits for TP1.
            # Live used to apply the global config threshold to every position,
            # and a strategy that never asks for breakeven was closed at +1%.
            threshold = status.activation_percent
            if threshold is None or threshold <= 0:
                return status

            if profit_percent >= threshold:
                # Guard: the stop must not end up BEYOND the current price.
                # When locking 50% of the distance to TP, the level can lie farther
                # than the price has actually travelled ($75 vs $44 in one case), and
                # then the trigger check is true immediately — the position closes at
                # the moment of activation. A stop that fires when placed is no stop.
                if not self._stop_is_behind_price(status, current_price):
                    logger.debug(
                        f"BE waiting: {status.side} {status.symbol} stop "
                        f"{status.stop_price:.4f} not yet passed by price "
                        f"{current_price:.4f}"
                    )
                    return status

                status.state = BreakEvenState.ACTIVE
                status.activated_at = utcnow()

                logger.info(
                    f"BE ACTIVATED: {status.side} {status.symbol} | "
                    f"Profit: {profit_percent:.2f}% | Stop set to: {status.stop_price:.4f}",
                    extra={"log_type": "signal"},
                )

                if self.on_be_activated:
                    self.on_be_activated(status)

                # Don't check trigger on the same update that activated BE.
                # The BE stop will be synced to exchange, which handles the
                # actual trigger in real-time. Next update will check trigger.
                return status

        return self._check_trigger(status, current_price)

    @staticmethod
    def _stop_is_behind_price(status: BreakEvenStatus, current_price: float) -> bool:
        """Whether the breakeven stop sits on the losing side of the current price.

        For LONG that is below the price, for SHORT above. A violation means the
        lock level lies farther than the price has actually travelled.
        """
        if status.stop_price is None:
            return False
        if status.side == "LONG":
            return status.stop_price < current_price
        return status.stop_price > current_price

    def _check_trigger(self, status: BreakEvenStatus, current_price: float) -> BreakEvenStatus:
        # Check if stop triggered (backup for exchange SL).
        # Use current_price (close), not candle extremes, to avoid
        # false triggers from stale high/low data.
        if status.state == BreakEvenState.ACTIVE:
            triggered = False

            if status.side == "LONG" and current_price <= status.stop_price:
                triggered = True
            elif status.side == "SHORT" and current_price >= status.stop_price:
                triggered = True

            if triggered:
                self._handle_be_triggered(status, status.stop_price)

        return status

    def update_entry_price(
        self, position_id: str, new_entry_price: float
    ) -> Optional[BreakEvenStatus]:
        """
        Update entry price after DCA (Dollar Cost Averaging).

        Recalculates break-even and stop prices based on new average entry.

        Args:
            position_id: Position ID
            new_entry_price: New average entry price after DCA

        Returns:
            Updated status or None if position not found
        """
        if position_id not in self.positions:
            return None

        status = self.positions[position_id]
        old_entry = status.entry_price

        # Recalculate break-even and stop prices
        total_fees = self.config.get_total_fees_percent() / 100
        buffer = self.config.buffer_percent / 100

        # Use TP1-distance-based lock if TP prices available
        if status.original_take_profit and len(status.original_take_profit) > 0:
            tp1 = status.original_take_profit[0]
            tp1_distance = abs(tp1 - new_entry_price)
            lock_distance = tp1_distance * self.config.lock_profit_ratio

            if status.side == "LONG":
                be_price = new_entry_price * (1 + total_fees)
                stop_price = new_entry_price + lock_distance + (new_entry_price * buffer)
            else:
                be_price = new_entry_price * (1 - total_fees)
                stop_price = new_entry_price - lock_distance - (new_entry_price * buffer)
        else:
            lock_profit = self.config.lock_profit_percent / 100

            if status.side == "LONG":
                be_price = new_entry_price * (1 + total_fees)
                stop_price = new_entry_price * (1 + lock_profit + buffer)
            else:
                be_price = new_entry_price * (1 - total_fees)
                stop_price = new_entry_price * (1 - lock_profit - buffer)

        # Update status
        status.entry_price = new_entry_price
        status.break_even_price = be_price
        status.stop_price = stop_price

        # Reset to MONITORING state (need to re-activate BE after DCA)
        if status.state == BreakEvenState.ACTIVE:
            status.state = BreakEvenState.MONITORING
            status.activated_at = None

        logger.info(
            f"{P.DCA} BE updated: {status.side} {status.symbol} | "
            f"entry {old_entry:.4f} -> {new_entry_price:.4f}, "
            f"BE stop {status.stop_price:.4f}"
        )

        return status

    def _handle_be_triggered(self, status: BreakEvenStatus, trigger_price: float) -> None:
        """Handle break-even stop triggered."""
        status.state = BreakEvenState.TRIGGERED
        status.triggered_at = utcnow()

        logger.info(
            f"BE TRIGGERED: {status.side} {status.symbol} @ {trigger_price:.4f} | "
            f"Max profit was: {status.max_profit_percent:.2f}%",
            extra={"log_type": "signal"},
        )

        if self.on_be_triggered:
            self.on_be_triggered(status)

        # Check for re-entry
        if self.config.enable_reentry:
            reentry_order = self._create_reentry_order(status, trigger_price)
            if reentry_order:
                status.state = BreakEvenState.REENTRY_PENDING
                status.reentry_order_id = reentry_order.position_id
                status.reentry_price = reentry_order.price

    def _create_reentry_order(
        self, status: BreakEvenStatus, trigger_price: float
    ) -> Optional[ReentryOrder]:
        """
        Create re-entry order if conditions are met.

        Args:
            status: Break-even status
            trigger_price: Price at which BE was triggered

        Returns:
            ReentryOrder or None if re-entry not possible
        """
        # Check max re-entries
        if status.reentry_count >= self.config.max_reentries:
            logger.info(
                f"Max re-entries ({self.config.max_reentries}) reached for {status.position_id}"
            )
            return None

        # Check signal validity (skip if ignore_signal_expiry_for_reentry is True)
        if not self.config.ignore_signal_expiry_for_reentry and status.signal_expires_at:
            now = utcnow()
            remaining = (status.signal_expires_at - now).total_seconds()

            if remaining < self.config.min_signal_time_remaining_seconds:
                logger.info(
                    f"Signal expires in {remaining:.0f}s, skipping re-entry for {status.position_id}"
                )
                return None

        # Check if original SL/TP still make sense
        if not self._is_reentry_valid(status, trigger_price):
            logger.info(f"Re-entry not valid for {status.position_id}")
            return None

        # Calculate re-entry price
        offset = self.config.reentry_price_offset_percent / 100

        if status.side == "LONG":
            # For LONG: re-enter below current price
            reentry_price = trigger_price * (1 - offset)
            order_side = "BUY"
        else:
            # For SHORT: re-enter above current price
            reentry_price = trigger_price * (1 + offset)
            order_side = "SELL"

        # Create re-entry order
        reentry_order = ReentryOrder(
            position_id=f"{status.position_id}_reentry_{status.reentry_count + 1}",
            symbol=status.symbol,
            side=order_side,
            order_type=self.config.reentry_order_type,
            price=reentry_price,
            quantity=status.quantity,
            stop_loss=status.original_stop_loss,
            take_profit=status.original_take_profit or [],
            expires_at=utcnow() + timedelta(seconds=self.config.reentry_expiry_seconds),
            original_signal_id=status.original_signal_id or status.position_id,
            reentry_number=status.reentry_count + 1,
        )

        self.pending_reentries[reentry_order.position_id] = reentry_order
        status.reentry_count += 1
        status.reentry_expires_at = reentry_order.expires_at

        # Calculate price improvement
        if status.side == "LONG":
            improvement = ((status.entry_price - reentry_price) / status.entry_price) * 100
        else:
            improvement = ((reentry_price - status.entry_price) / status.entry_price) * 100

        order_type = "LIMIT" if self.config.reentry_use_limit_order else "MARKET"

        logger.info(
            f"RE-ENTRY ORDER CREATED: {order_side} {status.symbol} @ {reentry_price:.4f} ({order_type}) | "
            f"Improvement: {improvement:+.2f}% from original {status.entry_price:.4f} | "
            f"Expires: {reentry_order.expires_at} | Re-entry #{reentry_order.reentry_number}",
            extra={"log_type": "signal"},
        )

        if self.on_reentry_created:
            self.on_reentry_created(reentry_order)

        return reentry_order

    def _is_reentry_valid(self, status: BreakEvenStatus, current_price: float) -> bool:
        """
        Check if re-entry still makes sense.

        Validates:
        - Original SL is still valid (hasn't been hit)
        - Original TP is still achievable (reasonable distance)
        - If require_better_price: current price must be better than original entry
        """
        if not status.original_stop_loss or not status.original_take_profit:
            return False

        sl = status.original_stop_loss
        tp = status.original_take_profit[0] if status.original_take_profit else None

        if not tp:
            return False

        # Check if re-entry price would be better than original entry (B)
        if self.config.reentry_require_better_price:
            min_improvement = self.config.reentry_min_improvement_percent / 100
            reentry_offset = self.config.reentry_price_offset_percent / 100

            if status.side == "LONG":
                # For LONG: we want to re-enter LOWER than original entry
                # Re-entry price = current_price * (1 - offset)
                expected_reentry = current_price * (1 - reentry_offset)
                improvement = (status.entry_price - expected_reentry) / status.entry_price

                if improvement < min_improvement:
                    logger.debug(
                        f"Re-entry not better: expected {expected_reentry:.4f} vs original {status.entry_price:.4f} "
                        f"(improvement {improvement*100:.2f}% < {min_improvement*100:.2f}%)"
                    )
                    return False
            else:  # SHORT
                # For SHORT: we want to re-enter HIGHER than original entry
                expected_reentry = current_price * (1 + reentry_offset)
                improvement = (expected_reentry - status.entry_price) / status.entry_price

                if improvement < min_improvement:
                    logger.debug(
                        f"Re-entry not better: expected {expected_reentry:.4f} vs original {status.entry_price:.4f} "
                        f"(improvement {improvement*100:.2f}% < {min_improvement*100:.2f}%)"
                    )
                    return False

        if status.side == "LONG":
            # For LONG: price should be above SL and have room to TP
            if current_price <= sl:
                return False

            # Check if TP is still at least 0.5% away
            distance_to_tp = (tp - current_price) / current_price
            if distance_to_tp < 0.005:
                return False

        else:  # SHORT
            # For SHORT: price should be below SL and have room to TP
            if current_price >= sl:
                return False

            distance_to_tp = (current_price - tp) / current_price
            if distance_to_tp < 0.005:
                return False

        return True

    def handle_reentry_filled(
        self, reentry_order_id: str, fill_price: float
    ) -> Optional[BreakEvenStatus]:
        """
        Handle re-entry order filled - register new position.

        Args:
            reentry_order_id: Re-entry order ID
            fill_price: Actual fill price

        Returns:
            New BreakEvenStatus or None
        """
        if reentry_order_id not in self.pending_reentries:
            logger.warning(f"Re-entry order {reentry_order_id} not found")
            return None

        reentry = self.pending_reentries.pop(reentry_order_id)

        # Find original position to get reentry count
        original_pos_id = reentry_order_id.rsplit("_reentry_", 1)[0]
        original_status = self.positions.get(original_pos_id)
        reentry_count = original_status.reentry_count if original_status else reentry.reentry_number

        # Register new position
        side = "LONG" if reentry.side == "BUY" else "SHORT"

        new_status = self.register_position(
            position_id=reentry_order_id,
            symbol=reentry.symbol,
            side=side,
            entry_price=fill_price,
            quantity=reentry.quantity,
            signal_id=reentry.original_signal_id,
            stop_loss=reentry.stop_loss,
            take_profit=reentry.take_profit,
            signal_expiry=reentry.expires_at,
        )

        new_status.reentry_count = reentry_count

        logger.info(
            f"RE-ENTRY FILLED: {side} {reentry.symbol} @ {fill_price:.4f} | "
            f"Re-entry #{reentry.reentry_number}",
            extra={"log_type": "trade"},
        )

        return new_status

    def cancel_reentry(self, reentry_order_id: str) -> bool:
        """Cancel a pending re-entry order."""
        if reentry_order_id in self.pending_reentries:
            del self.pending_reentries[reentry_order_id]
            logger.info(f"Re-entry order {reentry_order_id} cancelled")
            return True
        return False

    def force_activate_be(self, position_id: str) -> bool:
        """
        Force activate break-even stop for a position.
        Used for manual BE activation from Telegram buttons.

        Args:
            position_id: Position ID

        Returns:
            True if BE was activated successfully
        """
        if position_id not in self.positions:
            return False

        status = self.positions[position_id]

        # Can only force activate if in MONITORING state
        if status.state != BreakEvenState.MONITORING:
            return status.state == BreakEvenState.ACTIVE  # Already active is ok

        # Activate BE
        status.state = BreakEvenState.ACTIVE
        status.activated_at = utcnow()

        logger.info(
            f"BE force activated for {position_id}: " f"stop @ {status.stop_price:.4f}",
            extra={"log_type": "trade"},
        )

        # Call callback if registered
        if self.on_be_activated:
            try:
                self.on_be_activated(status)
            except Exception as e:
                logger.error(f"Error in on_be_activated callback: {e}")

        return True

    def remove_position(self, position_id: str) -> None:
        """Remove position from monitoring."""
        if position_id in self.positions:
            del self.positions[position_id]
            logger.info(f"Position {position_id} removed from BE monitoring")

    def get_status(self, position_id: str) -> Optional[BreakEvenStatus]:
        """Get break-even status for a position."""
        return self.positions.get(position_id)

    def get_all_active(self) -> List[BreakEvenStatus]:
        """Get all positions with active BE stops."""
        return [s for s in self.positions.values() if s.state == BreakEvenState.ACTIVE]

    def get_pending_reentries(self) -> List[ReentryOrder]:
        """Get all pending re-entry orders."""
        return list(self.pending_reentries.values())

    def cleanup_expired_reentries(self) -> int:
        """Remove expired re-entry orders."""
        now = utcnow()
        expired = [
            order_id for order_id, order in self.pending_reentries.items() if order.expires_at < now
        ]

        for order_id in expired:
            del self.pending_reentries[order_id]
            logger.info(f"Re-entry order {order_id} expired")

        return len(expired)


@dataclass
class TrailingStopConfig:
    """Trailing stop configuration."""

    # Activation
    enabled: bool = True
    activation_profit: float = 0.01  # 1% profit to activate

    # Distance types
    distance_type: str = "PERCENTAGE"  # PERCENTAGE, FIXED, ATR
    distance_value: float = 0.02  # 2% for percentage type

    # ATR settings (if using ATR type)
    atr_period: int = 14
    atr_multiplier: float = 2.0

    # Step trailing
    use_step_trailing: bool = False
    step_size: float = 0.005  # 0.5% steps

    # Tightening
    use_tightening: bool = False
    tightening_levels: List[Dict[str, float]] = field(
        default_factory=lambda: [
            {"profit": 0.05, "distance": 0.015},  # At 5% profit, tighten to 1.5%
            {"profit": 0.10, "distance": 0.010},  # At 10% profit, tighten to 1%
            {"profit": 0.20, "distance": 0.005},  # At 20% profit, tighten to 0.5%
        ]
    )

    # Limits
    min_distance: float = 0.002  # Minimum 0.2% distance
    max_distance: float = 0.05  # Maximum 5% distance

    def get_distance_for_profit(self, profit_percentage: float) -> float:
        """
        Get trailing stop distance based on profit level.

        Args:
            profit_percentage: Current profit percentage

        Returns:
            float: Trailing stop distance
        """
        if not self.use_tightening:
            return self.distance_value

        # Find appropriate tightening level
        distance = self.distance_value

        for level in self.tightening_levels:
            if profit_percentage >= level["profit"]:
                distance = level["distance"]

        # Apply limits
        distance = max(self.min_distance, min(distance, self.max_distance))

        return distance


@dataclass
class TrailingStopStatus:
    """Trailing stop status for a position."""

    position_id: str
    is_active: bool = False
    current_stop: Optional[float] = None
    suggested_stop: Optional[float] = None

    # Tracking
    highest_price: Optional[float] = None  # For long positions
    lowest_price: Optional[float] = None  # For short positions
    last_update: Optional[int] = None  # Timestamp

    # Statistics
    times_updated: int = 0
    total_distance_moved: float = 0.0

    # Status
    should_update: bool = False
    update_reason: Optional[str] = None


class TrailingStopManager:
    """Trailing stop manager class."""

    def __init__(self, config: TrailingStopConfig):
        """
        Initialize trailing stop manager.

        Args:
            config: Trailing stop configuration
        """
        self.config = config
        self.trailing_stops: Dict[str, TrailingStopStatus] = {}

        # ATR cache for ATR-based trailing stops
        self.atr_cache: Dict[str, float] = {}
        self.atr_cache_ttl = 300  # 5 minutes
        self.atr_cache_time: Dict[str, float] = {}

        logger.info("TrailingStopManager initialized")

    def initialize_trailing_stop(
        self, position_id: str, position_side: str, entry_price: float, current_price: float
    ) -> TrailingStopStatus:
        """
        Initialize trailing stop for a position.

        Args:
            position_id: Position ID
            position_side: LONG or SHORT
            entry_price: Position entry price
            current_price: Current market price

        Returns:
            TrailingStopStatus: Initial trailing stop status
        """
        status = TrailingStopStatus(position_id=position_id)

        # Calculate initial profit
        if position_side == "LONG":
            profit = (current_price - entry_price) / entry_price
            status.highest_price = current_price
        else:  # SHORT
            profit = (entry_price - current_price) / entry_price
            status.lowest_price = current_price

        # Check if trailing stop should be activated
        if profit >= self.config.activation_profit:
            status.is_active = True
            status.current_stop = self._calculate_stop_price(
                position_side, current_price, self.config.distance_value
            )
            status.last_update = int(time.time() * 1000)

            logger.info(f"Trailing stop initialized for {position_id} at {status.current_stop}")

        self.trailing_stops[position_id] = status
        return status

    def update_trailing_stop(
        self,
        position_id: str,
        position_side: str,
        entry_price: float,
        current_price: float,
        current_stop: Optional[float] = None,
        atr_value: Optional[float] = None,
    ) -> TrailingStopStatus:
        """
        Update trailing stop for a position.

        Args:
            position_id: Position ID
            position_side: LONG or SHORT
            entry_price: Position entry price
            current_price: Current market price
            current_stop: Current stop loss price
            atr_value: Current ATR value (if using ATR-based stops)

        Returns:
            TrailingStopStatus: Updated trailing stop status
        """
        # Get or create status
        if position_id not in self.trailing_stops:
            status = self.initialize_trailing_stop(
                position_id, position_side, entry_price, current_price
            )
        else:
            status = self.trailing_stops[position_id]

        # Update current stop if provided
        if current_stop:
            status.current_stop = current_stop

        # Calculate profit
        if position_side == "LONG":
            profit = (current_price - entry_price) / entry_price

            # Update highest price
            if status.highest_price is None or current_price > status.highest_price:
                status.highest_price = current_price
        else:  # SHORT
            profit = (entry_price - current_price) / entry_price

            # Update lowest price
            if status.lowest_price is None or current_price < status.lowest_price:
                status.lowest_price = current_price

        # Check activation
        if not status.is_active and profit >= self.config.activation_profit:
            status.is_active = True
            logger.info(f"Trailing stop activated for {position_id}")

        if status.is_active:
            # Calculate new stop price
            new_stop = self._calculate_new_stop(
                position_side, current_price, profit, status, atr_value
            )

            # Check if stop should be updated
            if self._should_update_stop(position_side, new_stop, status.current_stop):
                status.suggested_stop = new_stop
                status.should_update = True
                status.update_reason = self._get_update_reason(
                    position_side, new_stop, status.current_stop
                )

                # Track statistics
                if status.current_stop:
                    status.total_distance_moved += abs(new_stop - status.current_stop)

                logger.info(
                    f"Trailing stop update suggested for {position_id}: "
                    f"{status.current_stop} -> {new_stop} ({status.update_reason})"
                )

        return status

    def execute_update(self, position_id: str, new_stop: float) -> bool:
        """
        Execute trailing stop update.

        Args:
            position_id: Position ID
            new_stop: New stop price

        Returns:
            bool: Success status
        """
        if position_id not in self.trailing_stops:
            logger.error(f"Position {position_id} not found in trailing stops")
            return False

        status = self.trailing_stops[position_id]
        status.current_stop = new_stop
        status.should_update = False
        status.suggested_stop = None
        status.times_updated += 1
        status.last_update = int(time.time() * 1000)

        logger.info(f"Trailing stop updated for {position_id} to {new_stop}")
        return True

    def remove_trailing_stop(self, position_id: str) -> None:
        """Remove trailing stop for closed position."""
        if position_id in self.trailing_stops:
            del self.trailing_stops[position_id]
            logger.info(f"Trailing stop removed for {position_id}")

    def get_status(self, position_id: str) -> Optional[TrailingStopStatus]:
        """Get trailing stop status for a position."""
        return self.trailing_stops.get(position_id)

    def get_all_active(self) -> List[TrailingStopStatus]:
        """Get all active trailing stops."""
        return [status for status in self.trailing_stops.values() if status.is_active]

    # ==================== Private Methods ====================

    def _calculate_stop_price(
        self, position_side: str, current_price: float, distance: float
    ) -> float:
        """Calculate stop price based on distance."""
        if position_side == "LONG":
            return current_price * (1 - distance)
        else:  # SHORT
            return current_price * (1 + distance)

    def _calculate_new_stop(
        self,
        position_side: str,
        current_price: float,
        profit: float,
        status: TrailingStopStatus,
        atr_value: Optional[float] = None,
    ) -> float:
        """Calculate new trailing stop price."""
        # Get distance based on configuration
        if self.config.distance_type == "PERCENTAGE":
            distance = self.config.get_distance_for_profit(profit)

        elif self.config.distance_type == "FIXED":
            distance = self.config.distance_value / current_price

        elif self.config.distance_type == "ATR" and atr_value:
            distance = (atr_value * self.config.atr_multiplier) / current_price

        else:
            distance = self.config.distance_value

        # Apply step trailing if enabled
        if self.config.use_step_trailing:
            if position_side == "LONG" and status.highest_price:
                # Only update if price moved up by step size
                price_change = (current_price - status.highest_price) / status.highest_price
                if price_change < self.config.step_size:
                    return status.current_stop or 0

            elif position_side == "SHORT" and status.lowest_price:
                # Only update if price moved down by step size
                price_change = (status.lowest_price - current_price) / status.lowest_price
                if price_change < self.config.step_size:
                    return status.current_stop or 0

        # Calculate stop price
        return self._calculate_stop_price(position_side, current_price, distance)

    def _should_update_stop(
        self, position_side: str, new_stop: float, current_stop: Optional[float]
    ) -> bool:
        """Check if stop should be updated."""
        if not current_stop:
            return True

        if position_side == "LONG":
            # For long, only move stop up
            return new_stop > current_stop
        else:  # SHORT
            # For short, only move stop down
            return new_stop < current_stop

    def _get_update_reason(
        self, position_side: str, new_stop: float, current_stop: Optional[float]
    ) -> str:
        """Get reason for stop update."""
        if not current_stop:
            return "Initial trailing stop"

        distance_moved = abs(new_stop - current_stop)
        distance_pct = (distance_moved / current_stop) * 100

        if position_side == "LONG":
            return f"Price increased, stop moved up {distance_pct:.2f}%"
        else:
            return f"Price decreased, stop moved down {distance_pct:.2f}%"

    def calculate_optimal_distance(
        self, symbol: str, timeframe: str, volatility: float, trend_strength: float
    ) -> float:
        """
        Calculate optimal trailing stop distance based on market conditions.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            volatility: Current volatility
            trend_strength: Trend strength (0-1)

        Returns:
            float: Optimal distance
        """
        # Base distance
        base_distance = self.config.distance_value

        # Adjust for volatility
        # Higher volatility = wider stop
        volatility_multiplier = 1 + (volatility * 0.5)

        # Adjust for trend strength
        # Stronger trend = tighter stop
        trend_multiplier = 1 - (trend_strength * 0.3)

        # Calculate final distance
        optimal_distance = base_distance * volatility_multiplier * trend_multiplier

        # Apply limits
        optimal_distance = max(
            self.config.min_distance, min(optimal_distance, self.config.max_distance)
        )

        logger.debug(
            f"Optimal trailing distance for {symbol}: {optimal_distance:.4f} "
            f"(volatility: {volatility:.2f}, trend: {trend_strength:.2f})"
        )

        return optimal_distance
