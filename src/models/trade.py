"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : trade
    Project     : Project-103
    Description : Trade model for completed trades. This module provides trade data models for tracking completed trades.

    SPDX-License-Identifier: AGPL-3.0

"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.models.base import BaseModel, Identifiable, SerializableMixin, Timestamped, TimestampMixin

if TYPE_CHECKING:
    from src.models.position import Position


@dataclass
class Trade(BaseModel, TimestampMixin, SerializableMixin, Timestamped, Identifiable):
    """Completed trade model."""

    # Identifiers
    id: str = field(default_factory=lambda: f"TRD_{uuid.uuid4().hex[:12].upper()}")
    position_id: Optional[str] = None
    signal_id: Optional[str] = None

    # Trade details
    symbol: str = ""
    side: str = ""  # LONG or SHORT

    # Entry
    entry_price: float = 0.0
    entry_quantity: float = 0.0
    entry_time: int = 0
    entry_order_ids: List[str] = field(default_factory=list)

    # Exit
    exit_price: float = 0.0
    exit_quantity: float = 0.0
    exit_time: int = 0
    exit_order_ids: List[str] = field(default_factory=list)

    # P&L
    pnl: float = 0.0
    pnl_percentage: float = 0.0

    # Fees
    entry_commission: float = 0.0
    exit_commission: float = 0.0
    total_commission: float = 0.0

    # Risk management
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_profit: float = 0.0
    max_loss: float = 0.0
    max_drawdown: float = 0.0

    # Strategy
    strategy_name: str = ""
    strategy_version: str = ""

    # Statistics
    hold_time: int = 0  # seconds
    risk_reward_ratio: Optional[float] = None

    # Exit reason
    exit_reason: str = ""  # manual, stop_loss, take_profit, signal, etc.

    # Metadata
    exchange: str = "unknown"
    leverage: int = 1
    notes: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """Validate trade data."""
        errors = []

        if not self.symbol:
            errors.append("Symbol is required")

        if self.entry_price <= 0:
            errors.append("Entry price must be positive")

        if self.entry_quantity <= 0:
            errors.append("Entry quantity must be positive")

        if self.exit_price <= 0:
            errors.append("Exit price must be positive")

        if self.exit_time < self.entry_time:
            errors.append("Exit time cannot be before entry time")

        return errors

    @property
    def is_profitable(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl > 0

    @property
    def entry_value(self) -> float:
        """Get entry value."""
        return self.entry_price * self.entry_quantity

    @property
    def exit_value(self) -> float:
        """Get exit value."""
        return self.exit_price * self.exit_quantity

    @property
    def net_pnl(self) -> float:
        """Get net P&L after fees."""
        return self.pnl - self.total_commission

    @property
    def net_pnl_percentage(self) -> float:
        """Get net P&L percentage."""
        if self.entry_value == 0:
            return 0
        return (self.net_pnl / self.entry_value) * 100

    @property
    def return_on_investment(self) -> float:
        """Get ROI."""
        if self.entry_value == 0:
            return 0
        return (self.net_pnl / self.entry_value) * 100

    @property
    def commission_percentage(self) -> float:
        """Get commission as percentage of trade value."""
        total_value = self.entry_value + self.exit_value
        if total_value == 0:
            return 0
        return (self.total_commission / total_value) * 100

    def calculate_metrics(self) -> None:
        """Calculate trade metrics."""
        # Calculate P&L
        if self.side == "LONG":
            self.pnl = (self.exit_price - self.entry_price) * self.entry_quantity
        else:  # SHORT
            self.pnl = (self.entry_price - self.exit_price) * self.entry_quantity

        # Calculate P&L percentage
        if self.entry_value > 0:
            self.pnl_percentage = (self.pnl / self.entry_value) * 100

        # Calculate total commission
        self.total_commission = self.entry_commission + self.exit_commission

        # Calculate hold time
        if self.exit_time > self.entry_time:
            self.hold_time = (self.exit_time - self.entry_time) // 1000

        # Calculate risk/reward if stop loss was set
        if self.stop_loss:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.exit_price - self.entry_price)
            if risk > 0:
                self.risk_reward_ratio = reward / risk

    @classmethod
    def from_position(cls, position: "Position", **kwargs) -> "Trade":
        """
        Create trade from closed position.

        Args:
            position: Closed position
            **kwargs: Additional parameters

        Returns:
            Trade: Created trade
        """
        trade = cls(
            position_id=position.id,
            signal_id=position.signal_id,
            symbol=position.symbol,
            side=position.side.value,
            entry_price=position.entry_price,
            entry_quantity=position.quantity,
            entry_time=position.opened_at or position.created_at,
            entry_order_ids=position.entry_order_ids,
            exit_price=position.exit_price or position.mark_price,
            exit_quantity=position.quantity,
            exit_time=position.closed_at or int(time.time() * 1000),
            exit_order_ids=position.exit_order_ids,
            pnl=position.realized_pnl,
            pnl_percentage=position.pnl_percentage,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit[0] if position.take_profit else None,
            max_profit=position.max_profit,
            max_loss=position.max_loss,
            strategy_name=position.strategy_name,
            hold_time=position.hold_time,
            exit_reason=position.close_reason or "",
            exchange=position.exchange,
            leverage=position.leverage,
            notes=position.notes,
            tags=position.tags,
            metadata=position.metadata,
            **kwargs,
        )

        trade.calculate_metrics()
        return trade

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trade":
        """Create trade from dictionary."""
        trade = cls(
            id=data.get("id", f"TRD_{uuid.uuid4().hex[:12].upper()}"),
            position_id=data.get("position_id"),
            signal_id=data.get("signal_id"),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            entry_price=float(data.get("entry_price", 0)),
            entry_quantity=float(data.get("entry_quantity", 0)),
            entry_time=int(data.get("entry_time", 0)),
            entry_order_ids=data.get("entry_order_ids", []),
            exit_price=float(data.get("exit_price", 0)),
            exit_quantity=float(data.get("exit_quantity", 0)),
            exit_time=int(data.get("exit_time", 0)),
            exit_order_ids=data.get("exit_order_ids", []),
            pnl=float(data.get("pnl", 0)),
            pnl_percentage=float(data.get("pnl_percentage", 0)),
            entry_commission=float(data.get("entry_commission", 0)),
            exit_commission=float(data.get("exit_commission", 0)),
            total_commission=float(data.get("total_commission", 0)),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            max_profit=float(data.get("max_profit", 0)),
            max_loss=float(data.get("max_loss", 0)),
            max_drawdown=float(data.get("max_drawdown", 0)),
            strategy_name=data.get("strategy_name", ""),
            strategy_version=data.get("strategy_version", ""),
            hold_time=int(data.get("hold_time", 0)),
            risk_reward_ratio=data.get("risk_reward_ratio"),
            exit_reason=data.get("exit_reason", ""),
            exchange=data.get("exchange", "unknown"),
            leverage=int(data.get("leverage", 1)),
            notes=data.get("notes"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

        # Set timestamps
        trade.created_at = data.get("created_at", trade.created_at)
        trade.updated_at = data.get("updated_at", trade.updated_at)

        return trade

    def __repr__(self) -> str:
        """String representation."""
        pnl_str = f"+${self.pnl:.2f}" if self.pnl >= 0 else f"-${abs(self.pnl):.2f}"
        pnl_pct = f"({self.pnl_percentage:+.2f}%)"

        return (
            f"Trade({self.id} {self.symbol} {self.side} "
            f"{pnl_str} {pnl_pct} "
            f"Hold:{self.hold_time}s)"
        )


@dataclass
class TradeResult:
    """Trade execution result."""

    trade: Trade
    success: bool = True
    error: Optional[str] = None

    # Execution details
    execution_time: float = 0.0  # seconds
    slippage: float = 0.0  # percentage

    # Orders executed
    orders_placed: int = 0
    orders_filled: int = 0
    orders_failed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "trade": self.trade.to_dict(),
            "success": self.success,
            "error": self.error,
            "execution_time": self.execution_time,
            "slippage": self.slippage,
            "orders_placed": self.orders_placed,
            "orders_filled": self.orders_filled,
            "orders_failed": self.orders_failed,
        }


@dataclass
class TradeStatistics:
    """Trade statistics and performance metrics."""

    # Count statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    # P&L statistics
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0

    # Performance metrics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # Average metrics
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_pnl: float = 0.0
    avg_hold_time: int = 0  # seconds
    avg_risk_reward: float = 0.0

    # Best/Worst
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    longest_winning_streak: int = 0
    longest_losing_streak: int = 0
    current_streak: int = 0

    # Risk metrics
    max_drawdown: float = 0.0
    max_drawdown_percentage: float = 0.0
    recovery_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # Time analysis
    trades_by_hour: Dict[int, int] = field(default_factory=dict)
    trades_by_day: Dict[str, int] = field(default_factory=dict)
    trades_by_month: Dict[str, int] = field(default_factory=dict)

    def add_trade(self, trade: Trade) -> None:
        """Add trade to statistics."""
        self.total_trades += 1
        self.total_pnl += trade.pnl
        self.total_commission += trade.total_commission
        self.net_pnl = self.total_pnl - self.total_commission

        if trade.is_profitable:
            self.winning_trades += 1
            self.gross_profit += trade.pnl

            # Update streak
            if self.current_streak >= 0:
                self.current_streak += 1
                self.longest_winning_streak = max(self.longest_winning_streak, self.current_streak)
            else:
                self.current_streak = 1
        else:
            self.losing_trades += 1
            self.gross_loss += abs(trade.pnl)

            # Update streak
            if self.current_streak <= 0:
                self.current_streak -= 1
                self.longest_losing_streak = max(
                    self.longest_losing_streak, abs(self.current_streak)
                )
            else:
                self.current_streak = -1

        # Update best/worst
        self.best_trade_pnl = max(self.best_trade_pnl, trade.pnl)
        self.worst_trade_pnl = min(self.worst_trade_pnl, trade.pnl)

        # Update time analysis
        trade_time = datetime.fromtimestamp(trade.exit_time / 1000)
        hour = trade_time.hour
        day = trade_time.strftime("%A")
        month = trade_time.strftime("%Y-%m")

        self.trades_by_hour[hour] = self.trades_by_hour.get(hour, 0) + 1
        self.trades_by_day[day] = self.trades_by_day.get(day, 0) + 1
        self.trades_by_month[month] = self.trades_by_month.get(month, 0) + 1

        # Update metrics
        self.update_metrics()

    def update_metrics(self) -> None:
        """Update calculated metrics."""
        if self.total_trades == 0:
            return

        # Win rate
        self.win_rate = self.winning_trades / self.total_trades

        # Averages
        if self.winning_trades > 0:
            self.avg_win = self.gross_profit / self.winning_trades

        if self.losing_trades > 0:
            self.avg_loss = self.gross_loss / self.losing_trades

        self.avg_pnl = self.net_pnl / self.total_trades

        # Profit factor
        if self.gross_loss > 0:
            self.profit_factor = self.gross_profit / self.gross_loss
        elif self.gross_profit > 0:
            self.profit_factor = float("inf")

        # Expectancy
        self.expectancy = (self.win_rate * self.avg_win) - ((1 - self.win_rate) * self.avg_loss)

        # Recovery factor
        if self.max_drawdown != 0:
            self.recovery_factor = self.net_pnl / abs(self.max_drawdown)

    def calculate_sharpe_ratio(self, returns: List[float], risk_free_rate: float = 0.02) -> float:
        """
        Calculate Sharpe ratio.

        Args:
            returns: List of returns
            risk_free_rate: Annual risk-free rate

        Returns:
            float: Sharpe ratio
        """
        if not returns:
            return 0

        import numpy as np

        returns_array = np.array(returns)
        avg_return = np.mean(returns_array)
        std_return = np.std(returns_array)

        if std_return == 0:
            return 0

        # Annualized Sharpe ratio (assuming daily returns)
        self.sharpe_ratio = np.sqrt(252) * (avg_return - risk_free_rate / 252) / std_return

        return self.sharpe_ratio

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_pnl": self.total_pnl,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "total_commission": self.total_commission,
            "net_pnl": self.net_pnl,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "avg_pnl": self.avg_pnl,
            "avg_hold_time": self.avg_hold_time,
            "avg_risk_reward": self.avg_risk_reward,
            "best_trade_pnl": self.best_trade_pnl,
            "worst_trade_pnl": self.worst_trade_pnl,
            "longest_winning_streak": self.longest_winning_streak,
            "longest_losing_streak": self.longest_losing_streak,
            "current_streak": self.current_streak,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_percentage": self.max_drawdown_percentage,
            "recovery_factor": self.recovery_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
        }
