# Backtest–live parity: one model, two runtimes

> A backtest and the live bot are two runtimes executing the same trading model.
> Every place they price a fill, trigger a stop, or close a batch *differently*
> is a place where a validated edge silently fails to transfer — and at 500×
> leverage, "silently" means "expensively." This note is how the system keeps
> the two in lockstep.

---

## TL;DR

You optimise on the backtest; the numbers you trust — profit factor, drawdown,
the gate thresholds you tuned — are only meaningful if the live path makes the
**same decisions on the same prices.** A backtest that is even subtly more
optimistic than live is worse than no backtest: it manufactures confidence and
you size up on it.

Parity is maintained by four things: a **shared spine** (the same strategy code
runs in both runtimes), **mirrored execution** (every backtest portfolio
operation has a live counterpart), **explicit reconciliation** of the few places
reality forces them apart, and **tests that pin the invariants** both sides must
honour.

---

## 1. The two runtimes

```
backtest:  BacktestEngine → strategy.analyze() → PortfolioManager → TradeResult
              (candles)         (Signal)          (open/close/SL/TP/trailing/batch)

live:      DataProvider  → strategy.analyze() → SignalLifecycleManager → ConnectorRouter → exchange
              (ticks)          (Signal)          (entry → partial TPs → trailing → exit)
```

The crucial observation: **the same `strategy.analyze()` runs in both.** Signal
generation is shared code, so it cannot diverge. All the parity risk lives in
what happens *after* a `Signal` is returned — the execution layer. That is where
[`portfolio_manager.py`](../../src/backtesting/portfolio/portfolio_manager.py)
(backtest) and
[`signal_lifecycle_manager.py`](../../src/execution/signal_lifecycle_manager.py)
(live) must agree.

---

## 2. The parity points

Every operation that moves money exists on both sides and must produce the same
result given the same prices:

| Operation | Backtest (`PortfolioManager`) | Live (`SignalLifecycleManager`) | Invariant |
|---|---|---|---|
| Entry price | slippage applied to mid → `execution_price` | real fill (bid/ask) ± slippage | entry = reference ± *unfavourable* slippage |
| PnL | `(exit − entry) · qty · contract_size` | `((exit − entry) / entry) · position_size_usdt` | identical **iff** `notional = entry · qty · cs` |
| Fees | `FixedCommission` (%) or `PerLotCommission` ($/lot) | `open_fee + close_fee`, `net = exchange_pnl − fees` | **same fee model per instrument** |
| Stop / target | `check_stop_loss` / `check_take_profit` | `_check_positions` / `_check_trailing_take_profit` | same directional comparison |
| Trailing | one-directional ratchet in `update_price` | `_place_trailing_stop_order` | stop only ever tightens |
| Batch close | `check_fan_batches_tick_by_tick` | `_check_fan_batch_close` | same trigger (worst hits BE / max loss) |
| Restart recovery | *(n/a — backtest never restarts)* | `position_recovery` reconciles exchange ↔ tracked | live-only; must rebuild exact state |

The **PnL row is the subtle one.** The backtest computes an absolute figure,
`(exit − entry) · qty · cs`; the live side computes a percentage and scales it by
notional, `((exit − entry) / entry) · position_size_usdt`. These are the same
number *only* when `position_size_usdt = entry · qty · contract_size`. That
identity — the definition of notional — is the bridge between the two
parameterisations, and it is exactly the kind of thing that drifts when one side
is edited and the other isn't.

---

## 3. Where they genuinely diverge — and how it's reconciled

Some divergence is real. The discipline is to handle it **explicitly in code**,
not pretend it away.

- **Bar vs tick.** The backtest resolves stops on candles (or on ticks with
  `--tick-execution`); live sees every tick. A coarse bar hides intrabar
  stop-outs and *overstates* edge for tight-stop strategies — the same
  resolution trap quantified in
  [write-up #1](01-edge-vs-overfit.md) (an idea that was +0.16R on 1h bars and
  −0.02R at 1m). Reconciliation: resolve tight stops at execution resolution.
- **Spread / slippage.** The backtest `--spread` flag converts to slippage on
  *entry and exit*; live works off real bid/ask. The trap is **double-counting**
  spread — applying it as slippage *and* again as a bid/ask gap. Single source,
  documented.
- **MT5 reality.** On MetaTrader 5 a limit order **opens a new position** (it
  cannot be used to close one for a take-profit), TP/SL must be set by *modifying
  the position*, and spread arrives as integer points. The live connector encodes
  these; the backtest must not assume the naive "limit-order-as-close" model — a
  classic source of phantom fills.
- **Exchange-reported vs computed PnL.** Live trusts the exchange's realised PnL
  and fees (`net_pnl = exchange_pnl − total_fees`); the backtest computes from
  first principles. They must agree to within fees, and a divergence is logged
  rather than hidden.
- **Lookback window** *(the elegant one).* The backtest's signal-history window
  is pinned to exactly the live warm-up lookback (`warmup.lookback_candles`), so
  a strategy can never see history in a backtest that it would not have had live.
  This removes an entire class of subtle divergence — and, as a side effect,
  bounds an O(n²) rescan to linear.
- **Filters.** A historical bug: some filters ran *only* in the backtest path, so
  live took trades the backtest had rejected. The fix was to move filtering into
  shared/live code. A related gotcha survives by design — the verbose
  *rejection* logs exist only in the backtest, so "why didn't it trade live?" is
  answered by re-running the backtest on fresh data, not by grepping live logs.

---

## 4. How parity breaks in practice

Three real failures, each a lesson now encoded as a rule:

1. **The fill bug.** A backtest fill check compared `side == "BUY"` while the
   strategy stored the side as `"LONG"` — so it fabricated instant fills *below*
   market and reported 79% win rate, profit factor 12. Pure backtest fantasy;
   live would never fill there. **Lesson: a too-good metric is a parity bug until
   the fill and pricing path are audited.**
2. **The commission mismatch.** One instrument was missing from the pairs config
   on the backtest side, so it charged crypto %-fees instead of $/lot — a ~64×
   overcharge that corrupted every backtest on that symbol. Same instrument, two
   fee models, zero parity. (This is exactly what the cost-model tests in
   [`tests/unit/backtesting/`](../../tests/unit/backtesting) now guard.)
3. **Flat-sim vs engine.** A per-trade simulation is a *third*, cruder runtime.
   Fine for ranking ideas, it lies for execution changes: one limit-entry tweak
   read as +11% return in the flat sim and as an 8% → 31% drawdown blow-out in
   the real engine. Execution changes are validated in the engine, never the
   sketch.

---

## 5. The discipline that maintains it

- **One change → one test.** Never stack two execution/pricing changes; each gets
  a backtest comparison before the next.
- **The mirror rule.** A change to `portfolio_manager.py` (backtest) usually
  needs a matching change in `signal_lifecycle_manager.py` (live), and vice
  versa. They are reviewed as a pair, never one alone.
- **Trace the full flow before touching pricing.** Entry → PnL across the
  position's life → trailing activation → batch close → exit. 500× leverage
  amplifies every rounding and sign error exponentially.
- **Tests as parity guards.** The money-path and cost-model unit tests in
  [`tests/unit/backtesting/`](../../tests/unit/backtesting) pin the exact
  formulas both runtimes must honour — PnL sign, `contract_size` scaling, the
  fee model, stop/target direction, the trailing ratchet, partial-close
  conservation. A future edit that breaks parity on one side trips a test.

---

## 6. The point

Parity is not a feature you add once; it is an invariant you defend on every
change. A backtest is trustworthy only to the degree the live path is its
faithful twin. The architecture makes the twins **share a spine** (the strategy),
**mirror their execution** method-for-method, and **reconcile in code** the few
places where market reality forces them apart — with a unit-test suite standing
over the formulas to catch the day they drift.
