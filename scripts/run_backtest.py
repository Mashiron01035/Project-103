#!/usr/bin/env python3
"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : run_backtest.py
    Project     : Project-103
    Description : CLI tool for running backtests on historical data.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


from src.backtesting import (
    BacktestConfig,
    BacktestEngine,
    CommissionConfig,
    DataConfig,
    DataSource,
    ExecutionMode,
    RiskConfig,
    SlippageConfig,
    StrategyConfig,
)
from src.backtesting.models import LimitEntryBacktestConfig
from src.core.config import get_config

# Global session info
SESSION_ID = None
SESSION_LOG_PATH = None

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> str:
    """
    Setup logging with automatic file output.

    Returns:
        Path to the log file
    """
    global SESSION_ID, SESSION_LOG_PATH

    # Generate session ID
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    SESSION_ID = f"{timestamp}_{short_uuid}"

    # Create logs directory
    logs_dir = os.path.join(PROJECT_ROOT, "logs", "backtest")
    os.makedirs(logs_dir, exist_ok=True)

    # Log file path
    SESSION_LOG_PATH = os.path.join(logs_dir, f"session_{SESSION_ID}.log")
    # The engine writes a per-day rejection breakdown next to the log — the path is
    # passed via the environment so it does not have to go through the backtest config
    os.environ["BACKTEST_SESSION_LOG"] = SESSION_LOG_PATH

    # Create symlink to latest backtest log for convenience
    latest_link = os.path.join(logs_dir, "latest")
    try:
        if os.path.exists(latest_link) or os.path.islink(latest_link):
            os.unlink(latest_link)
        os.symlink(SESSION_LOG_PATH, latest_link)
    except (OSError, NotImplementedError):
        pass

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything

    # Clear existing handlers
    root_logger.handlers.clear()

    # File handler - logs everything
    file_handler = logging.FileHandler(SESSION_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Console handler - only warnings+ unless verbose
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if verbose else logging.WARNING)
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    return SESSION_LOG_PATH


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run backtest on historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic backtest with default EMA strategy
  python scripts/run_backtest.py --symbols BTCUSDT --timeframe 15m --days 30

  # Multiple symbols with custom parameters
  python scripts/run_backtest.py --symbols BTCUSDT,ETHUSDT --timeframe 1h --days 90 --balance 10000

  # Specific date range
  python scripts/run_backtest.py --symbols BTCUSDT --timeframe 4h --start 2024-01-01 --end 2024-06-01

  # Custom risk settings
  python scripts/run_backtest.py --symbols BTCUSDT --timeframe 15m --days 30 --risk 0.02 --max-position 0.2

  # Export results to JSON
  python scripts/run_backtest.py --symbols BTCUSDT --timeframe 1h --days 30 --output results.json
        """,
    )

    # Symbols and timeframe
    parser.add_argument(
        "--symbols",
        "-s",
        type=str,
        required=True,
        help="Comma-separated list of trading pairs (e.g., BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        "-t",
        type=str,
        default="15m",
        choices=["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
        help="Candle timeframe (default: 15m)",
    )

    parser.add_argument(
        "--execution-timeframe",
        type=str,
        default=None,
        choices=["1m", "2m", "3m", "5m"],
        help="Execution timeframe for SL/TP checks (e.g., 1m). Signals still on --timeframe. Default: same as --timeframe",
    )

    parser.add_argument(
        "--tick-execution",
        action="store_true",
        default=False,
        help="Use MT5 tick data for accurate SL/TP resolution when both are within candle range",
    )

    # Date range
    date_group = parser.add_argument_group("Date range")
    date_group.add_argument("--days", "-d", type=int, help="Number of days to backtest")
    date_group.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    date_group.add_argument("--end", type=str, help="End date (YYYY-MM-DD), defaults to today")

    # Capital and risk
    capital_group = parser.add_argument_group("Capital and risk")
    capital_group.add_argument(
        "--balance",
        "-b",
        type=float,
        default=10000,
        help="Initial balance in USDT (default: 10000)",
    )
    capital_group.add_argument(
        "--risk",
        type=float,
        default=None,
        help="Risk per trade as decimal (default: from config.yaml)",
    )
    capital_group.add_argument(
        "--max-position",
        type=float,
        default=None,
        help="Max position size as decimal (default: from config.yaml)",
    )
    capital_group.add_argument(
        "--max-drawdown", type=float, default=0.5, help="Max allowed drawdown (default: 0.2 = 20%%)"
    )
    capital_group.add_argument(
        "--leverage", type=float, default=10.0, help="Leverage multiplier (default: 10.0 = 10x)"
    )
    capital_group.add_argument(
        "--position-sizing",
        type=str,
        default=None,
        choices=["fixed_fractional", "fixed_balance", "balance_ratio"],
        help="Position sizing method: fixed_fractional (risk/SL), fixed_balance (pos=balance), balance_ratio ($100=0.01lot). Default: from config.yaml",
    )
    capital_group.add_argument(
        "--dynamic-risk",
        action="store_true",
        default=False,
        help="Enable dynamic risk: reduce position size during drawdowns (DD>10%%: 50%% risk, DD>25%%: 25%% risk)",
    )
    capital_group.add_argument(
        "--daily-profit-target",
        type=float,
        default=None,
        help="Stop trading for the day when daily P&L reaches this %% (e.g. 15.0)",
    )
    capital_group.add_argument(
        "--daily-target-mode",
        type=str,
        default="stop_new",
        choices=["stop_new", "close_all", "close_profitable"],
        help="What to do when daily target hit: stop_new (default), close_all, close_profitable",
    )
    capital_group.add_argument(
        "--balance-cap",
        type=float,
        default=0,
        help="Effective balance for position sizing. "
        ">=100: dollar cap (e.g. 10000 = trade as if max $10k balance). "
        "<100: percent of CURRENT balance (e.g. 66 = always size as 66%% of current, "
        "scales up/down with compound). 0 = disabled.",
    )
    capital_group.add_argument(
        "--vol-tilt",
        type=str,
        default=None,
        help="Scale position size by trailing 5-day average daily range (only prior "
        'days used). Format: "ref=80,min=0.5,max=1.0,mode=ratio". '
        "mode=ratio -> mult = clamp(range5/ref, min, max); "
        "mode=step -> mult = min below ref, max at or above. Default: off.",
    )

    # Commission and slippage (defaults from config/config.yaml)
    fees_group = parser.add_argument_group("Fees")
    fees_group.add_argument(
        "--maker-fee", type=float, default=None, help="Maker fee (default: from config.yaml)"
    )
    fees_group.add_argument(
        "--taker-fee", type=float, default=None, help="Taker fee (default: from config.yaml)"
    )
    fees_group.add_argument(
        "--slippage", type=float, default=None, help="Fixed slippage (default: from config.yaml)"
    )
    fees_group.add_argument(
        "--spread",
        type=float,
        default=0,
        help="Fixed spread in price units (e.g. 0.20 for $0.20 on XAUUSD). "
        "Applied as slippage on each entry and exit.",
    )
    fees_group.add_argument(
        "--commission-per-lot",
        type=float,
        default=None,
        help="Override per-lot commission in $ (for MT5 instruments, e.g. 7.0)",
    )

    # Strategy (placeholder for future)
    strategy_group = parser.add_argument_group("Strategy")
    strategy_group.add_argument(
        "--strategy",
        type=str,
        default="mean_reversion,low_vol_scalper,trend_following",
        help="Strategy name(s), comma-separated (default: mean_reversion,low_vol_scalper,trend_following). bb_grid_scalper available but experimental.",
    )
    strategy_group.add_argument(
        "--no-regime-switching",
        action="store_true",
        default=False,
        help="Disable regime-based strategy selection (run all strategies regardless of market regime)",
    )
    strategy_group.add_argument(
        "--no-limit-entry",
        action="store_true",
        default=False,
        help="Disable limit order entry simulation (use instant market fill)",
    )
    strategy_group.add_argument(
        "--indicator-window",
        type=int,
        default=0,
        help="Indicator calculation window size (0=full history, 500=match live). "
        "When set, indicators are computed on last N candles like in live trading.",
    )
    strategy_group.add_argument(
        "--live-hedge-semantics",
        action="store_true",
        default=False,
        help="Emulate the live per-strategy position gate (signal_lifecycle_manager "
        "under hedge_mode): only a SAME-side own position blocks a new entry, so "
        "one strategy can hold a long and a short at once. Default (off) keeps "
        "the backtest rule where any own position blocks, capped by max_positions.",
    )
    strategy_group.add_argument(
        "--fast-ema", type=int, default=20, help="Fast EMA period (default: 20)"
    )
    strategy_group.add_argument(
        "--slow-ema", type=int, default=50, help="Slow EMA period (default: 50)"
    )
    strategy_group.add_argument(
        "--regime-gate-strategies",
        type=str,
        default="",
        help="Comma-separated list of strategies to block when ADX > threshold "
        "(e.g. session_breakout,swing_fade)",
    )
    strategy_group.add_argument(
        "--regime-gate-adx-threshold",
        type=float,
        default=35.0,
        help="ADX threshold above which regime-gated strategies are blocked (default: 35.0)",
    )
    strategy_group.add_argument(
        "--regime-gate-adx-tf",
        type=str,
        default="1m",
        help="Timeframe for ADX calculation (default: 1m)",
    )
    strategy_group.add_argument(
        "--regime-gate-rules",
        type=str,
        default="",
        help="Per-strategy regime gate rules. Format: "
        '"name1:rule1[:tf];name2:rule2[:tf]". Rules: "min=X", "max=X", or "X-Y" (range). '
        'Example: "session_breakout:min=25:15m;swing_fade:max=25:15m". '
        "Overrides simple --regime-gate-strategies for listed strategies.",
    )
    strategy_group.add_argument(
        "--direction-filter",
        type=str,
        default="",
        help='Per-strategy EMA direction filter. Format: "name:cond[:tf:period];...". '
        'Conditions: "above" (price > EMA), "rising" (slope > 0), "above+rising" (both). '
        'Example: "session_breakout:above+rising:4h:50". Default tf=4h period=50.',
    )
    strategy_group.add_argument(
        "--param",
        type=str,
        action="append",
        default=[],
        help="Override any strategy parameter without editing the yaml. "
        'Format: "strategy.key=value", repeatable. '
        "Example: --param session_breakout.range_minutes=45 "
        "--param swing_fade.use_limit_entry=false",
    )
    strategy_group.add_argument(
        "--strategy-risk",
        type=str,
        default="",
        help="Override risk_per_trade per strategy without touching "
        'config/strategies.yaml. Format: "name:value,name:value", '
        'e.g. "session_breakout:0.01,swing_fade:0.005". Sizing A/B only.',
    )

    # Output
    output_group = parser.add_argument_group("Output")
    output_group.add_argument("--output", "-o", type=str, help="Output file for results (JSON)")
    output_group.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    output_group.add_argument(
        "--note",
        action="store_true",
        default=False,
        help="Generate Obsidian note from backtest results",
    )
    output_group.add_argument(
        "--vault",
        type=str,
        default=os.environ.get("TRADING_VAULT_PATH", "notes"),
        help="Notes directory for --note (default: $TRADING_VAULT_PATH or ./notes)",
    )

    # Data source
    parser.add_argument(
        "--data-source",
        type=str,
        default="database",
        choices=["database", "csv"],
        help="Data source: database or csv (default: database)",
    )

    # Database
    parser.add_argument(
        "--database-url",
        type=str,
        default="postgresql://trader:trader@localhost:5432/project103",
        help="Database connection URL",
    )

    return parser.parse_args()


def validate_args(args):
    """Validate command line arguments."""
    if not args.days and not args.start:
        raise ValueError("Either --days or --start must be specified")

    if args.days and args.start:
        raise ValueError("Cannot specify both --days and --start")

    # Parse dates
    if args.days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()

    if start_date >= end_date:
        raise ValueError("Start date must be before end date")

    # Parse symbols
    symbols = [s.strip() for s in args.symbols.split(",")]
    # Normalize: uppercase the base symbol but preserve suffix case (e.g. XAUUSD.s)
    normalized = []
    for s in symbols:
        if "." in s:
            base, suffix = s.rsplit(".", 1)
            normalized.append(f"{base.upper()}.{suffix}")
        else:
            normalized.append(s.upper())
    symbols = normalized

    return symbols, start_date, end_date


def parse_direction_filter(spec: str) -> Dict[str, Dict[str, object]]:
    """Parse "name:cond[:tf:period];..." into {name: {tf, period, require_above, require_slope_positive}}.

    Conditions: "above", "rising", "above+rising".
    """
    if not spec:
        return {}
    out: Dict[str, Dict[str, object]] = {}
    for segment in spec.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split(":")
        if len(parts) < 2:
            raise ValueError(f"Bad direction-filter segment: {segment!r}")
        name = parts[0].strip()
        cond = parts[1].strip()
        tf = parts[2].strip() if len(parts) >= 3 else "4h"
        period = int(parts[3]) if len(parts) >= 4 else 50
        rule = {
            "tf": tf,
            "period": period,
            "require_above": "above" in cond,
            "require_slope_positive": "rising" in cond,
        }
        out[name] = rule
    return out


def parse_regime_gate_rules(spec: str) -> Dict[str, Dict[str, object]]:
    """Parse "name:rule[:tf];..." into {name: {min, max, tf}}.

    Rule examples: "25-35", "min=25", "max=35", "25-50". TF defaults to "1m".
    """
    if not spec:
        return {}
    rules: Dict[str, Dict[str, object]] = {}
    for segment in spec.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split(":")
        if len(parts) < 2:
            raise ValueError(f"Bad regime-gate-rules segment: {segment!r}")
        name = parts[0].strip()
        rule_str = parts[1].strip()
        tf = parts[2].strip() if len(parts) >= 3 else "1m"
        rule: Dict[str, object] = {"tf": tf}
        if rule_str.startswith("min="):
            rule["min"] = float(rule_str[4:])
        elif rule_str.startswith("max="):
            rule["max"] = float(rule_str[4:])
        elif "-" in rule_str:
            lo, hi = rule_str.split("-", 1)
            rule["min"] = float(lo)
            rule["max"] = float(hi)
        else:
            raise ValueError(
                f"Bad rule {rule_str!r} for {name}: expected 'min=X', 'max=X', or 'X-Y'"
            )
        rules[name] = rule
    return rules


def format_duration(td: timedelta) -> str:
    """Format timedelta to human readable string."""
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


def _sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_backtest_inputs(
    symbols: List[str],
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    csv_dir: str = "./data",
    tick_cache_dir: str = "./cache/ticks",
    include_ticks: bool = True,
) -> Dict[str, Dict[str, str]]:
    """
    Hash candle CSV and tick parquet files used by the backtest so identical
    runs can be detected by data fingerprint.

    Returns: {symbol: {"candles_sha256": ..., "candles_size": ...,
                       "ticks_sha256": ..., "ticks_files": ...}}
    """
    result: Dict[str, Dict[str, str]] = {}
    for symbol in symbols:
        entry: Dict[str, str] = {}

        candles_path = os.path.join(csv_dir, f"{symbol}_{timeframe}.csv")
        if os.path.exists(candles_path):
            entry["candles_path"] = candles_path
            entry["candles_size"] = str(os.path.getsize(candles_path))
            entry["candles_sha256"] = _sha256_file(candles_path)
        else:
            entry["candles_path"] = candles_path
            entry["candles_sha256"] = "<missing>"

        if include_ticks:
            symbol_dir = os.path.join(tick_cache_dir, symbol.replace(".", "_"))
            tick_files = []
            if os.path.isdir(symbol_dir):
                current = start_date.date()
                end = end_date.date()
                while current <= end:
                    pq = os.path.join(symbol_dir, f"{current}.parquet")
                    if os.path.exists(pq):
                        tick_files.append(pq)
                    current += timedelta(days=1)

            if tick_files:
                agg = hashlib.sha256()
                total_size = 0
                for pq in tick_files:
                    agg.update(_sha256_file(pq).encode())
                    total_size += os.path.getsize(pq)
                entry["ticks_dir"] = symbol_dir
                entry["ticks_files"] = str(len(tick_files))
                entry["ticks_total_size"] = str(total_size)
                entry["ticks_sha256"] = agg.hexdigest()
            else:
                entry["ticks_dir"] = symbol_dir
                entry["ticks_sha256"] = "<missing>"

        result[symbol] = entry
    return result


def _log_data_fingerprint(
    symbols: List[str],
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    include_ticks: bool,
) -> None:
    try:
        prints = hash_backtest_inputs(
            symbols=symbols,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            include_ticks=include_ticks,
        )
    except Exception as e:
        logger.warning(f"Failed to compute data fingerprint: {e}")
        return

    print()
    print("=" * 70)
    print("  DATA FINGERPRINT (SHA256)")
    print("=" * 70)
    logger.info("=" * 70)
    logger.info("DATA FINGERPRINT (SHA256)")
    logger.info("=" * 70)
    for symbol, info in prints.items():
        candles_hash = info.get("candles_sha256", "?")
        candles_short = candles_hash[:16] if candles_hash != "<missing>" else candles_hash
        candles_size = info.get("candles_size", "?")
        msg_c = f"  {symbol} candles ({timeframe}): {candles_short}  size={candles_size}B  path={info.get('candles_path')}"
        print(msg_c)
        logger.info(msg_c)
        if include_ticks:
            ticks_hash = info.get("ticks_sha256", "?")
            ticks_short = ticks_hash[:16] if ticks_hash != "<missing>" else ticks_hash
            ticks_files = info.get("ticks_files", "0")
            ticks_size = info.get("ticks_total_size", "?")
            msg_t = f"  {symbol} ticks: {ticks_short}  files={ticks_files}  total={ticks_size}B  dir={info.get('ticks_dir')}"
            print(msg_t)
            logger.info(msg_t)
    print("=" * 70)
    logger.info("=" * 70)


def _print_weekly_stats(trades):
    """Print weekly PnL statistics."""
    from collections import defaultdict

    # Group trades by week
    weekly_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        # Get ISO week (year, week_number)
        if hasattr(trade, "exit_time") and trade.exit_time:
            exit_date = trade.exit_time
        elif hasattr(trade, "exit_timestamp") and trade.exit_timestamp:
            exit_date = trade.exit_timestamp
        else:
            continue

        year, week, _ = exit_date.isocalendar()
        week_key = f"{year}-W{week:02d}"

        weekly_pnl[week_key]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        weekly_pnl[week_key]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            weekly_pnl[week_key]["wins"] += 1

    # Sort by week
    sorted_weeks = sorted(weekly_pnl.keys())

    # Print header
    print(f"  {'Week':<10} {'PnL':>12} {'Trades':>8} {'WR':>8}")
    print(f"  {'-'*10} {'-'*12} {'-'*8} {'-'*8}")

    total_pnl = 0.0
    for week in sorted_weeks:
        data = weekly_pnl[week]
        pnl = data["pnl"]
        total_pnl += pnl
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0

        # Color coding
        pnl_str = f"${pnl:+,.2f}"
        color = "\033[92m" if pnl >= 0 else "\033[91m"
        reset = "\033[0m"

        print(f"  {week:<10} {color}{pnl_str:>12}{reset} {trades_count:>8} {win_rate:>7.1f}%")

    # Total
    print(f"  {'-'*10} {'-'*12} {'-'*8} {'-'*8}")
    color = "\033[92m" if total_pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    print(f"  {'TOTAL':<10} {color}${total_pnl:+,.2f}{reset:>12}")


def _log_weekly_stats(trades):
    """Log weekly PnL statistics to file."""
    from collections import defaultdict

    weekly_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        if hasattr(trade, "exit_time") and trade.exit_time:
            exit_date = trade.exit_time
        elif hasattr(trade, "exit_timestamp") and trade.exit_timestamp:
            exit_date = trade.exit_timestamp
        else:
            continue

        year, week, _ = exit_date.isocalendar()
        week_key = f"{year}-W{week:02d}"

        weekly_pnl[week_key]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        weekly_pnl[week_key]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            weekly_pnl[week_key]["wins"] += 1

    sorted_weeks = sorted(weekly_pnl.keys())

    for week in sorted_weeks:
        data = weekly_pnl[week]
        pnl = data["pnl"]
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        logger.info(f"  {week}: ${pnl:+,.2f} | {trades_count} trades | WR: {win_rate:.1f}%")


def _print_daily_stats(trades):
    """Print daily PnL statistics."""
    from collections import defaultdict

    daily_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        if hasattr(trade, "exit_time") and trade.exit_time:
            exit_date = trade.exit_time
        elif hasattr(trade, "exit_timestamp") and trade.exit_timestamp:
            exit_date = trade.exit_timestamp
        else:
            continue

        day_key = exit_date.strftime("%Y-%m-%d")

        daily_pnl[day_key]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        daily_pnl[day_key]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            daily_pnl[day_key]["wins"] += 1

    sorted_days = sorted(daily_pnl.keys())

    print(f"  {'Date':<12} {'PnL':>12} {'Trades':>8} {'WR':>8}")
    print(f"  {'-'*12} {'-'*12} {'-'*8} {'-'*8}")

    total_pnl = 0.0
    for day in sorted_days:
        data = daily_pnl[day]
        pnl = data["pnl"]
        total_pnl += pnl
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0

        color = "\033[92m" if pnl >= 0 else "\033[91m"
        reset = "\033[0m"

        print(f"  {day:<12} {color}${pnl:+,.2f}{reset:>12} {trades_count:>8} {win_rate:>7.1f}%")

    print(f"  {'-'*12} {'-'*12} {'-'*8} {'-'*8}")
    color = "\033[92m" if total_pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    print(f"  {'TOTAL':<12} {color}${total_pnl:+,.2f}{reset:>12}")


def _log_daily_stats(trades):
    """Log daily PnL statistics to file."""
    from collections import defaultdict

    daily_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        if hasattr(trade, "exit_time") and trade.exit_time:
            exit_date = trade.exit_time
        elif hasattr(trade, "exit_timestamp") and trade.exit_timestamp:
            exit_date = trade.exit_timestamp
        else:
            continue

        day_key = exit_date.strftime("%Y-%m-%d")

        daily_pnl[day_key]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        daily_pnl[day_key]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            daily_pnl[day_key]["wins"] += 1

    sorted_days = sorted(daily_pnl.keys())

    for day in sorted_days:
        data = daily_pnl[day]
        pnl = data["pnl"]
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        logger.info(f"  {day}: ${pnl:+,.2f} | {trades_count} trades | WR: {win_rate:.1f}%")


def _get_session_name(hour: int) -> str:
    """Get trading session name based on UTC hour."""
    if 0 <= hour < 8:
        return "Asian"
    elif 8 <= hour < 16:
        return "European"
    else:
        return "US"


def _calculate_entry_stats(trades):
    """
    Calculate entry-based statistics (aggregating partial closes).

    Returns dict with entry-based metrics.
    """
    from collections import defaultdict

    # Group trades by unique entry (symbol + side + entry_time)
    entries = defaultdict(float)

    for trade in trades:
        if hasattr(trade, "entry_time") and trade.entry_time:
            entry_time = trade.entry_time.strftime("%Y-%m-%d %H:%M")
        else:
            continue

        symbol = trade.symbol
        side = trade.side
        key = f"{symbol}_{side}_{entry_time}"

        pnl = float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        entries[key] += pnl

    if not entries:
        return None

    total_entries = len(entries)
    winning_entries = sum(1 for pnl in entries.values() if pnl > 0)
    losing_entries = sum(1 for pnl in entries.values() if pnl <= 0)
    entry_wr = winning_entries / total_entries * 100 if total_entries > 0 else 0

    total_pnl = sum(entries.values())
    avg_win = sum(p for p in entries.values() if p > 0) / winning_entries if winning_entries else 0
    avg_loss = sum(p for p in entries.values() if p <= 0) / losing_entries if losing_entries else 0

    gross_profit = sum(p for p in entries.values() if p > 0)
    gross_loss = abs(sum(p for p in entries.values() if p <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total_entries": total_entries,
        "winning_entries": winning_entries,
        "losing_entries": losing_entries,
        "entry_wr": entry_wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_pnl": total_pnl / total_entries if total_entries > 0 else 0,
        "profit_factor": profit_factor,
        "trades_per_entry": len(trades) / total_entries if total_entries > 0 else 0,
    }


def _print_entry_stats(trades, trade_wr: float):
    """Print entry-based statistics to console."""
    stats = _calculate_entry_stats(trades)
    if not stats:
        return

    print(f"  {'Metric':<20} {'Per-Trade':>15} {'Per-Entry':>15}")
    print(f"  {'-'*20} {'-'*15} {'-'*15}")
    print(f"  {'Count':<20} {len(trades):>15} {stats['total_entries']:>15}")
    print(f"  {'Winning':<20} {'-':>15} {stats['winning_entries']:>15}")
    print(f"  {'Losing':<20} {'-':>15} {stats['losing_entries']:>15}")
    print(f"  {'Win Rate':<20} {trade_wr:>14.1f}% {stats['entry_wr']:>14.1f}%")
    print(f"  {'Avg Win':<20} {'-':>15} ${stats['avg_win']:>13,.2f}")
    print(f"  {'Avg Loss':<20} {'-':>15} ${stats['avg_loss']:>13,.2f}")
    print(f"  {'Avg PnL/Entry':<20} {'-':>15} ${stats['avg_pnl']:>13,.2f}")
    print(f"  {'Profit Factor':<20} {'-':>15} {stats['profit_factor']:>15.2f}")
    print(f"  {'-'*20} {'-'*15} {'-'*15}")
    print(f"  {'Trades per Entry':<20} {stats['trades_per_entry']:>15.2f}")


def _log_entry_stats(trades, trade_wr: float):
    """Log entry-based statistics to file."""
    stats = _calculate_entry_stats(trades)
    if not stats:
        return

    logger.info(f"  Per-Trade: {len(trades)} trades, WR: {trade_wr:.1f}%")
    logger.info(f"  Per-Entry: {stats['total_entries']} entries, WR: {stats['entry_wr']:.1f}%")
    logger.info(f"  Winning entries: {stats['winning_entries']}, Losing: {stats['losing_entries']}")
    logger.info(f"  Avg PnL per entry: ${stats['avg_pnl']:,.2f}")
    logger.info(f"  Avg trades per entry: {stats['trades_per_entry']:.2f}")


def _print_session_stats(trades):
    """Print session-based PnL statistics."""
    from collections import defaultdict

    session_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        # Get entry time for session classification
        if hasattr(trade, "entry_time") and trade.entry_time:
            entry_time = trade.entry_time
        elif hasattr(trade, "entry_timestamp") and trade.entry_timestamp:
            entry_time = trade.entry_timestamp
        else:
            continue

        session = _get_session_name(entry_time.hour)

        session_pnl[session]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        session_pnl[session]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            session_pnl[session]["wins"] += 1

    # Print header
    print(f"  {'Session':<12} {'Hours (UTC)':<14} {'PnL':>12} {'Trades':>8} {'WR':>8} {'Avg':>10}")
    print(f"  {'-'*12} {'-'*14} {'-'*12} {'-'*8} {'-'*8} {'-'*10}")

    session_order = ["Asian", "European", "US"]
    session_hours = {"Asian": "00:00-08:00", "European": "08:00-16:00", "US": "16:00-24:00"}

    total_pnl = 0.0
    for session in session_order:
        data = session_pnl[session]
        pnl = data["pnl"]
        total_pnl += pnl
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        avg_pnl = pnl / trades_count if trades_count > 0 else 0

        pnl_str = f"${pnl:+,.2f}"
        avg_str = f"${avg_pnl:+,.2f}"
        color = "\033[92m" if pnl >= 0 else "\033[91m"
        reset = "\033[0m"

        print(
            f"  {session:<12} {session_hours[session]:<14} {color}{pnl_str:>12}{reset} {trades_count:>8} {win_rate:>7.1f}% {avg_str:>10}"
        )

    print(f"  {'-'*12} {'-'*14} {'-'*12} {'-'*8} {'-'*8} {'-'*10}")
    color = "\033[92m" if total_pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    print(f"  {'TOTAL':<12} {'':<14} {color}${total_pnl:+,.2f}{reset:>12}")


def _log_session_stats(trades):
    """Log session-based PnL statistics to file."""
    from collections import defaultdict

    session_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        if hasattr(trade, "entry_time") and trade.entry_time:
            entry_time = trade.entry_time
        elif hasattr(trade, "entry_timestamp") and trade.entry_timestamp:
            entry_time = trade.entry_timestamp
        else:
            continue

        session = _get_session_name(entry_time.hour)

        session_pnl[session]["pnl"] += (
            float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        )
        session_pnl[session]["trades"] += 1
        if (hasattr(trade, "net_pnl") and trade.net_pnl > 0) or (
            hasattr(trade, "pnl") and trade.pnl > 0
        ):
            session_pnl[session]["wins"] += 1

    session_order = ["Asian", "European", "US"]
    session_hours = {"Asian": "00:00-08:00", "European": "08:00-16:00", "US": "16:00-24:00"}

    for session in session_order:
        data = session_pnl[session]
        pnl = data["pnl"]
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        avg_pnl = pnl / trades_count if trades_count > 0 else 0
        logger.info(
            f"  {session} ({session_hours[session]}): ${pnl:+,.2f} | {trades_count} trades | WR: {win_rate:.1f}% | Avg: ${avg_pnl:+,.2f}"
        )


def _print_regime_stats(trades):
    """Print regime-based PnL statistics."""
    from collections import defaultdict

    regime_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        # Get regime from trade metadata
        regime = "unknown"
        if hasattr(trade, "metadata") and trade.metadata:
            regime = trade.metadata.get("regime", "unknown")

        pnl = float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        regime_pnl[regime]["pnl"] += pnl
        regime_pnl[regime]["trades"] += 1
        if pnl > 0:
            regime_pnl[regime]["wins"] += 1

    if len(regime_pnl) <= 1 and "unknown" in regime_pnl:
        print("  Regime stats not available (regime switching disabled or no metadata)")
        return

    print(f"\n  {'Regime':<20} {'PnL':>14} {'Trades':>8} {'WR':>8} {'Avg PnL':>12}")
    print(f"  {'-'*20} {'-'*14} {'-'*8} {'-'*8} {'-'*12}")

    total_pnl = 0
    regime_order = [
        "ranging",
        "consolidation",
        "low_volatility",
        "trending_up",
        "trending_down",
        "high_volatility",
        "breakout",
        "unknown",
    ]

    for regime in regime_order:
        if regime not in regime_pnl:
            continue
        data = regime_pnl[regime]
        pnl = data["pnl"]
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        avg_pnl = pnl / trades_count if trades_count > 0 else 0
        total_pnl += pnl

        color = "\033[92m" if pnl >= 0 else "\033[91m"
        reset = "\033[0m"
        print(
            f"  {regime:<20} {color}${pnl:+,.2f}{reset:>14} {trades_count:>8} {win_rate:>7.1f}% ${avg_pnl:>+10,.2f}"
        )

    print(f"  {'-'*20} {'-'*14} {'-'*8} {'-'*8} {'-'*12}")
    color = "\033[92m" if total_pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    print(f"  {'TOTAL':<20} {color}${total_pnl:+,.2f}{reset:>14}")


def _log_regime_stats(trades):
    """Log regime-based PnL statistics to file."""
    from collections import defaultdict

    regime_pnl = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for trade in trades:
        regime = "unknown"
        if hasattr(trade, "metadata") and trade.metadata:
            regime = trade.metadata.get("regime", "unknown")

        pnl = float(trade.net_pnl) if hasattr(trade, "net_pnl") else float(trade.pnl)
        regime_pnl[regime]["pnl"] += pnl
        regime_pnl[regime]["trades"] += 1
        if pnl > 0:
            regime_pnl[regime]["wins"] += 1

    if len(regime_pnl) <= 1 and "unknown" in regime_pnl:
        logger.info("  Regime stats not available (regime switching disabled)")
        return

    regime_order = [
        "ranging",
        "consolidation",
        "low_volatility",
        "trending_up",
        "trending_down",
        "high_volatility",
        "breakout",
        "unknown",
    ]

    for regime in regime_order:
        if regime not in regime_pnl:
            continue
        data = regime_pnl[regime]
        pnl = data["pnl"]
        trades_count = data["trades"]
        win_rate = (data["wins"] / trades_count * 100) if trades_count > 0 else 0
        avg_pnl = pnl / trades_count if trades_count > 0 else 0
        logger.info(
            f"  {regime}: ${pnl:+,.2f} | {trades_count} trades | WR: {win_rate:.1f}% | Avg: ${avg_pnl:+,.2f}"
        )


def _log_results_to_file(result):
    """Log detailed results to the session log file."""
    logger.info("=" * 70)
    logger.info("BACKTEST RESULTS SUMMARY")
    logger.info("=" * 70)

    # Summary
    logger.info(
        f"Period: {result.start_date.date()} to {result.end_date.date()} ({result.duration.days} days)"
    )
    logger.info(f"Initial Balance: ${float(result.initial_balance):,.2f}")
    logger.info(f"Final Balance: ${float(result.final_balance):,.2f}")
    logger.info(
        f"Total Return: ${float(result.total_return):+,.2f} ({result.total_return_pct:+.2f}%)"
    )
    logger.info(f"Annualized Return: {result.annualized_return:+.2f}%")

    # Trades (per-trade)
    logger.info("-" * 70)
    logger.info(f"Total Trades: {result.total_trades}")
    logger.info(f"Winning: {result.winning_trades} ({result.win_rate:.1f}%)")
    logger.info(f"Losing: {result.losing_trades}")
    logger.info(f"Avg Win: ${result.avg_win:,.2f}")
    logger.info(f"Avg Loss: ${result.avg_loss:,.2f}")
    logger.info(f"Profit Factor: {result.profit_factor:.2f}")
    logger.info(f"Expectancy: ${result.expectancy:,.2f}")

    # Entry-based stats
    if result.trades:
        logger.info("-" * 70)
        logger.info("ENTRY-BASED STATS:")
        _log_entry_stats(result.trades, result.win_rate)

    # Risk
    logger.info("-" * 70)
    logger.info(f"Max Drawdown: ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")
    # see the comment in print_results: the line is only needed when the percentages
    # actually differ, otherwise it is a duplicate
    _true_dd_log = result.drawdown_info.max_drawdown_pct_true
    if abs(_true_dd_log - result.max_drawdown_pct) > 0.01:
        logger.info(
            f"Max Drawdown (true %): {_true_dd_log:.2f}%" f"  <- use this one to compare runs"
        )
    logger.info(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
    logger.info(f"Sortino Ratio: {result.sortino_ratio:.2f}")

    # Costs
    logger.info(f"Total Commission: ${result.total_commission:,.2f}")
    logger.info(f"Total Slippage: ${result.total_slippage:,.2f}")

    # Strategy breakdown
    if result.strategy_performance:
        logger.info("-" * 70)
        logger.info("STRATEGY PERFORMANCE:")
        for name, metrics in result.strategy_performance.items():
            logger.info(
                f"  {name}: {metrics.total_trades} trades, "
                f"WR: {metrics.win_rate*100:.1f}%, PnL: ${metrics.total_pnl:,.2f}"
            )

    # Symbol breakdown
    if result.symbol_performance:
        logger.info("-" * 70)
        logger.info("SYMBOL PERFORMANCE:")
        for symbol, metrics in result.symbol_performance.items():
            logger.info(
                f"  {symbol}: {metrics.total_trades} trades, "
                f"WR: {metrics.win_rate*100:.1f}%, PnL: ${metrics.total_pnl:,.2f}"
            )

    # Weekly breakdown
    if result.trades:
        logger.info("-" * 70)
        logger.info("WEEKLY PERFORMANCE:")
        _log_weekly_stats(result.trades)

    # Daily breakdown
    if result.trades:
        logger.info("-" * 70)
        logger.info("DAILY PERFORMANCE:")
        _log_daily_stats(result.trades)

    # Session breakdown
    if result.trades:
        logger.info("-" * 70)
        logger.info("SESSION PERFORMANCE (by entry time UTC):")
        _log_session_stats(result.trades)

    # Regime breakdown
    if result.trades:
        logger.info("-" * 70)
        logger.info("REGIME PERFORMANCE (per-symbol market regime):")
        _log_regime_stats(result.trades)

    # Log all trades
    if result.trades:
        logger.info("=" * 70)
        logger.info("TRADE DETAILS:")
        logger.info("=" * 70)
        for i, trade in enumerate(result.trades, 1):
            logger.info(
                f"#{i} {trade.symbol} {trade.side} | "
                f"Entry: {trade.entry_time.strftime('%Y-%m-%d %H:%M')} @ ${trade.entry_price:.4f} | "
                f"Exit: {trade.exit_time.strftime('%Y-%m-%d %H:%M')} @ ${trade.exit_price:.4f} | "
                f"PnL: ${trade.net_pnl:+.2f} ({trade.pnl_pct:+.2f}%) | "
                f"Reason: {trade.exit_reason}"
            )

    logger.info("=" * 70)


def print_results(result):
    """Print backtest results to console and log to file."""
    # Build results text for both console and log
    lines = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("  BACKTEST RESULTS")
    lines.append("=" * 70)
    lines.append("")

    # Summary
    lines.append("  SUMMARY")
    lines.append("-" * 70)
    lines.append(f"  Period:           {result.start_date.date()} to {result.end_date.date()}")
    lines.append(f"  Duration:         {result.duration.days} days")
    lines.append(f"  Initial Balance:  ${float(result.initial_balance):,.2f}")
    lines.append(f"  Final Balance:    ${float(result.final_balance):,.2f}")
    lines.append("")

    # Returns
    lines.append("  RETURNS")
    lines.append("-" * 70)
    total_return = float(result.total_return)
    total_return_pct = result.total_return_pct

    lines.append(f"  Total Return:     ${total_return:+,.2f} ({total_return_pct:+.2f}%)")
    lines.append(f"  Annualized:       {result.annualized_return:+.2f}%")
    lines.append("")

    # Print to console with colors
    print()
    print("=" * 70)
    print("  BACKTEST RESULTS")
    print("=" * 70)
    print()
    print("  SUMMARY")
    print("-" * 70)
    print(f"  Period:           {result.start_date.date()} to {result.end_date.date()}")
    print(f"  Duration:         {result.duration.days} days")
    print(f"  Initial Balance:  ${float(result.initial_balance):,.2f}")
    print(f"  Final Balance:    ${float(result.final_balance):,.2f}")
    print()
    print("  RETURNS")
    print("-" * 70)
    color = "\033[92m" if total_return >= 0 else "\033[91m"  # Green or Red
    reset = "\033[0m"
    print(f"  Total Return:     {color}${total_return:+,.2f} ({total_return_pct:+.2f}%){reset}")
    print(f"  Annualized:       {result.annualized_return:+.2f}%")
    print()

    # Trades
    print("  TRADES (Per-Trade)")
    print("-" * 70)
    print(f"  Total Trades:     {result.total_trades}")
    print(f"  Winning:          {result.winning_trades} ({result.win_rate:.1f}%)")
    print(f"  Losing:           {result.losing_trades}")
    print(f"  Avg Win:          ${result.avg_win:,.2f}")
    print(f"  Avg Loss:         ${result.avg_loss:,.2f}")
    print(f"  Profit Factor:    {result.profit_factor:.2f}")
    print(f"  Expectancy:       ${result.expectancy:,.2f}")
    print()

    # Entry-based stats (aggregating partial closes)
    if result.trades:
        print("  ENTRY-BASED STATS (aggregated partial closes)")
        print("-" * 70)
        _print_entry_stats(result.trades, result.win_rate)
        print()

    # Risk metrics
    print("  RISK METRICS")
    print("-" * 70)
    print(f"  Max Drawdown:     ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")
    # The percentage in the line above is the share of equity AT THE MOMENT of the
    # maximum DOLLAR drawdown. Usually the worst dip in dollars is also the worst in
    # percent, the numbers match and there is no point printing the same thing twice.
    # They differ when the largest dip happened on large equity and the relatively
    # deepest one on small equity (e.g. 20.06% vs 16.35%).
    _true_dd = result.drawdown_info.max_drawdown_pct_true
    if abs(_true_dd - result.max_drawdown_pct) > 0.01:
        print(
            f"  Max DD (true %):  {_true_dd:.2f}%"
            f"   <- compare runs by this one, not by the line above"
        )
    _eps = result.drawdown_info.episodes
    if _eps:
        _deep = sum(1 for e in _eps if e["depth_pct"] >= 5.0)
        print(f"  Drawdowns:        {len(_eps)} (deeper than 5%: {_deep})")
    print(f"  Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio:    {result.sortino_ratio:.2f}")
    print(f"  Calmar Ratio:     {result.calmar_ratio:.2f}")
    print(f"  VaR (95%):        {result.var_95:.2f}%")
    print(f"  CVaR (95%):       {result.cvar_95:.2f}%")
    print()

    # Costs
    print("  COSTS")
    print("-" * 70)
    print(f"  Total Commission: ${result.total_commission:,.2f}")
    print(f"  Total Slippage:   ${result.total_slippage:,.2f}")
    print()

    # Strategy breakdown
    if result.strategy_performance:
        print("  STRATEGY PERFORMANCE")
        print("-" * 70)
        for name, metrics in result.strategy_performance.items():
            print(f"  {name}:")
            print(
                f"    Trades: {metrics.total_trades}, Win Rate: {metrics.win_rate*100:.1f}%, PnL: ${metrics.total_pnl:,.2f}"
            )
        print()

    # Symbol breakdown
    if result.symbol_performance:
        print("  SYMBOL PERFORMANCE")
        print("-" * 70)
        for symbol, metrics in result.symbol_performance.items():
            print(f"  {symbol}:")
            print(
                f"    Trades: {metrics.total_trades}, Win Rate: {metrics.win_rate*100:.1f}%, PnL: ${metrics.total_pnl:,.2f}"
            )
        print()

    # Weekly breakdown
    if result.trades:
        print("  WEEKLY PERFORMANCE")
        print("-" * 70)
        _print_weekly_stats(result.trades)
        print()

    # Daily breakdown
    if result.trades:
        print("  DAILY PERFORMANCE")
        print("-" * 70)
        _print_daily_stats(result.trades)
        print()

    # Session breakdown
    if result.trades:
        print("  SESSION PERFORMANCE (by entry time UTC)")
        print("-" * 70)
        _print_session_stats(result.trades)
        print()

    # Regime performance
    if result.trades:
        print("  REGIME PERFORMANCE (per-symbol market regime)")
        print("-" * 70)
        _print_regime_stats(result.trades)
        print()

    print("=" * 70)

    # Log complete results to file
    _log_results_to_file(result)


def export_results(result, output_path: str, args=None):
    """Export results to JSON file."""
    # Extract config metadata for richer standalone notes
    config = result.config
    timeframes = []
    symbols = []
    leverage = 1.0
    strategy_names = []
    if config:
        timeframes = config.timeframes or []
        symbols = config.symbols or []
        if config.risk_config:
            leverage = config.risk_config.leverage
        strategy_names = [s.name for s in config.strategies] if config.strategies else []

    # Convert to serializable format
    data = {
        "summary": {
            "start_date": result.start_date.isoformat(),
            "end_date": result.end_date.isoformat(),
            "duration_days": result.duration.days,
            "initial_balance": float(result.initial_balance),
            "final_balance": float(result.final_balance),
        },
        "config": {
            "timeframes": timeframes,
            "symbols": symbols,
            "leverage": leverage,
            "strategy_names": strategy_names,
        },
        "returns": {
            "total_return": float(result.total_return),
            "total_return_pct": result.total_return_pct,
            "annualized_return": result.annualized_return,
        },
        "trades": {
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate": result.win_rate,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "profit_factor": result.profit_factor,
            "expectancy": result.expectancy,
        },
        "risk": {
            "max_drawdown": result.max_drawdown,
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "sortino_ratio": result.sortino_ratio,
            "calmar_ratio": result.calmar_ratio,
            "var_95": result.var_95,
            "cvar_95": result.cvar_95,
        },
        "costs": {
            "total_commission": result.total_commission,
            "total_slippage": result.total_slippage,
        },
        "strategy_performance": {
            name: {
                "total_trades": m.total_trades,
                "winning_trades": m.winning_trades,
                "losing_trades": m.losing_trades,
                "win_rate": m.win_rate,
                "profit_factor": m.profit_factor,
                "total_pnl": m.total_pnl,
            }
            for name, m in (result.strategy_performance or {}).items()
        },
        "symbol_performance": {
            symbol: {
                "total_trades": m.total_trades,
                "winning_trades": m.winning_trades,
                "losing_trades": m.losing_trades,
                "win_rate": m.win_rate,
                "total_pnl": m.total_pnl,
                "total_volume": m.total_volume,
            }
            for symbol, m in (result.symbol_performance or {}).items()
        },
        "trades_list": [
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_time": t.entry_time.isoformat(),
                "entry_price": t.entry_price,
                "exit_time": t.exit_time.isoformat(),
                "exit_price": t.exit_price,
                "quantity": t.entry_quantity,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "net_pnl": t.net_pnl,
                "exit_reason": t.exit_reason,
            }
            for t in result.trades
        ],
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nResults exported to {output_path}")


def export_trades_csv(result, output_path: str):
    """Export all trades to CSV for manual verification."""
    import csv

    if not result.trades:
        print("No trades to export")
        return

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")

        # Header
        writer.writerow(
            [
                "#",
                "Symbol",
                "Side",
                "Entry_Time",
                "Exit_Time",
                "Entry_Price",
                "Exit_Price",
                "Quantity",
                "Notional",
                "SL_Price",
                "SL_Distance_%",
                "TP1_Price",
                "PnL_$",
                "PnL_%",
                "Commission",
                "Slippage",
                "Net_PnL_$",
                "Exit_Reason",
                "Hold_Time_Min",
                "Balance_After",
                "Max_Profit_%",
                "Batch_ID",
                "Strategy",
                "Status",
                "Unrealized_$",
            ]
        )

        # Running balance to verify compounding
        balance = float(result.initial_balance)

        def n(value, decimals=2):
            """Format number with comma as decimal separator for RU locale."""
            return f"{value:.{decimals}f}".replace(".", ",")

        for i, t in enumerate(result.trades, 1):
            metadata = t.metadata or {}
            sl_price = metadata.get("stop_loss", "")
            tp_list = metadata.get("take_profits") or []
            tp1 = tp_list[0] if tp_list else metadata.get("take_profit", "")

            # SL distance %
            sl_dist = ""
            if sl_price and t.entry_price:
                sl_dist = n(abs(t.entry_price - sl_price) / t.entry_price * 100, 3)

            cs = (t.metadata or {}).get("contract_size", 1.0)
            notional = t.entry_price * t.entry_quantity * cs
            total_commission = t.entry_commission + t.exit_commission
            hold_minutes = t.hold_time.total_seconds() / 60 if t.hold_time else 0

            balance += t.net_pnl

            writer.writerow(
                [
                    i,
                    t.symbol,
                    t.side,
                    t.entry_time.strftime("%Y-%m-%d %H:%M") if t.entry_time else "",
                    t.exit_time.strftime("%Y-%m-%d %H:%M") if t.exit_time else "",
                    n(t.entry_price, 6),
                    n(t.exit_price, 6),
                    n(t.entry_quantity, 6),
                    n(notional, 2),
                    n(sl_price, 6) if isinstance(sl_price, (int, float)) else "",
                    sl_dist,
                    n(tp1, 6) if isinstance(tp1, (int, float)) else "",
                    n(t.pnl, 2),
                    n(t.pnl_pct, 3),
                    n(total_commission, 2),
                    n(t.slippage, 2),
                    n(t.net_pnl, 2),
                    t.exit_reason,
                    f"{hold_minutes:.0f}",
                    n(balance, 2),
                    n(t.max_favorable_excursion, 4) if t.max_favorable_excursion else "0",
                    metadata.get("fan_batch_id", ""),
                    t.strategy_name or "",
                    "closed",
                    "",
                ]
            )

        # Positions open at the end of the window — AFTER all completed ones.
        #
        # Their Net_PnL_$ is DELIBERATELY empty; the result is in Unrealized_$.
        # Otherwise any sum over Net_PnL_$ would silently include unclosed trades in
        # the statistics, and the report would disagree with the engine, which counts
        # only completed ones. An empty cell gives NaN — it cannot be summed by accident.
        open_positions = getattr(result, "open_positions", None) or []
        for j, pos in enumerate(open_positions, len(result.trades) + 1):
            meta = getattr(pos, "metadata", None) or {}
            cs = getattr(pos, "contract_size", 1.0) or 1.0
            tps = getattr(pos, "take_profits", None) or []
            tp1 = tps[0] if tps else (getattr(pos, "take_profit", None) or "")
            sl = getattr(pos, "stop_loss", None)
            sl_dist = (
                n(abs(pos.entry_price - sl) / pos.entry_price * 100, 3)
                if sl and pos.entry_price
                else ""
            )
            held = ""
            if getattr(pos, "entry_time", None) and result.end_date:
                held = f"{(result.end_date - pos.entry_time).total_seconds() / 60:.0f}"

            writer.writerow(
                [
                    j,
                    pos.symbol,
                    pos.side,
                    pos.entry_time.strftime("%Y-%m-%d %H:%M") if pos.entry_time else "",
                    "",  # no exit yet
                    n(pos.entry_price, 6),
                    n(pos.current_price, 6),  # price at the end of the window
                    n(pos.quantity, 6),
                    n(pos.entry_price * pos.quantity * cs, 2),
                    n(sl, 6) if isinstance(sl, (int, float)) else "",
                    sl_dist,
                    n(tp1, 6) if isinstance(tp1, (int, float)) else "",
                    "",
                    "",
                    n(getattr(pos, "entry_commission", 0.0), 2),
                    "",
                    "",  # Net_PnL_$ deliberately empty
                    "OPEN",
                    held,
                    "",
                    "",
                    meta.get("fan_batch_id", ""),
                    getattr(pos, "strategy_name", "") or "",
                    "OPEN",
                    n(getattr(pos, "unrealized_pnl", 0.0), 2),
                ]
            )

    print()
    msg = f"Trades exported to {output_path} ({len(result.trades)} trades"
    if open_positions:
        unreal = sum(getattr(pos, "unrealized_pnl", 0.0) for pos in open_positions)
        msg += (
            f" + {len(open_positions)} OPEN, unrealized "
            f"${unreal:,.2f} — NOT included in the statistics)"
        )
    else:
        msg += ")"
    print(msg)


async def run_backtest(args):
    """Run the backtest."""
    # Validate arguments
    symbols, start_date, end_date = validate_args(args)

    # Load config for fees
    bot_config = get_config()

    # Use CLI args if provided, otherwise use config values
    maker_fee = args.maker_fee if args.maker_fee is not None else bot_config.fees.maker_fee
    taker_fee = args.taker_fee if args.taker_fee is not None else bot_config.fees.taker_fee
    slippage = args.slippage if args.slippage is not None else bot_config.fees.slippage

    # Convert --spread (fixed $ amount) to slippage percentage
    # Spread is applied as half-spread on each side (entry and exit)
    # --spread is applied AS IS, in dollars: half per side.
    #
    # It used to be converted to a percentage via a hardcoded price
    # (XAUUSD+ = 2900, anything unknown = 1000), and the engine then multiplied the
    # percentage by the ACTUAL price. With gold at 4650 that overstated costs by 60%,
    # on GC (missing from the table) by 4x, and over 16 years of history
    # ($1046..$5623) the error varied fivefold within a single run.
    absolute_spread = args.spread if args.spread > 0 else 0.0
    if absolute_spread > 0:
        logger.info(
            f"Spread ${absolute_spread:.3f} — " f"${absolute_spread/2:.4f} per side, in price units"
        )

    # Override per-lot commission from CLI if provided
    if args.commission_per_lot is not None:
        from src.core.trading_pairs import trading_pairs

        for sym in symbols:
            pair_info = trading_pairs.get(sym)
            if pair_info:
                pair_info.commission_type = "per_lot"
                pair_info.commission_per_lot = args.commission_per_lot
    risk_per_trade = args.risk if args.risk is not None else bot_config.risk.max_risk_per_trade
    max_position = (
        args.max_position if args.max_position is not None else bot_config.risk.max_position_percent
    )
    position_sizing = (
        args.position_sizing
        if args.position_sizing is not None
        else getattr(bot_config.risk, "position_sizing_method", "fixed_fractional")
    )

    # Validate execution timeframe
    if args.execution_timeframe:
        tf_minutes = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
        }
        exec_min = tf_minutes.get(args.execution_timeframe, 0)
        signal_min = tf_minutes.get(args.timeframe, 0)
        if exec_min >= signal_min:
            raise ValueError(
                f"Execution timeframe ({args.execution_timeframe}) must be smaller "
                f"than signal timeframe ({args.timeframe})"
            )

    # Log configuration
    logger.info("=" * 70)
    logger.info("BACKTEST CONFIGURATION")
    logger.info("=" * 70)
    logger.info(f"Session ID: {SESSION_ID}")
    logger.info(f"Symbols: {', '.join(symbols)}")
    logger.info(f"Timeframe: {args.timeframe}")
    if args.execution_timeframe:
        logger.info(
            f"Execution TF: {args.execution_timeframe} (SL/TP on {args.execution_timeframe})"
        )
    logger.info(f"Period: {start_date.date()} to {end_date.date()}")
    logger.info(f"Initial Balance: ${args.balance:,.2f}")
    logger.info(f"Risk per Trade: {risk_per_trade*100:.1f}%")
    logger.info(f"Max Position: {max_position*100:.1f}%")
    logger.info(f"Position Sizing: {position_sizing}")
    logger.info(f"Leverage: {args.leverage}x")
    if args.dynamic_risk:
        logger.info("Dynamic Risk: ON (DD>10%: 50% risk, DD>25%: 25% risk)")
    if args.daily_profit_target:
        logger.info(
            f"Daily Profit Target: +{args.daily_profit_target}% (mode: {args.daily_target_mode})"
        )
    if args.balance_cap > 0:
        if args.balance_cap < 100:
            logger.info(
                f"Balance Cap: {args.balance_cap:.1f}% of current balance "
                f"(dynamic — positions sized at this fraction, scale with compound)"
            )
        else:
            logger.info(
                f"Balance Cap: ${args.balance_cap:,.0f} "
                f"(positions sized as if max ${args.balance_cap:,.0f})"
            )
    logger.info(f"Strategies: {args.strategy}")
    from src.core.trading_pairs import trading_pairs as _tp_log

    _pair_log = _tp_log.get(symbols[0]) if symbols else None
    if _pair_log is not None and getattr(_pair_log, "commission_type", None) == "per_lot":
        logger.info(f"Commission: ${_pair_log.commission_per_lot:.2f}/lot (forex/CFD)")
    else:
        logger.info(f"Maker Fee: {maker_fee*100:.3f}%")
        logger.info(f"Taker Fee: {taker_fee*100:.3f}%")
    # Print the model that is actually in use. A percentage used to be printed here
    # unconditionally, and with a spread given in dollars the line showed 0.000% —
    # while slippage over the run added up to thousands of dollars.
    if absolute_spread > 0:
        logger.info(
            f"Spread: ${absolute_spread:.3f} "
            f"(${absolute_spread/2:.4f} per side, in price units)"
        )
    else:
        logger.info(f"Slippage: {slippage*100:.3f}%")
    if args.live_hedge_semantics:
        logger.info("Position gate: LIVE hedge semantics (same-side only blocks)")
    if args.indicator_window > 0:
        logger.info(f"Indicator Window: {args.indicator_window} candles (live-matching mode)")
    logger.info("=" * 70)

    print()
    print("=" * 70)
    print("  BACKTEST CONFIGURATION")
    print("=" * 70)
    print(f"  Symbols:        {', '.join(symbols)}")
    print(f"  Timeframe:      {args.timeframe}")
    if args.execution_timeframe:
        print(f"  Exec TF:        {args.execution_timeframe} (SL/TP on {args.execution_timeframe})")
    if args.tick_execution:
        print("  Tick Execution: ON (SL/TP resolved via MT5 ticks)")
    print(f"  Period:         {start_date.date()} to {end_date.date()}")
    print(f"  Initial Balance: ${args.balance:,.2f}")
    print(f"  Risk per Trade: {risk_per_trade*100:.1f}%")
    print(f"  Max Position:   {max_position*100:.1f}%")
    print(f"  Position Sizing: {position_sizing}")
    print(f"  Leverage:       {args.leverage}x")
    if args.dynamic_risk:
        print("  Dynamic Risk:   ON (DD>10%: 50% risk, DD>25%: 25% risk)")
    if args.daily_profit_target:
        print(f"  Daily Target:   +{args.daily_profit_target}%")
    if args.balance_cap > 0:
        if args.balance_cap < 100:
            print(f"  Balance Cap:    {args.balance_cap:.1f}% of current (dynamic)")
        else:
            print(f"  Balance Cap:    ${args.balance_cap:,.0f}")
    print(f"  Strategies:     {args.strategy}")
    print("-" * 70)
    from src.core.trading_pairs import trading_pairs as _tp

    _pair = _tp.get(symbols[0]) if symbols else None
    if _pair is not None and getattr(_pair, "commission_type", None) == "per_lot":
        # Forex / metals / CFD (MT5): broker charges a fixed amount per lot,
        # NOT a % of notional. The maker/taker % below do not apply here.
        print(
            f"  Commission:     ${_pair.commission_per_lot:.2f} / lot  (forex/CFD — fixed per lot)"
        )
    else:
        # Crypto: exchange charges a % of notional (maker/taker).
        print(f"  Maker Fee:      {maker_fee*100:.3f}%  (crypto — % of notional)")
        print(f"  Taker Fee:      {taker_fee*100:.3f}%  (crypto — % of notional)")
    if absolute_spread > 0:
        print(f"  Spread:         ${absolute_spread:.3f}  " f"(${absolute_spread/2:.4f} per side)")
    else:
        print(f"  Slippage:       {slippage*100:.3f}%")
    print("=" * 70)

    if args.data_source == "csv":
        _log_data_fingerprint(
            symbols=symbols,
            timeframe=args.timeframe,
            start_date=start_date,
            end_date=end_date,
            include_ticks=bool(args.tick_execution),
        )

    # Load strategy parameters from strategies.yaml
    import yaml

    strategies_yaml_path = os.path.join(
        os.path.dirname(__file__), "..", "config", "strategies.yaml"
    )
    with open(strategies_yaml_path, encoding="utf-8") as f:
        strategies_yaml = yaml.safe_load(f)

    # Parse strategy names (comma-separated)
    strategy_names = [s.strip() for s in args.strategy.split(",")]
    # Per-strategy risk override: lets you measure the weighting between
    # strategies without touching the working config.
    risk_override = {}
    if args.strategy_risk:
        for item in args.strategy_risk.split(","):
            name, _, val = item.partition(":")
            if not val:
                raise ValueError(f"Bad --strategy-risk {item!r}: expected 'name:value'")
            risk_override[name.strip()] = float(val)

    # Per-parameter override: test a hypothesis without touching the working
    # config. Values are coerced to bool/list/number, otherwise "true" would
    # arrive as a (truthy) string and silently flip a flag.
    param_override: Dict[str, Dict[str, Any]] = {}
    for item in args.param:
        path, _, raw = item.partition("=")
        strat, _, key = path.partition(".")
        if not (strat and key and raw):
            raise ValueError(f"Bad --param {item!r}: expected 'strategy.key=value'")
        low = raw.strip().lower()
        if low in ("true", "false"):
            value: Any = low == "true"
        elif raw.strip().startswith("["):
            value = yaml.safe_load(raw)  # lists, e.g. some_strategy.levels=[]
        else:
            try:
                value = int(raw) if raw.strip().lstrip("-").isdigit() else float(raw)
            except ValueError:
                value = raw
        param_override.setdefault(strat.strip(), {})[key.strip()] = value

    strategy_configs = []
    for strat_name in strategy_names:
        strat_params = dict(strategies_yaml.get(strat_name, {}))
        for k, v in param_override.get(strat_name, {}).items():
            logger.info(f"[param] {strat_name}.{k}: " f"{strat_params.get(k, '(unset)')} -> {v}")
            strat_params[k] = v
        if strat_name in risk_override:
            old = strat_params.get("risk_per_trade")
            strat_params["risk_per_trade"] = risk_override[strat_name]
            logger.info(f"[risk-override] {strat_name}: " f"{old} -> {risk_override[strat_name]}")
        if args.verbose:
            logger.info(f"Loaded strategy config for '{strat_name}': {list(strat_params.keys())}")
        strategy_configs.append(
            StrategyConfig(
                name=strat_name,
                class_name=f"src.strategies.{strat_name}",
                parameters=strat_params,
                enabled=True,
                timeframes=[args.timeframe],
            )
        )

    # Volatility tilt config: "ref=80,min=0.5,max=1.0,mode=ratio"
    vol_tilt_cfg = None
    if args.vol_tilt:
        vol_tilt_cfg = {"ref": 80.0, "min": 0.5, "max": 1.0, "mode": "ratio"}
        for part in args.vol_tilt.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in vol_tilt_cfg:
                vol_tilt_cfg[k] = v if k == "mode" else float(v)
        logger.info(
            f"Vol tilt: mode={vol_tilt_cfg['mode']} ref=${vol_tilt_cfg['ref']:.0f} "
            f"clamp [{vol_tilt_cfg['min']:.2f}, {vol_tilt_cfg['max']:.2f}]"
        )

    # Create configuration
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_balance=Decimal(str(args.balance)),
        symbols=symbols,
        timeframes=[args.timeframe],
        strategies=strategy_configs,
        commission_config=CommissionConfig(maker_fee=maker_fee, taker_fee=taker_fee),
        slippage_config=SlippageConfig(
            fixed_slippage=slippage,
            absolute_spread_usd=absolute_spread,
        ),
        risk_config=RiskConfig(
            max_risk_per_trade=risk_per_trade,
            max_position_size=max_position,
            max_drawdown=args.max_drawdown,
            leverage=args.leverage,
            position_sizing_method=position_sizing,
            dynamic_risk=args.dynamic_risk,
            risk_override=(args.risk is not None),
            balance_cap=args.balance_cap,
            vol_tilt=vol_tilt_cfg,
        ),
        data_config=DataConfig(
            source=DataSource.CSV if args.data_source == "csv" else DataSource.DATABASE,
            database_url=args.database_url,
        ),
        execution_mode=ExecutionMode.REALISTIC,
        limit_entry_config=LimitEntryBacktestConfig(
            enabled=not args.no_limit_entry
            and strategies_yaml.get("limit_entry", {}).get("enabled", True),
            timeout_candles=strategies_yaml.get("limit_entry", {}).get("timeout_candles", 2),
            price_offset_percent=strategies_yaml.get("limit_entry", {}).get(
                "price_offset_percent", 0.02
            ),
            maker_fee=strategies_yaml.get("limit_entry", {}).get("maker_fee", 0.0002),
            taker_fee=strategies_yaml.get("limit_entry", {}).get("taker_fee", 0.00055),
        ),
        execution_timeframe=args.execution_timeframe,
        daily_profit_target_pct=args.daily_profit_target,
        daily_target_mode=args.daily_target_mode,
        tick_execution=args.tick_execution,
        fixed_spread=getattr(args, "spread", 0.0) or 0.0,
        indicator_window=args.indicator_window,
        live_hedge_semantics=args.live_hedge_semantics,
        regime_gate_strategies=[
            s.strip() for s in (args.regime_gate_strategies or "").split(",") if s.strip()
        ],
        regime_gate_adx_threshold=args.regime_gate_adx_threshold,
        regime_gate_adx_tf=args.regime_gate_adx_tf,
        regime_gate_rules=parse_regime_gate_rules(args.regime_gate_rules),
        direction_filters=parse_direction_filter(args.direction_filter),
    )

    # Create and run engine
    print("\nInitializing backtest engine...")
    engine = BacktestEngine(config)

    # Disable regime switching if requested
    if args.no_regime_switching:
        engine.regime_switching_enabled = False
        print("  (Regime switching DISABLED)")

    print("Running backtest...")
    start_time = datetime.now()

    result = await engine.run()

    elapsed = datetime.now() - start_time
    print(f"Backtest completed in {format_duration(elapsed)}")

    # Print results
    print_results(result)

    # Export if requested
    if args.output:
        export_results(result, args.output)

    # Always export trades CSV to backtest logs directory
    if result.trades:
        session_id = SESSION_ID or "unknown"
        csv_path = os.path.join("logs", "backtest", f"trades_{session_id}.csv")
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        try:
            export_trades_csv(result, csv_path)
        except PermissionError:
            # File likely open in Excel — try with timestamp suffix
            from datetime import datetime as dt

            fallback = os.path.join(
                "logs", "backtest", f"trades_{session_id}_{dt.now().strftime('%H%M%S')}.csv"
            )
            try:
                export_trades_csv(result, fallback)
            except PermissionError:
                print(f"WARNING: Could not write CSV (file locked): {csv_path}")

    # Always export JSON alongside log for reproducibility
    if result.trades:
        session_id = SESSION_ID or "unknown"
        json_path = os.path.join("logs", "backtest", f"results_{session_id}.json")
        try:
            export_results(result, json_path)
        except Exception:
            pass  # Non-critical

    # Generate Obsidian note if requested
    if getattr(args, "note", False):
        try:
            from scripts.generate_backtest_note import generate_note_from_result

            note_path = generate_note_from_result(
                result,
                session_id=SESSION_ID or "",
                vault_path=getattr(args, "vault", "notes"),
                args=args,
            )
            if note_path:
                print(f"\nObsidian note: {note_path}")
        except Exception as e:
            print(f"\nWarning: Could not generate Obsidian note: {e}")

    return result


async def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging with automatic file output
    log_path = setup_logging(verbose=args.verbose)
    print(f"Logging to: {log_path}")

    try:
        await run_backtest(args)

        # Print log path at the end
        print(f"\nFull log saved to: {log_path}")

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        print(f"\nError: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Backtest cancelled by user")
        print("\n\nBacktest cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Backtest failed: {e}")
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
