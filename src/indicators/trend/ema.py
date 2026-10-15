"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : ema
    Project     : Project-103
    Description : EMA (Exponential Moving Average) indicator. Trend-following indicator with more weight on recent prices.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class EMA(BaseIndicator):
    """EMA (Exponential Moving Average)"""

    def __init__(self, name: str = "ema"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 20) -> IndicatorResult:
        """Calculate EMA"""
        self.validate_candles(candles, period)

        closes = self.get_closes(candles)
        multiplier = 2 / (period + 1)

        # Initial SMA
        ema = sum(closes[:period]) / period

        # EMA calculation
        for i in range(period, len(closes)):
            ema = (closes[i] - ema) * multiplier + ema

        # Trend detection
        signals = []
        current_price = closes[-1]

        if current_price > ema:
            signals.append(SignalType.BULLISH.value)
        elif current_price < ema:
            signals.append(SignalType.BEARISH.value)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(ema, 8)},
            signals=signals,
            metadata={"period": period},
        )
