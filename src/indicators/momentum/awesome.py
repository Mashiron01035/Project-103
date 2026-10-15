"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : awesome
    Project     : Project-103
    Description : Awesome Oscillator indicator. Measures market momentum using median price SMAs.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class AwesomeOscillator(BaseIndicator):
    """
    Awesome Oscillator (AO)

    Shows market momentum as the difference between the 5-period and
    34-period simple moving averages of the median price.
    """

    def __init__(self, name: str = "ao"):
        super().__init__(name)

    def calculate(
        self, candles: List[Candle], fast_period: int = 5, slow_period: int = 34
    ) -> IndicatorResult:
        """
        Calculate the Awesome Oscillator

        Args:
            candles: List of candles
            fast_period: Fast SMA period (default 5)
            slow_period: Slow SMA period (default 34)

        Returns:
            IndicatorResult with a 'value' field
        """
        self.validate_candles(candles, slow_period)

        # Median price = (High + Low) / 2
        median_prices = [(candle.high + candle.low) / 2 for candle in candles]

        # SMA calculation
        fast_sma = self._calculate_sma(median_prices, fast_period)
        slow_sma = self._calculate_sma(median_prices, slow_period)

        # AO = Fast SMA - Slow SMA
        ao_value = fast_sma - slow_sma

        # Signal detection
        # This needs a history of AO values
        ao_history = []
        for i in range(slow_period - 1, len(median_prices)):
            fast = sum(median_prices[i - fast_period + 1 : i + 1]) / fast_period
            slow = sum(median_prices[i - slow_period + 1 : i + 1]) / slow_period
            ao_history.append(fast - slow)

        signals = self._detect_signals(ao_history)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(ao_value, 8)},
            signals=signals,
            metadata={"fast_period": fast_period, "slow_period": slow_period},
        )

    def _calculate_sma(self, values: List[float], period: int) -> float:
        """Simple moving average"""
        if len(values) < period:
            raise ValueError(f"Need at least {period} values for SMA calculation")
        return sum(values[-period:]) / period

    def _detect_signals(self, ao_history: List[float]) -> List[str]:
        """Detect trading signals"""
        signals = []

        if len(ao_history) < 2:
            return signals

        current_ao = ao_history[-1]
        previous_ao = ao_history[-2]

        # Bullish signal: AO positive and rising
        if current_ao > 0 and current_ao > previous_ao:
            signals.append(SignalType.BULLISH.value)

        # Bearish signal: AO negative and falling
        if current_ao < 0 and current_ao < previous_ao:
            signals.append(SignalType.BEARISH.value)

        # Zero-line cross
        if previous_ao < 0 and current_ao > 0:
            signals.append(SignalType.BULLISH_CROSSOVER.value)

        if previous_ao > 0 and current_ao < 0:
            signals.append(SignalType.BEARISH_CROSSOVER.value)

        return signals
