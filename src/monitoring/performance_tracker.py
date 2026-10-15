"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : performance_tracker
    Project     : Project-103
    Description : Performance tracking module for trading bot. Tracks and records all trading operations and their results.

    SPDX-License-Identifier: AGPL-3.0

"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.models.order import Order
from src.models.position import Position
from src.models.trade import Trade


@dataclass
class PerformanceEvent:
    """Event representing a performance-related occurrence"""

    event_type: str  # "trade_opened", "trade_closed", "order_filled", "position_update"
    timestamp: int

    # Event details
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    pnl: Optional[float] = None

    # Related objects
    position: Optional[Position] = None
    trade: Optional[Trade] = None
    order: Optional[Order] = None

    # Metadata
    strategy_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountUpdate:
    """Account state update"""

    timestamp: int
    balance: float
    equity: float
    margin_used: float
    unrealized_pnl: float
    realized_pnl: float
    positions_count: int


@dataclass
class PerformanceSnapshot:
    """Performance snapshot for a specific period"""

    timestamp: int
    period: str  # "1h", "24h", "7d", "30d", "all"

    # Basic metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # P&L metrics
    gross_pnl: float
    net_pnl: float  # After commissions
    roi: float

    # Risk metrics
    max_drawdown: float
    current_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float

    # Average metrics
    avg_win: float
    avg_loss: float
    avg_holding_time: float  # seconds

    # Strategy breakdown
    strategy_breakdown: Dict[str, Dict[str, float]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to dictionary"""
        return {
            "timestamp": self.timestamp,
            "period": self.period,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "roi": self.roi,
            "max_drawdown": self.max_drawdown,
            "current_drawdown": self.current_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "avg_holding_time": self.avg_holding_time,
            "strategy_breakdown": self.strategy_breakdown,
        }


class PerformanceMetrics:
    """Calculator for performance metrics"""

    def __init__(self):
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0
        self.profit_factor = 0.0
        self.recovery_factor = 0.0
        self.payoff_ratio = 0.0

    def update(self, trades: List[Trade], equity_curve: List[Tuple[int, float]]) -> None:
        """Update metrics based on trades and equity curve"""
        if not trades:
            return

        self.total_trades = len(trades)
        self.winning_trades = len([t for t in trades if t.is_profitable])
        self.losing_trades = len([t for t in trades if not t.is_profitable])

        # P&L
        self.gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        self.gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

        # Profit factor
        if self.gross_loss > 0:
            self.profit_factor = self.gross_profit / self.gross_loss

        # Payoff ratio
        if self.losing_trades > 0 and self.winning_trades > 0:
            avg_win = self.gross_profit / self.winning_trades
            avg_loss = self.gross_loss / self.losing_trades
            self.payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # Consecutive wins/losses
        self._calculate_consecutive_stats(trades)

    def _calculate_consecutive_stats(self, trades: List[Trade]) -> None:
        """Calculate consecutive wins/losses"""
        current_wins = 0
        current_losses = 0

        for trade in trades:
            if trade.is_profitable:
                current_wins += 1
                current_losses = 0
                self.max_consecutive_wins = max(self.max_consecutive_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                self.max_consecutive_losses = max(self.max_consecutive_losses, current_losses)


class PerformanceTracker:
    """Performance tracking system"""

    def __init__(self, initial_balance: float = 10000.0):
        """
        Initialize performance tracker

        Args:
            initial_balance: Starting account balance
        """
        self.initial_balance = initial_balance
        self.current_balance = initial_balance

        # History
        self.trades: List[Trade] = []
        self.events: List[PerformanceEvent] = []
        self.snapshots: List[PerformanceSnapshot] = []
        self.equity_curve: List[Tuple[int, float]] = [(int(time.time() * 1000), initial_balance)]

        # Current state
        self.open_positions: Dict[str, Position] = {}
        self.daily_stats: Dict[str, Dict[str, Any]] = {}

        # Metrics
        self.metrics = PerformanceMetrics()

    def track_event(self, event: PerformanceEvent) -> None:
        """
        Track a performance event

        Args:
            event: Performance event to track
        """
        self.events.append(event)

        # Process based on event type
        if event.event_type == "trade_closed" and event.trade:
            self._process_closed_trade(event)
        elif event.event_type == "trade_opened" and event.position:
            self._process_opened_trade(event)
        elif event.event_type == "position_update" and event.position:
            self._update_position(event)

        # Update metrics
        self._update_metrics()

    def _process_closed_trade(self, event: PerformanceEvent) -> None:
        """Process a closed trade"""
        if not event.trade:
            return

        trade = event.trade
        self.trades.append(trade)

        # Update balance
        self.current_balance += trade.net_pnl

        # Update equity curve
        self.equity_curve.append((event.timestamp, self.current_balance))

        # Update daily stats
        self._update_daily_stats(trade)

        # Remove from open positions if exists
        if trade.position_id in self.open_positions:
            del self.open_positions[trade.position_id]

    def _process_opened_trade(self, event: PerformanceEvent) -> None:
        """Process an opened trade/position"""
        if not event.position:
            return

        position = event.position
        self.open_positions[position.id] = position

    def _update_position(self, event: PerformanceEvent) -> None:
        """Update position state"""
        if not event.position:
            return

        position = event.position
        if position.id in self.open_positions:
            self.open_positions[position.id] = position

    def _update_daily_stats(self, trade: Trade) -> None:
        """Update daily statistics"""
        if not trade.exit_time:
            return

        date = datetime.fromtimestamp(trade.exit_time / 1000).date()
        date_str = date.strftime("%Y-%m-%d")

        if date_str not in self.daily_stats:
            self.daily_stats[date_str] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "gross_pnl": 0,
                "net_pnl": 0,
                "commission": 0,
            }

        stats = self.daily_stats[date_str]
        stats["trades"] += 1
        stats["wins"] += 1 if trade.is_profitable else 0
        stats["losses"] += 1 if not trade.is_profitable else 0
        stats["gross_pnl"] += trade.pnl
        stats["net_pnl"] += trade.net_pnl
        stats["commission"] += trade.total_commission

    def _update_metrics(self) -> None:
        """Update performance metrics"""
        self.metrics.update(self.trades, self.equity_curve)

    def get_performance_snapshot(self, period: str = "24h") -> PerformanceSnapshot:
        """
        Get performance snapshot for a period

        Args:
            period: Time period ("1h", "24h", "7d", "30d", "all")

        Returns:
            PerformanceSnapshot: Performance snapshot
        """
        # Filter trades by period
        cutoff_time = self._get_cutoff_time(period)
        period_trades = [t for t in self.trades if t.exit_time >= cutoff_time]

        if not period_trades:
            return self._empty_snapshot(period)

        # Calculate metrics
        winning = [t for t in period_trades if t.is_profitable]
        losing = [t for t in period_trades if not t.is_profitable]

        # P&L
        gross_pnl = sum(t.pnl for t in period_trades)
        net_pnl = sum(t.net_pnl for t in period_trades)

        # Averages
        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0
        avg_holding = sum(t.hold_time for t in period_trades) / len(period_trades)

        # Risk metrics
        equity_period = [(t, e) for t, e in self.equity_curve if t >= cutoff_time]
        max_dd = self._calculate_max_drawdown(equity_period)
        sharpe = self._calculate_sharpe_ratio(period_trades)
        sortino = self._calculate_sortino_ratio(period_trades)

        # Breakdown by strategies
        strategy_breakdown = self._get_strategy_breakdown(period_trades)

        return PerformanceSnapshot(
            timestamp=int(time.time() * 1000),
            period=period,
            total_trades=len(period_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=len(winning) / len(period_trades) * 100 if period_trades else 0,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            roi=(net_pnl / self.initial_balance) * 100 if self.initial_balance > 0 else 0,
            max_drawdown=max_dd,
            current_drawdown=self._get_current_drawdown(),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_holding_time=avg_holding,
            strategy_breakdown=strategy_breakdown,
        )

    def _get_cutoff_time(self, period: str) -> int:
        """Get cutoff timestamp for period"""
        now = int(time.time() * 1000)

        period_map = {
            "1h": 3600 * 1000,
            "24h": 24 * 3600 * 1000,
            "7d": 7 * 24 * 3600 * 1000,
            "30d": 30 * 24 * 3600 * 1000,
            "all": 0,
        }

        offset = period_map.get(period, 0)
        return now - offset if offset > 0 else 0

    def _empty_snapshot(self, period: str) -> PerformanceSnapshot:
        """Create empty snapshot"""
        return PerformanceSnapshot(
            timestamp=int(time.time() * 1000),
            period=period,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            gross_pnl=0,
            net_pnl=0,
            roi=0,
            max_drawdown=0,
            current_drawdown=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            avg_win=0,
            avg_loss=0,
            avg_holding_time=0,
            strategy_breakdown={},
        )

    def _calculate_max_drawdown(self, equity_curve: List[Tuple[int, float]]) -> float:
        """Calculate maximum drawdown"""
        if not equity_curve:
            return 0

        peak = equity_curve[0][1]
        max_dd = 0

        for _, equity in equity_curve:
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, drawdown)

        return max_dd

    def _get_current_drawdown(self) -> float:
        """Calculate current drawdown"""
        if not self.equity_curve:
            return 0

        peak = max(e for _, e in self.equity_curve)
        current = self.equity_curve[-1][1]

        if peak == 0:
            return 0

        return (peak - current) / peak * 100

    def _calculate_sharpe_ratio(self, trades: List[Trade], risk_free_rate: float = 0) -> float:
        """Calculate Sharpe ratio"""
        if not trades:
            return 0

        returns = [t.pnl_percentage for t in trades]
        avg_return = np.mean(returns)
        std_return = np.std(returns)

        if std_return == 0:
            return 0

        return (avg_return - risk_free_rate) / std_return

    def _calculate_sortino_ratio(self, trades: List[Trade], target_return: float = 0) -> float:
        """Calculate Sortino ratio"""
        if not trades:
            return 0

        returns = [t.pnl_percentage for t in trades]
        avg_return = np.mean(returns)

        # Only negative deviations
        downside_returns = [r for r in returns if r < target_return]
        if not downside_returns:
            return float("inf") if avg_return > target_return else 0

        downside_std = np.std(downside_returns)

        if downside_std == 0:
            return 0

        return (avg_return - target_return) / downside_std

    def _get_strategy_breakdown(self, trades: List[Trade]) -> Dict[str, Dict[str, float]]:
        """Get breakdown by strategies"""
        breakdown = {}

        for trade in trades:
            strategy = trade.strategy_name or "unknown"

            if strategy not in breakdown:
                breakdown[strategy] = {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0}

            breakdown[strategy]["trades"] += 1
            breakdown[strategy]["wins"] += 1 if trade.is_profitable else 0
            breakdown[strategy]["pnl"] += trade.net_pnl

        # Calculate win rate
        for strategy in breakdown:
            if breakdown[strategy]["trades"] > 0:
                breakdown[strategy]["win_rate"] = (
                    breakdown[strategy]["wins"] / breakdown[strategy]["trades"] * 100
                )

        return breakdown

    def get_equity_curve(self) -> pd.DataFrame:
        """
        Get equity curve as DataFrame

        Returns:
            pd.DataFrame: Equity curve with metrics
        """
        if not self.equity_curve:
            return pd.DataFrame()

        df = pd.DataFrame(self.equity_curve, columns=["timestamp", "equity"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

        # Add metrics
        df["returns"] = df["equity"].pct_change()
        df["cumulative_returns"] = (1 + df["returns"]).cumprod() - 1
        df["drawdown"] = (df["equity"] / df["equity"].cummax() - 1) * 100

        return df

    def get_trade_analysis(self) -> pd.DataFrame:
        """
        Get detailed trade analysis

        Returns:
            pd.DataFrame: Trade analysis
        """
        if not self.trades:
            return pd.DataFrame()

        data = []
        for trade in self.trades:
            data.append(
                {
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "entry": trade.entry_price,
                    "exit": trade.exit_price,
                    "quantity": trade.entry_quantity,
                    "pnl": trade.pnl,
                    "pnl_percent": trade.pnl_percentage,
                    "commission": trade.total_commission,
                    "net_pnl": trade.net_pnl,
                    "holding_time_hours": trade.hold_time / 3600 if trade.hold_time else 0,
                    "strategy": trade.strategy_name,
                    "exit_reason": trade.exit_reason,
                    "opened_at": pd.to_datetime(trade.entry_time, unit="ms"),
                    "closed_at": pd.to_datetime(trade.exit_time, unit="ms"),
                }
            )

        df = pd.DataFrame(data)

        # Add analytical columns
        df["cumulative_pnl"] = df["net_pnl"].cumsum()
        df["trade_number"] = range(1, len(df) + 1)
        df["is_winning"] = df["pnl"] > 0

        return df
