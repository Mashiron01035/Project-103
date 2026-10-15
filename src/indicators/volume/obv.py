"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : obv
    Project     : Project-103
    Description : OBV (On-Balance Volume) indicator. Measures cumulative buying and selling pressure.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class OBV(BaseIndicator):
    """
    OBV (On-Balance Volume)

    Volume accumulation/distribution indicator.
    Shows the relationship between volume and price.
    """

    def __init__(self, name: str = "obv"):
        super().__init__(name)

    def calculate(self, candles: List[Candle]) -> IndicatorResult:
        """
        Calculate OBV

        Args:
            candles: List of candles

        Returns:
            IndicatorResult with a 'value' field
        """
        self.validate_candles(candles, 2)

        obv = 0
        obv_values = [0]

        for i in range(1, len(candles)):
            if candles[i].close > candles[i - 1].close:
                obv += candles[i].volume
            elif candles[i].close < candles[i - 1].close:
                obv -= candles[i].volume

            obv_values.append(obv)

        # Signal detection
        signals = self._detect_signals(
            obv_values[-2:] if len(obv_values) >= 2 else [], [c.close for c in candles[-2:]]
        )

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(obv, 2)},
            signals=signals,
            metadata={},
        )

    def _detect_signals(self, obv_history: List[float], prices: List[float]) -> List[str]:
        """Detect trading signals"""
        signals = []

        if len(obv_history) < 2 or len(prices) < 2:
            return signals

        obv_curr = obv_history[-1]
        obv_prev = obv_history[-2]
        price_curr = prices[-1]
        price_prev = prices[-2]

        # Bullish signal: OBV rising
        if obv_curr > obv_prev:
            signals.append(SignalType.BULLISH.value)

        # Bearish signal: OBV falling
        if obv_curr < obv_prev:
            signals.append(SignalType.BEARISH.value)

        # Divergence
        if price_curr < price_prev and obv_curr > obv_prev:
            signals.append(SignalType.BULLISH_DIVERGENCE.value)

        if price_curr > price_prev and obv_curr < obv_prev:
            signals.append(SignalType.BEARISH_DIVERGENCE.value)

        return signals
