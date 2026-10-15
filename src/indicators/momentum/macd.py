"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : macd
    Project     : Project-103
    Description : MACD (Moving Average Convergence Divergence) indicator. Trend-following momentum indicator.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class MACD(BaseIndicator):
    """
    MACD (Moving Average Convergence Divergence)

    Trend indicator showing the relationship between two moving averages.
    Consists of:
    - MACD line: difference between the fast and slow EMA
    - Signal line: EMA of the MACD line
    - Histogram: difference between the MACD and signal lines
    """

    def __init__(self, name: str = "macd"):
        super().__init__(name)

    def calculate(
        self, candles: List[Candle], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> IndicatorResult:
        """
        Calculate MACD

        Args:
            candles: List of candles
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line period (default 9)

        Returns:
            IndicatorResult with 'macd', 'signal', 'histogram' fields
        """
        self.validate_candles(candles, slow + signal)

        closes = self.get_closes(candles)

        # Fast and slow EMA
        fast_ema = self._calculate_ema(closes, fast)
        slow_ema = self._calculate_ema(closes, slow)

        # MACD line = Fast EMA - Slow EMA
        macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(slow_ema))]

        # Signal line = EMA of the MACD line
        signal_line = self._calculate_ema(macd_line, signal)

        # Histogram = MACD - Signal
        histogram = macd_line[-1] - signal_line[-1]

        # Signal detection
        signals = self._detect_signals(
            macd_line[-2:] if len(macd_line) >= 2 else [],
            signal_line[-2:] if len(signal_line) >= 2 else [],
            histogram,
        )

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={
                "macd": round(macd_line[-1], 8),
                "signal": round(signal_line[-1], 8),
                "histogram": round(histogram, 8),
            },
            signals=signals,
            metadata={"fast": fast, "slow": slow, "signal_period": signal},
        )

    def _calculate_ema(self, values: List[float], period: int) -> List[float]:
        """EMA calculation"""
        if len(values) < period:
            raise ValueError(f"Need at least {period} values for EMA calculation")

        multiplier = 2 / (period + 1)
        ema_values = []

        # Initial SMA
        sma = sum(values[:period]) / period
        ema_values.append(sma)

        # EMA for the remaining values
        for i in range(period, len(values)):
            ema = (values[i] - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)

        return ema_values

    def _detect_signals(
        self, macd_history: List[float], signal_history: List[float], histogram: float
    ) -> List[str]:
        """Detect trading signals"""
        signals = []

        # MACD / signal line crossover
        if len(macd_history) >= 2 and len(signal_history) >= 2:
            crossover_signals = self.detect_crossover(macd_history, signal_history)
            signals.extend(crossover_signals)

        # Histogram direction
        if histogram > 0:
            signals.append(SignalType.BULLISH.value)
        elif histogram < 0:
            signals.append(SignalType.BEARISH.value)

        return signals
