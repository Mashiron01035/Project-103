"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.01.2026.
    File        : volume_profile_provider
    Project     : Project-103
    Description : Data provider for Volume Profile calculation from trades.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from src.indicators.volume.volume_profile_trades import (
    Trade,
    VolumeProfileResult,
    VolumeProfileTrades,
    calculate_volume_profile_sql,
)

logger = logging.getLogger(__name__)


class VolumeProfileProvider:
    """
    Provides volume profile data from trades stored in TimescaleDB.

    Uses asyncpg for efficient querying of large trade datasets.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize provider.

        Args:
            database_url: PostgreSQL connection URL. If None, uses DATABASE_URL env var.
        """
        self.database_url = database_url or os.environ.get(
            "DATABASE_URL", "postgresql://mashiron:1452@localhost:5432/project_102"
        )
        self._pool: Optional[asyncpg.Pool] = None
        self._calculator = VolumeProfileTrades()

    async def connect(self) -> None:
        """Create connection pool."""
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    self.database_url, min_size=2, max_size=10, command_timeout=60
                )
                logger.info("VolumeProfileProvider connected to database")
            except Exception as e:
                logger.error(f"Failed to connect to database: {e}")
                raise

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("VolumeProfileProvider disconnected")

    async def get_volume_profile(
        self,
        symbol: str,
        lookback_minutes: int = 60,
        bins: int = 50,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Optional[VolumeProfileResult]:
        """
        Get volume profile for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            lookback_minutes: Minutes of trade data to use (if start/end not specified)
            bins: Number of price levels
            start_time: Start time (overrides lookback_minutes)
            end_time: End time (defaults to now)

        Returns:
            VolumeProfileResult or None if insufficient data
        """
        await self.connect()

        if end_time is None:
            end_time = datetime.now(timezone.utc)

        if start_time is None:
            start_time = end_time - timedelta(minutes=lookback_minutes)

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT timestamp, price, quantity, side
                    FROM market_data.trades
                    WHERE symbol = $1
                      AND timestamp >= $2
                      AND timestamp <= $3
                    ORDER BY timestamp
                    """,
                    symbol,
                    start_time,
                    end_time,
                )

                if not rows:
                    logger.debug(f"No trades found for {symbol} in range")
                    return None

                # Convert to Trade objects
                trades = [
                    Trade(
                        timestamp=row["timestamp"],
                        price=float(row["price"]),
                        quantity=float(row["quantity"]),
                        side=row["side"],
                    )
                    for row in rows
                ]

                logger.debug(f"Calculating volume profile from {len(trades)} trades")

                return self._calculator.calculate(trades, bins)

        except Exception as e:
            logger.error(f"Error fetching volume profile: {e}")
            return None

    async def get_volume_profile_levels(
        self, symbol: str, lookback_minutes: int = 60, bins: int = 50
    ) -> Dict[str, float]:
        """
        Get key volume profile levels (POC, VAH, VAL).

        Simplified method for quick level detection.

        Args:
            symbol: Trading pair symbol
            lookback_minutes: Minutes of trade data
            bins: Number of price levels

        Returns:
            Dict with 'poc', 'va_high', 'va_low' keys
        """
        result = await self.get_volume_profile(symbol, lookback_minutes, bins)

        if result is None:
            return {"poc": 0, "va_high": 0, "va_low": 0}

        return {
            "poc": result.poc,
            "va_high": result.va_high,
            "va_low": result.va_low,
            "delta": result.delta,
            "delta_percent": result.delta_percent,
        }

    async def get_volume_profile_multi_timeframe(
        self, symbol: str, timeframes_minutes: List[int] = None
    ) -> Dict[str, VolumeProfileResult]:
        """
        Get volume profiles for multiple lookback periods.

        Args:
            symbol: Trading pair symbol
            timeframes_minutes: List of lookback periods in minutes
                Default: [15, 60, 240, 1440] (15m, 1h, 4h, 1d)

        Returns:
            Dict mapping timeframe label to VolumeProfileResult
        """
        if timeframes_minutes is None:
            timeframes_minutes = [15, 60, 240, 1440]

        results = {}
        labels = {15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}

        for minutes in timeframes_minutes:
            label = labels.get(minutes, f"{minutes}m")
            result = await self.get_volume_profile(symbol, minutes)
            if result:
                results[label] = result

        return results

    async def get_trades_count(self, symbol: str, lookback_minutes: int = 60) -> int:
        """
        Get count of trades in the specified period.

        Useful for checking if there's enough data for volume profile.

        Args:
            symbol: Trading pair symbol
            lookback_minutes: Minutes to look back

        Returns:
            Number of trades
        """
        await self.connect()

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=lookback_minutes)

        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM market_data.trades
                    WHERE symbol = $1
                      AND timestamp >= $2
                      AND timestamp <= $3
                    """,
                    symbol,
                    start_time,
                    end_time,
                )
                return result or 0

        except Exception as e:
            logger.error(f"Error counting trades: {e}")
            return 0

    async def get_high_volume_nodes(
        self,
        symbol: str,
        lookback_minutes: int = 60,
        bins: int = 50,
        threshold_multiplier: float = 1.5,
    ) -> List[Dict[str, Any]]:
        """
        Get high volume nodes (potential support/resistance).

        Args:
            symbol: Trading pair symbol
            lookback_minutes: Minutes of trade data
            bins: Number of price levels
            threshold_multiplier: Multiple of average volume to be HVN

        Returns:
            List of high volume levels with price and volume info
        """
        result = await self.get_volume_profile(symbol, lookback_minutes, bins)

        if result is None:
            return []

        hvns = self._calculator.find_high_volume_nodes(result, threshold_multiplier)

        return [
            {
                "price_low": hvn.price_low,
                "price_high": hvn.price_high,
                "price_mid": hvn.price_mid,
                "volume": hvn.total_volume,
                "buy_volume": hvn.buy_volume,
                "sell_volume": hvn.sell_volume,
                "delta": hvn.delta,
                "delta_percent": hvn.delta_percent,
            }
            for hvn in hvns
        ]

    async def get_low_volume_nodes(
        self,
        symbol: str,
        lookback_minutes: int = 60,
        bins: int = 50,
        threshold_multiplier: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Get low volume nodes (price tends to move quickly through these).

        Args:
            symbol: Trading pair symbol
            lookback_minutes: Minutes of trade data
            bins: Number of price levels
            threshold_multiplier: Multiple of average volume to be LVN

        Returns:
            List of low volume levels
        """
        result = await self.get_volume_profile(symbol, lookback_minutes, bins)

        if result is None:
            return []

        lvns = self._calculator.find_low_volume_nodes(result, threshold_multiplier)

        return [
            {
                "price_low": lvn.price_low,
                "price_high": lvn.price_high,
                "price_mid": lvn.price_mid,
                "volume": lvn.total_volume,
            }
            for lvn in lvns
        ]


# Singleton instance for convenience
_provider_instance: Optional[VolumeProfileProvider] = None


async def get_volume_profile_provider() -> VolumeProfileProvider:
    """Get or create the singleton VolumeProfileProvider instance."""
    global _provider_instance

    if _provider_instance is None:
        _provider_instance = VolumeProfileProvider()
        await _provider_instance.connect()

    return _provider_instance
