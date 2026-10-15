# Value-at-Risk for gold: six models, fourteen years, and the two days none of them saw

> One-day VaR and Expected Shortfall for a gold futures position, forecast by six
> standard models and backtested out of sample over 3,666 trading days. The note
> is about what separates a model that passes from one that does not — and about
> the two days that every model missed by a factor of two or more. Models and
> tests live in [`src/research/var.py`](../../src/research/var.py); the run is
> [`scripts/var_backtest.py`](../../scripts/var_backtest.py).

---

## TL;DR

| Model | 99% exceptions (long) | Passes all tests, long | Passes all tests, short |
|---|---|---|---|
| Historical simulation, 250 days | 1.31% | no — exceptions cluster, ES short | no |
| Historical simulation, 500 days | 1.25% | no — exceptions cluster | no |
| EWMA, normal (RiskMetrics) | 2.07% | no — twice the allowed rate | no |
| GARCH(1,1), Student-t | 1.17% | yes | no — too conservative, clusters |
| Filtered HS on GARCH | 1.09% | yes | no — clusters |
| **Filtered HS on HAR realised variance** | 1.28% | **yes** | **yes** |

*(COMEX gold futures, roll-free daily returns, 2010-06 → 2026-08; forecasts from
2012-05; 99% and 97.5% one-day horizon; tests at 5%.)*

- **The normal distribution is the first thing to go.** RiskMetrics has the right
  volatility dynamics but twice the allowed exceptions and an ES 20% too low.
- **Historical simulation fails on timing, not on count.** It has roughly the
  right number of exceptions but in the wrong places: it had the lowest VaR of
  the six the day before the worst crash of the sample, and the highest months
  after volatility had calmed down.
- **Only the model fed by intraday data passes on both sides.** A HAR forecast of
  5-minute realised variance, scaled by its own empirical residuals, passes
  coverage, independence and the ES test for long and short positions.
- **Every model is blown through on 15 Apr 2013 and 30 Jan 2026**, by 1.4–3.7×
  its VaR. That is what stress tests are for; no VaR model replaces them.

---

## 1. Data: a futures series without rolls

A continuous futures chart glued from front contracts is not a return series: at
each of the 81 rolls in this sample the price jumps by the calendar spread, and
that jump is not a gain or loss anyone had.

The returns here are measured **inside one contract**: the return for day *t*
uses the contract that was the volume leader at the close of *t − 1*, priced at
both closes. Rolls disappear; weekend gaps, holiday sessions and limit days stay,
because they are real risk for whoever holds the position. The trading day ends
at 17:00 New York (CME). The resulting series has 4,166 daily returns; the
largest are all genuine: −9.8% on 30 Jan 2026, −8.8% on 15 Apr 2013, +6.7% on
24 Mar 2020.

For gold the correction turns out to be small. The spliced series gives nearly
the same backtest table for the long side (EWMA-N has 74 exceptions instead of
76, every other model within two, no verdict changes), because gold's calendar
spread is small next to its daily volatility. That had to be checked, not
assumed: on a market with steep contango or backwardation, such as oil or natural
gas, a roll jump can be as large as a VaR exception.

The same 1-minute bars give each day's realised variance (sum of squared 5-minute
returns inside the session), which one of the models uses.

## 2. Models

All forecasts are causal: the forecast for day *t* uses information up to the
close of *t − 1*. Fitted models are re-estimated every 20 days on the last 1,000.

| Model | Volatility | Tail shape |
|---|---|---|
| HS250 / HS500 | none — the window *is* the distribution | empirical, last 250 / 500 days |
| EWMA-N | exponential average of squared returns, λ = 0.94 | normal |
| GARCH-t | GARCH(1,1), maximum likelihood | Student-t, ν fitted at every refit |
| FHS-GARCH | GARCH(1,1) | empirical quantile of GARCH-standardised returns |
| HAR-FHS | HAR on log realised variance (1-, 5- and 22-day terms) | empirical quantile of HAR-standardised returns |

## 3. Tests

| Test | Question |
|---|---|
| Kupiec proportion of failures | Is the share of days with loss > VaR equal to 1%? |
| Christoffersen independence | Does an exception make another one tomorrow more likely? |
| Conditional coverage | Both at once |
| ES exceedance (McNeil–Frey style) | On VaR97.5 exceptions, does the loss average the forecast ES? |
| Basel traffic light | Share of rolling 250-day windows with ≥ 10 exceptions at 99% (red zone) |

## 4. Results

Long position (loss = falling gold), 3,666 out-of-sample days:

| Model | Exceptions 99% | Rate | p Kupiec | p indep. | Back-to-back | Loss / ES | p ES | Red-zone windows | Mean VaR99 |
|---|---|---|---|---|---|---|---|---|---|
| HS250 | 48 | 1.31% | 0.07 | **0.03** | 3 | 1.08 | **0.03** | 2.3% | 2.61% |
| HS500 | 46 | 1.25% | 0.14 | **0.02** | 3 | 1.05 | 0.10 | 7.3% | 2.61% |
| EWMA-N | 76 | 2.07% | **0.000** | 0.74 | 2 | 1.20 | **0.000** | 2.7% | 2.23% |
| GARCH-t | 43 | 1.17% | 0.31 | 0.11 | 2 | 1.02 | 0.29 | 0 | 2.53% |
| FHS-GARCH | 40 | 1.09% | 0.58 | 0.08 | 2 | 1.04 | 0.15 | 0 | 2.56% |
| HAR-FHS | 47 | 1.28% | 0.10 | 0.64 | 1 | 1.09 | 0.07 | 0 | 2.60% |

Short position (loss = rising gold):

| Model | Exceptions 99% | Rate | p Kupiec | p indep. | Back-to-back | Loss / ES | p ES | Red-zone windows |
|---|---|---|---|---|---|---|---|---|
| HS250 | 59 | 1.61% | **0.001** | 0.09 | 3 | 1.06 | **0.02** | 1.8% |
| HS500 | 52 | 1.42% | **0.02** | **0.001** | 5 | 1.07 | **0.02** | 3.0% |
| EWMA-N | 73 | 1.99% | **0.000** | 0.25 | 3 | 1.13 | **0.000** | 1.9% |
| GARCH-t | 29 | 0.79% | 0.19 | **0.02** | 2 | 0.95 | 0.98 | 0 |
| FHS-GARCH | 43 | 1.17% | 0.31 | **0.01** | 3 | 1.00 | 0.53 | 0 |
| HAR-FHS | 48 | 1.31% | 0.07 | 0.66 | 1 | 1.03 | 0.21 | 0 |

The average VaR is nearly the same for every model that passes and for HS
(2.5–2.6%). The conditional models do not hold more capital on average; they hold
it **at different times**, and that is the whole difference.

### Historical simulation is late twice

| Date | Event | HS250 | EWMA-N | GARCH-t | HAR-FHS |
|---|---|---|---|---|---|
| 12 Apr 2013 | before the 2013 crash | 2.12 | 1.97 | 2.51 | 2.09 |
| 16 Apr 2013 | day after −8.8% | 2.57 | 6.15 | 7.71 | 4.10 |
| 10 Apr 2014 | a year later, markets calm | **3.78** | 1.97 | 2.45 | 2.57 |
| 21 Apr 2014 | crash leaves the HS window | 3.22 | 1.98 | 2.48 | 2.36 |
| 29 Jan 2026 | day before −9.8% | **2.96** | 4.31 | 4.47 | 4.63 |
| 2 Feb 2026 | after the crash | 3.99 | 6.90 | 7.66 | 8.46 |
| 1 Jun 2026 | volatility back down | **4.74** | 3.12 | 3.00 | 3.48 |

*(VaR99, % of position, long side.)*

After a crash, historical simulation barely moves — one extreme day is one
observation out of 250 — and then keeps it for exactly a year, dropping it on a
date that has nothing to do with the market (the "ghost" effect). Going into
January 2026, when gold's volatility had been rising for weeks, HS250 had the
lowest VaR of all models. Exceptions therefore come in clusters after volatility
rises, which is what the independence test detects. Volatility clustering on
gold is enormous (note [01](01-edge-vs-overfit.md): z = +278 on 1-minute data);
a model without it is mis-timed by construction.

### Symmetric tails cost GARCH-t the short side

GARCH-t passes for the long position and over-covers the short one (0.79% instead
of 1%), with clustered exceptions on that side. The Student-t is symmetric, gold's
tails are not, and one ν for both sides puts the cushion where it is not needed.
Replacing the parametric tail with empirical quantiles of the standardised
residuals (FHS) fixes the rate but not the clustering, which comes from the
volatility forecast itself.

### Realised variance fixes the timing

The HAR model reads volatility from 5-minute returns inside the session instead
of from one close-to-close number per day, so it reacts within a day to a change
in regime and does not over-react to a single gap. It is the only model with one
back-to-back exception in 14 years and an independence p-value above 0.6 on both
sides. On 29 Jan 2026 it had the highest VaR of all models.

## 5. Where every model fails

| Date | Loss | Highest VaR99 among the six | Loss / that VaR |
|---|---|---|---|
| 30 Jan 2026 | −9.76% | 6.97 (HAR-FHS) | 1.4× |
| 15 Apr 2013 | −8.76% | 4.75 (FHS-GARCH) | 1.8× |
| 11 Aug 2020 | −5.84% | 3.13 (HS250) | 1.9× |
| 24 Mar 2020 (short) | +6.73% | 5.90 (HAR-FHS) | 1.1× |

The worst exceedance is 2.3–3.3× the model's own ES. These days are not tail
quantiles of a stable distribution; they are regime breaks, and a one-day 99%
model is not built to see them. In practice that means a separate stress scenario
— for gold, "−10% in a day" now has two historical instances in this sample — and
position limits that do not rely on VaR alone.

### The weekend

Monday returns carry the weekend gap. Historical simulation, which treats every
day alike, has 14 exceptions in 730 Mondays (1.92%, binomial p = 0.017) against
1.16% on other days. GARCH-t has 10 (1.37%, p = 0.20), HAR-FHS shows no Monday
excess at all. A day-of-week scale is a cheap fix for any unconditional model.

## Limits

- Six models, two sides and four tests are many comparisons; a single p-value
  near 0.05 is weak evidence. The conclusions above rest on patterns that repeat
  across tests and sides, not on one test.
- COMEX futures, not spot or CFD: the broker's daily close and weekend handling
  differ, and the script runs on a continuous spot/CFD series too (`--bars`).
- One-day horizon only; ten-day VaR by √10 scaling would inherit every error above
  and add the ones of the scaling.
- The ES test is a simple exceedance test. It detects understated ES on average,
  not a misshapen tail beyond VaR.
- The HAR model uses realised variance from the futures session only, so the
  overnight and weekend part of risk is absorbed into its empirical scaling.

## Reproduce

```bash
# per-contract 1-minute futures bars (symbol, timestamp, close, volume)
poetry run python scripts/var_backtest.py --contracts "data/gc_parts/*.parquet" --side long
# or one continuous spot/CFD series
poetry run python scripts/var_backtest.py --bars data/XAUUSD_1m.csv --side short
```

The futures bars come from the CME Globex feed and are not redistributed.
