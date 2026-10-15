"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : std
    Project     : Project-103
    Description : Standard Deviation indicator. Measures price dispersion from the mean.

    SPDX-License-Identifier: AGPL-3.0

"""

import math
from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult


class StandardDeviation(BaseIndicator):
    """
    Standard Deviation

    Measures price dispersion around the mean.
    Used to estimate volatility.
    """

    def __init__(self, name: str = "std"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 20) -> IndicatorResult:
        """Calculate the standard deviation"""
        self.validate_candles(candles, period)

        closes = self.get_closes(candles)
        recent_closes = closes[-period:]

        # Mean
        mean = sum(recent_closes) / period

        # Standard deviation
        variance = sum((x - mean) ** 2 for x in recent_closes) / period
        std = math.sqrt(variance)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(std, 8)},
            signals=[],
            metadata={"period": period},
        )
