"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : limit_order_manager.py
    Project     : Project-103
    Description : Limit order entry manager — order-book-aware pricing, timeout monitoring, and automatic fallback to a market order.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.execution.smart_entry import SmartEntryAnalyzer, SmartEntryResult

logger = logging.getLogger(__name__)


class PendingOrderStatus(Enum):
    """Status of pending limit order."""

    PENDING = "pending"  # Waiting to be filled
    PARTIALLY_FILLED = "partial"  # Partially filled
    FILLED = "filled"  # Fully filled
    TIMEOUT = "timeout"  # Timeout, need market fallback
    CANCELLED = "cancelled"  # Cancelled by user
    FAILED = "failed"  # Failed to place


@dataclass
class PendingLimitOrder:
    """Tracks a pending limit order."""

    # Identifiers
    internal_id: str  # Our tracking ID
    exchange_order_id: Optional[str] = None  # Exchange order ID
    signal_id: str = ""  # Original signal ID

    # Order details
    symbol: str = ""
    side: str = ""  # BUY or SELL
    quantity: float = 0.0
    limit_price: float = 0.0

    # Fill tracking
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0

    # Status
    status: PendingOrderStatus = PendingOrderStatus.PENDING

    # Timing
    created_at: float = 0.0  # Unix timestamp
    timeout_at: float = 0.0  # When to give up
    filled_at: Optional[float] = None

    # Smart entry analysis
    analysis: Optional[SmartEntryResult] = None

    # Market fallback tracking
    market_fallback_used: bool = False
    market_order_id: Optional[str] = None
    position_ticket: Optional[int] = None  # MT5 position ticket

    # Original signal data for position creation
    signal_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def remaining_quantity(self) -> float:
        """Unfilled quantity."""
        return self.quantity - self.filled_quantity

    @property
    def is_expired(self) -> bool:
        """Check if order has timed out."""
        return time.time() > self.timeout_at

    @property
    def fill_percent(self) -> float:
        """Percentage filled."""
        if self.quantity == 0:
            return 0.0
        return (self.filled_quantity / self.quantity) * 100


class LimitOrderManager:
    """
    Manages limit order entry with smart pricing and timeout fallback.

    Flow:
    1. Analyze order book via SmartEntryAnalyzer
    2. Place limit order at optimal price
    3. Monitor for fill or timeout
    4. On timeout: cancel limit, place market order
    5. Create position once filled
    """

    def __init__(
        self,
        connector,
        config: Dict[str, Any],
        on_order_filled: Optional[Callable] = None,
        on_order_timeout: Optional[Callable] = None,
    ):
        """
        Initialize LimitOrderManager.

        Args:
            connector: Exchange connector with place_order, cancel_order, get_order
            config: Configuration dict with limit_entry settings
            on_order_filled: Callback when order is filled
            on_order_timeout: Callback when order times out
        """
        self.connector = connector
        self.config = config
        self.on_order_filled = on_order_filled
        self.on_order_timeout = on_order_timeout

        # Extract limit entry config
        limit_config = config.get("limit_entry", {})
        self.enabled = limit_config.get("enabled", True)
        self.default_timeout_seconds = limit_config.get("timeout_seconds", 1800)  # 30 min default
        self.min_spread_for_limit = limit_config.get("min_spread_percent", 0.01)
        self.max_spread_for_limit = limit_config.get("max_spread_percent", 0.5)
        self.check_interval = limit_config.get("check_interval", 5.0)  # Check every 5s

        # Smart entry analyzer
        self.analyzer = SmartEntryAnalyzer(
            connector=connector,
            min_liquidity_score=limit_config.get("min_liquidity", 0.3),
            min_fill_probability=limit_config.get("min_fill_prob", 0.5),
            price_offset_percent=limit_config.get("price_offset", 0.02),
        )

        # Pending orders storage
        self.pending_orders: Dict[str, PendingLimitOrder] = {}

        # Background monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

        # Statistics
        self.stats = {
            "limit_orders_placed": 0,
            "limit_orders_filled": 0,
            "limit_orders_timeout": 0,
            "market_fallbacks": 0,
            "avg_fill_time_seconds": 0.0,
            "total_saved_fees": 0.0,  # Estimated savings vs market
        }

        logger.info(
            f"LimitOrderManager initialized (enabled={self.enabled}, timeout={self.default_timeout_seconds}s)"
        )

    async def start(self):
        """Start the limit order monitoring loop."""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("LimitOrderManager monitoring started")

    async def stop(self):
        """Stop monitoring and cancel pending orders."""
        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Cancel all pending orders
        for order_id in list(self.pending_orders.keys()):
            await self.cancel_order(order_id, reason="manager_shutdown")

        logger.info("LimitOrderManager stopped")

    async def enter_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        signal_id: str,
        signal_data: Dict[str, Any],
        current_price: Optional[float] = None,
        timeout_seconds: Optional[int] = None,
        ai_adjusted_price: Optional[float] = None,
    ) -> PendingLimitOrder:
        """
        Enter a position using smart limit order.

        Args:
            symbol: Trading pair
            side: "BUY" or "SELL"
            quantity: Order quantity
            signal_id: Signal ID for tracking
            signal_data: Full signal data for position creation
            current_price: Current price for fallback
            timeout_seconds: Custom timeout (None = default)
            ai_adjusted_price: AI-recommended entry price (if provided, uses this instead of order book analysis)

        Returns:
            PendingLimitOrder: Order tracking object
        """
        # Generate internal ID
        internal_id = f"LMT_{signal_id}_{int(time.time() * 1000)}"
        timeout = timeout_seconds or self.default_timeout_seconds

        # Determine limit price: AI recommendation takes priority
        if ai_adjusted_price is not None:
            # Use AI-adjusted price directly
            limit_price = ai_adjusted_price
            logger.info(f"Using AI-adjusted entry price for {symbol}: {limit_price}")

            # Still run analysis for metadata, but don't use its price
            analysis = await self.analyzer.analyze(
                symbol=symbol, side=side, quantity=quantity, current_price=current_price
            )
        else:
            # No AI adjustment - use order book analysis
            analysis = await self.analyzer.analyze(
                symbol=symbol, side=side, quantity=quantity, current_price=current_price
            )
            limit_price = analysis.recommended_price

        # Create pending order object
        pending = PendingLimitOrder(
            internal_id=internal_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            created_at=time.time(),
            timeout_at=time.time() + timeout,
            analysis=analysis,
            signal_data=signal_data,
        )

        # Per-signal override: strategies like swing_fade require exact-pivot fill
        # and bypass the analyzer's use_limit gate.
        per_signal_use_limit = bool(
            (signal_data or {}).get("metadata", {}).get("use_limit_entry", False)
        )

        # Decide: limit or immediate market?
        if not per_signal_use_limit and (not self.enabled or not analysis.use_limit):
            # Use market order immediately
            logger.info(f"Using market order for {symbol} {side}: {analysis.reason}")
            return await self._place_market_order(pending)

        # Place limit order
        try:
            # For Hedge Mode: position_idx 1=Long (BUY), 2=Short (SELL)
            position_idx = signal_data.get("position_idx", 1 if side == "BUY" else 2)

            order_params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "quantity": quantity,
                "price": limit_price,  # AI-adjusted or order book analysis
                "time_in_force": "GTC",
                "reduce_only": False,
                "post_only": True,  # Ensure maker fee
                "position_idx": position_idx,  # Required for Hedge Mode
                # Telemetry attribution only — connectors read these, exchanges never see them.
                "signal_id": signal_id,
                "strategy": signal_data.get("strategy") or signal_data.get("strategy_name"),
            }

            # Attach SL/TP to the pending order so the filled position is
            # protected server-side from the first tick. MT5 carries sl/tp on
            # pending orders (connector.place_order reads them), so a fill that
            # happens while the bot is disconnected is NOT naked, and the routine
            # post-fill set_trading_stop window is eliminated. Post-fill SL/TP
            # placement stays as an idempotent re-assertion.
            _sl, _tp = self._extract_sl_tp(signal_data)
            if _sl:
                order_params["stop_loss"] = _sl
            if _tp:
                order_params["take_profit"] = _tp

            response = await self.connector.place_order(order_params)

            if response.get("success"):
                pending.exchange_order_id = response.get("order_id")
                pending.status = PendingOrderStatus.PENDING

                # Track it
                self.pending_orders[internal_id] = pending
                self.stats["limit_orders_placed"] += 1

                logger.info(
                    f"Limit order placed: {symbol} {side} {quantity} @ {analysis.recommended_price:.6f} "
                    f"(spread={analysis.spread_percent:.3f}%, timeout={timeout}s)"
                )

                return pending
            else:
                # Failed to place limit, fallback to market
                logger.warning(
                    f"Failed to place limit order: {response.get('error')}, using market"
                )
                return await self._place_market_order(pending)

        except Exception as e:
            logger.error(f"Error placing limit order: {e}, using market fallback")
            return await self._place_market_order(pending)

    @staticmethod
    def _extract_sl_tp(signal_data: Optional[Dict[str, Any]]) -> tuple:
        """Pull (stop_loss, take_profit) floats from a signal_data dict.

        take_profit may be a list (multi-TP) — the first level is used for the
        position's server-side TP. Returns (None, None) when a level is absent.
        """
        sd = signal_data or {}
        sl = sd.get("stop_loss")
        tp = sd.get("take_profit")
        if isinstance(tp, (list, tuple)):
            tp = tp[0] if tp else None
        return (float(sl) if sl else None, float(tp) if tp else None)

    async def _place_market_order(self, pending: PendingLimitOrder) -> PendingLimitOrder:
        """Place market order (fallback)."""
        try:
            # For Hedge Mode: position_idx 1=Long (BUY), 2=Short (SELL)
            position_idx = pending.signal_data.get(
                "position_idx", 1 if pending.side == "BUY" else 2
            )

            order_params = {
                "symbol": pending.symbol,
                "side": pending.side,
                "type": "MARKET",
                "quantity": pending.quantity,
                "reduce_only": False,
                "position_idx": position_idx,  # Required for Hedge Mode
                "strategy": pending.signal_data.get("strategy")
                or pending.signal_data.get("strategy_name"),
                "signal_id": pending.signal_id,
            }

            # Keep the fallback market entry protected too (rare path: limit
            # placement failed). Same SL/TP the pending order would have carried.
            _sl, _tp = self._extract_sl_tp(pending.signal_data)
            if _sl:
                order_params["stop_loss"] = _sl
            if _tp:
                order_params["take_profit"] = _tp

            # Include SL in order (MT5: binds SL to specific position)
            sl = pending.signal_data.get("stop_loss")
            if sl and sl > 0:
                order_params["stop_loss"] = sl

            response = await self.connector.place_order(order_params)

            if response.get("success"):
                pending.market_fallback_used = True
                pending.market_order_id = response.get("order_id")
                pending.position_ticket = response.get("position_ticket")  # MT5 position ticket
                pending.status = PendingOrderStatus.FILLED
                pending.filled_quantity = pending.quantity
                pending.filled_at = time.time()

                # Get fill price - market orders may not return price immediately
                fill_price = float(response.get("price", 0) or response.get("avgPrice", 0))
                if fill_price <= 0:
                    # Try to fetch actual fill price from order details
                    try:
                        await asyncio.sleep(0.3)  # Brief wait for order to settle
                        order_details = await self.connector.get_order(
                            symbol=pending.symbol, order_id=pending.market_order_id
                        )
                        if order_details:
                            fill_price = float(
                                order_details.get("average_price", 0)
                                or order_details.get("avgPrice", 0)
                            )
                    except Exception as e:
                        logger.warning(f"Could not fetch fill price for {pending.symbol}: {e}")

                # Fallback to limit_price or signal entry price
                if fill_price <= 0:
                    fill_price = pending.limit_price or pending.signal_data.get("entry_price", 0)
                    logger.warning(
                        f"Using fallback price {fill_price} for {pending.symbol} market order"
                    )

                pending.average_fill_price = fill_price
                self.stats["market_fallbacks"] += 1

                # Trigger callback
                if self.on_order_filled:
                    await self.on_order_filled(pending)

                logger.info(
                    f"Market order filled: {pending.symbol} {pending.side} "
                    f"@ {pending.average_fill_price:.6f}"
                )
            else:
                pending.status = PendingOrderStatus.FAILED
                logger.error(f"Market order failed: {response.get('error')}")

        except Exception as e:
            pending.status = PendingOrderStatus.FAILED
            logger.error(f"Market order error: {e}")

        return pending

    async def _monitoring_loop(self):
        """Background loop to monitor pending orders."""
        logger.info("Limit order monitoring loop started")

        while self._running:
            try:
                await self._check_pending_orders()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)

        logger.info("Limit order monitoring loop stopped")

    async def _check_pending_orders(self):
        """Check status of all pending orders."""
        orders_to_remove = []

        for internal_id, pending in list(self.pending_orders.items()):
            if pending.status != PendingOrderStatus.PENDING:
                continue

            try:
                # Check if timed out
                if pending.is_expired:
                    logger.info(
                        f"Limit order timeout: {pending.symbol} {pending.side} "
                        f"(waited {time.time() - pending.created_at:.0f}s)"
                    )
                    await self._handle_timeout(pending)
                    continue

                # Query order status from exchange
                if pending.exchange_order_id:
                    order_status = await self.connector.get_order(
                        symbol=pending.symbol, order_id=pending.exchange_order_id
                    )

                    if order_status:
                        await self._update_order_status(pending, order_status)

            except Exception as e:
                logger.error(f"Error checking order {internal_id}: {e}")

        # Clean up completed orders
        for internal_id in orders_to_remove:
            if internal_id in self.pending_orders:
                del self.pending_orders[internal_id]

    async def _update_order_status(
        self, pending: PendingLimitOrder, exchange_status: Dict[str, Any]
    ):
        """Update pending order based on exchange status."""
        status = exchange_status.get("status", "").upper()

        # Safe conversion with validation
        try:
            filled_qty = float(exchange_status.get("filled_quantity") or 0)
        except (TypeError, ValueError):
            logger.warning(f"Invalid filled_quantity: {exchange_status.get('filled_quantity')}")
            filled_qty = 0.0

        try:
            avg_price = float(exchange_status.get("average_price") or 0)
        except (TypeError, ValueError):
            logger.warning(f"Invalid average_price: {exchange_status.get('average_price')}")
            avg_price = 0.0

        pending.filled_quantity = filled_qty
        if avg_price > 0:
            pending.average_fill_price = avg_price

        if status == "FILLED" or filled_qty >= pending.quantity * 0.999:
            # Fully filled
            pending.status = PendingOrderStatus.FILLED
            pending.filled_at = time.time()

            fill_time = pending.filled_at - pending.created_at
            self.stats["limit_orders_filled"] += 1
            self._update_avg_fill_time(fill_time)
            self._calculate_fee_savings(pending)

            logger.info(
                f"Limit order FILLED: {pending.symbol} {pending.side} "
                f"@ {pending.average_fill_price:.6f} (fill time: {fill_time:.1f}s)"
            )

            # Trigger callback
            if self.on_order_filled:
                await self.on_order_filled(pending)

        elif filled_qty > 0:
            # Partial fill
            pending.status = PendingOrderStatus.PARTIALLY_FILLED
            logger.debug(
                f"Limit order partial fill: {pending.symbol} " f"{pending.fill_percent:.1f}% filled"
            )

        elif status in ["CANCELLED", "REJECTED", "EXPIRED"]:
            pending.status = PendingOrderStatus.CANCELLED
            logger.warning(f"Limit order {status}: {pending.symbol}")

    async def _handle_timeout(self, pending: PendingLimitOrder):
        """Handle order timeout — cancel limit, then place market unless the
        signal opts out via signal_data.metadata.cancel_on_limit_timeout=True."""
        pending.status = PendingOrderStatus.TIMEOUT
        self.stats["limit_orders_timeout"] += 1

        # Cancel the limit order
        if pending.exchange_order_id:
            try:
                await self.connector.cancel_order(
                    symbol=pending.symbol, order_id=pending.exchange_order_id
                )
                logger.info(f"Cancelled timed-out limit order: {pending.exchange_order_id}")
            except Exception as e:
                logger.warning(f"Error cancelling limit order: {e}")

        # Trigger timeout callback
        if self.on_order_timeout:
            await self.on_order_timeout(pending)

        # Per-signal opt-out: skip market fallback (no position opened).
        signal_meta = (pending.signal_data or {}).get("metadata", {}) or {}
        if signal_meta.get("cancel_on_limit_timeout"):
            pending.status = PendingOrderStatus.CANCELLED
            self.stats["cancellations"] = self.stats.get("cancellations", 0) + 1
            logger.info(
                f"Limit order CANCELLED (no market fallback): {pending.symbol} "
                f"{pending.side} (filled {pending.fill_percent:.1f}%)"
            )
            return

        # Place market order for remaining quantity
        if pending.remaining_quantity > 0:
            pending.quantity = pending.remaining_quantity
            await self._place_market_order(pending)

    async def cancel_order(self, internal_id: str, reason: str = "user_cancel") -> bool:
        """
        Cancel a pending limit order.

        Args:
            internal_id: Our internal order ID
            reason: Cancellation reason

        Returns:
            bool: Success
        """
        pending = self.pending_orders.get(internal_id)
        if not pending:
            logger.warning(f"Order not found: {internal_id}")
            return False

        if pending.status not in [PendingOrderStatus.PENDING, PendingOrderStatus.PARTIALLY_FILLED]:
            logger.warning(f"Cannot cancel order with status {pending.status}")
            return False

        try:
            if pending.exchange_order_id:
                await self.connector.cancel_order(
                    symbol=pending.symbol, order_id=pending.exchange_order_id
                )

            pending.status = PendingOrderStatus.CANCELLED
            logger.info(f"Order cancelled: {internal_id} ({reason})")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order {internal_id}: {e}")
            return False

    def get_pending_orders(self) -> List[PendingLimitOrder]:
        """Get all pending limit orders."""
        return [
            o
            for o in self.pending_orders.values()
            if o.status in [PendingOrderStatus.PENDING, PendingOrderStatus.PARTIALLY_FILLED]
        ]

    def get_order(self, internal_id: str) -> Optional[PendingLimitOrder]:
        """Get specific order by internal ID."""
        return self.pending_orders.get(internal_id)

    def get_statistics(self) -> Dict[str, Any]:
        """Get manager statistics."""
        return {
            **self.stats,
            "pending_count": len(self.get_pending_orders()),
            "fill_rate": (
                self.stats["limit_orders_filled"] / self.stats["limit_orders_placed"]
                if self.stats["limit_orders_placed"] > 0
                else 0
            ),
        }

    def _update_avg_fill_time(self, fill_time: float):
        """Update average fill time statistic."""
        filled = self.stats["limit_orders_filled"]
        current_avg = self.stats["avg_fill_time_seconds"]

        # Running average
        self.stats["avg_fill_time_seconds"] = (current_avg * (filled - 1) + fill_time) / filled

    def _calculate_fee_savings(self, pending: PendingLimitOrder):
        """Calculate estimated fee savings from limit vs market."""
        if pending.market_fallback_used:
            return

        # Fee difference: taker (0.055%) - maker (0.02%) = 0.035%
        maker_fee = 0.0002
        taker_fee = 0.00055

        trade_value = pending.filled_quantity * pending.average_fill_price
        savings = trade_value * (taker_fee - maker_fee)

        self.stats["total_saved_fees"] += savings
