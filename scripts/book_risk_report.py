"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 15.10.2026.
    File        : book_risk_report.py
    Project     : Project-103
    Description : Risk anatomy of a multi-strategy book from a backtest trade log —
                  drawdown under different denominators, IID vs block-bootstrap
                  Monte Carlo, drift in the mean, diversification and joint losses,
                  attribution of the worst episode, window dependence, daily VaR/ES
                  and (optionally) PnL by forecast-volatility quintile.
                  Companion to docs/writeups/03-book-risk.md.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research.monte_carlo import block_bootstrap_sample  # noqa: E402


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", decimal=",", encoding="utf-8-sig")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
    if "Status" in df.columns:
        df = df[df["Status"] == "closed"]
    df = df[df["Net_PnL_$"].notna()].copy()
    df["exit"] = pd.to_datetime(df["Exit_Time"])
    df["day"] = df["exit"].dt.normalize()
    return df.sort_values("exit").reset_index(drop=True)


def max_dd(pnl: np.ndarray) -> float:
    """Largest peak-to-trough decline of cumulative PnL, in the units of ``pnl``."""
    eq = np.concatenate([[0.0], np.cumsum(pnl)])
    return float((np.maximum.accumulate(eq) - eq).max())


def resample(x: np.ndarray, mode: str, rng: np.random.Generator, block: int) -> np.ndarray:
    if mode == "iid":
        return rng.choice(x, size=len(x), replace=True)
    if mode == "shuffle":
        return rng.permutation(x)
    return block_bootstrap_sample(x, rng, block)


def mc_dd(x: np.ndarray, mode: str, runs: int, rng, block: int) -> np.ndarray:
    return np.array([max_dd(resample(x, mode, rng, block)) for _ in range(runs)])


def acf(x: np.ndarray, lag: int = 1) -> float:
    return float(np.corrcoef(x[:-lag], x[lag:])[0, 1])


def section(title: str) -> None:
    print(f"\n== {title}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trades", type=Path, help="trades_*.csv from run_backtest.py")
    ap.add_argument("--sizing", type=float, required=True, help="capital positions are sized on")
    ap.add_argument("--balance", type=float, help="starting account balance (default = sizing)")
    ap.add_argument("--runs", type=int, default=10_000)
    ap.add_argument("--prices", type=Path, help="1m OHLC CSV for the volatility-quintile table")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    base = args.sizing
    balance0 = args.balance or base
    rng = np.random.default_rng(args.seed)
    df = load_trades(args.trades)
    pnl = df["Net_PnL_$"].astype(float).values

    days = pd.bdate_range(df["day"].min(), df["day"].max())
    by_strat = (
        df.pivot_table(index="day", columns="Strategy", values="Net_PnL_$", aggfunc="sum")
        .reindex(days)
        .fillna(0.0)
    )
    daily = by_strat.sum(axis=1).values
    pct = 100.0 / base  # $ -> % of sizing capital

    print(f"{len(df)} trades, {len(days)} trading days, {by_strat.shape[1]} strategies")
    print(f"{df['exit'].min()} .. {df['exit'].max()}, net {pnl.sum() * pct:.0f}% of sizing")

    section("1. one drawdown, three denominators (closed trades)")
    bal = np.concatenate([[balance0], balance0 + np.cumsum(pnl)])
    peak = np.maximum.accumulate(bal)
    dd_usd, dd_acc = peak - bal, (peak - bal) / peak * 100
    for label, i in [
        ("largest in $", dd_usd.argmax()),
        ("largest in % of account", dd_acc.argmax()),
    ]:
        print(
            f"{label:24s} ${dd_usd[i]:7.0f}  {dd_acc[i]:5.1f}% of account (peak ${peak[i]:.0f})  "
            f"{dd_usd[i] * pct:5.1f}% of sizing"
        )

    section("2. serial dependence")
    print(f"acf1 trade PnL {acf(pnl):+.3f}   acf1 daily PnL {acf(daily):+.3f}")
    a = np.abs(daily - daily.mean())
    print(f"acf |daily PnL| lag 1/5/10: {acf(a, 1):+.3f} {acf(a, 5):+.3f} {acf(a, 10):+.3f}")
    rm = pd.Series(daily).rolling(20).mean().std()
    null = np.array(
        [pd.Series(rng.permutation(daily)).rolling(20).mean().std() for _ in range(2000)]
    )
    print(
        f"std of 20-day mean PnL {rm:.1f} vs shuffled p50 {np.median(null):.1f} "
        f"p95 {np.percentile(null, 95):.1f}  -> percentile {np.mean(null < rm) * 100:.1f}"
    )

    section(f"3. Monte Carlo max drawdown, % of sizing ({args.runs} runs)")
    for label, x in [("trades", pnl), ("days", daily)]:
        block = max(1, int(round(np.sqrt(len(x)))))
        hist = max_dd(x) * pct
        for mode in ("iid", "shuffle", "block"):
            s = mc_dd(x, mode, args.runs, rng, block) * pct
            print(
                f"{label:6s} {mode:7s} hist {hist:5.1f}  p50 {np.percentile(s, 50):5.1f}  "
                f"p95 {np.percentile(s, 95):5.1f}  p99 {np.percentile(s, 99):5.1f}  "
                f"hist at percentile {np.mean(s < hist) * 100:4.1f}"
            )
    for b in (1, 5, 10, 20, 40):
        s = mc_dd(daily, "block", args.runs // 2, rng, b) * pct
        print(
            f"days block={b:<3d} p95 {np.percentile(s, 95):5.1f}  p99 {np.percentile(s, 99):5.1f}"
        )

    section("4. diversification")
    corr = by_strat.corr().values
    off = corr[np.triu_indices_from(corr, 1)]
    print(f"pairwise daily corr mean {off.mean():+.3f}  range {off.min():+.3f} .. {off.max():+.3f}")
    cov = by_strat.cov().values
    var_share = cov.sum(axis=1) / cov.sum() * 100
    solo = {s: max_dd(by_strat[s].values) * pct for s in by_strat.columns}
    print(f"sum of solo max DD {sum(solo.values()):.0f}% vs book {max_dd(daily) * pct:.0f}%")
    eq = np.cumsum(daily)
    t = int(np.argmax(np.maximum.accumulate(eq) - eq))
    p = int(np.argmax(eq[: t + 1]))
    episode = by_strat.iloc[p + 1 : t + 1].sum()
    print(f"worst episode {days[p + 1].date()} .. {days[t].date()}, {(eq[p] - eq[t]) * pct:.0f}%")
    print(f"{'strategy':18s} {'solo DD%':>8s} {'var share%':>10s} {'episode share%':>14s}")
    for j, s in sorted(enumerate(by_strat.columns), key=lambda js: -solo[js[1]]):
        share = episode[s] / episode.sum() * 100
        print(f"{s:18s} {solo[s]:8.1f} {var_share[j]:10.1f} {share:14.1f}")
    neg = (by_strat < 0).sum(axis=1).values
    p_neg = (by_strat < 0).mean().values
    sims = (rng.random((20_000, len(p_neg))) < p_neg).sum(axis=1)
    for k in (4, 5, 6):
        print(
            f">= {k} strategies losing: {int((neg >= k).sum())} days, "
            f"{np.mean(sims >= k) * len(days):.1f} expected if independent"
        )

    section("5. window dependence of max drawdown (% of sizing)")
    for win in (63, 126):
        vals = np.array([max_dd(daily[i : i + win]) for i in range(0, len(daily) - win + 1, 5)])
        vals *= pct
        print(
            f"{win}-day windows: min {vals.min():.1f}  median {np.median(vals):.1f}  max {vals.max():.1f}"
        )

    section("6. daily VaR / ES, % of sizing")
    for q in (0.95, 0.99):
        var = -np.quantile(daily, 1 - q)
        es = -daily[daily <= -var].mean()
        print(f"VaR{q * 100:.0f} {var * pct:5.1f}   ES{q * 100:.0f} {es * pct:5.1f}")
    for day, v in pd.Series(daily, index=days).nsmallest(3).items():
        print(f"  {day.date()} {v * pct:6.1f}%  strategies losing: {(by_strat.loc[day] < 0).sum()}")

    if args.prices:
        section("7. daily PnL by forecast-volatility quintile")
        px = pd.read_csv(
            args.prices, usecols=["timestamp", "high", "low", "close"], parse_dates=["timestamp"]
        )
        g = px.groupby(px["timestamp"].dt.normalize())
        rng_pct = (g["high"].max() - g["low"].min()) / g["close"].last() * 100
        rng_pct = rng_pct[rng_pct.index.dayofweek < 5]
        j = pd.DataFrame(
            {
                "forecast": rng_pct.ewm(span=10).mean().shift(1),  # prior days only
                "realised": rng_pct,
                "pnl": pd.Series(daily, index=days) * pct,
            }
        ).dropna()
        print(f"corr(forecast, realised range) {j['forecast'].corr(j['realised']):.2f}")
        print(f"corr(forecast, |PnL|) {j['forecast'].corr(j['pnl'].abs()):+.2f}")
        j["quintile"] = pd.qcut(j["forecast"], 5, labels=False) + 1
        print(j.groupby("quintile")["pnl"].agg(["mean", "std", "min", "count"]).round(1))


if __name__ == "__main__":
    main()
