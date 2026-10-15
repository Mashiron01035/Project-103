"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : signal
    Project     : Project-103
    Description : Trading signal model. This module provides trading signal data models.

    SPDX-License-Identifier: AGPL-3.0

"""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.base import BaseModel, Identifiable, SerializableMixin, Timestamped, TimestampMixin


class SignalType(Enum):
    """Signal type enumeration."""

    BUY = "BUY"
    SELL = "SELL"
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    REDUCE = "REDUCE"
    REVERSE = "REVERSE"
    HEDGE = "HEDGE"


class SignalStrength(Enum):
    """Signal strength enumeration."""

    VERY_WEAK = "VERY_WEAK"
    WEAK = "WEAK"
    NEUTRAL = "NEUTRAL"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"

    @classmethod
    def from_score(cls, score: float) -> "SignalStrength":
        """
        Get strength from numeric score.

        Args:
            score: Strength score (0-1)

        Returns:
            SignalStrength: Corresponding strength level
        """
        if score < 0.2:
            return cls.VERY_WEAK
        elif score < 0.4:
            return cls.WEAK
        elif score < 0.6:
            return cls.NEUTRAL
        elif score < 0.8:
            return cls.STRONG
        else:
            return cls.VERY_STRONG

    def to_score(self) -> float:
        """
        Convert to numeric score.

        Returns:
            float: Numeric score (0-1)
        """
        mapping = {
            self.VERY_WEAK: 0.1,
            self.WEAK: 0.3,
            self.NEUTRAL: 0.5,
            self.STRONG: 0.7,
            self.VERY_STRONG: 0.9,
        }
        return mapping[self]


class SignalStatus(Enum):
    """Signal status enumeration."""

    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass
class Signal(BaseModel, TimestampMixin, SerializableMixin, Timestamped, Identifiable):
    """Trading signal model."""

    # Identifiers
    id: str = field(default_factory=lambda: f"SIG_{uuid.uuid4().hex[:12].upper()}")

    # Signal details
    symbol: str = ""
    type: SignalType = SignalType.BUY
    strength: SignalStrength = SignalStrength.NEUTRAL
    confidence: float = 0.5  # Confidence score (0-1)

    # Pricing
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: List[float] = field(default_factory=list)  # Multiple TP levels

    # Risk management
    position_size: Optional[float] = None
    risk_amount: Optional[float] = None
    risk_percentage: Optional[float] = None
    leverage: int = 1

    # Strategy info
    strategy_name: str = ""
    strategy_version: str = ""
    timeframe: str = ""

    # Indicators that triggered signal
    indicators: Dict[str, Any] = field(default_factory=dict)
    conditions: List[str] = field(default_factory=list)

    # Status
    status: SignalStatus = SignalStatus.PENDING

    # Validation
    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)
    validation_score: float = 0.0

    # Execution
    order_ids: List[str] = field(default_factory=list)
    executed_price: Optional[float] = None
    executed_size: Optional[float] = None
    execution_time: Optional[int] = None

    # Expiration
    expires_at: Optional[int] = None
    ttl_seconds: Optional[int] = None

    # Performance tracking
    pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None

    # Metadata
    source: str = "unknown"
    exchange: str = "unknown"
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """
        Validate signal data.

        Returns:
            List[str]: Validation errors
        """
        errors = []

        # Check required fields
        if not self.symbol:
            errors.append("Symbol is required")

        # Check confidence
        if not 0 <= self.confidence <= 1:
            errors.append("Confidence must be between 0 and 1")

        # Check stop loss for risk management
        if self.type in [SignalType.LONG, SignalType.BUY]:
            if self.stop_loss and self.entry_price:
                if self.stop_loss >= self.entry_price:
                    errors.append("Stop loss must be below entry for long/buy")
        elif self.type in [SignalType.SHORT, SignalType.SELL]:
            if self.stop_loss and self.entry_price:
                if self.stop_loss <= self.entry_price:
                    errors.append("Stop loss must be above entry for short/sell")

        # Check take profit
        if self.take_profit and self.entry_price:
            for tp in self.take_profit:
                if self.type in [SignalType.LONG, SignalType.BUY]:
                    if tp <= self.entry_price:
                        errors.append("Take profit must be above entry for long/buy")
                elif self.type in [SignalType.SHORT, SignalType.SELL]:
                    if tp >= self.entry_price:
                        errors.append("Take profit must be below entry for short/sell")

        # Check risk
        if self.risk_percentage:
            if not 0 < self.risk_percentage <= 100:
                errors.append("Risk percentage must be between 0 and 100")

        # Check leverage
        if self.leverage < 1:
            errors.append("Leverage must be >= 1")

        self.validation_errors = errors
        self.is_valid = len(errors) == 0

        return errors

    @property
    def is_expired(self) -> bool:
        """Check if signal has expired."""
        if self.expires_at:
            import time

            return time.time() * 1000 > self.expires_at
        if self.ttl_seconds:
            return self.age_seconds > self.ttl_seconds
        return False

    @property
    def side(self) -> str:
        """Get signal side (BUY/SELL) for compatibility with lifecycle manager."""
        if self.type in [SignalType.LONG, SignalType.BUY]:
            return "BUY"
        elif self.type in [SignalType.SHORT, SignalType.SELL]:
            return "SELL"
        return self.type.value

    @property
    def timestamp(self) -> int:
        """Get signal timestamp for compatibility with history module."""
        return self.created_at

    @property
    def signal_type(self) -> SignalType:
        """Get signal type for compatibility with history module."""
        return self.type

    @property
    def is_active(self) -> bool:
        """Check if signal is active."""
        return (
            self.status in [SignalStatus.PENDING, SignalStatus.VALIDATED, SignalStatus.EXECUTING]
            and not self.is_expired
        )

    @property
    def is_executed(self) -> bool:
        """Check if signal is executed."""
        return self.status in [SignalStatus.EXECUTED, SignalStatus.PARTIALLY_EXECUTED]

    @property
    def risk_reward_ratio(self) -> Optional[float]:
        """Calculate risk/reward ratio."""
        if not (self.entry_price and self.stop_loss and self.take_profit):
            return None

        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit[0] - self.entry_price) if self.take_profit else 0

        if risk == 0:
            return None

        return reward / risk

    @property
    def stop_loss_percentage(self) -> Optional[float]:
        """Get stop loss distance as percentage."""
        if not (self.entry_price and self.stop_loss):
            return None

        return abs(self.entry_price - self.stop_loss) / self.entry_price * 100

    @property
    def take_profit_percentage(self) -> Optional[float]:
        """Get first take profit distance as percentage."""
        if not (self.entry_price and self.take_profit):
            return None

        return abs(self.take_profit[0] - self.entry_price) / self.entry_price * 100

    def calculate_position_size(
        self, account_balance: float, risk_percentage: Optional[float] = None
    ) -> float:
        """
        Calculate position size based on risk.

        Args:
            account_balance: Account balance
            risk_percentage: Risk percentage (uses signal's if not provided)

        Returns:
            float: Position size
        """
        risk_pct = risk_percentage or self.risk_percentage or 1.0
        risk_amount = account_balance * (risk_pct / 100)

        if self.stop_loss and self.entry_price:
            stop_distance = abs(self.entry_price - self.stop_loss)
            if stop_distance > 0:
                position_size = risk_amount / stop_distance
                return position_size

        # Fallback to risk amount divided by entry
        if self.entry_price:
            return risk_amount / self.entry_price

        return 0

    def update_status(self, status: SignalStatus) -> None:
        """
        Update signal status.

        Args:
            status: New status
        """
        self.status = status
        self.touch()

    def add_order(self, order_id: str) -> None:
        """
        Add order ID to signal.

        Args:
            order_id: Order ID
        """
        if order_id not in self.order_ids:
            self.order_ids.append(order_id)
            self.touch()

    def set_execution(self, price: float, size: float, timestamp: Optional[int] = None) -> None:
        """
        Set execution details.

        Args:
            price: Execution price
            size: Executed size
            timestamp: Execution timestamp
        """
        self.executed_price = price
        self.executed_size = size
        if timestamp:
            self.execution_time = timestamp
        else:
            import time

            self.execution_time = int(time.time() * 1000)

        self.status = SignalStatus.EXECUTED
        self.touch()

    def calculate_pnl(self, current_price: float) -> float:
        """
        Calculate P&L for executed signal.

        Args:
            current_price: Current market price

        Returns:
            float: P&L amount
        """
        if not (self.executed_price and self.executed_size):
            return 0

        if self.type in [SignalType.LONG, SignalType.BUY]:
            pnl = (current_price - self.executed_price) * self.executed_size
        else:  # SHORT/SELL
            pnl = (self.executed_price - current_price) * self.executed_size

        self.pnl = pnl
        self.pnl_percentage = (pnl / (self.executed_price * self.executed_size)) * 100

        # Track max profit/loss
        if self.max_profit is None or pnl > self.max_profit:
            self.max_profit = pnl
        if self.max_loss is None or pnl < self.max_loss:
            self.max_loss = pnl

        return pnl

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Signal":
        """Create signal from dictionary."""
        signal = cls(
            id=data.get("id", f"SIG_{uuid.uuid4().hex[:12].upper()}"),
            symbol=data.get("symbol", ""),
            type=SignalType(data.get("type", "BUY")),
            strength=SignalStrength(data.get("strength", "NEUTRAL")),
            confidence=float(data.get("confidence", 0.5)),
            entry_price=data.get("entry_price"),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit", []),
            position_size=data.get("position_size"),
            risk_amount=data.get("risk_amount"),
            risk_percentage=data.get("risk_percentage"),
            leverage=int(data.get("leverage", 1)),
            strategy_name=data.get("strategy_name", ""),
            strategy_version=data.get("strategy_version", ""),
            timeframe=data.get("timeframe", ""),
            indicators=data.get("indicators", {}),
            conditions=data.get("conditions", []),
            status=SignalStatus(data.get("status", "PENDING")),
            is_valid=data.get("is_valid", False),
            validation_errors=data.get("validation_errors", []),
            validation_score=float(data.get("validation_score", 0)),
            order_ids=data.get("order_ids", []),
            executed_price=data.get("executed_price"),
            executed_size=data.get("executed_size"),
            execution_time=data.get("execution_time"),
            expires_at=data.get("expires_at"),
            ttl_seconds=data.get("ttl_seconds"),
            pnl=data.get("pnl"),
            pnl_percentage=data.get("pnl_percentage"),
            max_profit=data.get("max_profit"),
            max_loss=data.get("max_loss"),
            source=data.get("source", "unknown"),
            exchange=data.get("exchange", "unknown"),
            notes=data.get("notes"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

        # Set timestamps
        signal.created_at = data.get("created_at", signal.created_at)
        signal.updated_at = data.get("updated_at", signal.updated_at)

        return signal

    def __repr__(self) -> str:
        """String representation."""
        status_icon = "✓" if self.is_valid else "✗"
        strength_icon = "●" * int(self.strength.to_score() * 5)

        return (
            f"Signal({self.id} {self.symbol} {self.type.value} "
            f"{status_icon} {strength_icon} "
            f"Conf:{self.confidence:.2f})"
        )


@dataclass
class SignalMetrics:
    """Signal performance metrics."""

    total_signals: int = 0
    executed_signals: int = 0
    winning_signals: int = 0
    losing_signals: int = 0

    total_pnl: float = 0.0
    total_profit: float = 0.0
    total_loss: float = 0.0

    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0

    avg_risk_reward: float = 0.0
    avg_confidence: float = 0.0

    by_type: Dict[SignalType, Dict[str, float]] = field(default_factory=dict)
    by_strategy: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def add_signal(self, signal: Signal) -> None:
        """
        Add signal to metrics.

        Args:
            signal: Signal to add
        """
        self.total_signals += 1

        if signal.is_executed:
            self.executed_signals += 1

            if signal.pnl:
                self.total_pnl += signal.pnl

                if signal.pnl > 0:
                    self.winning_signals += 1
                    self.total_profit += signal.pnl
                else:
                    self.losing_signals += 1
                    self.total_loss += abs(signal.pnl)

        # Update averages
        self.update_averages()

    def update_averages(self) -> None:
        """Update average metrics."""
        if self.executed_signals > 0:
            self.win_rate = self.winning_signals / self.executed_signals

        if self.winning_signals > 0:
            self.avg_profit = self.total_profit / self.winning_signals

        if self.losing_signals > 0:
            self.avg_loss = self.total_loss / self.losing_signals

        if self.avg_loss > 0:
            self.profit_factor = self.avg_profit / self.avg_loss

    def get_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        """
        Calculate Sharpe ratio.

        Args:
            risk_free_rate: Annual risk-free rate

        Returns:
            float: Sharpe ratio
        """
        if self.executed_signals == 0:
            return 0

        # Simplified Sharpe calculation
        avg_return = self.total_pnl / self.executed_signals

        # Calculate standard deviation
        # This would need individual signal returns for accuracy
        # Using simplified estimation
        std_dev = abs(self.avg_profit - self.avg_loss) / 2

        if std_dev == 0:
            return 0

        return (avg_return - risk_free_rate) / std_dev
