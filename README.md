# Systematic trading research framework

[![CI](https://github.com/Mashiron01035/Project-103/actions/workflows/ci.yml/badge.svg)](https://github.com/Mashiron01035/Project-103/actions/workflows/ci.yml)

Tools for answering one question honestly: **is this trading idea real, or did
the search manufacture it?** The repository contains the statistical tests I use
to separate edge from overfit, an execution-accurate backtest engine, and the
live-trading runtime that runs the same strategy code against MetaTrader 5 and
Bybit. Most of the work is on gold (XAUUSD) at 1-minute and tick resolution.

## Research notes

The write-ups are the main point of this repository; the code exists to make
their results reproducible.

| Note | Finding |
|---|---|
| [Separating real edge from overfit: surrogate tests on gold](docs/writeups/01-edge-vs-overfit.md) | On 470k one-minute XAUUSD returns, directional autocorrelation is indistinguishable from shuffled noise (z = −2.2) while volatility clustering is overwhelming (z = +278). A gallery of ideas that looked profitable until a specific test killed them. |
| [Backtest–live parity: one model, two runtimes](docs/writeups/02-backtest-live-parity.md) | How the backtest and the live bot are kept making the same decisions on the same prices, and the bugs that broke parity — each one now pinned by a test. |

## What is in here

| Package | Purpose |
|---|---|
| [`src/research/`](src/research) | Permutation (shuffle-surrogate) tests, triple-barrier labelling, purged k-fold and walk-forward CV with embargo, block-bootstrap Monte Carlo of drawdown and risk of ruin |
| [`src/backtesting/`](src/backtesting) | Candle-by-candle engine with optional tick-level SL/TP resolution, per-instrument cost models, drawdown-episode analysis, rejection reports |
| [`src/strategies/`](src/strategies) | `BaseStrategy` contract, strategy manager, two demo strategies |
| [`src/execution/`](src/execution) | Live signal lifecycle (entry → partial TPs → trailing/breakeven → exit), MT5 / Bybit / mock connectors, position recovery after restart |
| [`src/bot/`](src/bot) | Live runtime: initialization, strategy and health loops, crash recovery |
| [`src/data/`](src/data), [`src/indicators/`](src/indicators) | Data providers (MT5, Bybit), storage with write-ahead log, indicator factory |
| [`scripts/`](scripts) | Backtest runner, Monte Carlo analysis, tick-to-candle builder, an MCP server for driving backtests from an LLM client — see [`scripts/README.md`](scripts/README.md) |

Signal flow, shared by backtest and live:

```
DataProvider → candles → StrategyManager → Strategy.analyze() → Signal
            → backtest: PortfolioManager          (simulated fills)
            → live:     SignalLifecycleManager → ConnectorRouter → broker
```

## Measurement bugs this engine guards against

Each of these produced a plausible-looking but wrong backtest before it was found.
They are listed because finding them is most of the work.

- **Equity without locked margin.** Margin moved out of the free balance on entry
  was missing from equity, so every open position looked like a drawdown the size
  of its margin. On the `session_breakout` demo below this reported an 85% max
  drawdown; the real figure is 16%, with identical trades. Fixed and covered by a
  regression test.
- **Shorts triggered on bid.** Candles are built from bid prices, but a short's
  stop fires when the *ask* reaches it. Reconciling against broker fills found
  missed stops and false take-profits; shorts now trigger on bid + spread.
- **Percentage spread on a dollar-quoted cost.** A spread calibrated as a fraction
  of the price at $2,900 gold overstates costs 1.6× at $4,650 and 5× across 16
  years of history. Spread is modelled in price units (`--spread`).
- **Broker server time zone.** The MT5 server runs on New York + 7 and switches with
  US daylight-saving dates, not European ones. Using European dates mislabelled
  every timestamp by an hour for four weeks a year; conversion is now per timestamp.
- **Commission model per instrument.** Crypto pays a percentage of notional;
  forex/CFD pays dollars per lot. Mixing them once overcharged a gold backtest 64×.
- **Drawdown percentage at the dollar maximum.** The percentage at the largest
  dollar drawdown is not the largest percentage drawdown; the report shows both
  and the per-episode depth, time to trough and recovery.
- **Silent data gaps.** The report states the span actually loaded (not the one
  requested), and the candle cache is keyed on a fingerprint of the source file so
  appended data cannot be silently ignored.

## Quick start

```bash
poetry install
poetry run pytest tests/ -q
```

### Data

Market data is not included. The CSV source reads `data/<SYMBOL>_<TIMEFRAME>.csv`
with UTC timestamps:

```
timestamp,open,high,low,close,volume,quote_volume,spread
2025-01-01 23:00:00,2625.845,2626.275,2625.075,2625.4215,91,0.0,82.0
```

`spread` is in broker points (MT5 convention). Contract size, lot step and the
commission model per symbol come from [`config/trading_pairs.yaml`](config/trading_pairs.yaml);
add a block there before backtesting a new CFD or forex symbol.

### Backtest

```bash
poetry run python scripts/run_backtest.py \
  --symbols XAUUSD --timeframe 1m \
  --start 2025-01-01 --end 2026-04-30 \
  --strategy session_breakout --balance 10000 \
  --data-source csv --no-regime-switching --spread 0.13
```

Several strategies at once: `--strategy session_breakout,swing_fade`.
Tick-level SL/TP resolution: add `--tick-execution` (needs MT5 tick data under
`cache/ticks/`). The full flag reference is in [`scripts/README.md`](scripts/README.md).

### Monte Carlo on the trades

```bash
poetry run python scripts/monte_carlo_analysis.py logs/backtest/trades_<id>.csv \
  --runs 10000 --ruin-thresholds 20,30,50 --plot
```

Block bootstrap is the default reference: it keeps losing streaks and regime
clusters together, which IID resampling breaks apart and so understates tail risk.

### Research API

```python
import numpy as np
from src.research import permutation_test, monte_carlo_dd

returns = np.diff(np.log(close))                       # 1-minute log returns
acf1 = lambda x: np.corrcoef(x[:-1], x[1:])[0, 1]

direction = permutation_test(returns, acf1, alternative="two-sided")
volatility = permutation_test(np.abs(returns), acf1)
print(direction.z_score, volatility.z_score)           # ≈ -2.2 and ≈ +278 on XAUUSD

mc = monte_carlo_dd(trade_pnls, starting_balance=10_000, mode="block",
                    ruin_thresholds=[20, 30, 50])
print(mc.dd_p95, mc.ruin)
```

## Demo strategies

The live strategies are not part of this repository. Two structurally different
demo strategies exercise the engine instead, on neutral, untuned defaults:

- `session_breakout` — opening-range breakout, market entry (momentum)
- `swing_fade` — fades the first swing of the session with a limit order at the pivot (mean reversion)

XAUUSD 1m, 2025-01-01 → 2026-04-30, $10,000, 1% risk per trade, $6/lot commission,
no spread:

| Strategy | Trades | Win rate | Profit factor | Return | Max drawdown |
|---|---|---|---|---|---|
| `session_breakout` | 242 | 49.6% | 1.00 | −0.3% | 16.3% |
| `swing_fade` | 165 | 13.3% | 0.61 | −44.7% | 48.7% |

Neither has an edge, and that is the expected result: an untuned breakout is a
coin flip after costs, and fading the first swing loses on a trending year. It is
the same conclusion the first research note reaches with a surrogate test, without
having to trade anything.

## Development

```bash
make format    # black + isort
make lint      # ruff + mypy
make check     # format + lint + tests
```

CI runs black, isort, ruff and pytest as hard gates; mypy runs but does not block
the build yet.

## License

[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)
