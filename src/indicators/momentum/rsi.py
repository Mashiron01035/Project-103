"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : rsi
    Project     : Project-103
    Description : RSI (Relative Strength Index) indicator. Measures momentum and identifies overbought/oversold conditions.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class RSI(BaseIndicator):
    """
    RSI (Relative Strength Index)

    Shows trend strength and overbought/oversold conditions.
    Values: 0-100
    - > 70: Overbought
    - < 30: Oversold
    """

    def __init__(self, name: str = "rsi"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 14) -> IndicatorResult:
        """
        Calculate RSI

        Args:
            candles: List of candles (at least period + 1)
            period: Calculation period (default 14)

        Returns:
            IndicatorResult with a 'value' field (0-100)
        """
        self.validate_candles(candles, period + 1)

        closes = self.get_closes(candles)

        # Price changes
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Split into gains and losses
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        # Average gain and loss
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smoothed (exponential) average
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        # RSI
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        # Signal detection
        signals = self._detect_signals(rsi)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(rsi, 2)},
            signals=signals,
            metadata={"period": period},
        )

    def _detect_signals(self, rsi: float) -> List[str]:
        """
        Detect trading signals from RSI

        Args:
            rsi: Current RSI value

        Returns:
            List of signals
        """
        signals = []

        # Overbought
        if rsi >= 70:
            signals.append(SignalType.OVERBOUGHT.value)

        # Oversold
        if rsi <= 30:
            signals.append(SignalType.OVERSOLD.value)

        # Strong bullish signal
        if rsi > 50:
            signals.append(SignalType.BULLISH.value)

        # Strong bearish signal
        if rsi < 50:
            signals.append(SignalType.BEARISH.value)

        return signals
