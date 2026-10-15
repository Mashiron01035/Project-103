"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : volatility_filter
    Project     : Project-103
    Description : Volatility filter. Filters signals based on market volatility levels.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from typing import Any, Dict, Optional

from src.indicators.base_indicator import Candle
from src.strategies.base_strategy import Signal
from src.strategies.filters.base_filter import BaseFilter, FilterResult

logger = logging.getLogger(__name__)


class VolatilityFilter(BaseFilter):
    """
    Volatility filter

    Rejects signals when volatility is too high or too low
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the volatility filter

        Config:
            min_volatility: float - Minimum allowed volatility
            max_volatility: float - Maximum allowed volatility
            period: int - Volatility calculation period
        """
        super().__init__("volatility_filter", config)

        self.min_volatility = self.config.get("min_volatility", 0.005)  # 0.5%
        self.max_volatility = self.config.get("max_volatility", 0.05)  # 5%
        self.period = self.config.get("period", 20)

    def apply(
        self, signal: Signal, candles: list[Candle], market_data: Optional[Dict[str, Any]] = None
    ) -> FilterResult:
        """Apply the volatility filter"""
        if not self.enabled:
            return FilterResult(passed=True)

        # Check for an ATR indicator in the snapshot
        volatility = None

        if "atr" in signal.indicators_snapshot:
            atr_value = signal.indicators_snapshot["atr"].get("value", 0)
            current_price = signal.entry_price
            volatility = atr_value / current_price if current_price > 0 else 0

        # Without ATR, compute volatility manually
        if volatility is None:
            if len(candles) < self.period:
                return FilterResult(
                    passed=False, reason="Not enough candles to calculate volatility"
                )

            recent_candles = candles[-self.period :]
            price_changes = [
                abs(candles[i].close - candles[i - 1].close) / candles[i - 1].close
                for i in range(1, len(recent_candles))
            ]
            volatility = sum(price_changes) / len(price_changes)

        # Bounds check
        if volatility < self.min_volatility:
            return FilterResult(
                passed=False,
                reason=f"Volatility too low: {volatility:.4f} < {self.min_volatility}",
                metadata={"volatility": volatility},
            )

        if volatility > self.max_volatility:
            return FilterResult(
                passed=False,
                reason=f"Volatility too high: {volatility:.4f} > {self.max_volatility}",
                metadata={"volatility": volatility},
            )

        return FilterResult(passed=True, metadata={"volatility": volatility})
