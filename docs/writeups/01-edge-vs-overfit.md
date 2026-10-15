# Separating real edge from overfit: surrogate tests on gold

> A profitable-looking backtest is the *default* outcome of searching, not
> evidence of an edge. This note is about the tooling and the discipline that
> tell the two apart — and a gallery of ideas that looked great until a test
> killed them. All of it runs on the [`src/research/`](../../src/research) module.

---

## TL;DR

On XAUUSD 1-minute data, **directional structure is statistically
indistinguishable from noise, while volatility structure is overwhelming.** A
single shuffle-surrogate test makes the gap impossible to miss:

| Statistic | Observed | Shuffled null mean | z-score | Significant (α=0.01)? |
|---|---|---|---|---|
| `acf1(returns)` — *direction* | −0.0034 | −0.0001 | **−2.2** | no |
| `acf1(\|returns\|)` — *volatility* | +0.394 | −0.0000 | **+278** | yes |

*(470,142 one-minute returns, 2025-01 → 2026-04; 500 surrogates, seed 42.)*

The consequence sets the whole research agenda: a durable edge cannot come from
predicting **which way** price goes — that signal isn't there. It has to come
from **how much and when** — sizing, gating, timing — because *that* structure
(volatility/regime) is real and, as it turns out, stable across years.

Everything below is how I arrived at that conclusion without fooling myself.

---

## 1. The problem: backtests overfit by construction

Three forces manufacture fake edge, and all three are silent:

- **Selection / multiple testing.** Try forty rules, keep the best-looking one,
  and you've sampled the right tail of pure noise. The survivor's metrics are
  conditioned on having survived.
- **Compounding.** With position size proportional to balance, a single lucky
  early trade is re-levered into every later one. On a compounded equity curve
  one outlier can be inflated 100–500×, turning noise into a "trend."
- **Resolution and execution gaps.** A coarse backtest bar hides intrabar
  stop-outs; a per-trade simulation hides the path-dependence of real execution.

So "the equity curve goes up and to the right" is worth almost nothing on its
own. The question is never *does it look profitable* — it's *is there structure
here that the ordering and the selection didn't invent.*

---

## 2. The test that does the most work: the shuffle surrogate

The cheapest, highest-leverage check is a **permutation (surrogate) test**. The
null hypothesis is deliberately humble: *"the ordering carries no information —
any value of my statistic is explained by the marginal distribution alone."*

You sample that null **directly**: shuffle the series thousands of times
(destroying temporal order, keeping the return distribution), recompute the
statistic on each surrogate, and see where the real value lands. Inside the
shuffled band ⇒ noise. Far outside it ⇒ real structure.

```python
import numpy as np, pandas as pd
from src.research import permutation_test

close = pd.read_csv("data/XAUUSD_1m.csv", usecols=["close"])["close"].to_numpy()
r = np.diff(np.log(close))

def acf1(x):                      # lag-1 autocorrelation
    x = x - x.mean()
    return float(np.sum(x[:-1] * x[1:]) / np.sum(x * x))

direction  = permutation_test(r,        acf1, n_permutations=500, alternative="two-sided")
volatility = permutation_test(np.abs(r), acf1, n_permutations=500, alternative="greater")

print(direction.z_score,  direction.p_value)    # -2.2   0.024  -> bid-ask bounce, economically nil
print(volatility.z_score, volatility.p_value)   # +278   0.002  -> volatility clustering, real structure
```

The implementation is ~40 lines:
[`src/research/permutation.py`](../../src/research/permutation.py). It returns
the observed statistic, the null moments, a z-score and a **+1-smoothed**
p-value (an unobserved tail is not proof of impossibility).

**Reading the gold result.** The faint −0.0034 autocorrelation in raw returns is
bid-ask bounce — a microstructure artefact, not a tradeable signal, and at
z = −2.2 it isn't even robust. The +0.394 autocorrelation in *absolute* returns
is volatility clustering: the most reliable stylised fact in markets, and here
it screams at z = +278. Same data, opposite verdicts.

That asymmetry is the thesis in one table: **direction is the note (noise);
volatility is the tone (predictable).** Forecasts of the tone — not the note —
are what a leveraged system can actually monetise, through sizing, regime gates
and time-of-day vetoes.

---

## 3. A gallery of ideas the tests killed

The value of rigor isn't the edge it keeps — it's the twenty plausible ideas it
throws away before they cost real money. A representative sample:

- **Fair-value gaps / displacement.** Naked FVG is a coin flip with negative
  expectancy once costs are paid; a displacement filter just re-derives
  momentum. It only "worked" on the single window it was tuned on.
- **Liquidity-sweep reversion (turtle-soup).** Fading a sweep of the prior
  day's high/low loses every year — on gold a sweep is a *breakout*, not a trap.
  The "smart" version (forced-flow + confluence + an ML gate) was rejected by a
  three-agent adversarial review: confluence moved the fade-rate not at all
  (AUC 0.503), and post-sweep direction was a coin flip (multivariate AUC 0.456
  — it *anti*-generalised).
- **Balanced price range, on coarse bars.** Looked like **+0.16R** per trade on
  1-hour bars. Re-resolved at 1-minute it was **−0.02R**: the hourly bar was
  hiding the intrabar stop-outs. (Lesson below.)
- **High-win-rate small-bounce fade.** Structurally negative-EV *even at zero
  cost* — the win rate sits below break-even in all 36 configurations and all 6
  years. Not a cost problem; a no-signal problem.
- **News straddle around 13:30 UTC.** The volatility spike is real (triggers 98%
  vs 60% placebo) but **symmetric** — no follow-through. A straddle in a linear
  instrument is not direction-agnostic; all 22 configs lose. Another confirmation
  that vol is for *sizing*, not for *entries*.

Two tests recur in those rejections and deserve names:

**The symmetry test.** Flip the side of a rule and the sign of its result should
flip too. If "fade longs" and "fade shorts" both make money, you haven't found a
fade edge — you've found **drift** leaking through an asymmetric stop, a mirage
that dies the moment the trend reverses.

**The cost/resolution test.** Re-run every scalp- or tight-stop idea at execution
resolution (1m or tick) and with real fees. The BPR collapse above (+0.16R →
−0.02R) is the canonical example: coarse-bar backtests systematically *overstate*
edge for anything with a stop inside the bar.

---

## 4. Three traps that manufacture fake edge

Beyond rejecting ideas, rigor means recognising when the *measurement* lies.

1. **Compound inflation.** Always cross-check a compounded result against a
   flat-balance run. If the edge evaporates when every trade is the same size,
   the "edge" was one or two outliers re-levered by the compounding — fixing a
   problem that doesn't exist.
2. **Coarse-TF resolution bias.** A 1h/15m backtest hides intrabar stop-outs and
   inflates tight-stop strategies. Resolve stops on 1-minute or tick data.
3. **Flat-sim vs engine gap.** A per-trade simulation is fine for ranking ideas
   but radically misleading for *execution* changes (sizing × stop interactions,
   fallbacks, partial fills). One V2 limit-entry tweak read as +11% return in a
   flat simulation and as a drawdown blow-out (8% → 31%) in the real engine.
   Execution changes get validated in the engine, never in a sketch.

And the meta-lesson, learned the hard way: **a result that is too good is a bug
until proven otherwise.** A mean-reversion strategy once showed 79% win rate and
profit factor 12. The cause was a one-line fill check comparing `side == "BUY"`
against a side stored as `"LONG"` — it fabricated instant fills below market. The
real edge was negative. Now, anything with a fairy-tale metric gets its fill and
pricing path audited before its PnL is believed.

---

## 5. The full validation stack

A candidate that survives the surrogate test still has to clear the rest before
it is allowed near capital. The [`src/research/`](../../src/research) module is
this stack, as reusable code:

| Question | Tool | Module |
|---|---|---|
| Is there any structure at all? | shuffle surrogate / permutation | [`permutation.py`](../../src/research/permutation.py) |
| Is it drift, not edge? | symmetry test (flip the side) | *(methodology)* |
| Does it survive OOS without leakage? | purged walk-forward CV + embargo | [`cross_validation.py`](../../src/research/cross_validation.py) |
| Are the labels path-aware and regime-comparable? | triple-barrier + CUSUM events | [`labeling.py`](../../src/research/labeling.py) |
| Can position sizing survive the *path distribution*? | Monte Carlo drawdown / risk-of-ruin | [`monte_carlo.py`](../../src/research/monte_carlo.py) |

The cross-validation deserves a word, because it's where most ML-on-markets work
quietly leaks. A triple-barrier label at time *t₀* "knows" prices until the
barrier is touched at *t₁*. If a neighbouring sample whose window overlaps *t₀*
lands in the training fold, the model has seen the test's future — and reports a
fantastic, fake out-of-sample score. `PurgedKFold` drops those overlapping
samples and adds an embargo after each test block; `purged_walk_forward_cv` does
the same along a rolling time axis (López de Prado, AFML ch. 7). Every method
here is covered by tests in
[`tests/unit/research/`](../../tests/unit/research).

---

## 6. So what *does* survive?

Nothing that predicts direction — Section 2 closed that door. What survives is
**conditional on regime and volatility, not on a forecast of the next move:**

- size down (or stand aside) when the volatility regime says the next bar is a
  fat tail;
- gate entries to the hours and regimes where a given strategy's *own* edge
  actually lives;
- treat the volatility spike around scheduled news as a reason to *reduce risk*,
  not as an entry.

These don't try to be right about the note. They exploit the fact that the tone
is predictable (its year-over-year correlation is ~0.87, versus ~−0.01 for
direction). In live use, a volatility veto roughly halved one strategy's
drawdown without touching its hit rate — a risk win, not a return trick, and
exactly the kind of edge the surrogate test says *should* exist.

---

## 7. The point

The edge isn't a strategy. It's the process that rejects twenty strategies to
keep the one — and that can tell, before risking a dollar, whether a beautiful
equity curve is signal or selection. The strategies are replaceable. The
falsification discipline, and the tooling that encodes it, is the moat.

> **Reproduce:** the surrogate snippet in Section 2 runs against any 1-minute
> close series. The validation stack lives in
> [`src/research/`](../../src/research) with tests in
> [`tests/unit/research/`](../../tests/unit/research).
