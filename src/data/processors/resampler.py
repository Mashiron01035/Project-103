"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : resampler
    Project     : Project-103
    Description : Data resampler for converting between timeframes.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List

from src.models import Candle

logger = logging.getLogger(__name__)


class DataResampler:
    """Resamples candle data to different timeframes."""

    def __init__(self):
        """Initialize data resampler."""
        self.timeframe_minutes = {
            "1m": 1,
            "2m": 2,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "12h": 720,
            "1d": 1440,
            "1w": 10080,
            "1M": 43200,
        }

    def resample(self, candles: List[Candle], target_timeframe: str) -> List[Candle]:
        """
        Resample candles to target timeframe.

        Args:
            candles: Source candles
            target_timeframe: Target timeframe

        Returns:
            Resampled candles
        """
        if not candles:
            return []

        source_tf = candles[0].timeframe
        source_minutes = self.timeframe_minutes.get(source_tf, 1)
        target_minutes = self.timeframe_minutes.get(target_timeframe, 1)

        # Can only upsample (to higher timeframes)
        if target_minutes <= source_minutes:
            logger.warning(f"Cannot resample from {source_tf} to {target_timeframe}")
            return candles

        # Group candles by target timeframe
        grouped = self._group_candles(candles, target_minutes)

        # Aggregate groups
        resampled = []
        for group in grouped.values():
            if group:
                resampled.append(self._aggregate_candles(group, target_timeframe))

        return sorted(resampled, key=lambda c: c.timestamp)

    def _group_candles(self, candles: List[Candle], target_minutes: int) -> Dict[int, List[Candle]]:
        """Group candles by target timeframe."""
        grouped = {}

        for candle in candles:
            dt = datetime.fromtimestamp(candle.timestamp / 1000)

            # Calculate target period start
            total_minutes = dt.hour * 60 + dt.minute
            period_minutes = (total_minutes // target_minutes) * target_minutes

            period_dt = dt.replace(
                hour=period_minutes // 60, minute=period_minutes % 60, second=0, microsecond=0
            )

            period_ts = int(period_dt.timestamp() * 1000)

            if period_ts not in grouped:
                grouped[period_ts] = []
            grouped[period_ts].append(candle)

        return grouped

    def _aggregate_candles(self, candles: List[Candle], timeframe: str) -> Candle:
        """Aggregate multiple candles into one."""
        first = candles[0]
        last = candles[-1]

        return Candle(
            symbol=first.symbol,
            timeframe=timeframe,
            timestamp=first.timestamp,
            open=first.open,
            high=max(c.high for c in candles),
            low=min(c.low for c in candles),
            close=last.close,
            volume=sum(c.volume for c in candles),
            quote_volume=sum(c.quote_volume for c in candles if c.quote_volume),
            is_closed=last.is_closed,
            source="resampled",
            exchange=first.exchange,
        )
