"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : trend_filter
    Project     : Project-103
    Description : Trend filter. Filters signals based on overall market trend direction.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from typing import Any, Dict, Optional

from src.indicators.base_indicator import Candle
from src.strategies.base_strategy import OrderSide, Signal
from src.strategies.filters.base_filter import BaseFilter, FilterResult

logger = logging.getLogger(__name__)


class TrendFilter(BaseFilter):
    """
    Trend filter

    Rejects signals that go against the main trend
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the trend filter

        Config:
            use_ema: bool - Use EMA to determine the trend
            ema_period: int - EMA period
            allow_counter_trend: bool - Allow counter-trend trades
            trend_strength_threshold: float - Minimum trend strength
        """
        super().__init__("trend_filter", config)

        self.use_ema = self.config.get("use_ema", True)
        self.ema_period = self.config.get("ema_period", 50)
        self.allow_counter_trend = self.config.get("allow_counter_trend", False)
        self.trend_strength_threshold = self.config.get("trend_strength_threshold", 0.0)

    def apply(
        self, signal: Signal, candles: list[Candle], market_data: Optional[Dict[str, Any]] = None
    ) -> FilterResult:
        """Apply the trend filter"""
        if not self.enabled:
            return FilterResult(passed=True)

        # Determine the trend
        trend_direction = self._determine_trend(signal, candles)

        if trend_direction == "unknown":
            return FilterResult(passed=False, reason="Unable to determine trend direction")

        # If counter-trend trading is allowed, always pass
        if self.allow_counter_trend:
            return FilterResult(passed=True, metadata={"trend": trend_direction})

        # Check that the signal agrees with the trend
        if signal.side == OrderSide.BUY.value and trend_direction != "up":
            return FilterResult(
                passed=False,
                reason=f"BUY signal against {trend_direction} trend",
                metadata={"trend": trend_direction, "signal_side": signal.side},
            )

        if signal.side == OrderSide.SELL.value and trend_direction != "down":
            return FilterResult(
                passed=False,
                reason=f"SELL signal against {trend_direction} trend",
                metadata={"trend": trend_direction, "signal_side": signal.side},
            )

        return FilterResult(passed=True, metadata={"trend": trend_direction})

    def _determine_trend(self, signal: Signal, candles: list[Candle]) -> str:
        """
        Determine the trend direction

        Returns:
            "up", "down", or "unknown"
        """
        # Check for an EMA in the snapshot
        if self.use_ema and "ema" in signal.indicators_snapshot:
            ema_value = signal.indicators_snapshot["ema"].get("value")
            current_price = signal.entry_price

            if ema_value and current_price:
                diff_percent = (current_price - ema_value) / ema_value

                if abs(diff_percent) < self.trend_strength_threshold:
                    return "unknown"

                return "up" if current_price > ema_value else "down"

        # Alternative: compare the price with an MA
        if len(candles) < self.ema_period:
            return "unknown"

        recent_closes = [c.close for c in candles[-self.ema_period :]]
        sma = sum(recent_closes) / len(recent_closes)
        current_price = candles[-1].close

        diff_percent = (current_price - sma) / sma

        if abs(diff_percent) < self.trend_strength_threshold:
            return "unknown"

        return "up" if current_price > sma else "down"
