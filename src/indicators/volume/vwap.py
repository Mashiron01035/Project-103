"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : vwap
    Project     : Project-103
    Description : VWAP (Volume Weighted Average Price) indicator. Shows average price weighted by volume.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


class VWAP(BaseIndicator):
    """
    VWAP (Volume Weighted Average Price)

    Volume-weighted average price: the average price the asset traded
    at over the period, weighted by volume.
    """

    def __init__(self, name: str = "vwap"):
        super().__init__(name)

    def calculate(self, candles: List[Candle], period: int = 20) -> IndicatorResult:
        """
        Calculate VWAP

        Args:
            candles: List of candles
            period: Calculation period (0 = from the session start)

        Returns:
            IndicatorResult with a 'value' field
        """
        self.validate_candles(candles, 1)

        # If period = 0, use all candles
        if period == 0:
            period = len(candles)
        else:
            period = min(period, len(candles))

        # Take the last `period` candles
        period_candles = candles[-period:]

        # Typical price = (High + Low + Close) / 3
        typical_prices = [(c.high + c.low + c.close) / 3 for c in period_candles]

        # VWAP = Sum(Typical price * Volume) / Sum(Volume)
        total_pv = sum(
            typical_prices[i] * period_candles[i].volume for i in range(len(period_candles))
        )
        total_volume = sum(c.volume for c in period_candles)

        if total_volume == 0:
            vwap = typical_prices[-1]
        else:
            vwap = total_pv / total_volume

        # Signal detection
        current_price = candles[-1].close
        signals = self._detect_signals(current_price, vwap)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={"value": round(vwap, 8)},
            signals=signals,
            metadata={"period": period},
        )

    def _detect_signals(self, current_price: float, vwap: float) -> List[str]:
        """Detect trading signals"""
        signals = []

        # Price above VWAP — bullish signal
        if current_price > vwap:
            signals.append(SignalType.BULLISH.value)

        # Price below VWAP — bearish signal
        if current_price < vwap:
            signals.append(SignalType.BEARISH.value)

        return signals
