# How much can a strategy book lose? Drawdown risk, measured honestly

> The maximum drawdown printed under a backtest is one draw from a distribution,
> measured in a unit that is often wrong, on a window that was chosen. This note
> takes a nine-strategy gold book apart to show how much each of those choices
> moves the number, and which parts of "risk" survive the scrutiny. All figures
> come from [`scripts/book_risk_report.py`](../../scripts/book_risk_report.py),
> which runs on any trade log the backtest engine writes.

---

## TL;DR

| Question | Naive answer | Answer after the checks |
|---|---|---|
| How deep was the worst drawdown? | 13.9% (of the account) | 68% of the capital positions are sized on |
| How deep can it plausibly get? | p99 86% (IID bootstrap) | p99 110% (block bootstrap) |
| Was the historical drawdown bad luck? | 90th percentile — unlucky | 40th percentile — typical |
| Does diversification work? | — | Yes: solo drawdowns sum to 298%, the book's is 63% |
| Do strategies lose together on bad days? | — | No more often than independent coins |
| Is risk concentrated in high volatility? | — | No: the worst day was in the second-calmest quintile |

*(XAUUSD, 9 strategies, 2,444 trades over 334 trading days, 2025-07 → 2026-10;
1-minute candles with tick-level stop/target resolution; drawdowns in % of the
sizing capital unless stated.)*

The useful number is the last row of the Monte Carlo table, not the first line of
the backtest report — and the reason is not losing streaks, which this book does
not have, but **drift in the expected return over time**.

---

## The book

Nine intraday and multi-day strategies on gold, run together in one backtest with
a shared account. Their names and rules are not part of this repository; their
types are:

| | Type | Holding |
|---|---|---|
| A | momentum, both directions | minutes–hours |
| B | trend following | days |
| C | momentum, short only | minutes–hours |
| D | weekly mean reversion | one day |
| E, F | scheduled flow (benchmark fixings) | minutes |
| G | scheduled flow (weekly close) | hours |
| H | trend following | hours |
| I | grid mean reversion | hours |

Positions are sized on a fixed capital of \$1,200 while the account starts at
\$3,000, so every trade risks the same dollar amount for the whole run (flat
sizing — see section 1 for why). The backtest is in-sample: parameters were
calibrated on parts of this window, and 2025–26 was an exceptional year for gold.
The book is strongly profitable on it; that is not the subject here and not a
forecast. The subject is how much it loses on the way.

---

## 1. One drawdown, three numbers

Before any statistics, the drawdown has to be measured in the right unit.

| Episode (closed trades)                 | \$      | % of account | % of sizing capital |
|-----------------------------------------|---------|---|---|
| July 2025, account at its \$3,000 start | 416     | **13.9%** | 35% |
| December 2025, account at \$7,081       | **821** | 11.6% | **68%** |

The percentage of the account ranks the July episode as the worst; in risk units
it was half as deep as December's. With flat sizing the account grows while the
stake per trade does not, so **drawdown as a percentage of the account shrinks
mechanically** as the run goes on — a late disaster looks small. The denominator
that matters is the capital the positions are sized on, because that is what is
re-risked on every trade. Everything below uses it.

Three other measurement errors moved this number by multiples before they were
found:

- **Equity without locked margin.** Margin moved out of the free balance on entry
  was not added back to equity, so every open position showed up as a drawdown
  the size of its margin. On the `session_breakout` demo in the README the engine
  reported 85%; the real figure is 16%, with identical trades.
- **Percentage at the dollar maximum.** The engine used to print the percentage
  drawdown *at the moment of the largest dollar drawdown*, which is not the
  largest percentage drawdown. On one run it printed 20.1%; the true maximum was
  16.4%. The report now lists every episode with depth, time to trough and
  recovery.
- **Compounding.** Sized on a growing balance, one stop-loss late in a run
  appeared as a −\$10,072 loss; the same trade under flat sizing lost \$45. A 224×
  inflation that looked like a structural failure of the strategy and triggered
  a round of pointless parameter changes. Compounded runs are for projecting growth, never for
  diagnosing risk.

---

## 2. Monte Carlo: which resampling, and why it matters here

The standard robustness check resamples the trade list and rebuilds the equity
curve thousands of times. The resampling scheme decides the answer:

| Resampling (10,000 runs) | p50 | p95 | p99 | historical DD at percentile |
|---|---|---|---|---|
| trades, IID bootstrap | 54.8 | 81.2 | 96.9 | 84 |
| trades, shuffle | 54.5 | 79.9 | 95.0 | 85 |
| trades, block bootstrap | 66.6 | 91.8 | 112.8 | 55 |
| days, IID bootstrap | 44.8 | 70.0 | 85.9 | 90 |
| days, shuffle | 44.6 | 67.2 | 81.7 | 92 |
| days, block bootstrap | 66.6 | 86.6 | 110.2 | 40 |

Historical maximum drawdown: 68.4% on trades, 62.9% on daily PnL.

IID resampling says the realised drawdown was an unlucky 90th-percentile outcome
and that 86% is a 1-in-100 event. Block resampling says the realised drawdown was
*ordinary* and that the 1-in-100 drawdown exceeds the whole sizing capital.

The usual explanation — losing streaks that IID breaks apart — **does not apply to
this book**:

| Dependence check | Value |
|---|---|
| lag-1 autocorrelation of trade PnL | +0.003 |
| lag-1 autocorrelation of daily PnL | −0.007 |
| autocorrelation of \|daily PnL\| at lags 1 / 5 / 10 | −0.02 / +0.08 / −0.12 |

No streaks, no volatility clustering in PnL. What the block bootstrap preserves is
something else: **the expected return itself moves**. The standard deviation of
the 20-day mean of daily PnL is 45.4; across 2,000 shuffles of the same days it is
32.8 at the median and 41.2 at the 95th percentile, so the real series sits at
the 99th percentile. The book has good months and bad months — the edge is
regime-dependent — and a run of below-average weeks is exactly what produces a
deep drawdown. IID resampling averages every bad month out with good ones.

The result is not an artefact of the block length. With a mean block of one day
the block bootstrap reproduces IID (p99 86.8); from 5 to 40 days it sits on a
plateau of p99 105–110.

---

## 3. Diversification works in the body of the distribution

| | Solo max DD | Share of book variance | Share of the book's worst episode |
|---|---|---|---|
| A | 84.5 | 29.6 | 0 |
| B | 55.8 | 19.8 | 34.8 |
| C | 37.4 | 16.9 | 39.0 |
| D | 24.6 | 0.0 | 0 |
| E | 23.8 | 2.6 | 0 |
| F | 23.0 | 19.2 | 18.5 |
| G | 19.7 | 0.6 | 8.5 |
| H | 18.4 | 10.9 | 0 |
| I | 10.9 | 0.5 | 0 |
| **Book** | **62.9** | | |

- The solo drawdowns add up to 298%; the book's is 63%. Pairwise correlation of
  daily PnL averages −0.01 (range −0.17 to +0.29).
- **The strategy with the deepest solo drawdown contributed nothing to the book's
  worst episode.** A carries 30% of the variance and an 85% solo drawdown, yet the
  book's deepest decline (29 Sep → 6 Oct 2026) came from B, C, F and G. The worst
  moment of a portfolio is made of different days than the worst moments of its
  parts.
- Joint losses are not more frequent than chance. Days with four or more of the
  nine strategies losing: 27 observed, 34.5 expected if strategies lost
  independently at their own rates. Five or more: 6 vs 7.5. Six or more: 1 vs 1.0.

The practical consequence is that **per-strategy risk fixes do not transfer to the
book**. In an earlier test a filter that halved one strategy's solo drawdown left
the portfolio's maximum drawdown unchanged to the cent: the trades it removed were
not the ones that made the portfolio's worst moment. A risk change is evaluated on
the book or not at all.

---

## 4. A single window can show almost anything

Maximum drawdown over every 63-trading-day window (one quarter, stepped weekly)
ranges from **10.9% to 62.9%**, with a median of 20.9%. A three-month backtest of
the same book can honestly report any of those numbers. Over half-year windows the
figure settles (the two halves of the sample give 59.8% and 62.9%), which is a
minimum length for quoting a drawdown at all — and still only one draw, as
section 2 shows.

---

## 5. Daily tail and volatility

| | % of sizing capital |
|---|---|
| daily VaR 95% | 11.2 |
| daily expected shortfall 95% | 15.4 |
| daily VaR 99% | 17.1 |
| daily expected shortfall 99% | 20.8 |
| worst day (2 Oct 2026, five strategies losing) | 23.1 |

Gold's volatility is forecastable — a 10-day exponential average of the daily
range correlates 0.53 with the next day's — so a natural idea is to scale the
book down when volatility is forecast high. Bucketing days by that forecast:

| Forecast-volatility quintile | Mean day | Std of day | Worst day |
|---|---|---|---|
| 1 (calmest) | +4.5 | 10.2 | −12.1 |
| 2 | +4.7 | 14.2 | **−23.1** |
| 3 | +5.1 | 12.5 | −16.3 |
| 4 | +8.9 | 14.0 | −17.6 |
| 5 (most volatile) | +8.1 | 12.2 | −12.8 |

The dispersion of daily PnL barely changes across quintiles, the worst day sits in
the second-calmest one, and the mean is highest when volatility is high. Scaling
down on forecast volatility would cut the most profitable days and leave the tail
where it is. The forecast correlates only +0.15 with the size of the book's daily
PnL: the residual risk of the book is close to orthogonal to market volatility,
so a portfolio-level volatility overlay has little to remove.

---

## What I take from it

- **Size from the block-bootstrap tail, not from the historical drawdown.** Here
  that means planning for a drawdown above 100% of the sizing capital at 1-in-100,
  where the backtest report shows 14%. The sizing capital is deliberately a
  fraction of the account for exactly this reason.
- **Measure drawdown in units of what is at risk** — the sizing capital, with flat
  sizing — and treat percentages of a growing account as cosmetic.
- **Test risk changes on the book.** Correlations are near zero, joint losses are
  at chance level and the worst episode is made by different strategies than the
  worst solo drawdowns. Nothing measured on one strategy predicts its effect on
  the portfolio.
- **Watch the mean, not the streak.** The dangerous dependence is a slow drift in
  expected return across regimes. It is invisible to autocorrelation tests and to
  IID Monte Carlo, and it is what a drawdown limit has to be designed against.

## Limits

- One instrument and one 15-month window in an unusual year for gold. Every
  resampling scheme can only reshuffle what happened; none can produce a regime
  that is not in the sample.
- The backtest is in-sample with respect to parts of the parameter search, so the
  historical drawdown is a lower bound, not an estimate.
- Closed-trade PnL is used throughout; intraday open-position swings add to the
  drawdown and are not in these numbers.
- Strategy-level rules are not published, so the figures are reproducible on your
  own trade log with the same script, not on this book.

## Reproduce

```bash
poetry run python scripts/book_risk_report.py logs/backtest/trades_<id>.csv \
  --sizing 1200 --balance 3000 --prices data/XAUUSD_1m.csv
```

`--sizing` is the capital positions are sized on (the balance cap in
`run_backtest.py`); `--prices` is optional and adds the volatility-quintile table.
