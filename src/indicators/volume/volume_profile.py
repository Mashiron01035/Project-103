"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : volume_profile
    Project     : Project-103
    Description : Volume Profile indicator. Displays volume distribution across price levels.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import Dict, List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult


class VolumeProfile(BaseIndicator):
    """
    Volume Profile

    Shows the distribution of traded volume across price levels.
    Helps identify support/resistance zones.
    """

    def __init__(self, name: str = "volume_profile"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], bins: int = 20) -> IndicatorResult:
        """
        Calculate the Volume Profile

        Args:
            candles: List of candles
            bins: Number of price levels

        Returns:
            IndicatorResult with the volume profile data
        """
        self.validate_candles(candles, 1)

        # Minimum and maximum prices
        min_price = min(c.low for c in candles)
        max_price = max(c.high for c in candles)

        # Bin size
        bin_size = (max_price - min_price) / bins

        # Distribute volume across bins
        volume_by_price: Dict[int, float] = {i: 0 for i in range(bins)}

        for candle in candles:
            # Typical price of the candle
            typical_price = (candle.high + candle.low + candle.close) / 3

            # Find the bin the price falls into
            bin_index = int((typical_price - min_price) / bin_size)
            bin_index = min(bin_index, bins - 1)

            volume_by_price[bin_index] += candle.volume

        # Level with the maximum volume (Point of Control)
        poc_bin = max(volume_by_price, key=volume_by_price.get)
        poc_price = min_price + (poc_bin + 0.5) * bin_size

        # High-volume zone (Value Area)
        total_volume = sum(volume_by_price.values())
        target_volume = total_volume * 0.7  # 70% of the volume

        # Sort bins by volume
        sorted_bins = sorted(volume_by_price.items(), key=lambda x: x[1], reverse=True)

        # Collect bins until 70% of the volume is reached
        value_area_bins = []
        accumulated_volume = 0
        for bin_idx, volume in sorted_bins:
            value_area_bins.append(bin_idx)
            accumulated_volume += volume
            if accumulated_volume >= target_volume:
                break

        # Value Area boundaries
        va_high = min_price + (max(value_area_bins) + 1) * bin_size
        va_low = min_price + min(value_area_bins) * bin_size

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={
                "poc": round(poc_price, 8),
                "va_high": round(va_high, 8),
                "va_low": round(va_low, 8),
            },
            signals=[],
            metadata={"bins": bins, "total_volume": total_volume},
        )
