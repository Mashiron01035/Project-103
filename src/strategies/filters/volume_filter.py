"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : volume_filter
    Project     : Project-103
    Description : Volume filter. Filters signals based on trading volume levels.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from typing import Any, Dict, Optional

from src.indicators.base_indicator import Candle
from src.strategies.base_strategy import Signal
from src.strategies.filters.base_filter import BaseFilter, FilterResult

logger = logging.getLogger(__name__)


class VolumeFilter(BaseFilter):
    """
    Trading volume filter

    Rejects signals when volume is insufficient
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the volume filter

        Config:
            min_volume_ratio: float - Minimum ratio of current to average volume
            lookback_period: int - Period for the average volume
            require_increasing_volume: bool - Require rising volume
        """
        super().__init__("volume_filter", config)

        self.min_volume_ratio = self.config.get("min_volume_ratio", 1.0)
        self.lookback_period = self.config.get("lookback_period", 20)
        self.require_increasing_volume = self.config.get("require_increasing_volume", False)

    def apply(
        self, signal: Signal, candles: list[Candle], market_data: Optional[Dict[str, Any]] = None
    ) -> FilterResult:
        """Apply the volume filter"""
        if not self.enabled:
            return FilterResult(passed=True)

        if len(candles) < self.lookback_period + 1:
            return FilterResult(passed=False, reason="Not enough candles for volume analysis")

        # Current volume
        current_volume = candles[-1].volume

        # Average volume over the period
        lookback_candles = candles[-(self.lookback_period + 1) : -1]
        avg_volume = sum(c.volume for c in lookback_candles) / len(lookback_candles)

        # Volume ratio
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        # Minimum ratio check
        if volume_ratio < self.min_volume_ratio:
            return FilterResult(
                passed=False,
                reason=(
                    f"Volume too low: {volume_ratio:.2f}x < " f"{self.min_volume_ratio}x average"
                ),
                metadata={
                    "current_volume": current_volume,
                    "avg_volume": avg_volume,
                    "volume_ratio": volume_ratio,
                },
            )

        # Rising volume check
        if self.require_increasing_volume:
            if len(candles) >= 3:
                prev_volume = candles[-2].volume
                if current_volume <= prev_volume:
                    return FilterResult(
                        passed=False,
                        reason="Volume not increasing",
                        metadata={"current_volume": current_volume, "prev_volume": prev_volume},
                    )

        return FilterResult(
            passed=True,
            metadata={
                "current_volume": current_volume,
                "avg_volume": avg_volume,
                "volume_ratio": volume_ratio,
            },
        )
