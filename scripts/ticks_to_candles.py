"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 04.04.2026.
    File        : ticks_to_candles.py
    Project     : Project-103
    Description : Convert tick parquet files to 1-minute candle CSV.

    SPDX-License-Identifier: AGPL-3.0

"""

import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ticks_to_candles(symbol: str, timeframe: str = "1min", label: str = "1m"):
    """Convert tick data to OHLCV candles."""
    # Tick directory uses underscore instead of dot
    tick_symbol = symbol.replace(".", "_")
    tick_dir = Path(PROJECT_ROOT) / "cache" / "ticks" / tick_symbol

    if not tick_dir.exists():
        print(f"Tick directory not found: {tick_dir}")
        sys.exit(1)

    files = sorted(tick_dir.glob("*.parquet"))
    print(f"Found {len(files)} tick files for {symbol}")

    all_candles = []

    for i, f in enumerate(files):
        ticks = pd.read_parquet(f)
        if len(ticks) == 0:
            continue

        # Convert time_msc to datetime index
        ticks["timestamp"] = pd.to_datetime(ticks["time_msc"], unit="ms", utc=True)
        ticks.set_index("timestamp", inplace=True)
        ticks.index = ticks.index.tz_localize(None)  # Remove tz for consistency

        # Bid price (match MT5 broker feed used by live)
        # Previously used mid = (bid + ask)/2 — caused +half-spread offset vs live,
        # which shifted SL/TP thresholds and could keep positions open in backtest
        # while live closed them (or vice versa).
        # Resample to candles using bid only.
        candles = ticks["bid"].resample(timeframe).ohlc()
        candles.columns = ["open", "high", "low", "close"]

        # Volume: sum existing column if present (Bybit MT5), else use tick count (Dukascopy)
        if "volume" in ticks.columns:
            candles["volume"] = ticks["volume"].resample(timeframe).sum()
        else:
            candles["volume"] = ticks["bid"].resample(timeframe).count()
        candles["quote_volume"] = 0.0

        # Spread: average spread per candle in points
        ticks["spread_raw"] = ticks["ask"] - ticks["bid"]
        candles["spread"] = (ticks["spread_raw"].resample(timeframe).mean() * 100).round(0)

        # Drop empty candles (no ticks in that minute)
        candles.dropna(subset=["open"], inplace=True)

        all_candles.append(candles)

        if (i + 1) % 10 == 0:
            print(f"  Processed {i + 1}/{len(files)} days...")

    if not all_candles:
        print("No candles generated!")
        sys.exit(1)

    df = pd.concat(all_candles)
    df.sort_index(kind="stable", inplace=True)

    # A minute that falls into two files is MERGED, not picked.
    #
    # keep="first" used to be here, and it silently corrupted bars. A single tick at
    # exactly 00:00:00.000 lands in the previous day's file and forms a one-tick
    # "bar" — O=H=L=C. It came first and displaced the real minute: a midnight bar
    # had a $0 range instead of $3.08. There were four such seams in the cache.
    #
    # Concatenation is chronological (files are sorted), so first/last within a
    # group give the correct open and close.
    dup = df.index.duplicated(keep=False)
    if dup.any():
        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
            "spread": "mean",
        }
        agg = {c: how for c, how in agg.items() if c in df.columns}
        merged = df[dup].groupby(level=0).agg(agg)
        if "spread" in merged.columns:
            merged["spread"] = merged["spread"].round(0)
        merged = merged[[c for c in df.columns if c in merged.columns]]
        df = pd.concat([df[~dup], merged]).sort_index()
        print(f"  Merged minutes that fell into two files: {len(merged)}")

    # Save to data/ directory (same format as download_mt5_historical)
    # Writes to data/_incoming/ by default, NOT over the working CSV: rebuilding
    # from an incomplete tick cache would otherwise silently replace the working history.
    # Direct write: --overwrite.
    direct = "--overwrite" in sys.argv
    out_dir = Path(PROJECT_ROOT) / "data" if direct else Path(PROJECT_ROOT) / "data" / "_incoming"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{symbol}_{label}.csv"

    df.to_csv(out_file, index_label="timestamp")

    # Also save as parquet in cache/
    cache_dir = Path(PROJECT_ROOT) / "cache"
    start_date = df.index[0].strftime("%Y-%m-%d")
    end_date = df.index[-1].strftime("%Y-%m-%d")
    parquet_file = cache_dir / f"{symbol}_{label}_{start_date}_{end_date}.parquet"
    df.to_parquet(parquet_file, compression="snappy")

    print("\nDone!")
    print(f"  Candles: {len(df):,}")
    print(f"  Range: {df.index[0]} to {df.index[-1]}")
    print(f"  CSV: {out_file}")
    print(f"  Parquet: {parquet_file}")


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD.s"
    timeframe = "1m"

    # Parse --timeframe flag
    for i, arg in enumerate(sys.argv):
        if arg == "--timeframe" and i + 1 < len(sys.argv):
            timeframe = sys.argv[i + 1]

    # Convert shorthand to pandas resample format
    tf_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}
    pandas_tf = tf_map.get(timeframe, timeframe)
    ticks_to_candles(symbol, timeframe=pandas_tf, label=timeframe)
