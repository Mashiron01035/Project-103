"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : sma
    Project     : Project-103
    Description : SMA (Simple Moving Average) indicator. Trend-following indicator using arithmetic mean.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class SMA(BaseIndicator):
    """SMA (Simple Moving Average)"""

    def __init__(self, name: str = "sma"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 20) -> IndicatorResult:
        """Calculate SMA"""
        self.validate_candles(candles, period)

        closes = self.get_closes(candles)
        sma = sum(closes[-period:]) / period

        # Trend detection
        signals = []
        current_price = closes[-1]

        if current_price > sma:
            signals.append(SignalType.BULLISH.value)
        elif current_price < sma:
            signals.append(SignalType.BEARISH.value)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(sma, 8)},
            signals=signals,
            metadata={"period": period},
        )
