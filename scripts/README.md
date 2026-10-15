# Scripts

Command reference for the tooling in `scripts/`. Run everything through Poetry
from the repo root. Overview and research notes are in the root
[`README.md`](../README.md); this file is the "what do I type" reference.

---

## `run_backtest.py` — backtest runner

The main entry point. Candle-by-candle simulation with optional tick-level SL/TP.

```bash
# Single strategy, date range, CSV data
poetry run python scripts/run_backtest.py \
  --symbols XAUUSD --timeframe 1m \
  --start 2026-01-01 --end 2026-04-30 \
  --strategy session_breakout --balance 10000 --data-source csv

# Portfolio (several strategies), last N days, no regime selection
poetry run python scripts/run_backtest.py --symbols XAUUSD --timeframe 1m \
  --days 90 --strategy session_breakout,swing_fade \
  --balance 10000 --data-source csv --no-regime-switching

# Tick-level SL/TP resolution (needs tick data under ./cache/ticks/)
poetry run python scripts/run_backtest.py --symbols XAUUSD --timeframe 1m \
  --days 30 --strategy swing_fade --data-source csv --tick-execution

# Export trades + result JSON
... --output results.json
```

Trades are written to `logs/backtest/trades_*.csv` — feed that into the analysis
scripts below.

### Key flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--symbols, -s` | *(required)* | Comma-separated pairs, e.g. `XAUUSD` |
| `--timeframe, -t` | `15m` | Signal candle timeframe (`1m`…`1d`) |
| `--start` / `--end` | — | Date range `YYYY-MM-DD` (`--end` is exclusive) |
| `--days, -d` | — | Alternative to start/end: last N days |
| `--strategy` | demo set | Strategy name(s), comma-separated |
| `--balance, -b` | `10000` | Initial balance |
| `--data-source` | `csv` | `csv` or `database` |
| `--tick-execution` | off | Resolve SL/TP inside ambiguous bars with MT5 ticks |
| `--execution-timeframe` | = signal TF | Run SL/TP checks on a finer timeframe |
| `--no-regime-switching` | off | Run all given strategies (no regime-based selection) |
| `--leverage` | `10` | Leverage multiplier |
| `--risk` | from config | Risk per trade (decimal), overrides config |
| `--position-sizing` | from config | `fixed_fractional` / `fixed_balance` / `balance_ratio` |
| `--balance-cap` | `0` | Size as if balance = N (or N% if < 100) |
| `--daily-profit-target` | — | Daily target %, behaviour per `--daily-target-mode` |
| `--spread` | `0` | Spread in price units (half per side) |
| `--commission-per-lot` | from pairs | Override forex/CFD per-lot commission |
| `--maker-fee` / `--taker-fee` | from config | Override crypto percentage fees |
| `--param` | — | Override a strategy parameter: `strategy.key=value` (repeatable) |
| `--strategy-risk` | — | Per-strategy risk override: `name:value,...` |
| `--indicator-window` | `0` | Compute indicators on the last N candles (live-matching) |
| `--live-hedge-semantics` | off | Only same-side positions block a new entry (live hedge rule) |
| `--output` | — | Export results to JSON |
| `--note` | off | Write a markdown report to `--vault` (default `./notes`) |

---

## `monte_carlo_analysis.py` — robustness / risk-of-ruin

Stress-tests a trades CSV: permutation (shuffle), IID + block bootstrap,
worst-case (drop top winners), loss-streak / recovery distributions, rolling
stability, streak-stop simulation.

```bash
poetry run python scripts/monte_carlo_analysis.py logs/backtest/trades_XXXX.csv \
  --runs 10000 --block-len 0 --ruin-thresholds 20,30,50 --plot
```

- `--block-len 0` → auto ≈ √n (block bootstrap preserves streaks/autocorrelation).
- `--ruin-thresholds` → max-DD% levels for `P(maxDD ≥ X)`.
- `--plot` → PNG (equity fan chart + histograms) next to the CSV.
- `--pct-mode` → resample `PnL_%` instead of `$` (order-independent).

---

## `exposure_analysis.py` — peak exposure / effective leverage

Walks a trades CSV and reports peak concurrent notional and effective leverage
over time (margin-call risk).

```bash
poetry run python scripts/exposure_analysis.py logs/backtest/trades_XXXX.csv --top-n 10
```

---

## `equity_chart.py` — equity curve PNG

Generates an equity-curve + drawdown + per-symbol P&L chart from closed signals
stored in the database (paper/live runs).

```bash
poetry run python scripts/equity_chart.py --hours 168 --output equity.png
```

---

## `ticks_to_candles.py` — tick → candle conversion

Converts tick parquet files (under `cache/ticks/`) into 1-minute candle CSV used
by the backtester.

```bash
poetry run python scripts/ticks_to_candles.py XAUUSD.s              # 1m (default)
poetry run python scripts/ticks_to_candles.py XAUUSD.s --timeframe 5m
```

---

## `download_historical.py` — historical data manager

Subcommand CLI over the data store (Bybit download + DB inspection).

```bash
poetry run python scripts/download_historical.py download --symbols XAUUSD --timeframe 1m --days 30
poetry run python scripts/download_historical.py list                 # available data
poetry run python scripts/download_historical.py stats                # DB statistics
poetry run python scripts/download_historical.py gaps --symbol XAUUSD --timeframe 1m
poetry run python scripts/download_historical.py export --symbol XAUUSD --timeframe 1h --output xau_1h.csv
poetry run python scripts/download_historical.py delete --before 2025-01-01
```

---

## `benchmark_latency.py` — pipeline latency benchmark

Times the hot path (data load → indicators → signal → sizing → order build).

```bash
poetry run python scripts/benchmark_latency.py --symbol XAUUSD.s --iterations 50
```

---

## `cli.py` — live bot control

Talks to a running bot (status / trading toggle / leverage / pairs).

```bash
poetry run python scripts/cli.py status         # bot status
poetry run python scripts/cli.py trading on      # enable trading
poetry run python scripts/cli.py stop            # emergency stop
poetry run python scripts/cli.py balance         # balance & P&L
poetry run python scripts/cli.py leverage 10     # set leverage 10x
poetry run python scripts/cli.py pairs           # list pairs
```

---

## `backtest_mcp.py` — MCP server for Claude

Exposes the backtest engine to Claude as an MCP server (launch backtests, read
structured results without copy-pasting session logs). Registered as an MCP
server rather than run by hand:

```bash
poetry run python scripts/backtest_mcp.py
```

Requires the `mcp` package (`pip install mcp`), which is not a project dependency.
