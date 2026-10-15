"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : __init__.py
    Project     : Project-103
    Description : Polars-native data loader for backtesting. Loads historical
              market data from CSV / Database / file cache. Returns polars
              DataFrames with a 'timestamp' column (no index).

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import polars as pl

from src.backtesting.models import DataConfig, DataSource

logger = logging.getLogger(__name__)

# Timeframe -> polars group_by_dynamic "every" duration string
TIMEFRAME_EVERY = {
    "1m": "1m",
    "2m": "2m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
}

# Timeframe hierarchy (smaller to larger)
TIMEFRAME_MINUTES = {
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
}


class DataLoader:
    """Loads and prepares historical data for backtesting (polars-native)."""

    def __init__(self, config: Optional[DataConfig] = None):
        self.config = config or DataConfig()
        self.cache: Dict[str, pl.DataFrame] = {}

        if self.config.cache_enabled:
            os.makedirs(self.config.cache_dir, exist_ok=True)

    async def load_candles(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pl.DataFrame:
        """Load candle data for a symbol as a polars DataFrame ('timestamp' column)."""
        cache_key = self._get_cache_key(symbol, timeframe, start_date, end_date)

        if cache_key in self.cache:
            data = self.cache[cache_key]
            self._report_span(data, symbol, timeframe, start_date, end_date, "memory")
            return data

        if self.config.cache_enabled:
            cached_data = self._load_from_file_cache(cache_key)
            if cached_data is not None:
                self.cache[cache_key] = cached_data
                self._report_span(cached_data, symbol, timeframe, start_date, end_date, "cache")
                return cached_data

        if self.config.source == DataSource.DATABASE:
            data = await self._load_from_database(symbol, timeframe, start_date, end_date)
        elif self.config.source == DataSource.CSV:
            data = self._load_from_csv(symbol, timeframe, start_date, end_date)
        else:
            raise ValueError(f"Unsupported data source: {self.config.source}")

        data = self._validate_and_clean(data)

        self.cache[cache_key] = data
        if self.config.cache_enabled:
            self._save_to_file_cache(cache_key, data)

        self._report_span(data, symbol, timeframe, start_date, end_date, "file")
        return data

    @staticmethod
    def _missing_weekdays(last_day, end_day) -> int:
        """How many weekdays are left uncovered between the last bar and the end
        of the request.

        A calendar count does not work: --end excludes the given day and the market
        is closed at weekends, so "data ended early" would fire on almost every run.
        A warning that always fires is never read.
        """
        n = 0
        day = last_day + timedelta(days=1)
        while day < end_day:  # end_day is excluded by the request
            if day.weekday() < 5:
                n += 1
            day += timedelta(days=1)
        return n

    @staticmethod
    def _report_span(
        data: pl.DataFrame,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        origin: str,
    ) -> None:
        """Report the ACTUAL boundaries of the loaded span.

        It used to log "from <start> to <end>" — the requested range, not the
        received one, and only on a cache miss. Data, however, ends silently: a
        partially downloaded day, an empty placeholder in the tick cache. Because of
        that line a data gap once looked like "the strategies stopped trading".
        """
        if data.height:
            first, last = data["timestamp"][0], data["timestamp"][-1]
            span = f"{first:%Y-%m-%d %H:%M} .. {last:%Y-%m-%d %H:%M}"
            missed = DataLoader._missing_weekdays(last.date(), end_date.date())
            if missed:
                span += f" — REQUESTED up to {end_date.date()}, missing " f"{missed} trading days"
        else:
            span = f"EMPTY for {start_date.date()} .. {end_date.date()}"
        logger.info(f"Loaded {data.height} candles for {symbol} {timeframe} " f"[{origin}]: {span}")

    async def load_multiple_symbols(
        self,
        symbols: List[str],
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, pl.DataFrame]:
        """Load candle data for multiple symbols."""
        result = {}
        for symbol in symbols:
            try:
                data = await self.load_candles(symbol, timeframe, start_date, end_date)
                result[symbol] = data
            except Exception as e:
                logger.error(f"Failed to load data for {symbol}: {e}")
        return result

    async def _load_from_database(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pl.DataFrame:
        """Load data from TimescaleDB with auto-aggregation from 1m if needed."""
        try:
            import asyncpg

            db_url = self.config.database_url or os.environ.get(
                "DATABASE_URL",
                "postgresql://localhost:5432/project_103",
            )
            conn = await asyncpg.connect(db_url)

            query = """
                SELECT timestamp, open, high, low, close,
                       volume, quote_volume, trades_count as trades
                FROM market_data.candles
                WHERE symbol = $1 AND timeframe = $2
                    AND timestamp >= $3 AND timestamp < $4
                ORDER BY timestamp ASC
            """
            rows = await conn.fetch(query, symbol, timeframe, start_date, end_date)

            period_minutes = (end_date - start_date).total_seconds() / 60
            timeframe_minutes = TIMEFRAME_MINUTES.get(timeframe, 15)
            expected_candles = period_minutes / timeframe_minutes
            data_coverage = (len(rows) / expected_candles) if expected_candles > 0 else 0
            should_aggregate = (
                data_coverage < 0.5
                and timeframe != "1m"
                and TIMEFRAME_MINUTES.get(timeframe, 0) > 1
            )

            if should_aggregate:
                rows_1m = await conn.fetch(query, symbol, "1m", start_date, end_date)
                await conn.close()
                if not rows_1m:
                    return pl.DataFrame()
                data = pl.from_dicts([dict(r) for r in rows_1m])
                data = data.with_columns(pl.col("timestamp").cast(pl.Datetime))
                return self._resample_candles(data, timeframe)

            await conn.close()
            if not rows:
                logger.warning(f"No data found for {symbol} {timeframe}")
                return pl.DataFrame()

            data = pl.from_dicts([dict(r) for r in rows])
            data = data.with_columns(pl.col("timestamp").cast(pl.Datetime))
            return data

        except ImportError:
            logger.error("asyncpg not installed. Using CSV fallback.")
            return self._load_from_csv(symbol, timeframe, start_date, end_date)
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

    def _resample_candles(self, data: pl.DataFrame, target_timeframe: str) -> pl.DataFrame:
        """Resample OHLCV data to a larger timeframe (polars group_by_dynamic)."""
        if target_timeframe not in TIMEFRAME_EVERY:
            raise ValueError(f"Unsupported timeframe: {target_timeframe}")

        aggs = [
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        ]
        if "quote_volume" in data.columns:
            aggs.append(pl.col("quote_volume").sum().alias("quote_volume"))
        if "trades" in data.columns:
            aggs.append(pl.col("trades").sum().alias("trades"))

        return (
            data.sort("timestamp")
            .group_by_dynamic("timestamp", every=TIMEFRAME_EVERY[target_timeframe])
            .agg(aggs)
            .drop_nulls()
        )

    def _load_from_csv(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pl.DataFrame:
        """Load data from CSV file into a polars DataFrame ('timestamp' column)."""
        csv_path = self.config.csv_path or "./data"
        file_path = os.path.join(csv_path, f"{symbol}_{timeframe}.csv")

        if not os.path.exists(file_path):
            # Try to aggregate from a lower timeframe CSV
            lower_tfs = ["1m", "3m", "5m", "15m", "30m", "1h"]
            target_idx = lower_tfs.index(timeframe) if timeframe in lower_tfs else None
            if target_idx is not None:
                for ltf in lower_tfs[:target_idx]:
                    ltf_path = os.path.join(csv_path, f"{symbol}_{ltf}.csv")
                    if os.path.exists(ltf_path):
                        logger.info(f"Aggregating {ltf} CSV -> {timeframe} for {symbol}")
                        ltf_data = self._read_csv_file(ltf_path, start_date, end_date)
                        if not ltf_data.is_empty():
                            data = self._resample_candles(ltf_data, timeframe)
                            logger.info(
                                f"Aggregated {len(ltf_data)} {ltf} -> {len(data)} {timeframe} candles"
                            )
                            return data
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        return self._read_csv_file(file_path, start_date, end_date)

    def _read_csv_file(
        self,
        file_path: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pl.DataFrame:
        """Read + parse + date-filter a candle CSV with polars."""
        df = pl.read_csv(file_path)
        ts_col = "timestamp" if "timestamp" in df.columns else "date"
        df = df.with_columns(
            pl.col(ts_col).cast(pl.Utf8).str.to_datetime(strict=False).alias("timestamp")
        )
        if ts_col == "date":
            df = df.drop("date")
        df = df.filter((pl.col("timestamp") >= start_date) & (pl.col("timestamp") < end_date))
        return df

    def _validate_and_clean(self, data: pl.DataFrame) -> pl.DataFrame:
        """Validate and clean candle data (polars)."""
        if data.is_empty():
            return data

        required_cols = ["open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in data.columns:
                raise ValueError(f"Missing required column: {col}")

        # Remove duplicate timestamps (keep first), sort chronologically
        data = data.unique(subset="timestamp", keep="first").sort("timestamp")

        # Fix invalid OHLC relationships (only the offending rows)
        invalid = (
            (pl.col("high") < pl.col("low"))
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
        )
        n_invalid = data.select(invalid.sum()).item()
        if n_invalid:
            logger.warning(f"Found {n_invalid} invalid candles, correcting...")
            data = data.with_columns(
                [
                    pl.when(invalid)
                    .then(pl.max_horizontal("open", "high", "low", "close"))
                    .otherwise(pl.col("high"))
                    .alias("high"),
                    pl.when(invalid)
                    .then(pl.min_horizontal("open", "high", "low", "close"))
                    .otherwise(pl.col("low"))
                    .alias("low"),
                ]
            )

        # Drop rows with null/NaN in critical columns; fill missing volume with 0
        data = data.drop_nulls(subset=["open", "high", "low", "close"])
        if data["open"].dtype.is_float():
            data = data.filter(
                pl.col("open").is_not_nan()
                & pl.col("high").is_not_nan()
                & pl.col("low").is_not_nan()
                & pl.col("close").is_not_nan()
            )
        data = data.with_columns(pl.col("volume").fill_null(0))

        return data

    def _get_cache_key(
        self, symbol: str, timeframe: str, start_date: datetime, end_date: datetime
    ) -> str:
        # The key includes a fingerprint of the source CSV. Without it, appending
        # data does not invalidate the cache: a re-run over the same window quietly
        # takes the old snapshot, and a before/after comparison runs on different
        # data while looking identical. Exactly that happened after a data top-up.
        key = f"{symbol}_{timeframe}_{start_date.date()}_{end_date.date()}"
        stamp = self._source_stamp(symbol, timeframe)
        return f"{key}_{stamp}" if stamp else key

    def _source_stamp(self, symbol: str, timeframe: str) -> str:
        """Short fingerprint of the source file: size and modification time."""
        if self.config.source != DataSource.CSV:
            return ""  # a database has no file
        path = os.path.join(self.config.csv_path or "./data", f"{symbol}_{timeframe}.csv")
        try:
            st = os.stat(path)
        except OSError:
            return ""
        return f"{st.st_size}-{int(st.st_mtime)}"

    def _load_from_file_cache(self, cache_key: str) -> Optional[pl.DataFrame]:
        """Load data from parquet file cache."""
        cache_file = os.path.join(self.config.cache_dir, f"{cache_key}.parquet")
        if os.path.exists(cache_file):
            try:
                logger.debug(f"Loading from file cache: {cache_file}")
                return pl.read_parquet(cache_file)
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
        return None

    def _save_to_file_cache(self, cache_key: str, data: pl.DataFrame):
        """Save data to parquet file cache."""
        cache_file = os.path.join(self.config.cache_dir, f"{cache_key}.parquet")
        try:
            data.write_parquet(cache_file, compression="snappy")
            logger.debug(f"Saved to file cache: {cache_file}")
            self._drop_superseded(cache_key)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

    def _drop_superseded(self, cache_key: str) -> None:
        """Delete snapshots of the same window taken from an older CSV version.

        Otherwise the source fingerprint in the key would pile up one file per data
        top-up — same window, different data. Only the fresh one is needed.
        """
        head, _, tail = cache_key.rpartition("_")
        # Without the fingerprint, trimming would eat the window end date and delete
        # snapshots of neighbouring periods. Remove only what is surely a fingerprint.
        if not re.fullmatch(r"\d+-\d+", tail):
            return
        head += "_"
        try:
            for name in os.listdir(self.config.cache_dir):
                if (
                    name.startswith(head)
                    and name.endswith(".parquet")
                    and name != f"{cache_key}.parquet"
                ):
                    os.remove(os.path.join(self.config.cache_dir, name))
        except OSError as e:
            logger.debug(f"Could not remove stale cache snapshots: {e}")

    def clear_cache(self):
        """Clear all cached data."""
        self.cache.clear()
        if self.config.cache_enabled:
            import shutil

            if os.path.exists(self.config.cache_dir):
                shutil.rmtree(self.config.cache_dir)
                os.makedirs(self.config.cache_dir)
        logger.info("Cache cleared")

    def get_data_info(self, data: pl.DataFrame) -> Dict[str, Any]:
        """Get information about loaded data."""
        if data.is_empty():
            return {"rows": 0}
        ts = data["timestamp"]
        return {
            "rows": len(data),
            "start": ts.min(),
            "end": ts.max(),
            "columns": data.columns,
        }

    def _count_missing_candles(self, data: pl.DataFrame) -> int:
        """Count gaps in a 1m candle series."""
        if data.is_empty() or len(data) < 2:
            return 0
        deltas = data["timestamp"].diff().dt.total_minutes()
        gaps = deltas.filter(deltas > 1) - 1
        return int(gaps.sum()) if len(gaps) else 0
