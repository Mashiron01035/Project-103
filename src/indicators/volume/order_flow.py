"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 06.12.2025.
    File        : order_flow
    Project     : Project-103
    Description : Order Flow Imbalance indicator. Analyzes order book data
                  to detect buying/selling pressure and liquidity imbalances.

    SPDX-License-Identifier: AGPL-3.0

"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


@dataclass
class OrderBookData:
    """Order book snapshot data for analysis"""

    timestamp: int
    bid_volume: float  # Total bid volume (top N levels)
    ask_volume: float  # Total ask volume (top N levels)
    bid_value: float  # Total bid value in quote currency
    ask_value: float  # Total ask value in quote currency
    spread: float  # Bid-ask spread
    mid_price: float  # Mid price
    imbalance: float  # Pre-calculated imbalance (-1 to 1)

    @property
    def volume_ratio(self) -> float:
        """Bid/Ask volume ratio"""
        if self.ask_volume == 0:
            return float("inf") if self.bid_volume > 0 else 1.0
        return self.bid_volume / self.ask_volume


class OrderFlowImbalance(BaseIndicator):
    """
    Order Flow Imbalance Indicator

    Analyzes order book data to detect:
    - Buy/Sell pressure imbalance
    - Large orders accumulation
    - Liquidity clusters
    - Potential reversal zones

    Signals:
    - BULLISH: Strong bid imbalance (buying pressure)
    - BEARISH: Strong ask imbalance (selling pressure)
    - BULLISH_DIVERGENCE: Price falling but bids increasing
    - BEARISH_DIVERGENCE: Price rising but asks increasing
    """

    def __init__(self, name: str = "order_flow"):
        super().__init__(name)
        self._orderbook_history: List[OrderBookData] = []
        self._max_history = 100

    def calculate(
        self,
        candles: List[Candle],
        orderbook_data: Optional[List[OrderBookData]] = None,
        period: int = 14,
        imbalance_threshold: float = 0.3,
        **params,
    ) -> IndicatorResult:
        """
        Calculate order flow imbalance

        Args:
            candles: Price candles for context
            orderbook_data: List of OrderBookData snapshots
            period: Lookback period for analysis
            imbalance_threshold: Threshold for signal generation (0-1)

        Returns:
            IndicatorResult with imbalance values and signals
        """
        self.validate_candles(candles, min(period, 5))

        signals = []
        values = {
            "imbalance": 0.0,
            "volume_ratio": 1.0,
            "bid_pressure": 0.0,
            "ask_pressure": 0.0,
            "cumulative_delta": 0.0,
            "spread_normalized": 0.0,
        }

        # If no orderbook data, estimate from candle volume
        if not orderbook_data:
            values = self._estimate_from_candles(candles, period)
            signals = self._generate_signals_from_candles(candles, values, imbalance_threshold)
        else:
            values = self._calculate_from_orderbook(orderbook_data, period)
            signals = self._generate_signals_from_orderbook(
                candles, orderbook_data, values, imbalance_threshold
            )

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values=values,
            signals=signals,
            metadata={
                "period": period,
                "threshold": imbalance_threshold,
                "data_source": "orderbook" if orderbook_data else "candles",
            },
        )

    def _estimate_from_candles(self, candles: List[Candle], period: int) -> Dict[str, float]:
        """
        Estimate order flow from candle data using volume analysis
        """
        if len(candles) < period:
            period = len(candles)

        recent = candles[-period:]

        # Calculate buying/selling volume estimation
        buy_volume = 0.0
        sell_volume = 0.0
        cumulative_delta = 0.0

        for candle in recent:
            # Estimate buy/sell volume based on close position in range
            range_size = candle.high - candle.low
            if range_size > 0:
                # Close position in range (0 = low, 1 = high)
                close_position = (candle.close - candle.low) / range_size

                # Distribute volume based on close position
                estimated_buy = candle.volume * close_position
                estimated_sell = candle.volume * (1 - close_position)
            else:
                estimated_buy = candle.volume / 2
                estimated_sell = candle.volume / 2

            buy_volume += estimated_buy
            sell_volume += estimated_sell
            cumulative_delta += estimated_buy - estimated_sell

        total_volume = buy_volume + sell_volume
        if total_volume == 0:
            total_volume = 1  # Prevent division by zero

        imbalance = (buy_volume - sell_volume) / total_volume
        volume_ratio = buy_volume / sell_volume if sell_volume > 0 else 1.0

        # Normalize values
        bid_pressure = buy_volume / total_volume
        ask_pressure = sell_volume / total_volume

        return {
            "imbalance": round(imbalance, 4),
            "volume_ratio": round(volume_ratio, 4),
            "bid_pressure": round(bid_pressure, 4),
            "ask_pressure": round(ask_pressure, 4),
            "cumulative_delta": round(cumulative_delta, 2),
            "spread_normalized": 0.0,  # Not available from candles
        }

    def _calculate_from_orderbook(
        self, orderbook_data: List[OrderBookData], period: int
    ) -> Dict[str, float]:
        """
        Calculate order flow from actual orderbook data
        """
        if not orderbook_data:
            return self._empty_values()

        recent = orderbook_data[-period:] if len(orderbook_data) >= period else orderbook_data

        # Average values over period
        total_bid_volume = sum(ob.bid_volume for ob in recent)
        total_ask_volume = sum(ob.ask_volume for ob in recent)
        avg_spread = sum(ob.spread for ob in recent) / len(recent)
        avg_mid_price = sum(ob.mid_price for ob in recent) / len(recent)

        # Current imbalance
        current = recent[-1]

        # Cumulative delta from orderbook
        cumulative_delta = sum(ob.bid_volume - ob.ask_volume for ob in recent)

        total_volume = total_bid_volume + total_ask_volume
        if total_volume == 0:
            total_volume = 1

        imbalance = current.imbalance
        volume_ratio = current.volume_ratio

        # Normalize spread
        spread_normalized = (avg_spread / avg_mid_price * 100) if avg_mid_price > 0 else 0

        return {
            "imbalance": round(imbalance, 4),
            "volume_ratio": round(volume_ratio, 4),
            "bid_pressure": round(total_bid_volume / total_volume, 4),
            "ask_pressure": round(total_ask_volume / total_volume, 4),
            "cumulative_delta": round(cumulative_delta, 2),
            "spread_normalized": round(spread_normalized, 6),
        }

    def _generate_signals_from_candles(
        self, candles: List[Candle], values: Dict[str, float], threshold: float
    ) -> List[str]:
        """Generate signals from candle-estimated values"""
        signals = []
        imbalance = values["imbalance"]

        # Strong imbalance signals
        if imbalance > threshold:
            signals.append(SignalType.BULLISH.value)
        elif imbalance < -threshold:
            signals.append(SignalType.BEARISH.value)

        # Check for divergence (price vs cumulative delta)
        if len(candles) >= 10:
            price_change = candles[-1].close - candles[-10].close
            delta = values["cumulative_delta"]

            # Bullish divergence: price down but buying pressure up
            if price_change < 0 and delta > 0:
                signals.append(SignalType.BULLISH_DIVERGENCE.value)
            # Bearish divergence: price up but selling pressure up
            elif price_change > 0 and delta < 0:
                signals.append(SignalType.BEARISH_DIVERGENCE.value)

        return signals

    def _generate_signals_from_orderbook(
        self,
        candles: List[Candle],
        orderbook_data: List[OrderBookData],
        values: Dict[str, float],
        threshold: float,
    ) -> List[str]:
        """Generate signals from orderbook data"""
        signals = []
        imbalance = values["imbalance"]

        # Strong imbalance signals
        if imbalance > threshold:
            signals.append(SignalType.BULLISH.value)
        elif imbalance < -threshold:
            signals.append(SignalType.BEARISH.value)

        # Volume ratio extremes
        volume_ratio = values["volume_ratio"]
        if volume_ratio > 2.0:  # 2x more bids than asks
            signals.append(SignalType.BULLISH.value)
        elif volume_ratio < 0.5:  # 2x more asks than bids
            signals.append(SignalType.BEARISH.value)

        # Check for absorption (large orders being filled)
        if len(orderbook_data) >= 5:
            recent = orderbook_data[-5:]

            # Bid absorption: bids decreasing while price stable = selling into bids
            bid_change = recent[-1].bid_volume - recent[0].bid_volume
            price_change = candles[-1].close - candles[-5].close if len(candles) >= 5 else 0

            if bid_change < 0 and abs(price_change / candles[-1].close) < 0.001:
                signals.append(SignalType.BEARISH.value)  # Hidden selling
            elif bid_change > 0 and abs(price_change / candles[-1].close) < 0.001:
                signals.append(SignalType.BULLISH.value)  # Hidden buying

        return signals

    def _empty_values(self) -> Dict[str, float]:
        """Return empty/neutral values"""
        return {
            "imbalance": 0.0,
            "volume_ratio": 1.0,
            "bid_pressure": 0.5,
            "ask_pressure": 0.5,
            "cumulative_delta": 0.0,
            "spread_normalized": 0.0,
        }

    def add_orderbook_snapshot(self, data: OrderBookData) -> None:
        """
        Add orderbook snapshot to history

        Args:
            data: OrderBookData snapshot
        """
        self._orderbook_history.append(data)

        # Trim history if too long
        if len(self._orderbook_history) > self._max_history:
            self._orderbook_history = self._orderbook_history[-self._max_history :]

    def get_orderbook_history(self, limit: int = None) -> List[OrderBookData]:
        """Get orderbook history"""
        if limit:
            return self._orderbook_history[-limit:]
        return self._orderbook_history.copy()
