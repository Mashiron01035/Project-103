"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 12.02.2026.
    File        : adx
    Project     : Project-103
    Description : ADX (Average Directional Index) indicator. Measures trend strength.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult


class ADX(BaseIndicator):
    """
    ADX (Average Directional Index)

    Measures trend strength on a scale of 0-100.
    - ADX < 20: weak/no trend (ranging market)
    - ADX 20-25: developing trend
    - ADX > 25: strong trend
    - ADX > 50: very strong trend

    Also provides +DI and -DI directional indicators.
    """

    def __init__(self, name: str = "adx"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 14) -> IndicatorResult:
        """
        Calculate ADX with +DI and -DI using Wilder's smoothing.

        Args:
            candles: List of candles (minimum 2 * period + 1)
            period: Smoothing period (default 14)

        Returns:
            IndicatorResult with values:
                - "value": ADX value (0-100)
                - "plus_di": +DI value
                - "minus_di": -DI value
        """
        min_candles = 2 * period + 1
        self.validate_candles(candles, min_candles)

        # Step 1: Calculate True Range, +DM, -DM for each bar
        true_ranges = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_high = candles[i - 1].high
            prev_low = candles[i - 1].low
            prev_close = candles[i - 1].close

            # True Range
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

            # Directional Movement
            up_move = high - prev_high
            down_move = prev_low - low

            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        # Step 2: First smoothed values (simple average over first 'period' bars)
        smoothed_tr = sum(true_ranges[:period])
        smoothed_plus_dm = sum(plus_dm_list[:period])
        smoothed_minus_dm = sum(minus_dm_list[:period])

        # Calculate first +DI, -DI
        plus_di = 100.0 * smoothed_plus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
        minus_di = 100.0 * smoothed_minus_dm / smoothed_tr if smoothed_tr > 0 else 0.0

        # First DX
        di_sum = plus_di + minus_di
        dx_values = []
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        dx_values.append(dx)

        # Step 3: Continue Wilder's smoothing for remaining bars, collecting DX values
        for i in range(period, len(true_ranges)):
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

            plus_di = 100.0 * smoothed_plus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
            minus_di = 100.0 * smoothed_minus_dm / smoothed_tr if smoothed_tr > 0 else 0.0

            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            dx_values.append(dx)

        # Step 4: Calculate ADX using Wilder's smoothing of DX values
        if len(dx_values) >= period:
            # First ADX = simple average of first 'period' DX values
            adx = sum(dx_values[:period]) / period

            # Subsequent ADX values
            for i in range(period, len(dx_values)):
                adx = (adx * (period - 1) + dx_values[i]) / period
        else:
            adx = dx_values[-1] if dx_values else 0.0

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={
                "value": round(adx, 2),
                "plus_di": round(plus_di, 2),
                "minus_di": round(minus_di, 2),
            },
            signals=[],
            metadata={"period": period},
        )
