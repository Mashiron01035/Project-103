"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : aggregator.py
    Project     : Project-103
    Description : Signal aggregation logic. Aggregates signals from multiple strategies and resolves conflicts.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.signal import Signal, SignalStatus, SignalStrength, SignalType

logger = logging.getLogger(__name__)


class AggregationMethod(Enum):
    """Signal aggregation methods."""

    CONSENSUS = "consensus"  # Require consensus from multiple strategies
    WEIGHTED_AVERAGE = "weighted_average"  # Weight by confidence/strength
    PRIORITY = "priority"  # Use highest priority strategy
    UNION = "union"  # Accept all signals
    STRONGEST = "strongest"  # Accept only strongest signals


@dataclass
class AggregatedSignal:
    """Result of signal aggregation."""

    signal: Optional[Signal]
    contributing_signals: List[Signal]
    method: AggregationMethod
    metadata: Dict[str, Any]


class SignalAggregator:
    """
    Aggregates trading signals from multiple strategies.

    Handles conflicts between signals from different strategies
    and produces a unified signal based on configured method.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize SignalAggregator.

        Args:
            config: Aggregator configuration
        """
        self.config = config or {}

        # Aggregation settings
        self.method = AggregationMethod(self.config.get("method", "strongest"))
        self.min_consensus_percent = self.config.get("min_consensus_percent", 0.6)
        self.strategy_weights = self.config.get("strategy_weights", {})

        # Conflict resolution
        self.allow_opposing_signals = self.config.get("allow_opposing_signals", False)
        self.min_signal_strength = SignalStrength(
            self.config.get("min_signal_strength", "MODERATE")
        )

        logger.info(f"SignalAggregator initialized with method={self.method.value}")

    def aggregate(self, signals: List[Signal], symbol: str) -> AggregatedSignal:
        """
        Aggregate signals from multiple strategies.

        Args:
            signals: List of signals to aggregate
            symbol: Trading symbol

        Returns:
            AggregatedSignal object
        """
        if not signals:
            return AggregatedSignal(
                signal=None,
                contributing_signals=[],
                method=self.method,
                metadata={"reason": "No signals provided"},
            )

        # Filter signals for the symbol
        symbol_signals = [s for s in signals if s.symbol == symbol]

        if not symbol_signals:
            return AggregatedSignal(
                signal=None,
                contributing_signals=[],
                method=self.method,
                metadata={"reason": f"No signals for {symbol}"},
            )

        # Apply aggregation method
        if self.method == AggregationMethod.CONSENSUS:
            return self._aggregate_consensus(symbol_signals)
        elif self.method == AggregationMethod.WEIGHTED_AVERAGE:
            return self._aggregate_weighted_average(symbol_signals)
        elif self.method == AggregationMethod.PRIORITY:
            return self._aggregate_priority(symbol_signals)
        elif self.method == AggregationMethod.STRONGEST:
            return self._aggregate_strongest(symbol_signals)
        else:  # UNION
            return self._aggregate_union(symbol_signals)

    def _aggregate_consensus(self, signals: List[Signal]) -> AggregatedSignal:
        """
        Aggregate signals using consensus method.

        Requires a minimum percentage of strategies to agree.

        Args:
            signals: List of signals

        Returns:
            AggregatedSignal object
        """
        # Count signals by type
        long_signals = [s for s in signals if s.signal_type == SignalType.LONG]
        short_signals = [s for s in signals if s.signal_type == SignalType.SHORT]

        total = len(signals)
        long_percent = len(long_signals) / total
        short_percent = len(short_signals) / total

        metadata = {
            "total_signals": total,
            "long_signals": len(long_signals),
            "short_signals": len(short_signals),
            "long_percent": long_percent,
            "short_percent": short_percent,
        }

        # Check for consensus
        if long_percent >= self.min_consensus_percent:
            # Merge LONG signals
            signal = self._merge_signals(long_signals, SignalType.LONG)
            metadata["consensus_type"] = "LONG"

            return AggregatedSignal(
                signal=signal,
                contributing_signals=long_signals,
                method=self.method,
                metadata=metadata,
            )

        elif short_percent >= self.min_consensus_percent:
            # Merge SHORT signals
            signal = self._merge_signals(short_signals, SignalType.SHORT)
            metadata["consensus_type"] = "SHORT"

            return AggregatedSignal(
                signal=signal,
                contributing_signals=short_signals,
                method=self.method,
                metadata=metadata,
            )

        # No consensus
        metadata["reason"] = "No consensus reached"
        return AggregatedSignal(
            signal=None, contributing_signals=signals, method=self.method, metadata=metadata
        )

    def _aggregate_weighted_average(self, signals: List[Signal]) -> AggregatedSignal:
        """
        Aggregate signals using weighted average method.

        Weights signals by strategy priority and confidence.

        Args:
            signals: List of signals

        Returns:
            AggregatedSignal object
        """
        # Group by signal type
        long_signals = [s for s in signals if s.signal_type == SignalType.LONG]
        short_signals = [s for s in signals if s.signal_type == SignalType.SHORT]

        # Calculate weighted scores
        long_score = sum(self._get_signal_weight(s) * s.confidence for s in long_signals)
        short_score = sum(self._get_signal_weight(s) * s.confidence for s in short_signals)

        metadata = {
            "long_score": long_score,
            "short_score": short_score,
            "long_signals": len(long_signals),
            "short_signals": len(short_signals),
        }

        # Select stronger direction
        if long_score > short_score and long_signals:
            signal = self._merge_signals(long_signals, SignalType.LONG)
            metadata["selected_type"] = "LONG"

            return AggregatedSignal(
                signal=signal,
                contributing_signals=long_signals,
                method=self.method,
                metadata=metadata,
            )

        elif short_score > long_score and short_signals:
            signal = self._merge_signals(short_signals, SignalType.SHORT)
            metadata["selected_type"] = "SHORT"

            return AggregatedSignal(
                signal=signal,
                contributing_signals=short_signals,
                method=self.method,
                metadata=metadata,
            )

        # Tie or no clear winner
        metadata["reason"] = "No clear weighted preference"
        return AggregatedSignal(
            signal=None, contributing_signals=signals, method=self.method, metadata=metadata
        )

    def _aggregate_priority(self, signals: List[Signal]) -> AggregatedSignal:
        """
        Aggregate signals using priority method.

        Uses signal from highest priority strategy.

        Args:
            signals: List of signals

        Returns:
            AggregatedSignal object
        """
        # Sort by strategy weight (priority)
        sorted_signals = sorted(signals, key=lambda s: self._get_signal_weight(s), reverse=True)

        highest_priority = sorted_signals[0]

        metadata = {
            "selected_strategy": highest_priority.strategy_name,
            "strategy_weight": self._get_signal_weight(highest_priority),
            "total_signals": len(signals),
        }

        return AggregatedSignal(
            signal=highest_priority,
            contributing_signals=[highest_priority],
            method=self.method,
            metadata=metadata,
        )

    def _aggregate_strongest(self, signals: List[Signal]) -> AggregatedSignal:
        """
        Aggregate signals using strongest signal method.

        Selects signal with highest confidence/strength.

        Args:
            signals: List of signals

        Returns:
            AggregatedSignal object
        """
        # Filter by minimum strength
        strong_signals = [s for s in signals if self._meets_min_strength(s)]

        if not strong_signals:
            return AggregatedSignal(
                signal=None,
                contributing_signals=signals,
                method=self.method,
                metadata={"reason": "No signals meet minimum strength"},
            )

        # Sort by strength and confidence
        sorted_signals = sorted(
            strong_signals,
            key=lambda s: (self._strength_to_value(s.strength), s.confidence),
            reverse=True,
        )

        strongest = sorted_signals[0]

        metadata = {
            "selected_strategy": strongest.strategy_name,
            "confidence": strongest.confidence,
            "strength": strongest.strength.value,
            "total_signals": len(signals),
            "strong_signals": len(strong_signals),
        }

        return AggregatedSignal(
            signal=strongest,
            contributing_signals=[strongest],
            method=self.method,
            metadata=metadata,
        )

    def _aggregate_union(self, signals: List[Signal]) -> AggregatedSignal:
        """
        Aggregate signals using union method.

        Accepts all signals (returns list).

        Args:
            signals: List of signals

        Returns:
            AggregatedSignal object
        """
        # For union, we return the strongest signal but note all
        if signals:
            strongest = max(signals, key=lambda s: s.confidence)

            return AggregatedSignal(
                signal=strongest,
                contributing_signals=signals,
                method=self.method,
                metadata={
                    "total_signals": len(signals),
                    "note": "All signals accepted, strongest returned",
                },
            )

        return AggregatedSignal(
            signal=None,
            contributing_signals=[],
            method=self.method,
            metadata={"reason": "No signals"},
        )

    def _merge_signals(self, signals: List[Signal], signal_type: SignalType) -> Signal:
        """
        Merge multiple signals of the same type into one.

        Args:
            signals: Signals to merge
            signal_type: Type of signals (LONG or SHORT)

        Returns:
            Merged signal
        """
        if not signals:
            raise ValueError("Cannot merge empty signal list")

        if len(signals) == 1:
            return signals[0]

        # Use first signal as template
        base_signal = signals[0].copy()

        # Calculate weighted average confidence
        total_weight = sum(self._get_signal_weight(s) for s in signals)
        avg_confidence = sum(
            s.confidence * self._get_signal_weight(s) / total_weight for s in signals
        )

        # Calculate average prices
        avg_entry = sum(s.entry_price for s in signals) / len(signals)
        avg_sl = sum(s.stop_loss for s in signals) / len(signals)

        # Merge take profit levels (use average of each level)
        all_tp_levels = [s.take_profit for s in signals if s.take_profit]
        if all_tp_levels:
            max_tp_count = max(len(tp) for tp in all_tp_levels)
            avg_tp = []
            for i in range(max_tp_count):
                level_values = [tp[i] for tp in all_tp_levels if i < len(tp)]
                if level_values:
                    avg_tp.append(sum(level_values) / len(level_values))
        else:
            avg_tp = []

        # Update merged signal
        base_signal.confidence = avg_confidence
        base_signal.entry_price = avg_entry
        base_signal.stop_loss = avg_sl
        base_signal.take_profit = avg_tp
        base_signal.strength = self._confidence_to_strength(avg_confidence)

        # Recalculate R:R
        if avg_tp:
            if signal_type == SignalType.LONG:
                risk = avg_entry - avg_sl
                reward = avg_tp[0] - avg_entry
            else:
                risk = avg_sl - avg_entry
                reward = avg_entry - avg_tp[0]

            base_signal.risk_reward_ratio = reward / risk if risk > 0 else 0

        # Update metadata
        base_signal.metadata["merged_from"] = [s.id for s in signals]
        base_signal.metadata["merged_strategies"] = [s.strategy_name for s in signals]
        base_signal.status = SignalStatus.PENDING

        return base_signal

    def _get_signal_weight(self, signal: Signal) -> float:
        """
        Get weight for a signal based on strategy.

        Args:
            signal: Signal object

        Returns:
            Weight value
        """
        return self.strategy_weights.get(signal.strategy_name, 1.0)

    def _meets_min_strength(self, signal: Signal) -> bool:
        """
        Check if signal meets minimum strength requirement.

        Args:
            signal: Signal to check

        Returns:
            True if meets requirement
        """
        return self._strength_to_value(signal.strength) >= self._strength_to_value(
            self.min_signal_strength
        )

    @staticmethod
    def _strength_to_value(strength: SignalStrength) -> int:
        """
        Convert signal strength to numeric value.

        Args:
            strength: SignalStrength enum

        Returns:
            Numeric value (higher = stronger)
        """
        return {
            SignalStrength.WEAK: 1,
            SignalStrength.MODERATE: 2,
            SignalStrength.STRONG: 3,
            SignalStrength.VERY_STRONG: 4,
        }.get(strength, 0)

    @staticmethod
    def _confidence_to_strength(confidence: float) -> SignalStrength:
        """
        Convert confidence to signal strength.

        Args:
            confidence: Confidence value (0-1)

        Returns:
            SignalStrength enum
        """
        if confidence >= 0.9:
            return SignalStrength.VERY_STRONG
        elif confidence >= 0.75:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        else:
            return SignalStrength.WEAK

    def check_conflicts(self, signals: List[Signal]) -> Dict[str, Any]:
        """
        Check for conflicts between signals.

        Args:
            signals: List of signals to check

        Returns:
            Conflict information
        """
        if not signals:
            return {"has_conflicts": False}

        # Group by symbol
        by_symbol: Dict[str, List[Signal]] = {}
        for signal in signals:
            if signal.symbol not in by_symbol:
                by_symbol[signal.symbol] = []
            by_symbol[signal.symbol].append(signal)

        conflicts = []

        for symbol, symbol_signals in by_symbol.items():
            # Check for opposing signals
            long_count = sum(1 for s in symbol_signals if s.signal_type == SignalType.LONG)
            short_count = sum(1 for s in symbol_signals if s.signal_type == SignalType.SHORT)

            if long_count > 0 and short_count > 0:
                conflicts.append(
                    {
                        "symbol": symbol,
                        "type": "opposing_signals",
                        "long_count": long_count,
                        "short_count": short_count,
                    }
                )

        return {
            "has_conflicts": len(conflicts) > 0,
            "conflicts": conflicts,
            "symbols_checked": len(by_symbol),
        }
