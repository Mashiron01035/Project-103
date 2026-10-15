"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : strategy_selector
    Project     : Project-103
    Description : Strategy selector. Dynamically selects and switches between strategies based on market conditions.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.indicators.base_indicator import Candle

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regimes"""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"
    CONSOLIDATION = "consolidation"
    UNKNOWN = "unknown"


class TransitionPhase(Enum):
    """Phases of a transition between regimes"""

    STABLE = "stable"  # Stable regime, a single strategy set is active
    TRANSITION = "transition"  # Transition phase, buffer zone


@dataclass
class MarketCondition:
    """Market condition"""

    regime: MarketRegime
    volatility: float  # Volatility (ATR-based)
    trend_strength: float  # Trend strength (-1 to 1)
    volume_profile: str  # "high", "medium", "low"
    liquidity_score: float  # Liquidity score (0 to 1)
    confidence: float  # Confidence in the regime detection (0 to 1)
    metadata: Dict[str, Any]  # Additional information


class StrategySelector:
    """
    Strategy selector

    Dynamically selects suitable strategies based on:
    - The current market regime
    - Volatility
    - Liquidity
    - User-defined rules
    """

    # Buffer zones for volatility (volatility thresholds)
    VOLATILITY_THRESHOLDS = {
        "low_vol_max": 0.005,  # < 0.5% = LOW_VOLATILITY
        "low_vol_buffer_max": 0.008,  # 0.5% - 0.8% = LOW_VOL <-> CONSOLIDATION buffer
        "consol_max": 0.02,  # 0.8% - 2% = CONSOLIDATION
        "consol_buffer_max": 0.025,  # 2% - 2.5% = CONSOLIDATION <-> HIGH_VOL buffer
        "high_vol_min": 0.03,  # > 3% = HIGH_VOLATILITY
    }

    # Hysteresis — the threshold must be exceeded by X% to start a transition
    HYSTERESIS_MULTIPLIER = 1.3  # 30% beyond the threshold

    def __init__(self, confirmation_threshold: int = 20, use_transition_logic: bool = True):
        """
        Initialize the selector

        Args:
            confirmation_threshold: Candles needed to confirm a regime change (50 = 12.5 hours on 15m)
            use_transition_logic: Use the confirmed-transition logic
        """
        self._strategy_rules: Dict[str, Callable[[MarketCondition], bool]] = {}
        self._regime_strategy_map: Dict[MarketRegime, List[str]] = {
            regime: [] for regime in MarketRegime
        }
        self._strategy_pairs: Dict[str, List[str]] = {}  # strategy -> list of pairs
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN

        # Transition logic flag (disabled by default - strategies work better independently)
        self._use_transition_logic = use_transition_logic

        # Transition state tracking (per symbol)
        self._stable_regime: Dict[str, MarketRegime] = {}  # symbol -> confirmed regime
        self._pending_regime: Dict[
            str, MarketRegime
        ] = {}  # symbol -> regime we're transitioning to
        self._confirmation_count: Dict[str, int] = {}  # symbol -> confirmation counter
        self._confirmation_threshold = confirmation_threshold  # candles needed to confirm
        self._transition_phase: Dict[str, TransitionPhase] = {}  # symbol -> current phase

        logger.info(
            f"Strategy selector initialized (transition_logic={'ON' if use_transition_logic else 'OFF'})"
        )

    def register_strategy_pairs(self, strategy_name: str, pairs: List[str]) -> None:
        """
        Register the pairs for a strategy

        Args:
            strategy_name: Strategy name
            pairs: List of trading pairs (e.g., ["BTCUSDT", "ETHUSDT"])
        """
        self._strategy_pairs[strategy_name] = pairs
        logger.debug(f"Registered {len(pairs)} pairs for strategy: {strategy_name}")

    def is_pair_allowed(self, strategy_name: str, symbol: str) -> bool:
        """
        Check whether a pair is allowed for a strategy

        Args:
            strategy_name: Strategy name
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            True if the pair is allowed (or there are no restrictions)
        """
        if strategy_name not in self._strategy_pairs:
            return True  # No restrictions = all pairs allowed

        allowed_pairs = self._strategy_pairs[strategy_name]
        if not allowed_pairs:
            return True  # Empty list = all pairs allowed

        return symbol in allowed_pairs

    def update_regime_with_confirmation(
        self, symbol: str, detected_regime: MarketRegime, volatility: float
    ) -> tuple[MarketRegime, TransitionPhase]:
        """
        Update the regime with time confirmation, buffer zones and hysteresis.

        Args:
            symbol: Trading pair
            detected_regime: Regime detected on the current candle
            volatility: Current volatility

        Returns:
            (effective_regime, phase) - the effective regime and transition phase
        """
        # Initialization for a new symbol
        if symbol not in self._stable_regime:
            self._stable_regime[symbol] = detected_regime
            self._pending_regime[symbol] = None
            self._confirmation_count[symbol] = 0
            self._transition_phase[symbol] = TransitionPhase.STABLE
            return detected_regime, TransitionPhase.STABLE

        current_stable = self._stable_regime[symbol]
        current_phase = self._transition_phase[symbol]
        pending = self._pending_regime[symbol]

        # Same regime as the stable one — reset the transition
        if detected_regime == current_stable:
            if current_phase == TransitionPhase.TRANSITION:
                logger.debug(f"{symbol}: Transition cancelled, back to {current_stable.value}")
            self._pending_regime[symbol] = None
            self._confirmation_count[symbol] = 0
            self._transition_phase[symbol] = TransitionPhase.STABLE
            return current_stable, TransitionPhase.STABLE

        # Regime differs from the stable one
        if current_phase == TransitionPhase.STABLE:
            # Check hysteresis — the threshold must be clearly exceeded
            if not self._passes_hysteresis(current_stable, detected_regime, volatility):
                # Hysteresis not passed — stay in the stable regime
                return current_stable, TransitionPhase.STABLE

            # Hysteresis passed — start the transition
            self._pending_regime[symbol] = detected_regime
            self._confirmation_count[symbol] = 1
            self._transition_phase[symbol] = TransitionPhase.TRANSITION
            logger.debug(
                f"{symbol}: Transition started {current_stable.value} → {detected_regime.value} "
                f"(1/{self._confirmation_threshold}, hysteresis passed)"
            )
            return current_stable, TransitionPhase.TRANSITION

        elif current_phase == TransitionPhase.TRANSITION:
            if detected_regime == pending:
                # Continue confirming
                self._confirmation_count[symbol] += 1
                count = self._confirmation_count[symbol]

                if count >= self._confirmation_threshold:
                    # Confirmed! Switch the stable regime
                    old_regime = current_stable
                    self._stable_regime[symbol] = detected_regime
                    self._pending_regime[symbol] = None
                    self._confirmation_count[symbol] = 0
                    self._transition_phase[symbol] = TransitionPhase.STABLE
                    logger.info(
                        f"{symbol}: Regime confirmed: {old_regime.value} → {detected_regime.value}"
                    )
                    return detected_regime, TransitionPhase.STABLE
                else:
                    logger.debug(
                        f"{symbol}: Transition progress {current_stable.value} → {detected_regime.value} "
                        f"({count}/{self._confirmation_threshold})"
                    )
                    return current_stable, TransitionPhase.TRANSITION
            else:
                # Regime changed to a third one — reset and start a new transition
                self._pending_regime[symbol] = detected_regime
                self._confirmation_count[symbol] = 1
                logger.debug(
                    f"{symbol}: Transition reset, new target: {detected_regime.value} "
                    f"(1/{self._confirmation_threshold})"
                )
                return current_stable, TransitionPhase.TRANSITION

        return current_stable, current_phase

    def _is_in_buffer_zone(self, volatility: float) -> bool:
        """Check whether volatility is inside a buffer zone."""
        thresholds = self.VOLATILITY_THRESHOLDS
        # Buffer between LOW_VOL and CONSOLIDATION
        if thresholds["low_vol_max"] <= volatility < thresholds["low_vol_buffer_max"]:
            return True
        # Buffer between CONSOLIDATION and HIGH_VOL
        if thresholds["consol_max"] <= volatility < thresholds["consol_buffer_max"]:
            return True
        return False

    def _passes_hysteresis(
        self, current_regime: MarketRegime, new_regime: MarketRegime, volatility: float
    ) -> bool:
        """
        Check whether a transition passes hysteresis.

        Hysteresis requires clearly exceeding the threshold (30%) to prevent
        frequent switching when the value oscillates around the boundary.

        Examples:
        - LOW_VOL -> CONSOL: volatility must be > 0.5% * 1.3 = 0.65%
        - CONSOL -> LOW_VOL: volatility must be < 0.5% / 1.3 = 0.38%
        """
        thresholds = self.VOLATILITY_THRESHOLDS
        h = self.HYSTERESIS_MULTIPLIER

        # LOW_VOLATILITY -> another regime (exit upwards)
        if current_regime == MarketRegime.LOW_VOLATILITY:
            # Must exceed the threshold with hysteresis
            hysteresis_threshold = thresholds["low_vol_max"] * h
            if volatility < hysteresis_threshold:
                return False  # Hysteresis not passed

        # CONSOLIDATION/RANGING -> LOW_VOLATILITY (exit downwards)
        elif new_regime == MarketRegime.LOW_VOLATILITY:
            # Must drop below the threshold with hysteresis
            hysteresis_threshold = thresholds["low_vol_max"] / h
            if volatility > hysteresis_threshold:
                return False  # Hysteresis not passed

        # CONSOLIDATION -> HIGH_VOLATILITY (exit upwards)
        elif current_regime == MarketRegime.CONSOLIDATION and new_regime in [
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.BREAKOUT,
        ]:
            hysteresis_threshold = thresholds["high_vol_min"] * h
            if volatility < hysteresis_threshold:
                return False

        # HIGH_VOLATILITY -> CONSOLIDATION (exit downwards)
        elif current_regime in [MarketRegime.HIGH_VOLATILITY, MarketRegime.BREAKOUT]:
            if new_regime in [MarketRegime.CONSOLIDATION, MarketRegime.RANGING]:
                hysteresis_threshold = thresholds["high_vol_min"] / h
                if volatility > hysteresis_threshold:
                    return False

        return True  # Hysteresis passed

    def get_transition_info(self, symbol: str) -> Dict[str, Any]:
        """Return information about the current transition state for a symbol."""
        return {
            "stable_regime": self._stable_regime.get(symbol, MarketRegime.UNKNOWN).value,
            "pending_regime": self._pending_regime.get(symbol).value
            if self._pending_regime.get(symbol)
            else None,
            "confirmation_count": self._confirmation_count.get(symbol, 0),
            "confirmation_threshold": self._confirmation_threshold,
            "phase": self._transition_phase.get(symbol, TransitionPhase.STABLE).value,
        }

    def register_strategy_rule(
        self, strategy_name: str, rule: Callable[[MarketCondition], bool]
    ) -> None:
        """
        Register a strategy selection rule

        Args:
            strategy_name: Strategy name
            rule: Function that takes a MarketCondition and returns bool

        Example:
            >>> def momentum_rule(market: MarketCondition) -> bool:
            ...     return (market.regime == MarketRegime.TRENDING_UP and
            ...             market.volatility > 0.02)
            >>> selector.register_strategy_rule("momentum", momentum_rule)
        """
        self._strategy_rules[strategy_name] = rule
        logger.debug(f"Registered rule for strategy: {strategy_name}")

    def map_strategy_to_regime(self, strategy_name: str, regimes: List[MarketRegime]) -> None:
        """
        Map a strategy to market regimes

        Args:
            strategy_name: Strategy name
            regimes: List of market regimes the strategy suits

        Example:
            >>> selector.map_strategy_to_regime(
            ...     "trend_following",
            ...     [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]
            ... )
        """
        for regime in regimes:
            if strategy_name not in self._regime_strategy_map[regime]:
                self._regime_strategy_map[regime].append(strategy_name)

        logger.debug(f"Mapped strategy {strategy_name} to regimes: {[r.value for r in regimes]}")

    def detect_market_regime(
        self, candles: List[Candle], indicators: Optional[Dict[str, Any]] = None
    ) -> MarketCondition:
        """
        Detect the current market regime

        Args:
            candles: List of candles
            indicators: Computed indicators (optional)

        Returns:
            MarketCondition with market information
        """
        if len(candles) < 50:
            logger.warning("Not enough candles to determine market regime")
            return MarketCondition(
                regime=MarketRegime.UNKNOWN,
                volatility=0.0,
                trend_strength=0.0,
                volume_profile="low",
                liquidity_score=0.0,
                confidence=0.0,
                metadata={},
            )

        # Compute metrics
        closes = [c.close for c in candles[-50:]]
        highs = [c.high for c in candles[-50:]]
        lows = [c.low for c in candles[-50:]]
        volumes = [c.volume for c in candles[-50:]]

        # Volatility (range-based)
        price_ranges = [hi - lo for hi, lo in zip(highs, lows)]
        avg_range = sum(price_ranges) / len(price_ranges)
        avg_price = sum(closes) / len(closes)
        volatility = avg_range / avg_price if avg_price > 0 else 0.0

        # Trend strength (linear regression)
        trend_strength = self._calculate_trend_strength(closes)

        # Volume
        avg_volume = sum(volumes) / len(volumes)
        recent_volume = sum(volumes[-10:]) / 10
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0

        if volume_ratio > 1.5:
            volume_profile = "high"
        elif volume_ratio < 0.7:
            volume_profile = "low"
        else:
            volume_profile = "medium"

        # Determine the regime
        regime, confidence = self._determine_regime(
            trend_strength=trend_strength, volatility=volatility, volume_ratio=volume_ratio
        )

        self._current_regime = regime

        return MarketCondition(
            regime=regime,
            volatility=volatility,
            trend_strength=trend_strength,
            volume_profile=volume_profile,
            liquidity_score=min(volume_ratio, 1.0),
            confidence=confidence,
            metadata={
                "avg_volume": avg_volume,
                "recent_volume": recent_volume,
                "avg_price": avg_price,
                "avg_range": avg_range,
            },
        )

    def _calculate_trend_strength(self, prices: List[float]) -> float:
        """
        Trend strength via linear regression

        Args:
            prices: List of prices

        Returns:
            Value from -1 (strong downtrend) to 1 (strong uptrend)
        """
        n = len(prices)
        if n < 2:
            return 0.0

        # Simple linear regression
        x_mean = n / 2
        y_mean = sum(prices) / n

        numerator = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator

        # Normalize the slope by the average price
        normalized_slope = slope / y_mean if y_mean > 0 else 0.0

        # Clamp to -1..1
        return max(-1.0, min(1.0, normalized_slope * 100))

    def _determine_regime(
        self, trend_strength: float, volatility: float, volume_ratio: float
    ) -> tuple[MarketRegime, float]:
        """
        Determine the market regime from the metrics

        Args:
            trend_strength: Trend strength
            volatility: Volatility
            volume_ratio: Volume ratio

        Returns:
            (MarketRegime, confidence)
        """
        confidence = 0.7  # Base confidence

        # High volatility
        if volatility > 0.03:
            if volume_ratio > 1.5:
                return MarketRegime.BREAKOUT, 0.8
            return MarketRegime.HIGH_VOLATILITY, 0.75

        # Low volatility (very quiet market < 0.5%)
        if volatility < 0.005:
            return MarketRegime.LOW_VOLATILITY, 0.7

        # Strong uptrend
        if trend_strength > 0.3:
            confidence = min(0.9, 0.6 + abs(trend_strength))
            return MarketRegime.TRENDING_UP, confidence

        # Strong downtrend
        if trend_strength < -0.3:
            confidence = min(0.9, 0.6 + abs(trend_strength))
            return MarketRegime.TRENDING_DOWN, confidence

        # Consolidation (weak trend + medium volatility 0.5%-2%)
        if abs(trend_strength) < 0.1 and volatility < 0.02:
            return MarketRegime.CONSOLIDATION, 0.75

        # Range
        if abs(trend_strength) < 0.2:
            return MarketRegime.RANGING, 0.65

        return MarketRegime.UNKNOWN, 0.5

    def select_strategies(
        self,
        market_condition: MarketCondition,
        available_strategies: List[str],
        symbol: Optional[str] = None,
    ) -> List[str]:
        """
        Select suitable strategies for the current market conditions

        Args:
            market_condition: Market condition
            available_strategies: List of available strategies
            symbol: Trading pair (if given, filters by allowed pairs)

        Returns:
            List of selected strategy names
        """
        detected_regime = market_condition.regime

        # Apply the confirmed-transition logic (if enabled)
        if self._use_transition_logic and symbol:
            effective_regime, phase = self.update_regime_with_confirmation(
                symbol, detected_regime, market_condition.volatility
            )

            if phase == TransitionPhase.TRANSITION:
                # In the transition phase — special logic
                return self._select_strategies_in_transition(
                    symbol=symbol,
                    stable_regime=effective_regime,
                    pending_regime=self._pending_regime.get(symbol),
                    available_strategies=available_strategies,
                )

            # STABLE phase — use the effective (confirmed) regime
            working_regime = effective_regime
        else:
            # Without transition logic — use the detected regime directly
            working_regime = detected_regime

        selected = []

        # Check user rules
        for strategy_name in available_strategies:
            # Filter by pairs
            if symbol and not self.is_pair_allowed(strategy_name, symbol):
                continue

            if strategy_name in self._strategy_rules:
                rule = self._strategy_rules[strategy_name]
                try:
                    if rule(market_condition):
                        selected.append(strategy_name)
                        continue
                except Exception as e:
                    logger.error(f"Error evaluating rule for {strategy_name}: {e}")

        # If there are no rules or none fired, use the mapping
        if not selected:
            regime_strategies = self._regime_strategy_map.get(working_regime, [])
            selected = [
                s
                for s in regime_strategies
                if s in available_strategies and (not symbol or self.is_pair_allowed(s, symbol))
            ]

        logger.debug(
            f"Selected strategies for {working_regime.value} " f"(symbol={symbol}): {selected}"
        )

        return selected

    def _select_strategies_in_transition(
        self,
        symbol: str,
        stable_regime: MarketRegime,
        pending_regime: Optional[MarketRegime],
        available_strategies: List[str],
    ) -> List[str]:
        """
        Strategy selection in the transition phase.

        Logic:
        - Stable-regime strategies keep trading their pairs
        - New-regime strategies start trading on UNIQUE pairs
          (ones not used by the stable-regime strategies)
        """
        selected = []

        # Stable-regime strategies
        stable_strategies = self._regime_strategy_map.get(stable_regime, [])
        stable_strategies = [
            s
            for s in stable_strategies
            if s in available_strategies and self.is_pair_allowed(s, symbol)
        ]

        # Pairs already used by the stable strategies
        stable_pairs = set()
        for strategy_name in stable_strategies:
            pairs = self._strategy_pairs.get(strategy_name, [])
            stable_pairs.update(pairs)

        # Add the stable strategies
        selected.extend(stable_strategies)

        # New-regime strategies (unique pairs only)
        if pending_regime:
            pending_strategies = self._regime_strategy_map.get(pending_regime, [])
            for strategy_name in pending_strategies:
                if strategy_name not in available_strategies:
                    continue
                if strategy_name in selected:
                    continue

                # Check whether this strategy has pairs not taken by the stable ones
                strategy_pairs = set(self._strategy_pairs.get(strategy_name, []))

                if not strategy_pairs:
                    # No pair restrictions — let it through during the transition
                    continue

                # Unique pairs = strategy pairs - stable pairs
                unique_pairs = strategy_pairs - stable_pairs

                # If the current symbol is among the unique pairs — add the strategy
                if symbol in unique_pairs:
                    selected.append(strategy_name)
                    logger.debug(
                        f"{symbol}: {strategy_name} added in TRANSITION "
                        f"(unique pair for {pending_regime.value})"
                    )

        logger.debug(
            f"TRANSITION: {symbol} stable={stable_regime.value} pending={pending_regime.value if pending_regime else 'None'} "
            f"strategies={selected}"
        )

        return selected

    def get_current_regime(self) -> MarketRegime:
        """
        Get the current market regime

        Returns:
            MarketRegime
        """
        return self._current_regime

    def get_regime_strategies(self, regime: Optional[MarketRegime] = None) -> List[str]:
        """
        Get the strategies for a given regime

        Args:
            regime: Market regime (if None, the current one is used)

        Returns:
            List of strategy names
        """
        if regime is None:
            regime = self._current_regime

        return self._regime_strategy_map.get(regime, []).copy()

    def clear_mappings(self) -> None:
        """Clear all strategy-to-regime mappings"""
        for regime in self._regime_strategy_map:
            self._regime_strategy_map[regime] = []
        self._strategy_rules.clear()

        logger.info("Cleared all strategy mappings")

    def __repr__(self) -> str:
        return (
            f"StrategySelector("
            f"current_regime={self._current_regime.value}, "
            f"rules={len(self._strategy_rules)}, "
            f"mappings={sum(len(v) for v in self._regime_strategy_map.values())})"
        )
