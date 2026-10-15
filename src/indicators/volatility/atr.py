"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : atr
    Project     : Project-103
    Description : ATR (Average True Range) indicator. Measures market volatility.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult


class ATR(BaseIndicator):
    """
    ATR (Average True Range)

    Measures market volatility.
    The higher the ATR, the higher the volatility.
    """

    def __init__(self, name: str = "atr"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 14) -> IndicatorResult:
        """Calculate ATR"""
        self.validate_candles(candles, period + 1)

        # True Range for each candle
        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        # Average over the period (first ATR value)
        atr = sum(true_ranges[:period]) / period

        # Smoothed (exponential) average
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(atr, 8)},
            signals=[],
            metadata={"period": period},
        )
