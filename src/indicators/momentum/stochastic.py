"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : stochastic
    Project     : Project-103
    Description : Stochastic Oscillator indicator. Compares closing price to price range over time.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class Stochastic(BaseIndicator):
    """
    Stochastic Oscillator

    Shows where the current close sits within the price range
    over a given period.
    Values: 0-100
    - > 80: Overbought
    - < 20: Oversold
    """

    def __init__(self, name: str = "stochastic"):
        super().__init__(name)

    def calculate(
        self, candles: List[Candle], k_period: int = 14, d_period: int = 3, smooth: int = 3
    ) -> IndicatorResult:
        """
        Calculate the Stochastic Oscillator

        Args:
            candles: List of candles
            k_period: %K period (default 14)
            d_period: %D period (default 3)
            smooth: Smoothing period (default 3)

        Returns:
            IndicatorResult with 'k' and 'd' fields
        """
        self.validate_candles(candles, k_period + d_period)

        closes = self.get_closes(candles)
        highs = self.get_highs(candles)
        lows = self.get_lows(candles)

        # %K (Fast Stochastic)
        k_values = []
        for i in range(k_period - 1, len(candles)):
            period_highs = highs[i - k_period + 1 : i + 1]
            period_lows = lows[i - k_period + 1 : i + 1]

            highest_high = max(period_highs)
            lowest_low = min(period_lows)

            if highest_high == lowest_low:
                k = 50.0
            else:
                k = ((closes[i] - lowest_low) / (highest_high - lowest_low)) * 100

            k_values.append(k)

        # Smoothed %K (Slow Stochastic)
        if smooth > 1:
            smoothed_k = []
            for i in range(smooth - 1, len(k_values)):
                avg_k = sum(k_values[i - smooth + 1 : i + 1]) / smooth
                smoothed_k.append(avg_k)
            k_values = smoothed_k

        # %D (signal line)
        d_values = []
        for i in range(d_period - 1, len(k_values)):
            d = sum(k_values[i - d_period + 1 : i + 1]) / d_period
            d_values.append(d)

        # Take the latest values
        k_value = k_values[-1] if k_values else 50.0
        d_value = d_values[-1] if d_values else 50.0

        # Signal detection
        signals = self._detect_signals(
            k_value,
            d_value,
            k_values[-2:] if len(k_values) >= 2 else [],
            d_values[-2:] if len(d_values) >= 2 else [],
        )

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"k": round(k_value, 2), "d": round(d_value, 2)},
            signals=signals,
            metadata={"k_period": k_period, "d_period": d_period, "smooth": smooth},
        )

    def _detect_signals(
        self, k: float, d: float, k_history: List[float], d_history: List[float]
    ) -> List[str]:
        """Detect trading signals"""
        signals = []

        # Overbought/oversold
        if k >= 80 and d >= 80:
            signals.append(SignalType.OVERBOUGHT.value)
        if k <= 20 and d <= 20:
            signals.append(SignalType.OVERSOLD.value)

        # Line crossover
        if len(k_history) >= 2 and len(d_history) >= 2:
            crossover_signals = self.detect_crossover(k_history, d_history)
            signals.extend(crossover_signals)

        return signals
