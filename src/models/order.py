"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : order
    Project     : Project-103
    Description : Order model for trading orders. This module provides order data models for various order types.

    SPDX-License-Identifier: AGPL-3.0

"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.base import BaseModel, Identifiable, SerializableMixin, Timestamped, TimestampMixin


class OrderType(Enum):
    """Order type enumeration."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


class OrderSide(Enum):
    """Order side enumeration."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status enumeration."""

    PENDING = "PENDING"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REPLACED = "REPLACED"
    STOPPED = "STOPPED"
    TRIGGERED = "TRIGGERED"


class OrderTimeInForce(Enum):
    """Order time in force enumeration."""

    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill
    GTX = "GTX"  # Good Till Crossing
    GTD = "GTD"  # Good Till Date
    DAY = "DAY"  # Day order
    POST_ONLY = "POST_ONLY"  # Maker only


@dataclass
class Order(BaseModel, TimestampMixin, SerializableMixin, Timestamped, Identifiable):
    """Trading order model."""

    # Identifiers
    id: str = field(default_factory=lambda: f"ORD_{uuid.uuid4().hex[:12].upper()}")
    client_order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None

    # Order details
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    price: Optional[float] = None

    # Stop orders
    stop_price: Optional[float] = None
    trigger_price: Optional[float] = None
    trigger_by: str = "LastPrice"  # LastPrice, MarkPrice, IndexPrice

    # Execution
    time_in_force: OrderTimeInForce = OrderTimeInForce.GTC
    reduce_only: bool = False
    close_on_trigger: bool = False
    post_only: bool = False

    # Status
    status: OrderStatus = OrderStatus.PENDING

    # Filled information
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    executed_quantity: float = 0.0
    average_price: float = 0.0
    last_fill_price: Optional[float] = None
    last_fill_quantity: Optional[float] = None

    # Fees
    commission: float = 0.0
    commission_asset: str = "USDT"
    fee_rate: Optional[float] = None

    # Position
    position_id: Optional[str] = None
    position_side: Optional[str] = None  # LONG, SHORT, BOTH

    # Signal
    signal_id: Optional[str] = None
    strategy_name: str = ""

    # Risk management
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop: Optional[float] = None

    # Timestamps
    submitted_at: Optional[int] = None
    accepted_at: Optional[int] = None
    filled_at: Optional[int] = None
    cancelled_at: Optional[int] = None
    expires_at: Optional[int] = None

    # Metadata
    exchange: str = "unknown"
    source: str = "unknown"
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Rejection/error info
    reject_reason: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def validate(self) -> List[str]:
        """
        Validate order data.

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

        # Check price for limit orders
        if self.type in [OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_MAKER]:
            if not self.price or self.price <= 0:
                errors.append(f"Price required for {self.type.value} orders")

        # Check stop price for stop orders
        if self.type in [OrderType.STOP, OrderType.STOP_LIMIT]:
            if not self.stop_price or self.stop_price <= 0:
                errors.append(f"Stop price required for {self.type.value} orders")

        # Check filled quantities
        if self.filled_quantity > self.quantity:
            errors.append("Filled quantity cannot exceed order quantity")

        # Check reduce only logic
        if self.reduce_only and self.type == OrderType.MARKET and not self.close_on_trigger:
            if self.side == OrderSide.BUY and self.position_side == "SHORT":
                pass  # Valid
            elif self.side == OrderSide.SELL and self.position_side == "LONG":
                pass  # Valid
            else:
                errors.append("Reduce only order side doesn't match position")

        return errors

    @property
    def is_active(self) -> bool:
        """Check if order is active."""
        return self.status in [OrderStatus.PENDING, OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED]

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED

    @property
    def is_partially_filled(self) -> bool:
        """Check if order is partially filled."""
        return self.status == OrderStatus.PARTIALLY_FILLED

    @property
    def is_cancelled(self) -> bool:
        """Check if order is cancelled."""
        return self.status == OrderStatus.CANCELLED

    @property
    def is_rejected(self) -> bool:
        """Check if order is rejected."""
        return self.status == OrderStatus.REJECTED

    @property
    def is_stop_order(self) -> bool:
        """Check if order is a stop order."""
        return self.type in [OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP]

    @property
    def fill_percentage(self) -> float:
        """Get fill percentage."""
        if self.quantity == 0:
            return 0
        return (self.filled_quantity / self.quantity) * 100

    @property
    def total_commission(self) -> float:
        """Get total commission paid."""
        return self.commission

    @property
    def effective_price(self) -> float:
        """Get effective price including fees."""
        if self.filled_quantity == 0:
            return 0

        total_value = self.average_price * self.filled_quantity

        if self.side == OrderSide.BUY:
            # Add commission to cost
            return (total_value + self.commission) / self.filled_quantity
        else:  # SELL
            # Subtract commission from proceeds
            return (total_value - self.commission) / self.filled_quantity

    @property
    def slippage(self) -> Optional[float]:
        """Calculate slippage from intended price."""
        if not self.price or self.average_price == 0:
            return None

        return abs(self.average_price - self.price) / self.price * 100

    @property
    def time_to_fill(self) -> Optional[int]:
        """Get time to fill in seconds."""
        if self.submitted_at and self.filled_at:
            return (self.filled_at - self.submitted_at) // 1000
        return None

    def update_fill(
        self,
        filled_qty: float,
        fill_price: float,
        commission: float = 0,
        timestamp: Optional[int] = None,
    ) -> None:
        """
        Update order with fill information.

        Args:
            filled_qty: Quantity filled in this update
            fill_price: Fill price
            commission: Commission for this fill
            timestamp: Fill timestamp
        """
        # Update filled quantity
        prev_filled = self.filled_quantity
        self.filled_quantity += filled_qty
        self.remaining_quantity = max(0, self.quantity - self.filled_quantity)

        # Update average price
        if self.filled_quantity > 0:
            total_value = (prev_filled * self.average_price) + (filled_qty * fill_price)
            self.average_price = total_value / self.filled_quantity

        # Update last fill info
        self.last_fill_price = fill_price
        self.last_fill_quantity = filled_qty

        # Update commission
        self.commission += commission

        # Update status
        if self.filled_quantity >= self.quantity:
            self.status = OrderStatus.FILLED
            if timestamp:
                self.filled_at = timestamp
            else:
                self.filled_at = int(time.time() * 1000)
        elif self.filled_quantity > 0:
            self.status = OrderStatus.PARTIALLY_FILLED

        self.touch()

    def cancel(self, reason: Optional[str] = None) -> None:
        """
        Cancel order.

        Args:
            reason: Cancellation reason
        """
        self.status = OrderStatus.CANCELLED
        self.reject_reason = reason
        self.cancelled_at = int(time.time() * 1000)
        self.touch()

    def reject(self, reason: str, error_code: Optional[str] = None) -> None:
        """
        Reject order.

        Args:
            reason: Rejection reason
            error_code: Error code
        """
        self.status = OrderStatus.REJECTED
        self.reject_reason = reason
        self.error_code = error_code
        self.touch()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        """Create order from dictionary."""
        order = cls(
            id=data.get("id", f"ORD_{uuid.uuid4().hex[:12].upper()}"),
            client_order_id=data.get("client_order_id"),
            exchange_order_id=data.get("exchange_order_id"),
            symbol=data.get("symbol", ""),
            side=OrderSide(data.get("side", "BUY")),
            type=OrderType(data.get("type", "MARKET")),
            quantity=float(data.get("quantity", 0)),
            price=data.get("price"),
            stop_price=data.get("stop_price"),
            trigger_price=data.get("trigger_price"),
            trigger_by=data.get("trigger_by", "LastPrice"),
            time_in_force=OrderTimeInForce(data.get("time_in_force", "GTC")),
            reduce_only=data.get("reduce_only", False),
            close_on_trigger=data.get("close_on_trigger", False),
            post_only=data.get("post_only", False),
            status=OrderStatus(data.get("status", "PENDING")),
            filled_quantity=float(data.get("filled_quantity", 0)),
            remaining_quantity=float(data.get("remaining_quantity", 0)),
            executed_quantity=float(data.get("executed_quantity", 0)),
            average_price=float(data.get("average_price", 0)),
            last_fill_price=data.get("last_fill_price"),
            last_fill_quantity=data.get("last_fill_quantity"),
            commission=float(data.get("commission", 0)),
            commission_asset=data.get("commission_asset", "USDT"),
            fee_rate=data.get("fee_rate"),
            position_id=data.get("position_id"),
            position_side=data.get("position_side"),
            signal_id=data.get("signal_id"),
            strategy_name=data.get("strategy_name", ""),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            trailing_stop=data.get("trailing_stop"),
            submitted_at=data.get("submitted_at"),
            accepted_at=data.get("accepted_at"),
            filled_at=data.get("filled_at"),
            cancelled_at=data.get("cancelled_at"),
            expires_at=data.get("expires_at"),
            exchange=data.get("exchange", "unknown"),
            source=data.get("source", "unknown"),
            notes=data.get("notes"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            reject_reason=data.get("reject_reason"),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )

        # Set timestamps
        order.created_at = data.get("created_at", order.created_at)
        order.updated_at = data.get("updated_at", order.updated_at)

        return order

    def __repr__(self) -> str:
        """String representation."""
        status_icon = {
            OrderStatus.PENDING: "⏳",
            OrderStatus.NEW: "📝",
            OrderStatus.PARTIALLY_FILLED: "◐",
            OrderStatus.FILLED: "✓",
            OrderStatus.CANCELLED: "✗",
            OrderStatus.REJECTED: "⛔",
        }.get(self.status, "?")

        price_str = f"@{self.price:.4f}" if self.price else "@Market"
        fill_str = f" Fill:{self.fill_percentage:.1f}%" if self.filled_quantity > 0 else ""

        return (
            f"Order({self.id} {self.symbol} {self.side.value} "
            f"{self.quantity:.4f} {price_str} "
            f"{status_icon}{fill_str})"
        )


@dataclass
class OrderExecutionReport:
    """Order execution report."""

    order_id: str
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # Execution details
    executed_quantity: float = 0.0
    executed_price: float = 0.0
    commission: float = 0.0
    commission_asset: str = "USDT"

    # Status
    execution_type: str = "TRADE"  # TRADE, CANCELLED, REJECTED, EXPIRED
    order_status: OrderStatus = OrderStatus.NEW

    # Timestamps
    execution_time: int = field(default_factory=lambda: int(time.time() * 1000))

    # Additional info
    is_maker: bool = False
    trade_id: Optional[str] = None

    @property
    def execution_value(self) -> float:
        """Get total execution value."""
        return self.executed_quantity * self.executed_price

    @property
    def net_value(self) -> float:
        """Get net value after commission."""
        return self.execution_value - self.commission

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "execution_id": self.execution_id,
            "executed_quantity": self.executed_quantity,
            "executed_price": self.executed_price,
            "commission": self.commission,
            "commission_asset": self.commission_asset,
            "execution_type": self.execution_type,
            "order_status": self.order_status.value,
            "execution_time": self.execution_time,
            "is_maker": self.is_maker,
            "trade_id": self.trade_id,
        }
