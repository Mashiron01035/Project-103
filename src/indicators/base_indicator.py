"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : base_indicator
    Project     : Project-103
    Description : Base indicator class. Defines the abstract interface and common functionality for all technical indicators.

    SPDX-License-Identifier: AGPL-3.0

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SignalType(Enum):
    """Indicator signal types"""

    OVERSOLD = "oversold"
    OVERBOUGHT = "overbought"
    BULLISH = "bullish"
    BEARISH = "bearish"
    BULLISH_DIVERGENCE = "bullish_divergence"
    BEARISH_DIVERGENCE = "bearish_divergence"
    BULLISH_CROSSOVER = "bullish_crossover"
    BEARISH_CROSSOVER = "bearish_crossover"
    NEUTRAL = "neutral"


@dataclass
class Candle:
    """Candle structure used for calculations"""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades_count: int = 0
    spread: float = 0.0  # Average spread in price units (e.g. 0.20 for XAUUSD)

    def to_dict(self) -> dict:
        return {
            "t": self.timestamp,
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
            "n": self.trades_count,
        }


@dataclass
class IndicatorResult:
    """Indicator calculation result"""

    name: str
    timestamp: int
    values: Dict[str, float]
    signals: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "values": self.values,
            "signals": self.signals,
            "metadata": self.metadata,
        }


class BaseIndicator(ABC):
    """
    Abstract base class for all indicators

    Every indicator must inherit from this class
    and implement calculate()
    """

    def __init__(self, name: str):
        """
        Initialize the indicator

        Args:
            name: Indicator name
        """
        self.name = name
        self._cache: Dict[str, IndicatorResult] = {}

    @abstractmethod
    def calculate(self, candles: List[Candle], **params) -> IndicatorResult:
        """
        Calculate the indicator

        Args:
            candles: List of candles to calculate on
            **params: Additional indicator parameters

        Returns:
            IndicatorResult with the calculation results
        """
        pass

    def validate_candles(self, candles: List[Candle], min_length: int) -> None:
        """
        Validate the input data

        Args:
            candles: List of candles
            min_length: Minimum number of candles

        Raises:
            ValueError: If there is not enough data
        """
        if not candles:
            raise ValueError(f"{self.name}: No candles provided")

        if len(candles) < min_length:
            raise ValueError(
                f"{self.name}: Need at least {min_length} candles, " f"got {len(candles)}"
            )

    def get_closes(self, candles: List[Candle]) -> List[float]:
        """Get close prices"""
        return [c.close for c in candles]

    def get_highs(self, candles: List[Candle]) -> List[float]:
        """Get high prices"""
        return [c.high for c in candles]

    def get_lows(self, candles: List[Candle]) -> List[float]:
        """Get low prices"""
        return [c.low for c in candles]

    def get_volumes(self, candles: List[Candle]) -> List[float]:
        """Get volumes"""
        return [c.volume for c in candles]

    def get_typical_prices(self, candles: List[Candle]) -> List[float]:
        """Get typical prices (HLC/3)"""
        return [(c.high + c.low + c.close) / 3 for c in candles]

    def generate_cache_key(self, candles: List[Candle], **params) -> str:
        """
        Build a cache key for the results

        Args:
            candles: List of candles
            **params: Calculation parameters

        Returns:
            String cache key
        """
        # Use the last candle timestamp and the parameters
        last_timestamp = candles[-1].timestamp if candles else 0
        params_str = "_".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{self.name}_{last_timestamp}_{params_str}"

    def get_cached_result(self, candles: List[Candle], **params) -> Optional[IndicatorResult]:
        """
        Get a result from the cache

        Args:
            candles: List of candles
            **params: Calculation parameters

        Returns:
            Cached IndicatorResult or None
        """
        cache_key = self.generate_cache_key(candles, **params)
        return self._cache.get(cache_key)

    def cache_result(self, candles: List[Candle], result: IndicatorResult, **params) -> None:
        """
        Store a result in the cache

        Args:
            candles: List of candles
            result: Result to store
            **params: Calculation parameters
        """
        cache_key = self.generate_cache_key(candles, **params)
        self._cache[cache_key] = result

    def clear_cache(self) -> None:
        """Clear the results cache"""
        self._cache.clear()

    def calculate_with_cache(self, candles: List[Candle], **params) -> IndicatorResult:
        """
        Calculate the indicator using the cache

        Args:
            candles: List of candles
            **params: Calculation parameters

        Returns:
            IndicatorResult
        """
        # Check the cache
        cached = self.get_cached_result(candles, **params)
        if cached is not None:
            return cached

        # Calculate and cache
        result = self.calculate(candles, **params)
        self.cache_result(candles, result, **params)

        return result

    def detect_divergence(
        self, prices: List[float], indicator_values: List[float], lookback: int = 14
    ) -> List[str]:
        """
        Detect divergence between price and the indicator

        Args:
            prices: List of prices
            indicator_values: Indicator values
            lookback: Lookback period for divergence

        Returns:
            List of detected signals
        """
        signals = []

        if len(prices) < lookback or len(indicator_values) < lookback:
            return signals

        # Take the latest values
        recent_prices = prices[-lookback:]
        recent_indicator = indicator_values[-lookback:]

        # Determine the trends
        price_trend = recent_prices[-1] - recent_prices[0]
        indicator_trend = recent_indicator[-1] - recent_indicator[0]

        # Bullish divergence: price falls, indicator rises
        if price_trend < 0 and indicator_trend > 0:
            signals.append(SignalType.BULLISH_DIVERGENCE.value)

        # Bearish divergence: price rises, indicator falls
        if price_trend > 0 and indicator_trend < 0:
            signals.append(SignalType.BEARISH_DIVERGENCE.value)

        return signals

    def detect_crossover(self, fast_line: List[float], slow_line: List[float]) -> List[str]:
        """
        Detect line crossovers

        Args:
            fast_line: Fast line
            slow_line: Slow line

        Returns:
            List of detected signals
        """
        signals = []

        if len(fast_line) < 2 or len(slow_line) < 2:
            return signals

        # Current and previous values
        fast_curr = fast_line[-1]
        fast_prev = fast_line[-2]
        slow_curr = slow_line[-1]
        slow_prev = slow_line[-2]

        # Bullish crossover: fast line crosses slow line from below
        if fast_prev <= slow_prev and fast_curr > slow_curr:
            signals.append(SignalType.BULLISH_CROSSOVER.value)

        # Bearish crossover: fast line crosses slow line from above
        if fast_prev >= slow_prev and fast_curr < slow_curr:
            signals.append(SignalType.BEARISH_CROSSOVER.value)

        return signals

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
