"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : position_tracker
    Project     : Project-103
    Description : Position tracking module. This module handles position management and tracking.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.core.exceptions import PositionError, PositionNotFoundError

logger = logging.getLogger(__name__)

# History size limits to prevent memory leaks
MAX_POSITION_HISTORY_SIZE = 1000


class PositionSide(Enum):
    """Position side enumeration."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionState(Enum):
    """Position state enumeration."""

    PENDING = "PENDING"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


@dataclass
class Position:
    """Position information."""

    # Identifiers
    id: str  # Position ID
    symbol: str  # Trading symbol
    exchange: str = "bybit"  # Exchange name

    # Position details
    side: str = ""  # LONG or SHORT
    state: str = "PENDING"  # Position state

    # Quantities
    quantity: float = 0.0  # Position size
    filled_quantity: float = 0.0  # Filled quantity
    remaining_quantity: float = 0.0  # Remaining to fill

    # Prices
    entry_price: float = 0.0  # Average entry price
    mark_price: float = 0.0  # Current mark price
    liquidation_price: Optional[float] = None  # Liquidation price
    exit_price: Optional[float] = None  # Exit price

    # P&L
    unrealized_pnl: float = 0.0  # Unrealized P&L
    realized_pnl: float = 0.0  # Realized P&L
    total_pnl: float = 0.0  # Total P&L
    pnl_percentage: float = 0.0  # P&L percentage

    # Risk management
    stop_loss: Optional[float] = None  # Stop loss price
    take_profit: List[float] = field(default_factory=list)  # Take profit levels
    trailing_stop: Optional[float] = None  # Trailing stop price

    # Order IDs
    entry_order_ids: List[str] = field(default_factory=list)
    stop_loss_order_id: Optional[str] = None
    take_profit_order_ids: List[str] = field(default_factory=list)
    exit_order_ids: List[str] = field(default_factory=list)

    # Leverage
    leverage: int = 1
    margin_mode: str = "cross"  # cross or isolated
    margin: float = 0.0  # Required margin

    # Timestamps
    opened_at: int = 0  # Opening timestamp
    updated_at: int = 0  # Last update timestamp
    closed_at: Optional[int] = None  # Closing timestamp

    # Statistics
    max_profit: float = 0.0  # Maximum profit reached
    max_loss: float = 0.0  # Maximum loss reached
    hold_time: int = 0  # Hold time in seconds

    # Metadata
    signal_id: str = ""  # Original signal ID
    strategy_name: str = ""  # Strategy that opened position
    close_reason: Optional[str] = None  # Reason for closing
    notes: Optional[str] = None  # Additional notes
    metadata: Dict[str, Any] = field(default_factory=dict)

    def calculate_pnl(self, current_price: float) -> Tuple[float, float]:
        """
        Calculate P&L at given price.

        Args:
            current_price: Current market price

        Returns:
            Tuple of (pnl_amount, pnl_percentage)
        """
        if self.entry_price == 0 or self.quantity == 0:
            return 0.0, 0.0

        if self.side == "LONG":
            pnl = (current_price - self.entry_price) * self.quantity
        else:  # SHORT
            pnl = (self.entry_price - current_price) * self.quantity

        pnl_percentage = (pnl / (self.entry_price * self.quantity)) * 100

        return pnl, pnl_percentage

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    def is_active(self) -> bool:
        """Check if position is active."""
        return self.state in ["OPEN", "OPENING", "CLOSING"]

    def is_profitable(self) -> bool:
        """Check if position is profitable."""
        return self.unrealized_pnl > 0


@dataclass
class PositionUpdate:
    """Position update information."""

    position_id: str
    current_price: float
    mark_price: float
    timestamp: int

    # Optional updates
    liquidation_price: Optional[float] = None
    funding_rate: Optional[float] = None
    volume_24h: Optional[float] = None

    # Market conditions
    spread: Optional[float] = None
    volatility: Optional[float] = None


@dataclass
class PositionStatus:
    """Position status after update."""

    position_id: str
    is_healthy: bool = True

    # Risk status
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    distance_to_liquidation: Optional[float] = None
    distance_to_stop_loss: Optional[float] = None

    # Suggested actions
    should_close: bool = False
    close_reason: Optional[str] = None
    suggested_stop_loss: Optional[float] = None
    suggested_trailing_stop: Optional[float] = None
    suggested_size_adjustment: Optional[float] = None

    # Warnings
    warnings: List[str] = field(default_factory=list)


@dataclass
class PositionMetrics:
    """Position performance metrics."""

    total_positions: int = 0
    open_positions: int = 0
    closed_positions: int = 0

    # P&L metrics
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    # Win/Loss statistics
    winning_positions: int = 0
    losing_positions: int = 0
    win_rate: float = 0.0

    # Average metrics
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    avg_hold_time: int = 0  # seconds

    # Best/Worst
    best_position_pnl: float = 0.0
    worst_position_pnl: float = 0.0

    # Risk metrics
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0


class PositionTracker:
    """Position tracker class."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize position tracker.

        Args:
            config: Configuration
        """
        self.config = config

        # Position storage
        self.positions: Dict[str, Position] = {}
        self.position_history: List[Position] = []

        # Metrics
        self.metrics = PositionMetrics()

        # Risk parameters
        self.max_positions = config.get("max_positions", 5)
        self.max_position_size = config.get("max_position_size", 10000)
        self.auto_close_profit = config.get("auto_close_profit", 0.10)  # 10%
        self.auto_close_loss = config.get("auto_close_loss", 0.05)  # 5%

        # Tracking
        self.last_update = time.time()
        self.update_interval = config.get("update_interval", 1.0)  # seconds

        logger.info("PositionTracker initialized")

    def open_position(self, order: Dict[str, Any], signal: Dict[str, Any]) -> Position:
        """
        Open a new position.

        Args:
            order: Executed order
            signal: Trading signal

        Returns:
            Position: Created position

        Raises:
            PositionError: If position cannot be opened
        """
        try:
            # Check position limits
            if len(self.positions) >= self.max_positions:
                raise PositionError(f"Maximum positions limit reached: {self.max_positions}")

            # Generate position ID
            position_id = self._generate_position_id()

            # Determine position side
            order_side = order.get("side")
            if order_side == "BUY":
                position_side = "LONG"
            elif order_side == "SELL":
                position_side = "SHORT"
            else:
                raise PositionError(f"Invalid order side: {order_side}")

            # Create position
            position = Position(
                id=position_id,
                symbol=order.get("symbol"),
                side=position_side,
                state="OPEN",
                quantity=order.get("executed_quantity", order.get("quantity")),
                filled_quantity=order.get("executed_quantity", 0),
                remaining_quantity=0,
                entry_price=order.get("average_price", order.get("price")),
                mark_price=order.get("average_price", order.get("price")),
                leverage=signal.get("leverage", 1),
                margin_mode=signal.get("margin_mode", "cross"),
                opened_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
                signal_id=signal.get("id", ""),
                strategy_name=signal.get("strategy_name", ""),
                metadata={
                    "signal": signal,
                    "entry_order": order,
                },
            )

            # Set risk management levels
            if signal.get("stop_loss"):
                position.stop_loss = signal.get("stop_loss")

            if signal.get("take_profit"):
                tp = signal.get("take_profit")
                position.take_profit = tp if isinstance(tp, list) else [tp]

            # Calculate initial margin
            position.margin = (position.quantity * position.entry_price) / position.leverage

            # Add entry order ID
            position.entry_order_ids.append(order.get("id"))

            # Store position
            self.positions[position_id] = position

            # Update metrics
            self.metrics.total_positions += 1
            self.metrics.open_positions += 1

            logger.info(
                f"Opened position {position_id}: {position.symbol} "
                f"{position.side} {position.quantity} @ {position.entry_price}"
            )

            return position

        except Exception as e:
            logger.error(f"Error opening position: {e}")
            raise PositionError(f"Failed to open position: {e}")

    def update_position(self, update: PositionUpdate) -> PositionStatus:
        """
        Update position with latest data.

        Args:
            update: Position update data

        Returns:
            PositionStatus: Position status after update
        """
        status = PositionStatus(position_id=update.position_id)

        try:
            position = self.positions.get(update.position_id)

            if not position:
                raise PositionNotFoundError(update.position_id)

            # Update prices
            position.mark_price = update.mark_price
            position.updated_at = update.timestamp

            if update.liquidation_price:
                position.liquidation_price = update.liquidation_price

            # Calculate P&L
            pnl, pnl_pct = position.calculate_pnl(update.current_price)
            position.unrealized_pnl = pnl
            position.pnl_percentage = pnl_pct
            position.total_pnl = position.realized_pnl + position.unrealized_pnl

            # Update max profit/loss
            position.max_profit = max(position.max_profit, pnl)
            position.max_loss = min(position.max_loss, pnl)

            # Update hold time
            if position.opened_at > 0:
                position.hold_time = (update.timestamp - position.opened_at) // 1000

            # Check position health
            status = self._check_position_health(position, update)

            # Check for auto-close conditions
            self._check_auto_close(position, status)

            # Check for trailing stop update
            self._check_trailing_stop(position, status, update.current_price)

            logger.debug(
                f"Updated position {update.position_id}: " f"P&L {pnl:.2f} ({pnl_pct:.2f}%)"
            )

        except Exception as e:
            logger.error(f"Error updating position {update.position_id}: {e}")
            status.is_healthy = False
            status.warnings.append(str(e))

        return status

    def close_position(
        self, position_id: str, exit_price: float, reason: str = "manual"
    ) -> Position:
        """
        Close a position.

        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Close reason

        Returns:
            Position: Closed position

        Raises:
            PositionNotFoundError: If position not found
        """
        try:
            position = self.positions.get(position_id)

            if not position:
                raise PositionNotFoundError(position_id)

            # Update position
            position.state = "CLOSED"
            position.exit_price = exit_price
            position.closed_at = int(time.time() * 1000)
            position.close_reason = reason

            # Calculate final P&L
            if position.side == "LONG":
                position.realized_pnl = (exit_price - position.entry_price) * position.quantity
            else:  # SHORT
                position.realized_pnl = (position.entry_price - exit_price) * position.quantity

            position.total_pnl = position.realized_pnl
            # Safe division - avoid division by zero
            notional = position.entry_price * position.quantity
            position.pnl_percentage = (
                (position.realized_pnl / notional) * 100 if notional > 0 else 0.0
            )

            # Update hold time
            if position.opened_at > 0:
                position.hold_time = (position.closed_at - position.opened_at) // 1000

            # Move to history
            del self.positions[position_id]
            self.position_history.append(position)

            # Trim history to prevent memory leak
            if len(self.position_history) > MAX_POSITION_HISTORY_SIZE:
                self.position_history = self.position_history[-MAX_POSITION_HISTORY_SIZE:]

            # Update metrics
            self.metrics.open_positions -= 1
            self.metrics.closed_positions += 1
            self.metrics.realized_pnl += position.realized_pnl

            if position.realized_pnl > 0:
                self.metrics.winning_positions += 1
            else:
                self.metrics.losing_positions += 1

            # Update win rate
            if self.metrics.closed_positions > 0:
                self.metrics.win_rate = (
                    self.metrics.winning_positions / self.metrics.closed_positions
                )

            logger.info(
                f"Closed position {position_id}: {position.symbol} "
                f"P&L {position.realized_pnl:.2f} ({position.pnl_percentage:.2f}%) "
                f"Reason: {reason}"
            )

            return position

        except Exception as e:
            logger.error(f"Error closing position {position_id}: {e}")
            raise PositionError(f"Failed to close position: {e}")

    def reduce_position(self, position_id: str, reduce_quantity: float) -> Position:
        """
        Reduce position size.

        Args:
            position_id: Position ID
            reduce_quantity: Quantity to reduce

        Returns:
            Position: Updated position
        """
        try:
            position = self.positions.get(position_id)

            if not position:
                raise PositionNotFoundError(position_id)

            if reduce_quantity > position.quantity:
                raise PositionError(
                    f"Reduce quantity {reduce_quantity} exceeds position size {position.quantity}"
                )

            # Update quantity
            position.quantity -= reduce_quantity
            position.updated_at = int(time.time() * 1000)

            # Recalculate margin
            position.margin = (position.quantity * position.entry_price) / position.leverage

            logger.info(
                f"Reduced position {position_id} by {reduce_quantity}. "
                f"New size: {position.quantity}"
            )

            return position

        except Exception as e:
            logger.error(f"Error reducing position {position_id}: {e}")
            raise PositionError(f"Failed to reduce position: {e}")

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        return self.positions.get(position_id)

    def get_all_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self.positions.values())

    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get positions for a symbol."""
        return [p for p in self.positions.values() if p.symbol == symbol]

    def get_position_history(
        self, limit: int = 100, symbol: Optional[str] = None
    ) -> List[Position]:
        """
        Get position history.

        Args:
            limit: Maximum number of positions
            symbol: Filter by symbol

        Returns:
            List[Position]: Historical positions
        """
        history = self.position_history[-limit:]

        if symbol:
            history = [p for p in history if p.symbol == symbol]

        return history

    def get_metrics(self) -> PositionMetrics:
        """Get position metrics."""
        # Update current metrics
        self._update_metrics()
        return self.metrics

    def calculate_total_exposure(self) -> float:
        """Calculate total exposure."""
        total = 0.0

        for position in self.positions.values():
            total += position.quantity * position.mark_price

        return total

    def calculate_total_margin(self) -> float:
        """Calculate total margin used."""
        total = 0.0

        for position in self.positions.values():
            total += position.margin

        return total

    def check_all_positions(self, market_data: Dict[str, Dict[str, Any]]) -> List[PositionStatus]:
        """
        Check all positions and return their status.

        Args:
            market_data: Current market data for all symbols

        Returns:
            List[PositionStatus]: Status of all positions
        """
        statuses = []

        for position in self.positions.values():
            symbol_data = market_data.get(position.symbol, {})

            if symbol_data:
                update = PositionUpdate(
                    position_id=position.id,
                    current_price=symbol_data.get("last_price", position.mark_price),
                    mark_price=symbol_data.get("mark_price", position.mark_price),
                    timestamp=int(time.time() * 1000),
                    liquidation_price=symbol_data.get("liquidation_price"),
                    spread=symbol_data.get("spread"),
                    volatility=symbol_data.get("volatility"),
                )

                status = self.update_position(update)
                statuses.append(status)

        return statuses

    # ==================== Private Methods ====================

    def _generate_position_id(self) -> str:
        """Generate unique position ID."""
        return f"POS_{uuid.uuid4().hex[:12].upper()}"

    def _check_position_health(self, position: Position, update: PositionUpdate) -> PositionStatus:
        """Check position health and risk status."""
        status = PositionStatus(position_id=position.id)

        # Check P&L status
        if position.pnl_percentage <= -5:
            status.risk_level = "CRITICAL"
            status.warnings.append(f"High loss: {position.pnl_percentage:.2f}%")
        elif position.pnl_percentage <= -3:
            status.risk_level = "HIGH"
            status.warnings.append(f"Significant loss: {position.pnl_percentage:.2f}%")
        elif position.pnl_percentage <= -1:
            status.risk_level = "MEDIUM"
        else:
            status.risk_level = "LOW"

        # Check distance to liquidation
        if position.liquidation_price and update.current_price > 0:
            if position.side == "LONG":
                distance = (
                    update.current_price - position.liquidation_price
                ) / update.current_price
            else:  # SHORT
                distance = (
                    position.liquidation_price - update.current_price
                ) / update.current_price

            status.distance_to_liquidation = distance

            if distance < 0.05:  # Less than 5% to liquidation
                status.risk_level = "CRITICAL"
                status.warnings.append(f"Close to liquidation: {distance:.2%}")

        # Check distance to stop loss
        if position.stop_loss and update.current_price > 0:
            if position.side == "LONG":
                distance = (update.current_price - position.stop_loss) / update.current_price
            else:  # SHORT
                distance = (position.stop_loss - update.current_price) / update.current_price

            status.distance_to_stop_loss = distance

            if distance < 0.01:  # Less than 1% to stop loss
                status.warnings.append(f"Close to stop loss: {distance:.2%}")

        # Check hold time
        if position.hold_time > 86400:  # More than 24 hours
            status.warnings.append(f"Long hold time: {position.hold_time // 3600} hours")

        # Check spread if available
        if update.spread and update.spread > 0.002:  # More than 0.2%
            status.warnings.append(f"High spread: {update.spread:.4f}")

        # Check volatility if available
        if update.volatility and update.volatility > 0.05:  # More than 5%
            status.warnings.append(f"High volatility: {update.volatility:.2%}")

        status.is_healthy = status.risk_level in ["LOW", "MEDIUM"]

        return status

    def _check_auto_close(self, position: Position, status: PositionStatus) -> None:
        """Check for auto-close conditions."""
        # Check profit target
        if position.pnl_percentage >= self.auto_close_profit * 100:
            status.should_close = True
            status.close_reason = (
                f"Auto-close: Profit target reached ({position.pnl_percentage:.2f}%)"
            )

        # Check loss limit
        elif position.pnl_percentage <= -self.auto_close_loss * 100:
            status.should_close = True
            status.close_reason = f"Auto-close: Loss limit reached ({position.pnl_percentage:.2f}%)"

        # Check risk level
        elif status.risk_level == "CRITICAL":
            status.should_close = True
            status.close_reason = "Auto-close: Critical risk level"

    def _check_trailing_stop(
        self, position: Position, status: PositionStatus, current_price: float
    ) -> None:
        """Check and update trailing stop."""
        if not position.trailing_stop:
            # Suggest trailing stop if profitable
            if position.pnl_percentage > 2:  # More than 2% profit
                if position.side == "LONG":
                    status.suggested_trailing_stop = current_price * 0.98  # 2% below current
                else:  # SHORT
                    status.suggested_trailing_stop = current_price * 1.02  # 2% above current
        else:
            # Update trailing stop if price moved favorably
            if position.side == "LONG":
                new_stop = current_price * 0.98
                if new_stop > position.trailing_stop:
                    status.suggested_trailing_stop = new_stop
            else:  # SHORT
                new_stop = current_price * 1.02
                if new_stop < position.trailing_stop:
                    status.suggested_trailing_stop = new_stop

    def _update_metrics(self) -> None:
        """Update position metrics."""
        # Calculate unrealized P&L
        self.metrics.unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())

        # Calculate total P&L
        self.metrics.total_pnl = self.metrics.realized_pnl + self.metrics.unrealized_pnl

        # Calculate averages
        if self.metrics.winning_positions > 0:
            winning_pnl = sum(p.realized_pnl for p in self.position_history if p.realized_pnl > 0)
            self.metrics.avg_profit = winning_pnl / self.metrics.winning_positions

        if self.metrics.losing_positions > 0:
            losing_pnl = sum(
                abs(p.realized_pnl) for p in self.position_history if p.realized_pnl < 0
            )
            self.metrics.avg_loss = losing_pnl / self.metrics.losing_positions

        # Calculate profit factor
        if self.metrics.avg_loss > 0:
            self.metrics.profit_factor = self.metrics.avg_profit / self.metrics.avg_loss

        # Calculate average hold time
        if self.position_history:
            total_hold_time = sum(p.hold_time for p in self.position_history)
            self.metrics.avg_hold_time = total_hold_time // len(self.position_history)

        # Find best and worst positions
        if self.position_history:
            self.metrics.best_position_pnl = max(p.realized_pnl for p in self.position_history)
            self.metrics.worst_position_pnl = min(p.realized_pnl for p in self.position_history)

        # Calculate max drawdown
        if self.position_history:
            equity_curve = []
            cumulative_pnl = 0

            for position in sorted(self.position_history, key=lambda p: p.closed_at or 0):
                cumulative_pnl += position.realized_pnl
                equity_curve.append(cumulative_pnl)

            if equity_curve:
                peak = equity_curve[0]
                max_dd = 0

                for value in equity_curve:
                    if value > peak:
                        peak = value
                    drawdown = (peak - value) / peak if peak > 0 else 0
                    max_dd = max(max_dd, drawdown)

                self.metrics.max_drawdown = max_dd
