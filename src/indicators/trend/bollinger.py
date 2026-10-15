"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : bollinger
    Project     : Project-103
    Description : Bollinger Bands indicator. Volatility bands placed above and below a moving average.

    SPDX-License-Identifier: AGPL-3.0

"""

import math
from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class BollingerBands(BaseIndicator):
    """
    Bollinger Bands

    Consists of three lines:
    - Middle line: SMA
    - Upper band: SMA + (std_dev * standard deviation)
    - Lower band: SMA - (std_dev * standard deviation)
    """

    def __init__(self, name: str = "bollinger"):
        super().__init__(name)

    def calculate(
        self, candles: List[Candle], period: int = 20, std_dev: float = 2.0
    ) -> IndicatorResult:
        """Calculate Bollinger Bands"""
        self.validate_candles(candles, period)

        closes = self.get_closes(candles)
        recent_closes = closes[-period:]

        # Middle line (SMA)
        middle = sum(recent_closes) / period

        # Standard deviation
        variance = sum((x - middle) ** 2 for x in recent_closes) / period
        std = math.sqrt(variance)

        # Upper and lower bands
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        # Band width
        width = upper - lower

        # Signal detection
        current_price = closes[-1]
        signals = []

        if current_price >= upper:
            signals.append(SignalType.OVERBOUGHT.value)
        elif current_price <= lower:
            signals.append(SignalType.OVERSOLD.value)
        elif current_price > middle:
            signals.append(SignalType.BULLISH.value)
        elif current_price < middle:
            signals.append(SignalType.BEARISH.value)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={
                "upper": round(upper, 8),
                "middle": round(middle, 8),
                "lower": round(lower, 8),
                "width": round(width, 8),
            },
            signals=signals,
            metadata={"period": period, "std_dev": std_dev},
        )
