"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : history.py
    Project     : Project-103
    Description : Signal history tracking and analytics. Maintains historical record of signals and their outcomes.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.models.signal import Signal, SignalStatus, SignalType

logger = logging.getLogger(__name__)


@dataclass
class SignalOutcome:
    """Outcome of a signal execution."""

    signal_id: str
    executed: bool
    execution_timestamp: Optional[int]
    exit_timestamp: Optional[int]

    # Execution details
    entry_price_actual: Optional[float]
    exit_price_actual: Optional[float]
    slippage_percent: Optional[float]

    # P&L
    pnl: Optional[float]
    pnl_percent: Optional[float]
    realized: bool

    # Exit reason
    exit_reason: Optional[str]  # "stop_loss", "take_profit", "manual", "timeout"

    # Metadata
    metadata: Dict[str, Any]


@dataclass
class SignalStats:
    """Statistics for signals."""

    total_signals: int
    executed_signals: int
    pending_signals: int
    rejected_signals: int
    expired_signals: int

    # By type
    long_signals: int
    short_signals: int

    # Performance
    winning_signals: int
    losing_signals: int
    win_rate: float

    # P&L
    total_pnl: float
    avg_pnl: float
    max_profit: float
    max_loss: float

    # Timing
    avg_holding_time: float  # seconds


class SignalHistory:
    """
    Tracks historical signals and their outcomes.

    Provides analytics and performance metrics for generated signals.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize SignalHistory.

        Args:
            config: History configuration
        """
        self.config = config or {}

        # Storage
        self.signals: Dict[str, Signal] = {}
        self.outcomes: Dict[str, SignalOutcome] = {}

        # Retention settings
        self.max_history_days = self.config.get("max_history_days", 90)
        self.auto_cleanup = self.config.get("auto_cleanup", True)

        logger.info("SignalHistory initialized")

    def add_signal(self, signal: Signal) -> None:
        """
        Add a signal to history.

        Args:
            signal: Signal to add
        """
        self.signals[signal.id] = signal
        logger.debug(f"Added signal {signal.id} to history")

        # Auto cleanup if enabled
        if self.auto_cleanup:
            self._cleanup_old_signals()

    def update_signal_status(
        self, signal_id: str, status: SignalStatus, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update signal status.

        Args:
            signal_id: Signal ID
            status: New status
            metadata: Optional metadata to add

        Returns:
            True if updated, False if signal not found
        """
        if signal_id not in self.signals:
            logger.warning(f"Signal {signal_id} not found in history")
            return False

        signal = self.signals[signal_id]
        signal.status = status
        signal.touch()  # Update timestamp

        if metadata:
            signal.metadata.update(metadata)

        logger.debug(f"Updated signal {signal_id} status to {status.value}")
        return True

    def add_outcome(self, outcome: SignalOutcome) -> None:
        """
        Add outcome for a signal.

        Args:
            outcome: Signal outcome
        """
        if outcome.signal_id not in self.signals:
            logger.warning(f"Cannot add outcome for unknown signal {outcome.signal_id}")
            return

        self.outcomes[outcome.signal_id] = outcome
        pnl_str = f"{outcome.pnl:.2f}" if outcome.pnl is not None else "N/A"
        logger.info(f"Added outcome for signal {outcome.signal_id}: " f"PnL={pnl_str}")

    def get_signal(self, signal_id: str) -> Optional[Signal]:
        """
        Get signal by ID.

        Args:
            signal_id: Signal ID

        Returns:
            Signal object or None
        """
        return self.signals.get(signal_id)

    def get_outcome(self, signal_id: str) -> Optional[SignalOutcome]:
        """
        Get outcome for signal.

        Args:
            signal_id: Signal ID

        Returns:
            SignalOutcome object or None
        """
        return self.outcomes.get(signal_id)

    def get_signals_by_symbol(self, symbol: str, limit: Optional[int] = None) -> List[Signal]:
        """
        Get signals for a specific symbol.

        Args:
            symbol: Trading symbol
            limit: Maximum number of signals to return

        Returns:
            List of signals
        """
        filtered = [s for s in self.signals.values() if s.symbol == symbol]

        # Sort by timestamp (newest first)
        filtered.sort(key=lambda s: s.timestamp, reverse=True)

        if limit:
            filtered = filtered[:limit]

        return filtered

    def get_signals_by_strategy(
        self, strategy_name: str, limit: Optional[int] = None
    ) -> List[Signal]:
        """
        Get signals for a specific strategy.

        Args:
            strategy_name: Strategy name
            limit: Maximum number of signals to return

        Returns:
            List of signals
        """
        filtered = [s for s in self.signals.values() if s.strategy_name == strategy_name]

        # Sort by timestamp (newest first)
        filtered.sort(key=lambda s: s.timestamp, reverse=True)

        if limit:
            filtered = filtered[:limit]

        return filtered

    def get_signals_by_status(
        self, status: SignalStatus, limit: Optional[int] = None
    ) -> List[Signal]:
        """
        Get signals with specific status.

        Args:
            status: Signal status
            limit: Maximum number of signals to return

        Returns:
            List of signals
        """
        filtered = [s for s in self.signals.values() if s.status == status]

        # Sort by timestamp (newest first)
        filtered.sort(key=lambda s: s.timestamp, reverse=True)

        if limit:
            filtered = filtered[:limit]

        return filtered

    def get_signals_in_period(self, start_time: int, end_time: int) -> List[Signal]:
        """
        Get signals within time period.

        Args:
            start_time: Start timestamp (milliseconds)
            end_time: End timestamp (milliseconds)

        Returns:
            List of signals
        """
        return [s for s in self.signals.values() if start_time <= s.timestamp <= end_time]

    def calculate_stats(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> SignalStats:
        """
        Calculate statistics for signals.

        Args:
            start_time: Start timestamp (optional)
            end_time: End timestamp (optional)
            symbol: Filter by symbol (optional)
            strategy: Filter by strategy (optional)

        Returns:
            SignalStats object
        """
        # Filter signals
        signals = list(self.signals.values())

        if start_time:
            signals = [s for s in signals if s.timestamp >= start_time]

        if end_time:
            signals = [s for s in signals if s.timestamp <= end_time]

        if symbol:
            signals = [s for s in signals if s.symbol == symbol]

        if strategy:
            signals = [s for s in signals if s.strategy_name == strategy]

        # Count by status
        executed = sum(1 for s in signals if s.status == SignalStatus.EXECUTED)
        pending = sum(1 for s in signals if s.status == SignalStatus.PENDING)
        rejected = sum(1 for s in signals if s.status == SignalStatus.REJECTED)
        expired = sum(1 for s in signals if s.status == SignalStatus.EXPIRED)

        # Count by type
        long_count = sum(1 for s in signals if s.signal_type == SignalType.LONG)
        short_count = sum(1 for s in signals if s.signal_type == SignalType.SHORT)

        # Performance metrics
        executed_outcomes = [
            self.outcomes[s.id]
            for s in signals
            if s.id in self.outcomes and self.outcomes[s.id].executed
        ]

        winning = sum(1 for o in executed_outcomes if o.pnl and o.pnl > 0)
        losing = sum(1 for o in executed_outcomes if o.pnl and o.pnl < 0)
        win_rate = winning / len(executed_outcomes) if executed_outcomes else 0

        # P&L metrics
        pnls = [o.pnl for o in executed_outcomes if o.pnl is not None]
        total_pnl = sum(pnls) if pnls else 0
        avg_pnl = total_pnl / len(pnls) if pnls else 0
        max_profit = max(pnls) if pnls else 0
        max_loss = min(pnls) if pnls else 0

        # Timing
        holding_times = []
        for outcome in executed_outcomes:
            if outcome.execution_timestamp and outcome.exit_timestamp:
                holding_time = (
                    outcome.exit_timestamp - outcome.execution_timestamp
                ) / 1000  # Convert to seconds
                holding_times.append(holding_time)

        avg_holding_time = sum(holding_times) / len(holding_times) if holding_times else 0

        return SignalStats(
            total_signals=len(signals),
            executed_signals=executed,
            pending_signals=pending,
            rejected_signals=rejected,
            expired_signals=expired,
            long_signals=long_count,
            short_signals=short_count,
            winning_signals=winning,
            losing_signals=losing,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            max_profit=max_profit,
            max_loss=max_loss,
            avg_holding_time=avg_holding_time,
        )

    def get_performance_by_strategy(self) -> Dict[str, SignalStats]:
        """
        Get performance metrics grouped by strategy.

        Returns:
            Dictionary mapping strategy names to stats
        """
        strategies = set(s.strategy_name for s in self.signals.values())

        return {strategy: self.calculate_stats(strategy=strategy) for strategy in strategies}

    def get_performance_by_symbol(self) -> Dict[str, SignalStats]:
        """
        Get performance metrics grouped by symbol.

        Returns:
            Dictionary mapping symbols to stats
        """
        symbols = set(s.symbol for s in self.signals.values())

        return {symbol: self.calculate_stats(symbol=symbol) for symbol in symbols}

    def get_recent_signals(self, limit: int = 10) -> List[Signal]:
        """
        Get most recent signals.

        Args:
            limit: Maximum number of signals

        Returns:
            List of signals
        """
        signals = list(self.signals.values())
        signals.sort(key=lambda s: s.timestamp, reverse=True)
        return signals[:limit]

    def export_to_dict(self, include_outcomes: bool = True) -> Dict[str, Any]:
        """
        Export history to dictionary.

        Args:
            include_outcomes: Whether to include outcomes

        Returns:
            Dictionary representation
        """
        data = {
            "signals": [s.to_dict() for s in self.signals.values()],
            "signal_count": len(self.signals),
        }

        if include_outcomes:
            data["outcomes"] = {
                signal_id: {
                    "signal_id": outcome.signal_id,
                    "executed": outcome.executed,
                    "pnl": outcome.pnl,
                    "pnl_percent": outcome.pnl_percent,
                    "exit_reason": outcome.exit_reason,
                }
                for signal_id, outcome in self.outcomes.items()
            }
            data["outcome_count"] = len(self.outcomes)

        return data

    def _cleanup_old_signals(self) -> None:
        """Remove signals older than max_history_days."""
        current_time = int(time.time() * 1000)
        max_age_ms = self.max_history_days * 24 * 60 * 60 * 1000

        old_signal_ids = [
            signal_id
            for signal_id, signal in self.signals.items()
            if current_time - signal.timestamp > max_age_ms
        ]

        for signal_id in old_signal_ids:
            del self.signals[signal_id]
            if signal_id in self.outcomes:
                del self.outcomes[signal_id]

        if old_signal_ids:
            logger.info(f"Cleaned up {len(old_signal_ids)} old signals")

    def clear(self) -> None:
        """Clear all history."""
        signal_count = len(self.signals)
        outcome_count = len(self.outcomes)

        self.signals.clear()
        self.outcomes.clear()

        logger.info(f"Cleared history: {signal_count} signals, {outcome_count} outcomes")
