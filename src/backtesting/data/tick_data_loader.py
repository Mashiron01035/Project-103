"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 31.03.2026.
    File        : tick_data_loader.py
    Project     : Project-103
    Description : Tick data loader for backtesting. Fetches historical tick data
                  from MT5, caches as daily parquet files, and provides fast
                  lookups for tick-level SL/TP resolution.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# MT5 server timezone offset (EET/EEST):
# Summer time (DST): UTC+3  — last Sunday of March → last Sunday of October
# Winter time:       UTC+2  — last Sunday of October → last Sunday of March


def _get_dst_transitions(year: int) -> tuple:
    """Return (summer_start, winter_start) datetimes for a given year.

    The broker server runs on "New York + 7": UTC+2 in winter, UTC+3 in summer, and it
    switches TOGETHER WITH THE US — second Sunday of March and first Sunday of November,
    02:00 NY (07:00 / 06:00 UTC). European dates (last Sundays of March and October)
    were used before, and during the desync weeks (~3 weeks in March, ~1 week at the
    end of October) every broker timestamp was labelled one hour later than reality.
    Evidence: broker XAUUSD vs CME GC over a fixed London-time window — outside the
    desync weeks the correlation peaks at the expected minute (+1.00); inside them it
    peaks one hour later. Session-timed strategies were shifted by an hour in those weeks.
    """
    # Second Sunday of March, 02:00 EST = 07:00 UTC
    d = datetime(year, 3, 1)
    while d.weekday() != 6:  # 6 = Sunday
        d = d + timedelta(days=1)
    d = d + timedelta(days=7)
    summer_start = d.replace(hour=7, minute=0, second=0, tzinfo=timezone.utc)

    # First Sunday of November, 02:00 EDT = 06:00 UTC
    d = datetime(year, 11, 1)
    while d.weekday() != 6:
        d = d + timedelta(days=1)
    winter_start = d.replace(hour=6, minute=0, second=0, tzinfo=timezone.utc)

    return summer_start, winter_start


def get_mt5_offset_for_date(dt: datetime) -> int:
    """Return MT5 server UTC offset in seconds for a specific date.

    Handles DST transitions automatically.
    """
    year = dt.year
    summer_start, winter_start = _get_dst_transitions(year)

    # Make dt offset-aware for comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if summer_start <= dt < winter_start:
        return 3 * 3600  # UTC+3 (summer/EEST)
    else:
        return 2 * 3600  # UTC+2 (winter/EET)


def server_ms_to_utc_ms(ms: "pd.Series", day: datetime) -> "pd.Series":
    """Convert server timestamps to UTC, correctly on the DST switch day.

    The offset used to be taken once per day, at midnight. The switch, however,
    happens later in the day, so the whole switch Sunday got the PRE-switch offset,
    while the session opens after the switch. It was visible in the data: the week
    opened at 23:00 instead of 22:00 (spring 2026-03-29) and at 22:00 instead of
    23:00 (autumn 2025-10-26). In spring it also overlaid an hour of foreign prices
    onto Monday 00:00-00:59 — a discrepancy of about $50.

    The boundary is drawn on the SERVER clock itself, not on the converted UTC: in
    autumn the server hour 03:00-03:59 occurs twice and the two passes are
    indistinguishable after conversion. On the server clock it is unambiguous — in
    both directions the switch reads as "from 04:00 the new zone applies":

        spring  03:59 -> 05:00 server time (04:00 does not exist)
        autumn  04:59 -> 04:00 server time (04:00 repeats)

    The repeated hour is treated as the first pass — timestamps are ascending and
    the market is closed at that time anyway (the week opens at 01:00 server time).
    """
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    off_before = get_mt5_offset_for_date(day_start)
    off_after = get_mt5_offset_for_date(day_start + timedelta(days=1) - timedelta(seconds=1))
    if off_before == off_after:
        return ms - off_before * 1000  # ordinary day, fast path

    boundary_ms = int((day_start + timedelta(hours=4)).timestamp()) * 1000
    return (ms - off_after * 1000).where(ms >= boundary_ms, ms - off_before * 1000)


def server_sec_to_utc_sec(server_sec: int) -> int:
    """A single server timestamp (seconds) -> UTC. Same rule as above.

    Needed by live trading: timestamps arrive one at a time, and the offset must not
    be cached at startup — a bot that lives through a DST switch without a restart
    would label candles one hour off, and every session window would move with them.
    """
    day = datetime.fromtimestamp(server_sec, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    off_before = get_mt5_offset_for_date(day)
    off_after = get_mt5_offset_for_date(day + timedelta(days=1) - timedelta(seconds=1))
    if off_before == off_after:
        return server_sec - off_before
    boundary = int((day + timedelta(hours=4)).timestamp())
    return server_sec - (off_after if server_sec >= boundary else off_before)


def _detect_mt5_server_offset() -> int:
    """Return current MT5 server UTC offset in seconds."""
    now = datetime.now(timezone.utc)
    offset = get_mt5_offset_for_date(now)
    logger.info(f"MT5 server offset: +{offset // 3600}h (DST-aware)")
    return offset


class TickDataLoader:
    """
    Loads, caches, and serves historical tick data for backtesting.

    Tick data is stored as daily parquet files and loaded lazily with an
    LRU cache to keep memory usage bounded.
    """

    def __init__(self, cache_dir: str = "./cache/ticks", lru_days: int = 5):
        """
        Initialize tick data loader.

        Args:
            cache_dir: Root directory for tick parquet cache
            lru_days: Max number of day-files to keep in memory
        """
        self.cache_dir = cache_dir
        self.lru_days = lru_days
        self._memory_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._mt5_provider = None
        self._server_utc_offset_sec = _detect_mt5_server_offset()
        offset_h = self._server_utc_offset_sec // 3600
        sign = "+" if offset_h >= 0 else ""
        logger.info(f"TickDataLoader: server UTC offset: {sign}{offset_h}h")

    async def prefetch_ticks(self, symbol: str, start_date: datetime, end_date: datetime) -> int:
        """
        Fetch tick data from MT5 for the entire date range, save as daily parquet files.

        Args:
            symbol: Trading symbol (e.g. "XAUUSD.s")
            start_date: Start date (UTC)
            end_date: End date (UTC)

        Returns:
            Total number of ticks fetched
        """
        symbol_dir = os.path.join(self.cache_dir, symbol.replace(".", "_"))
        os.makedirs(symbol_dir, exist_ok=True)

        provider = await self._get_mt5_provider()
        if provider is None:
            logger.error("MT5 provider not available for tick data fetching")
            return 0

        total_ticks = 0
        current_date = start_date.date()
        end = end_date.date()
        days_total = (end - current_date).days + 1
        days_done = 0

        while current_date <= end:
            parquet_path = os.path.join(symbol_dir, f"{current_date}.parquet")

            if os.path.exists(parquet_path) and not self._is_partial_day(
                parquet_path, current_date
            ):
                # Already cached, read count for logging
                try:
                    df = pd.read_parquet(parquet_path)
                    total_ticks += len(df)
                except Exception:
                    pass
                days_done += 1
                current_date += timedelta(days=1)
                continue

            # Fetch ticks for this day (UTC boundaries)
            day_start = datetime(
                current_date.year, current_date.month, current_date.day, tzinfo=timezone.utc
            )
            day_end = day_start + timedelta(days=1)

            ticks = await provider.get_historical_ticks(symbol, day_start, day_end)

            if ticks is not None and len(ticks) > 0:
                df = pd.DataFrame(ticks)

                # Normalize time_msc from server time to UTC.
                # The offset is computed PER TIMESTAMP, not per day at midnight:
                # otherwise the DST switch Sunday gets the old offset for the whole day.
                if "time_msc" in df.columns:
                    df["time_msc"] = server_ms_to_utc_ms(df["time_msc"].astype(np.int64), day_start)
                elif "time" in df.columns:
                    # Fallback: use 'time' (seconds) and convert to ms
                    df["time_msc"] = server_ms_to_utc_ms(
                        df["time"].astype(np.int64) * 1000, day_start
                    )

                # Keep only needed columns
                cols = ["time_msc", "bid", "ask", "last", "volume", "flags"]
                available = [c for c in cols if c in df.columns]
                df = df[available]

                df.to_parquet(parquet_path, index=False)
                total_ticks += len(df)

                logger.debug(f"Cached {len(df)} ticks for {symbol} {current_date}")
            else:
                # An empty file is cached so weekends are not re-fetched. But it
                # also blocks any later re-download forever (see the exists() check
                # above), which already cost four days of data: a day requested
                # before it had started was cached as empty for good, and two more
                # days were cached empty while the terminal was logged into an
                # account without the symbol.
                #
                # So a placeholder is cached ONLY when the absence of data is
                # explained: the day is fully over, the terminal knows the symbol,
                # and it is a Saturday.
                #
                # Why Saturday only: a one-off empty MT5 response is indistinguishable
                # from "market closed" and used to stick in the cache forever. That is
                # how a Sunday evening session (37,702 ticks) went missing, although it
                # downloaded fine on the second attempt. Only Saturday is guaranteed
                # empty; weekdays and Sundays are never empty except on holidays, and
                # re-requesting two holidays a year is cheaper than losing a
                # session.
                today_utc = datetime.now(timezone.utc).date()
                if current_date >= today_utc:
                    logger.info(
                        f"{symbol} {current_date}: day not finished, not caching "
                        f"an empty placeholder — will download later"
                    )
                elif not self._symbol_available(symbol):
                    logger.warning(
                        f"{symbol} {current_date}: symbol not available in the terminal "
                        f"(wrong account?) — not caching an empty placeholder"
                    )
                elif current_date.weekday() != 5:
                    logger.warning(
                        f"{symbol} {current_date}: empty although not a Saturday "
                        f"(holiday or glitch) — not caching a placeholder, "
                        f"will re-request next time"
                    )
                else:
                    pd.DataFrame(
                        columns=["time_msc", "bid", "ask", "last", "volume", "flags"]
                    ).to_parquet(parquet_path, index=False)

            days_done += 1
            if days_done % 10 == 0 or days_done == days_total:
                logger.info(
                    f"Tick prefetch {symbol}: {days_done}/{days_total} days, "
                    f"{total_ticks:,} ticks total"
                )

            current_date += timedelta(days=1)

        logger.info(
            f"Tick data ready for {symbol}: {total_ticks:,} ticks " f"over {days_total} days"
        )
        return total_ticks

    def get_ticks_for_candle(
        self, symbol: str, candle_start_ms: int, candle_end_ms: int
    ) -> Optional[np.ndarray]:
        """
        Get tick data within a candle's time window.

        Uses LRU cache for day-level parquet files.

        Args:
            symbol: Trading symbol
            candle_start_ms: Candle open time in UTC milliseconds
            candle_end_ms: Candle close time in UTC milliseconds

        Returns:
            numpy array with columns [time_msc, bid, ask] sorted by time,
            or None if no ticks available
        """
        # Determine which days we need
        start_date = datetime.fromtimestamp(candle_start_ms / 1000, tz=timezone.utc).date()
        end_date = datetime.fromtimestamp(candle_end_ms / 1000, tz=timezone.utc).date()

        frames = []
        current = start_date
        while current <= end_date:
            df = self._load_day(symbol, current)
            if df is not None and len(df) > 0:
                frames.append(df)
            current += timedelta(days=1)

        if not frames:
            return None

        combined = pd.concat(frames, ignore_index=True)

        # Filter to candle window
        mask = (combined["time_msc"] >= candle_start_ms) & (combined["time_msc"] < candle_end_ms)
        candle_ticks = combined.loc[mask]

        if candle_ticks.empty:
            return None

        # Return as numpy for fast iteration in tick_resolver
        cols = ["time_msc", "bid", "ask"]
        available = [c for c in cols if c in candle_ticks.columns]
        return candle_ticks[available].to_numpy()

    def _load_day(self, symbol: str, date) -> Optional[pd.DataFrame]:
        """Load a single day's tick data with LRU caching."""
        cache_key = f"{symbol.replace('.', '_')}_{date}"

        # Check memory cache
        if cache_key in self._memory_cache:
            self._memory_cache.move_to_end(cache_key)
            return self._memory_cache[cache_key]

        # Load from parquet
        symbol_dir = os.path.join(self.cache_dir, symbol.replace(".", "_"))
        parquet_path = os.path.join(symbol_dir, f"{date}.parquet")

        if not os.path.exists(parquet_path):
            return None

        try:
            df = pd.read_parquet(parquet_path)
        except Exception as e:
            logger.warning(f"Failed to read tick cache {parquet_path}: {e}")
            return None

        # Add to LRU cache, evict oldest if needed
        self._memory_cache[cache_key] = df
        if len(self._memory_cache) > self.lru_days:
            self._memory_cache.popitem(last=False)

        return df

    async def _get_mt5_provider(self):
        """Lazy-init MT5 provider for tick fetching."""
        if self._mt5_provider is not None:
            return self._mt5_provider

        try:
            from src.utils.mt5_bridge import MT5_AVAILABLE

            if not MT5_AVAILABLE:
                logger.warning("MT5 not available — tick data cannot be fetched")
                return None

            from src.data.providers.mt5_provider import DataProviderConfig, MT5DataProvider

            config = DataProviderConfig(exchange="mt5")
            self._mt5_provider = MT5DataProvider(config)
            await self._mt5_provider.connect()
            return self._mt5_provider
        except Exception as e:
            logger.error(f"Failed to initialize MT5 provider for ticks: {e}")
            return None

    @staticmethod
    def _is_partial_day(path: str, day) -> bool:
        """Whether the day was downloaded while still in progress, and is incomplete.

        An "exists" check skips such days forever. Real case: a day downloaded at
        09:07 on the same day contained only the first seven hours, and stayed
        truncated — the backtest silently ended there too.

        The test is simple and reliable: the file was written NO LATER than the day
        it describes, so at write time the day was not over yet. Empty files never
        reach this point — the size check filters them, they have their own logic.
        """
        try:
            # File size is useless here: parquet compresses homogeneous columns so
            # well that 20,000 ticks fit in 4 KB. The row count is read from the
            # metadata — no data is loaded.
            import pyarrow.parquet as pq

            if pq.ParquetFile(path).metadata.num_rows == 0:
                return False  # weekend placeholder, leave it alone
            written = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).date()
        except Exception:  # noqa: BLE001
            return False
        return written <= day

    @staticmethod
    def _symbol_available(symbol: str) -> bool:
        """Whether the connected terminal knows this symbol.

        Distinguishes "market closed" from "terminal on another account". The latter
        must not turn into a cached empty placeholder: a symbol may exist at the
        broker but not on the MetaQuotes demo server, which silently returns zero ticks.
        """
        try:
            from src.utils.mt5_bridge import mt5

            return mt5.symbol_info(symbol) is not None
        except Exception:  # noqa: BLE001
            return True  # could not check — behave as before

    def has_ticks_cached(self, symbol: str, start_date: datetime, end_date: datetime) -> bool:
        """Check if tick data is already cached for the date range."""
        symbol_dir = os.path.join(self.cache_dir, symbol.replace(".", "_"))
        if not os.path.exists(symbol_dir):
            return False

        current = start_date.date()
        end = end_date.date()
        while current <= end:
            parquet_path = os.path.join(symbol_dir, f"{current}.parquet")
            if not os.path.exists(parquet_path):
                return False
            current += timedelta(days=1)
        return True
