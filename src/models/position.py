"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : position
    Project     : Project-103
    Description : Position model for trading positions. This module provides position data models for tracking open positions.

    SPDX-License-Identifier: AGPL-3.0

"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.base import BaseModel, Identifiable, SerializableMixin, Timestamped, TimestampMixin


class PositionSide(Enum):
    """Position side enumeration."""

    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # For hedge mode


class PositionStatus(Enum):
    """Position status enumeration."""

    PENDING = "PENDING"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


@dataclass
class Position(BaseModel, TimestampMixin, SerializableMixin, Timestamped, Identifiable):
    """Trading position model."""

    # Identifiers
    id: str = field(default_factory=lambda: f"POS_{uuid.uuid4().hex[:12].upper()}")

    # Position details
    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    status: PositionStatus = PositionStatus.PENDING

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

    # Leverage and margin
    leverage: int = 1
    margin_mode: str = "cross"  # cross or isolated
    margin: float = 0.0  # Required margin
    initial_margin: float = 0.0  # Initial margin
    maintenance_margin: float = 0.0  # Maintenance margin
    margin_ratio: float = 0.0  # Current margin ratio

    # Timestamps
    opened_at: Optional[int] = None
    closed_at: Optional[int] = None

    # Statistics
    max_profit: float = 0.0  # Maximum profit reached
    max_loss: float = 0.0  # Maximum loss reached
    hold_time: int = 0  # Hold time in seconds

    # Signal and strategy
    signal_id: Optional[str] = None
    strategy_name: str = ""

    # Metadata
    exchange: str = "unknown"
    close_reason: Optional[str] = None  # Reason for closing
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """
        Validate position data.

        Returns:
            List[str]: Validation errors
        """
        errors = []

        # Check required fields
        if not self.symbol:
            errors.append("Symbol is required")

        # Check quantity
        if self.quantity <= 0:
            errors.append("Quantity must be positive")

        # Check prices
        if self.entry_price < 0:
            errors.append("Entry price cannot be negative")

        # Check leverage
        if self.leverage < 1:
            errors.append("Leverage must be >= 1")

        # Check stop loss
        if self.stop_loss:
            if self.side == PositionSide.LONG:
                if self.stop_loss >= self.entry_price:
                    errors.append("Stop loss must be below entry for long positions")
            else:  # SHORT
                if self.stop_loss <= self.entry_price:
                    errors.append("Stop loss must be above entry for short positions")

        # Check take profit
        if self.take_profit and self.entry_price > 0:
            for tp in self.take_profit:
                if self.side == PositionSide.LONG:
                    if tp <= self.entry_price:
                        errors.append("Take profit must be above entry for long positions")
                else:  # SHORT
                    if tp >= self.entry_price:
                        errors.append("Take profit must be below entry for short positions")

        return errors

    @property
    def is_active(self) -> bool:
        """Check if position is active."""
        return self.status in [PositionStatus.OPEN, PositionStatus.OPENING, PositionStatus.CLOSING]

    @property
    def is_closed(self) -> bool:
        """Check if position is closed."""
        return self.status in [PositionStatus.CLOSED, PositionStatus.LIQUIDATED]

    @property
    def is_profitable(self) -> bool:
        """Check if position is profitable."""
        return self.total_pnl > 0

    @property
    def position_value(self) -> float:
        """Get current position value."""
        return self.quantity * self.mark_price

    @property
    def entry_value(self) -> float:
        """Get entry value."""
        return self.quantity * self.entry_price

    @property
    def risk_reward_ratio(self) -> Optional[float]:
        """Calculate risk/reward ratio."""
        if not (self.stop_loss and self.take_profit):
            return None

        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit[0] - self.entry_price) if self.take_profit else 0

        if risk == 0:
            return None

        return reward / risk

    @property
    def stop_loss_distance(self) -> Optional[float]:
        """Get stop loss distance in price."""
        if self.stop_loss:
            return abs(self.entry_price - self.stop_loss)
        return None

    @property
    def stop_loss_percentage(self) -> Optional[float]:
        """Get stop loss distance as percentage."""
        if self.stop_loss and self.entry_price > 0:
            return (abs(self.entry_price - self.stop_loss) / self.entry_price) * 100
        return None

    @property
    def distance_to_liquidation(self) -> Optional[float]:
        """Get distance to liquidation as percentage."""
        if self.liquidation_price and self.mark_price > 0:
            if self.side == PositionSide.LONG:
                return ((self.mark_price - self.liquidation_price) / self.mark_price) * 100
            else:  # SHORT
                return ((self.liquidation_price - self.mark_price) / self.mark_price) * 100
        return None

    def calculate_pnl(self, current_price: float) -> tuple[float, float]:
        """
        Calculate P&L at given price.

        Args:
            current_price: Current market price

        Returns:
            tuple: (pnl_amount, pnl_percentage)
        """
        if self.entry_price == 0 or self.quantity == 0:
            return 0.0, 0.0

        if self.side == PositionSide.LONG:
            pnl = (current_price - self.entry_price) * self.quantity
        else:  # SHORT
            pnl = (self.entry_price - current_price) * self.quantity

        pnl_percentage = (pnl / self.entry_value) * 100

        return pnl, pnl_percentage

    def update_price(self, mark_price: float) -> None:
        """
        Update position with new mark price.

        Args:
            mark_price: Current mark price
        """
        self.mark_price = mark_price

        # Calculate P&L
        pnl, pnl_pct = self.calculate_pnl(mark_price)
        self.unrealized_pnl = pnl
        self.pnl_percentage = pnl_pct
        self.total_pnl = self.realized_pnl + self.unrealized_pnl

        # Update max profit/loss
        self.max_profit = max(self.max_profit, pnl)
        self.max_loss = min(self.max_loss, pnl)

        # Update hold time
        if self.opened_at:
            self.hold_time = (int(time.time() * 1000) - self.opened_at) // 1000

        self.touch()

    def add_entry(self, price: float, quantity: float) -> None:
        """
        Add to position (average in).

        Args:
            price: Entry price
            quantity: Additional quantity
        """
        total_quantity = self.quantity + quantity
        total_cost = (self.quantity * self.entry_price) + (quantity * price)

        self.entry_price = total_cost / total_quantity if total_quantity > 0 else 0
        self.quantity = total_quantity

        # Update margin
        self.margin = (self.quantity * self.entry_price) / self.leverage

        self.touch()

    def reduce(self, quantity: float, exit_price: float) -> float:
        """
        Reduce position size.

        Args:
            quantity: Quantity to reduce
            exit_price: Exit price

        Returns:
            float: Realized P&L from reduction
        """
        if quantity > self.quantity:
            quantity = self.quantity

        # Calculate P&L for reduced portion
        if self.side == PositionSide.LONG:
            pnl = (exit_price - self.entry_price) * quantity
        else:  # SHORT
            pnl = (self.entry_price - exit_price) * quantity

        # Update position
        self.quantity -= quantity
        self.realized_pnl += pnl
        self.total_pnl = self.realized_pnl + self.unrealized_pnl

        # Update margin
        self.margin = (self.quantity * self.entry_price) / self.leverage

        if self.quantity == 0:
            self.close(exit_price)

        self.touch()

        return pnl

    def close(self, exit_price: float, reason: Optional[str] = None) -> None:
        """
        Close position.

        Args:
            exit_price: Exit price
            reason: Close reason
        """
        self.exit_price = exit_price
        self.status = PositionStatus.CLOSED
        self.closed_at = int(time.time() * 1000)
        self.close_reason = reason

        # Calculate final P&L
        if self.side == PositionSide.LONG:
            self.realized_pnl = (exit_price - self.entry_price) * self.quantity
        else:  # SHORT
            self.realized_pnl = (self.entry_price - exit_price) * self.quantity

        self.total_pnl = self.realized_pnl
        self.unrealized_pnl = 0

        if self.entry_value > 0:
            self.pnl_percentage = (self.realized_pnl / self.entry_value) * 100

        # Update hold time
        if self.opened_at:
            self.hold_time = (self.closed_at - self.opened_at) // 1000

        self.touch()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        """Create position from dictionary."""
        position = cls(
            id=data.get("id", f"POS_{uuid.uuid4().hex[:12].upper()}"),
            symbol=data.get("symbol", ""),
            side=PositionSide(data.get("side", "LONG")),
            status=PositionStatus(data.get("status", "PENDING")),
            quantity=float(data.get("quantity", 0)),
            filled_quantity=float(data.get("filled_quantity", 0)),
            remaining_quantity=float(data.get("remaining_quantity", 0)),
            entry_price=float(data.get("entry_price", 0)),
            mark_price=float(data.get("mark_price", 0)),
            liquidation_price=data.get("liquidation_price"),
            exit_price=data.get("exit_price"),
            unrealized_pnl=float(data.get("unrealized_pnl", 0)),
            realized_pnl=float(data.get("realized_pnl", 0)),
            total_pnl=float(data.get("total_pnl", 0)),
            pnl_percentage=float(data.get("pnl_percentage", 0)),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit", []),
            trailing_stop=data.get("trailing_stop"),
            entry_order_ids=data.get("entry_order_ids", []),
            stop_loss_order_id=data.get("stop_loss_order_id"),
            take_profit_order_ids=data.get("take_profit_order_ids", []),
            exit_order_ids=data.get("exit_order_ids", []),
            leverage=int(data.get("leverage", 1)),
            margin_mode=data.get("margin_mode", "cross"),
            margin=float(data.get("margin", 0)),
            initial_margin=float(data.get("initial_margin", 0)),
            maintenance_margin=float(data.get("maintenance_margin", 0)),
            margin_ratio=float(data.get("margin_ratio", 0)),
            opened_at=data.get("opened_at"),
            closed_at=data.get("closed_at"),
            max_profit=float(data.get("max_profit", 0)),
            max_loss=float(data.get("max_loss", 0)),
            hold_time=int(data.get("hold_time", 0)),
            signal_id=data.get("signal_id"),
            strategy_name=data.get("strategy_name", ""),
            exchange=data.get("exchange", "unknown"),
            close_reason=data.get("close_reason"),
            notes=data.get("notes"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

        # Set timestamps
        position.created_at = data.get("created_at", position.created_at)
        position.updated_at = data.get("updated_at", position.updated_at)

        return position

    def __repr__(self) -> str:
        """String representation."""
        pnl_str = f"+{self.total_pnl:.2f}" if self.total_pnl >= 0 else f"{self.total_pnl:.2f}"
        pnl_pct_str = f"({self.pnl_percentage:+.2f}%)"
        status_icon = "●" if self.is_active else "○"

        return (
            f"Position({self.id} {self.symbol} {self.side.value} "
            f"{self.quantity:.4f}@{self.entry_price:.4f} "
            f"{status_icon} {pnl_str} {pnl_pct_str})"
        )


@dataclass
class PositionRisk:
    """Position risk metrics."""

    position_id: str

    # Risk levels
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    risk_score: float = 0.0  # 0-1

    # Risk metrics
    exposure: float = 0.0  # Position value
    max_loss: float = 0.0  # Maximum possible loss
    value_at_risk: float = 0.0  # VaR

    # Distance metrics
    distance_to_stop: Optional[float] = None  # % to stop loss
    distance_to_liquidation: Optional[float] = None  # % to liquidation

    # Warnings
    warnings: List[str] = field(default_factory=list)

    def add_warning(self, warning: str) -> None:
        """Add risk warning."""
        if warning not in self.warnings:
            self.warnings.append(warning)

    def calculate_risk_score(self) -> float:
        """Calculate overall risk score."""
        score = 0.0
        factors = 0

        # Factor 1: Distance to liquidation
        if self.distance_to_liquidation is not None:
            if self.distance_to_liquidation < 5:
                score += 1.0
            elif self.distance_to_liquidation < 10:
                score += 0.7
            elif self.distance_to_liquidation < 20:
                score += 0.4
            else:
                score += 0.1
            factors += 1

        # Factor 2: Distance to stop loss
        if self.distance_to_stop is not None:
            if self.distance_to_stop < 1:
                score += 0.8
            elif self.distance_to_stop < 2:
                score += 0.5
            elif self.distance_to_stop < 5:
                score += 0.3
            else:
                score += 0.1
            factors += 1

        if factors > 0:
            self.risk_score = score / factors

        # Determine risk level
        if self.risk_score > 0.8:
            self.risk_level = "CRITICAL"
        elif self.risk_score > 0.6:
            self.risk_level = "HIGH"
        elif self.risk_score > 0.3:
            self.risk_level = "MEDIUM"
        else:
            self.risk_level = "LOW"

        return self.risk_score


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
    avg_risk_reward: float = 0.0

    # Best/Worst
    best_position_pnl: float = 0.0
    worst_position_pnl: float = 0.0
    longest_hold: int = 0  # seconds

    # Risk metrics
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0

    def add_position(self, position: Position) -> None:
        """Add position to metrics."""
        self.total_positions += 1

        if position.is_active:
            self.open_positions += 1
            self.unrealized_pnl += position.unrealized_pnl
        else:
            self.closed_positions += 1
            self.realized_pnl += position.realized_pnl

            if position.realized_pnl > 0:
                self.winning_positions += 1
            else:
                self.losing_positions += 1

        self.total_pnl = self.realized_pnl + self.unrealized_pnl

        # Update averages
        if self.closed_positions > 0:
            self.win_rate = self.winning_positions / self.closed_positions


class PositionManager:
    """Manager for tracking and managing positions"""

    def __init__(self):
        """Initialize position manager"""
        self.positions: Dict[str, Position] = {}
        self.active_positions: Dict[str, Position] = {}
        self.closed_positions: Dict[str, Position] = {}

    def add_position(self, position: Position) -> None:
        """Add a position to the manager"""
        self.positions[position.id] = position
        if position.is_active:
            self.active_positions[position.id] = position

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID"""
        return self.positions.get(position_id)

    def get_active_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Get all active positions"""
        if symbol:
            return [p for p in self.active_positions.values() if p.symbol == symbol]
        return list(self.active_positions.values())

    def update_position(self, position: Position) -> None:
        """Update existing position"""
        self.positions[position.id] = position
        if position.is_active:
            self.active_positions[position.id] = position
        elif position.id in self.active_positions:
            del self.active_positions[position.id]
            self.closed_positions[position.id] = position

    def close_position(
        self, position_id: str, exit_price: float, reason: Optional[str] = None
    ) -> None:
        """Close a position"""
        position = self.positions.get(position_id)
        if position:
            position.close(exit_price, reason)
            self.update_position(position)

    def get_metrics(self) -> PositionMetrics:
        """Get position metrics"""
        metrics = PositionMetrics()
        for position in self.positions.values():
            metrics.add_position(position)
        return metrics
