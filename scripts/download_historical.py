#!/usr/bin/env python3
"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : download_historical.py
    Project     : Project-103
    Description : Historical data downloader for backtesting.
                  Downloads OHLCV candle data from Bybit and saves to TimescaleDB.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tqdm import tqdm

from src.data.providers.base_provider import DataProviderConfig
from src.data.providers.bybit_provider import BybitDataProvider

# Setup logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# Database Helper Functions
# =============================================================================


def get_db_connection(database_url: str):
    """Get raw database connection for queries."""
    try:
        import psycopg2

        return psycopg2.connect(database_url)
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise


def list_data(database_url: str):
    """List all available data in database."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            symbol,
            timeframe,
            COUNT(*) as candles,
            MIN(timestamp) as first_candle,
            MAX(timestamp) as last_candle
        FROM market_data.candles
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("\nNo data in database.\n")
        return

    # Calculate totals
    total_candles = sum(row[2] for row in rows)
    symbols = set(row[0] for row in rows)

    print()
    print("=" * 75)
    print("  Available Historical Data")
    print("=" * 75)
    print(f"  {'Symbol':<12} {'Timeframe':<10} {'Candles':>10} {'From':<12} {'To':<12}")
    print("-" * 75)

    for row in rows:
        symbol, timeframe, count, first_dt, last_dt = row
        first_str = first_dt.strftime("%Y-%m-%d") if first_dt else "N/A"
        last_str = last_dt.strftime("%Y-%m-%d") if last_dt else "N/A"
        print(f"  {symbol:<12} {timeframe:<10} {count:>10,} {first_str:<12} {last_str:<12}")

    print("-" * 75)
    print(f"  Total: {len(symbols)} symbols, {total_candles:,} candles")
    print("=" * 75)
    print()


def show_stats(database_url: str):
    """Show detailed database statistics."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    # Overall stats
    cursor.execute("SELECT COUNT(*) FROM market_data.candles")
    total_candles = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM market_data.candles")
    total_symbols = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM market_data.candles")
    date_range = cursor.fetchone()

    # Size estimate
    cursor.execute(
        """
        SELECT pg_size_pretty(pg_total_relation_size('market_data.candles'))
    """
    )
    table_size = cursor.fetchone()[0]

    # Per symbol stats
    cursor.execute(
        """
        SELECT
            symbol,
            COUNT(*) as candles,
            COUNT(DISTINCT timeframe) as timeframes,
            MIN(timestamp) as first,
            MAX(timestamp) as last
        FROM market_data.candles
        GROUP BY symbol
        ORDER BY candles DESC
    """
    )
    symbol_stats = cursor.fetchall()

    # Per timeframe stats
    cursor.execute(
        """
        SELECT
            timeframe,
            COUNT(*) as candles,
            COUNT(DISTINCT symbol) as symbols
        FROM market_data.candles
        GROUP BY timeframe
        ORDER BY
            CASE timeframe
                WHEN '1m' THEN 1
                WHEN '3m' THEN 2
                WHEN '5m' THEN 3
                WHEN '15m' THEN 4
                WHEN '30m' THEN 5
                WHEN '1h' THEN 6
                WHEN '2h' THEN 7
                WHEN '4h' THEN 8
                WHEN '6h' THEN 9
                WHEN '12h' THEN 10
                WHEN '1d' THEN 11
                WHEN '1w' THEN 12
            END
    """
    )
    timeframe_stats = cursor.fetchall()

    conn.close()

    print()
    print("=" * 60)
    print("  Database Statistics")
    print("=" * 60)
    print(f"  Total candles:  {total_candles:,}")
    print(f"  Total symbols:  {total_symbols}")
    print(f"  Table size:     {table_size}")
    if date_range[0] and date_range[1]:
        print(
            f"  Date range:     {date_range[0].strftime('%Y-%m-%d')} to {date_range[1].strftime('%Y-%m-%d')}"
        )
    print()

    print("  By Symbol:")
    print("-" * 60)
    for symbol, candles, timeframes, first, last in symbol_stats[:10]:
        days = (last - first).days if first and last else 0
        print(f"    {symbol:<12} {candles:>10,} candles, {timeframes} tf, {days} days")
    if len(symbol_stats) > 10:
        print(f"    ... and {len(symbol_stats) - 10} more symbols")
    print()

    print("  By Timeframe:")
    print("-" * 60)
    for timeframe, candles, symbols in timeframe_stats:
        print(f"    {timeframe:<6} {candles:>12,} candles across {symbols} symbols")

    print("=" * 60)
    print()


def delete_data(
    database_url: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    before_date: Optional[str] = None,
    confirm: bool = False,
):
    """Delete data from database."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    # Build WHERE clause
    conditions = []
    params = []

    if symbol:
        conditions.append("symbol = %s")
        params.append(symbol.upper())

    if timeframe:
        conditions.append("timeframe = %s")
        params.append(timeframe)

    if before_date:
        conditions.append("timestamp < %s")
        params.append(datetime.strptime(before_date, "%Y-%m-%d"))

    if not conditions:
        print("\nError: Specify at least one filter (--symbol, --timeframe, or --before)")
        print("Use --delete-all to delete everything.\n")
        conn.close()
        return

    where_clause = " AND ".join(conditions)

    # Count affected rows
    cursor.execute(f"SELECT COUNT(*) FROM market_data.candles WHERE {where_clause}", params)
    count = cursor.fetchone()[0]

    if count == 0:
        print("\nNo matching data found.\n")
        conn.close()
        return

    # Show what will be deleted
    print(f"\nWill delete {count:,} candles matching:")
    if symbol:
        print(f"  - Symbol: {symbol.upper()}")
    if timeframe:
        print(f"  - Timeframe: {timeframe}")
    if before_date:
        print(f"  - Before: {before_date}")
    print()

    if not confirm:
        sys.stdout.write("Are you sure? (yes/no): ")
        sys.stdout.flush()
        response = input()
        if response.lower() != "yes":
            print("Cancelled.\n")
            conn.close()
            return

    # Delete
    cursor.execute(f"DELETE FROM market_data.candles WHERE {where_clause}", params)
    conn.commit()

    print(f"Deleted {count:,} candles.\n")
    conn.close()


def delete_all_data(database_url: str, confirm: bool = False):
    """Delete all data from database."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM market_data.candles")
    count = cursor.fetchone()[0]

    if count == 0:
        print("\nDatabase is already empty.\n")
        conn.close()
        return

    print()
    print("!" * 60)
    print(f"  WARNING: Will delete ALL {count:,} candles from database!")
    print("  This action cannot be undone.")
    print("!" * 60)
    print()

    if not confirm:
        sys.stdout.write("Type 'DELETE ALL' to confirm: ")
        sys.stdout.flush()
        response = input()
        if response != "DELETE ALL":
            print("Cancelled.\n")
            conn.close()
            return

    cursor.execute("TRUNCATE TABLE market_data.candles")
    conn.commit()

    print(f"\nDeleted all {count:,} candles.\n")
    conn.close()


def check_gaps(database_url: str, symbol: str, timeframe: str):
    """Check for gaps in historical data."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    # Get timeframe in minutes
    tf_minutes = {
        "1m": 1,
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

    if timeframe not in tf_minutes:
        print(f"Unknown timeframe: {timeframe}")
        conn.close()
        return

    interval_minutes = tf_minutes[timeframe]

    cursor.execute(
        """
        SELECT timestamp FROM market_data.candles
        WHERE symbol = %s AND timeframe = %s
        ORDER BY timestamp
    """,
        (symbol.upper(), timeframe),
    )

    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 2:
        print(f"\nNot enough data for {symbol} {timeframe}\n")
        return

    # Find gaps
    gaps = []
    expected_interval = timedelta(minutes=interval_minutes)

    for i in range(1, len(rows)):
        prev_ts = rows[i - 1][0]
        curr_ts = rows[i][0]
        diff = curr_ts - prev_ts

        if diff > expected_interval * 1.5:  # Allow some tolerance
            missing = int(diff.total_seconds() / 60 / interval_minutes) - 1
            gaps.append((prev_ts, curr_ts, missing))

    print()
    print("=" * 60)
    print(f"  Gap Analysis: {symbol} {timeframe}")
    print("=" * 60)
    print(f"  Total candles: {len(rows):,}")
    print(
        f"  Date range:    {rows[0][0].strftime('%Y-%m-%d %H:%M')} to {rows[-1][0].strftime('%Y-%m-%d %H:%M')}"
    )
    print(f"  Gaps found:    {len(gaps)}")

    if gaps:
        print()
        print("  Gap Details:")
        print("-" * 60)
        total_missing = 0
        for i, (start, end, missing) in enumerate(gaps[:20]):
            print(
                f"    {start.strftime('%Y-%m-%d %H:%M')} -> {end.strftime('%Y-%m-%d %H:%M')} ({missing} missing)"
            )
            total_missing += missing

        if len(gaps) > 20:
            print(f"    ... and {len(gaps) - 20} more gaps")
            total_missing = sum(g[2] for g in gaps)

        print()
        print(f"  Total missing candles: ~{total_missing:,}")
    else:
        print("  No gaps detected!")

    print("=" * 60)
    print()


def export_data(database_url: str, symbol: str, timeframe: str, output_path: str):
    """Export data to CSV file."""
    conn = get_db_connection(database_url)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT timestamp, open, high, low, close, volume, quote_volume
        FROM market_data.candles
        WHERE symbol = %s AND timeframe = %s
        ORDER BY timestamp
    """,
        (symbol.upper(), timeframe),
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print(f"\nNo data found for {symbol} {timeframe}\n")
        return

    # Write CSV
    import csv

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "quote_volume"])
        for row in rows:
            writer.writerow(
                [
                    row[0].strftime("%Y-%m-%d %H:%M:%S"),
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                ]
            )

    print(f"\nExported {len(rows):,} candles to {output_path}\n")


# =============================================================================
# Downloader Class
# =============================================================================


class HistoricalDataDownloader:
    """Downloads historical candle data from exchange."""

    MAX_CANDLES_PER_REQUEST = 200
    REQUEST_DELAY = 0.1

    TIMEFRAME_MS = {
        "1m": 60 * 1000,
        "3m": 3 * 60 * 1000,
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "30m": 30 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "2h": 2 * 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "6h": 6 * 60 * 60 * 1000,
        "12h": 12 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
        "1w": 7 * 24 * 60 * 60 * 1000,
    }

    def __init__(
        self,
        database_url: str = "postgresql://trader:trader@localhost:5432/project103",
        testnet: bool = False,
    ):
        self.database_url = database_url
        self.testnet = testnet
        self.provider: Optional[BybitDataProvider] = None
        self.db_conn = None
        self.total_downloaded = 0
        self.total_saved = 0

    async def connect(self):
        """Connect to exchange and database."""
        config = DataProviderConfig(
            exchange="bybit",
            testnet=self.testnet,
            ws_enabled=False,
            max_candles_per_request=self.MAX_CANDLES_PER_REQUEST,
        )
        self.provider = BybitDataProvider(config)

        connected = await self.provider.connect()
        if not connected:
            raise ConnectionError("Failed to connect to Bybit API")

        # Connect to database directly (for market_data schema)
        import psycopg2

        self.db_conn = psycopg2.connect(self.database_url)

    def save_candles_to_market_data(self, candles: List[dict]) -> int:
        """Save candles directly to market_data.candles with ON CONFLICT."""
        if not candles or not self.db_conn:
            return 0

        cursor = self.db_conn.cursor()
        saved = 0

        for candle in candles:
            try:
                cursor.execute(
                    """
                    INSERT INTO market_data.candles
                    (timestamp, symbol, timeframe, open, high, low, close, volume,
                     quote_volume, trades_count, exchange, source, is_closed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (timestamp, symbol, timeframe) DO NOTHING
                """,
                    (
                        candle["timestamp"],
                        candle["symbol"],
                        candle["timeframe"],
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        candle.get("quote_volume", 0),
                        candle.get("trades", 0),
                        candle.get("exchange", "bybit"),
                        candle.get("source", "historical"),
                        candle.get("is_closed", True),
                    ),
                )
                saved += 1
            except Exception as e:
                logger.debug(f"Error saving candle: {e}")

        self.db_conn.commit()
        return saved

    async def disconnect(self):
        """Disconnect from exchange and database."""
        if self.db_conn:
            self.db_conn.close()
        if self.provider:
            await self.provider.disconnect()

    async def download(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        pbar_position: int = 0,
    ) -> int:
        """Download historical data for a single symbol."""
        if timeframe not in self.TIMEFRAME_MS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        candle_ms = self.TIMEFRAME_MS[timeframe]
        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)

        expected_candles = (end_ms - start_ms) // candle_ms
        downloaded = 0
        current_start = start_ms
        errors = 0

        with tqdm(
            total=expected_candles,
            desc=f"{symbol:12}",
            unit="candles",
            position=pbar_position,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as pbar:
            while current_start < end_ms:
                batch_end = min(current_start + (self.MAX_CANDLES_PER_REQUEST * candle_ms), end_ms)

                try:
                    candles = await self.provider.get_candles(
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=self.MAX_CANDLES_PER_REQUEST,
                        start_time=current_start,
                        end_time=batch_end,
                    )

                    if candles:
                        candle_dicts = []
                        for candle in candles:
                            candle_dicts.append(
                                {
                                    "symbol": candle.symbol,
                                    "timeframe": candle.timeframe,
                                    "timestamp": datetime.fromtimestamp(candle.timestamp / 1000),
                                    "open": candle.open,
                                    "high": candle.high,
                                    "low": candle.low,
                                    "close": candle.close,
                                    "volume": candle.volume,
                                    "quote_volume": candle.quote_volume or 0,
                                    "trades": 0,
                                    "is_closed": candle.is_closed,
                                    "exchange": candle.exchange,
                                    "source": candle.source,
                                }
                            )

                        saved = self.save_candles_to_market_data(candle_dicts)
                        downloaded += len(candles)
                        self.total_saved += saved
                        pbar.update(len(candles))

                        last_ts = candles[-1].timestamp
                        current_start = last_ts + candle_ms
                    else:
                        current_start = batch_end

                    await asyncio.sleep(self.REQUEST_DELAY)

                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        pbar.set_postfix_str(f"retry {errors}/3")
                        await asyncio.sleep(1)
                    else:
                        pbar.set_postfix_str("error")
                        logger.error(f"Error downloading {symbol}: {e}")
                        current_start = batch_end
                        errors = 0

        self.total_downloaded += downloaded
        return downloaded

    async def download_multiple(
        self, symbols: List[str], timeframe: str, start_date: datetime, end_date: datetime
    ) -> dict:
        """Download historical data for multiple symbols."""
        results = {}
        print(f"\nDownloading {len(symbols)} symbols...\n")

        for symbol in symbols:
            try:
                count = await self.download(
                    symbol, timeframe, start_date, end_date, pbar_position=0
                )
                results[symbol] = {"status": "success", "candles": count}
            except Exception as e:
                logger.error(f"Failed to download {symbol}: {e}")
                results[symbol] = {"status": "error", "error": str(e)}

        return results


# =============================================================================
# CLI
# =============================================================================


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Historical data manager for backtesting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  download    Download historical data from Bybit
  list        List available data in database
  stats       Show detailed database statistics
  gaps        Check for gaps in data
  export      Export data to CSV
  delete      Delete specific data
  delete-all  Delete all data

Examples:
  # Download data
  python scripts/download_historical.py download --symbols BTCUSDT,ETHUSDT --timeframe 15m --days 30

  # List available data
  python scripts/download_historical.py list

  # Show statistics
  python scripts/download_historical.py stats

  # Check for gaps
  python scripts/download_historical.py gaps --symbol BTCUSDT --timeframe 1m

  # Export to CSV
  python scripts/download_historical.py export --symbol BTCUSDT --timeframe 1h --output btc_1h.csv

  # Delete specific data
  python scripts/download_historical.py delete --symbol BTCUSDT --timeframe 1m

  # Delete old data
  python scripts/download_historical.py delete --before 2024-01-01
        """,
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Download command
    dl_parser = subparsers.add_parser("download", help="Download historical data")
    dl_group = dl_parser.add_mutually_exclusive_group(required=True)
    dl_group.add_argument("--symbol", "-s", type=str, help="Single trading pair")
    dl_group.add_argument("--symbols", type=str, help="Comma-separated list of pairs")
    dl_parser.add_argument(
        "--timeframe",
        "-t",
        type=str,
        required=True,
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"],
    )
    dl_parser.add_argument("--days", "-d", type=int, help="Days to download")
    dl_parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    dl_parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    dl_parser.add_argument("--testnet", action="store_true", help="Use testnet")

    # List command
    subparsers.add_parser("list", help="List available data")

    # Stats command
    subparsers.add_parser("stats", help="Show database statistics")

    # Gaps command
    gaps_parser = subparsers.add_parser("gaps", help="Check for data gaps")
    gaps_parser.add_argument("--symbol", "-s", type=str, required=True)
    gaps_parser.add_argument("--timeframe", "-t", type=str, required=True)

    # Export command
    export_parser = subparsers.add_parser("export", help="Export data to CSV")
    export_parser.add_argument("--symbol", "-s", type=str, required=True)
    export_parser.add_argument("--timeframe", "-t", type=str, required=True)
    export_parser.add_argument("--output", "-o", type=str, required=True, help="Output file path")

    # Delete command
    del_parser = subparsers.add_parser("delete", help="Delete specific data")
    del_parser.add_argument("--symbol", "-s", type=str, help="Symbol to delete")
    del_parser.add_argument("--timeframe", "-t", type=str, help="Timeframe to delete")
    del_parser.add_argument("--before", type=str, help="Delete before date (YYYY-MM-DD)")
    del_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # Delete all command
    delall_parser = subparsers.add_parser("delete-all", help="Delete ALL data")
    delall_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    # Global options
    parser.add_argument(
        "--database-url",
        type=str,
        default="postgresql://trader:trader@localhost:5432/project103",
        help="Database connection URL",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    return parser.parse_args()


def format_duration(seconds: float) -> str:
    """Format duration in human readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


async def run_download(args):
    """Run download command."""
    # Validate date args
    if not args.days and not args.start:
        print("Error: Either --days or --start must be specified")
        sys.exit(1)

    if args.days and args.start:
        print("Error: Cannot specify both --days and --start")
        sys.exit(1)

    # Parse dates
    if args.days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()

    # Parse symbols
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    # Calculate expected
    candle_ms = HistoricalDataDownloader.TIMEFRAME_MS[args.timeframe]
    total_ms = int((end_date - start_date).total_seconds() * 1000)
    expected_per_symbol = total_ms // candle_ms
    total_expected = expected_per_symbol * len(symbols)

    print()
    print("=" * 60)
    print("  Historical Data Downloader")
    print("=" * 60)
    print(f"  Symbols:    {', '.join(symbols)}")
    print(f"  Timeframe:  {args.timeframe}")
    print(f"  Period:     {start_date.date()} to {end_date.date()}")
    print(f"  Expected:   ~{total_expected:,} candles total")
    print("=" * 60)

    downloader = HistoricalDataDownloader(
        database_url=args.database_url, testnet=getattr(args, "testnet", False)
    )

    print("\nConnecting...")
    await downloader.connect()
    print("Connected!\n")

    start_time = datetime.now()

    try:
        if len(symbols) == 1:
            await downloader.download(symbols[0], args.timeframe, start_date, end_date)
        else:
            await downloader.download_multiple(symbols, args.timeframe, start_date, end_date)

        elapsed = (datetime.now() - start_time).total_seconds()

        print("\n" + "=" * 60)
        print("  Download Complete!")
        print("=" * 60)
        print(f"  Total downloaded: {downloader.total_downloaded:,} candles")
        print(f"  Total saved:      {downloader.total_saved:,} candles")
        print(f"  Time elapsed:     {format_duration(elapsed)}")
        if downloader.total_downloaded > 0:
            rate = downloader.total_downloaded / elapsed
            print(f"  Download rate:    {rate:.0f} candles/sec")
        print("=" * 60)

    finally:
        await downloader.disconnect()


async def main():
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger("src.data.providers").setLevel(logging.WARNING)
        logging.getLogger("src.data.storage").setLevel(logging.WARNING)

    try:
        if args.command == "download":
            await run_download(args)

        elif args.command == "list":
            list_data(args.database_url)

        elif args.command == "stats":
            show_stats(args.database_url)

        elif args.command == "gaps":
            check_gaps(args.database_url, args.symbol, args.timeframe)

        elif args.command == "export":
            export_data(args.database_url, args.symbol, args.timeframe, args.output)

        elif args.command == "delete":
            delete_data(args.database_url, args.symbol, args.timeframe, args.before, args.yes)

        elif args.command == "delete-all":
            delete_all_data(args.database_url, args.yes)

        else:
            # No command - show help
            print("\nHistorical Data Manager")
            print("=" * 40)
            print("\nCommands:")
            print("  download    Download data from Bybit")
            print("  list        List available data")
            print("  stats       Show statistics")
            print("  gaps        Check for gaps")
            print("  export      Export to CSV")
            print("  delete      Delete specific data")
            print("  delete-all  Delete all data")
            print("\nUse --help for more info")
            print()

    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
