"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.01.2026.
    File        : volume_profile_trades
    Project     : Project-103
    Description : Volume Profile indicator based on actual trades data.
                  More accurate than candle-based calculation.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Trade data structure."""

    timestamp: datetime
    price: float
    quantity: float
    side: str  # "Buy" or "Sell"


@dataclass
class VolumeProfileLevel:
    """Single level in volume profile."""

    price_low: float
    price_high: float
    price_mid: float
    total_volume: float
    buy_volume: float
    sell_volume: float
    trade_count: int

    @property
    def delta(self) -> float:
        """Buy volume minus sell volume."""
        return self.buy_volume - self.sell_volume

    @property
    def delta_percent(self) -> float:
        """Delta as percentage of total volume."""
        if self.total_volume == 0:
            return 0.0
        return (self.delta / self.total_volume) * 100


@dataclass
class VolumeProfileResult:
    """Result of volume profile calculation."""

    # Key levels
    poc: float  # Point of Control - price level with highest volume
    va_high: float  # Value Area High - upper bound of 70% volume
    va_low: float  # Value Area Low - lower bound of 70% volume

    # Additional metrics
    total_volume: float
    total_buy_volume: float
    total_sell_volume: float
    total_trades: int

    # Full profile
    levels: List[VolumeProfileLevel] = field(default_factory=list)

    # Time range
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def delta(self) -> float:
        """Total delta (buy - sell)."""
        return self.total_buy_volume - self.total_sell_volume

    @property
    def delta_percent(self) -> float:
        """Delta as percentage of total volume."""
        if self.total_volume == 0:
            return 0.0
        return (self.delta / self.total_volume) * 100

    @property
    def value_area_range(self) -> float:
        """Value area range in price."""
        return self.va_high - self.va_low

    @property
    def value_area_percent(self) -> float:
        """Value area as percentage of price."""
        if self.poc == 0:
            return 0.0
        return (self.value_area_range / self.poc) * 100

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "poc": self.poc,
            "va_high": self.va_high,
            "va_low": self.va_low,
            "total_volume": self.total_volume,
            "total_buy_volume": self.total_buy_volume,
            "total_sell_volume": self.total_sell_volume,
            "delta": self.delta,
            "delta_percent": self.delta_percent,
            "total_trades": self.total_trades,
            "value_area_range": self.value_area_range,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


class VolumeProfileTrades:
    """
    Volume Profile calculated from actual trade data.

    This is more accurate than candle-based volume profile because it uses
    the actual prices where trades occurred, not just OHLC approximations.

    Features:
    - POC (Point of Control): Price level with highest volume
    - Value Area: Price range containing 70% of total volume
    - Delta: Buy vs Sell volume at each level
    - High Volume Nodes (HVN): Levels with significantly high volume
    - Low Volume Nodes (LVN): Levels with significantly low volume
    """

    def __init__(self, value_area_percent: float = 70.0):
        """
        Initialize Volume Profile calculator.

        Args:
            value_area_percent: Percentage of volume for value area (default 70%)
        """
        self.value_area_percent = value_area_percent

    def calculate(
        self,
        trades: List[Trade],
        bins: int = 50,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
    ) -> VolumeProfileResult:
        """
        Calculate volume profile from trades.

        Args:
            trades: List of trades
            bins: Number of price levels
            price_min: Minimum price for range (auto-detect if None)
            price_max: Maximum price for range (auto-detect if None)

        Returns:
            VolumeProfileResult with POC, Value Area, and full profile
        """
        if not trades:
            return VolumeProfileResult(
                poc=0,
                va_high=0,
                va_low=0,
                total_volume=0,
                total_buy_volume=0,
                total_sell_volume=0,
                total_trades=0,
            )

        # Extract price range
        prices = [t.price for t in trades]
        if price_min is None:
            price_min = min(prices)
        if price_max is None:
            price_max = max(prices)

        # Handle edge case where all trades at same price
        if price_max == price_min:
            price_max = price_min * 1.001  # Add small buffer
            price_min = price_min * 0.999

        # Calculate bin size
        bin_size = (price_max - price_min) / bins

        # Initialize levels
        levels: List[VolumeProfileLevel] = []
        for i in range(bins):
            price_low = price_min + (i * bin_size)
            price_high = price_low + bin_size
            levels.append(
                VolumeProfileLevel(
                    price_low=price_low,
                    price_high=price_high,
                    price_mid=(price_low + price_high) / 2,
                    total_volume=0,
                    buy_volume=0,
                    sell_volume=0,
                    trade_count=0,
                )
            )

        # Aggregate trades into levels
        total_volume = 0
        total_buy = 0
        total_sell = 0

        for trade in trades:
            # Find which bin this trade belongs to
            bin_idx = int((trade.price - price_min) / bin_size)
            bin_idx = max(0, min(bin_idx, bins - 1))  # Clamp to valid range

            level = levels[bin_idx]
            level.total_volume += trade.quantity
            level.trade_count += 1

            if trade.side.upper() in ("BUY", "B"):
                level.buy_volume += trade.quantity
                total_buy += trade.quantity
            else:
                level.sell_volume += trade.quantity
                total_sell += trade.quantity

            total_volume += trade.quantity

        # Find POC (level with highest volume)
        poc_level = max(levels, key=lambda x: x.total_volume)
        poc = poc_level.price_mid

        # Calculate Value Area
        va_high, va_low = self._calculate_value_area(levels, total_volume)

        # Get time range
        timestamps = [t.timestamp for t in trades]
        start_time = min(timestamps) if timestamps else None
        end_time = max(timestamps) if timestamps else None

        return VolumeProfileResult(
            poc=poc,
            va_high=va_high,
            va_low=va_low,
            total_volume=total_volume,
            total_buy_volume=total_buy,
            total_sell_volume=total_sell,
            total_trades=len(trades),
            levels=levels,
            start_time=start_time,
            end_time=end_time,
        )

    def _calculate_value_area(
        self, levels: List[VolumeProfileLevel], total_volume: float
    ) -> Tuple[float, float]:
        """
        Calculate value area boundaries.

        Value area is the price range containing value_area_percent of volume,
        centered around the POC.

        Args:
            levels: List of volume levels
            total_volume: Total volume across all levels

        Returns:
            Tuple of (va_high, va_low)
        """
        if total_volume == 0 or not levels:
            return 0.0, 0.0

        target_volume = total_volume * (self.value_area_percent / 100)

        # Sort levels by volume (descending)
        sorted_levels = sorted(levels, key=lambda x: x.total_volume, reverse=True)

        # Accumulate levels until we reach target volume
        accumulated = 0
        va_levels = []

        for level in sorted_levels:
            va_levels.append(level)
            accumulated += level.total_volume
            if accumulated >= target_volume:
                break

        if not va_levels:
            return levels[-1].price_high, levels[0].price_low

        # Get boundaries
        va_high = max(lvl.price_high for lvl in va_levels)
        va_low = min(lvl.price_low for lvl in va_levels)

        return va_high, va_low

    def find_high_volume_nodes(
        self, result: VolumeProfileResult, threshold_multiplier: float = 1.5
    ) -> List[VolumeProfileLevel]:
        """
        Find High Volume Nodes (HVN).

        HVNs are levels where volume is significantly higher than average.
        These often act as support/resistance.

        Args:
            result: VolumeProfileResult from calculate()
            threshold_multiplier: Multiple of average volume to be considered HVN

        Returns:
            List of high volume levels
        """
        if not result.levels:
            return []

        # Calculate average volume per level
        volumes = [lvl.total_volume for lvl in result.levels]
        avg_volume = np.mean(volumes)
        threshold = avg_volume * threshold_multiplier

        return [lvl for lvl in result.levels if lvl.total_volume >= threshold]

    def find_low_volume_nodes(
        self, result: VolumeProfileResult, threshold_multiplier: float = 0.5
    ) -> List[VolumeProfileLevel]:
        """
        Find Low Volume Nodes (LVN).

        LVNs are levels where volume is significantly lower than average.
        Price tends to move quickly through these zones.

        Args:
            result: VolumeProfileResult from calculate()
            threshold_multiplier: Multiple of average volume to be considered LVN

        Returns:
            List of low volume levels
        """
        if not result.levels:
            return []

        # Calculate average volume per level (excluding zeros)
        volumes = [lvl.total_volume for lvl in result.levels if lvl.total_volume > 0]
        if not volumes:
            return []

        avg_volume = np.mean(volumes)
        threshold = avg_volume * threshold_multiplier

        return [lvl for lvl in result.levels if 0 < lvl.total_volume < threshold]

    def get_level_at_price(
        self, result: VolumeProfileResult, price: float
    ) -> Optional[VolumeProfileLevel]:
        """
        Get the volume profile level containing a specific price.

        Args:
            result: VolumeProfileResult from calculate()
            price: Price to look up

        Returns:
            VolumeProfileLevel or None if price not in range
        """
        for level in result.levels:
            if level.price_low <= price < level.price_high:
                return level
        return None


# Convenience function for SQL-based calculation
def calculate_volume_profile_sql(
    rows: List[Tuple], bins: int = 50, value_area_percent: float = 70.0
) -> VolumeProfileResult:
    """
    Calculate volume profile from database query results.

    Expected row format: (timestamp, price, quantity, side)

    Args:
        rows: List of tuples from database query
        bins: Number of price levels
        value_area_percent: Percentage for value area

    Returns:
        VolumeProfileResult
    """
    trades = [
        Trade(timestamp=row[0], price=float(row[1]), quantity=float(row[2]), side=row[3])
        for row in rows
    ]

    calculator = VolumeProfileTrades(value_area_percent)
    return calculator.calculate(trades, bins)
