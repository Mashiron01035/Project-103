"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 17.04.2026.
    File        : exposure_analysis.py
    Project     : Project-103
    Description : Compute peak concurrent exposure and effective leverage over time from a backtest trades CSV.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Peak concurrent exposure analysis")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--top-n", type=int, default=10, help="Show top-N peak moments")
    args = parser.parse_args()

    df = pd.read_csv(args.csv, sep=";", decimal=",")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]

    df["Entry_Time"] = pd.to_datetime(df["Entry_Time"])
    df["Exit_Time"] = pd.to_datetime(df["Exit_Time"])
    df["Notional"] = pd.to_numeric(df["Notional"], errors="coerce").fillna(0.0)

    print("=" * 78)
    print(f"FILE: {args.csv.name}")
    print(f"TRADES: {len(df)}")
    print(f"PERIOD: {df['Entry_Time'].min()} → {df['Exit_Time'].max()}")
    if "Strategy" in df.columns:
        print(f"STRATEGIES: {df['Strategy'].nunique()}")
        print("  " + ", ".join(df["Strategy"].value_counts().index.tolist()))
    print("=" * 78)

    # Also need Balance_After and Net_PnL_$ to reconstruct equity timeline
    df["Balance_After"] = pd.to_numeric(df["Balance_After"], errors="coerce")
    df["Net_PnL_$"] = pd.to_numeric(df["Net_PnL_$"], errors="coerce").fillna(0.0)
    # Initial balance = first Balance_After - first Net_PnL_$
    initial_balance = float(df["Balance_After"].iloc[0]) - float(df["Net_PnL_$"].iloc[0])

    # Build timeline events. Exits carry the balance-after (realized).
    events = []
    for _, row in df.iterrows():
        if pd.notna(row["Entry_Time"]) and pd.notna(row["Exit_Time"]):
            events.append(
                (row["Entry_Time"], row["Notional"], row.get("Strategy", ""), "enter", None)
            )
            events.append(
                (
                    row["Exit_Time"],
                    -row["Notional"],
                    row.get("Strategy", ""),
                    "exit",
                    row["Balance_After"],
                )
            )

    # Sort: earlier time first; within same time, exits before entries (conservative)
    events.sort(key=lambda e: (e[0], 0 if e[3] == "exit" else 1))

    # Walk through events. Track equity using realized balance from exits.
    running_exposure = 0.0
    current_equity = initial_balance  # updated when exits happen

    peak_leverage = 0.0
    peak_time = None
    peak_exposure_at_leverage = 0.0
    peak_equity_at_leverage = 0.0
    peak_strategies_at_leverage = {}

    peak_exposure = 0.0  # absolute peak notional
    peak_time_abs = None

    open_positions_by_strategy = {}
    open_notional_by_strategy = {}

    leverage_samples = []  # (time, leverage, exposure, equity)

    for ts, delta, strat, kind, bal_after in events:
        running_exposure += delta
        if kind == "enter":
            open_positions_by_strategy[strat] = open_positions_by_strategy.get(strat, 0) + 1
            open_notional_by_strategy[strat] = open_notional_by_strategy.get(strat, 0) + delta
        else:
            open_positions_by_strategy[strat] = open_positions_by_strategy.get(strat, 0) - 1
            open_notional_by_strategy[strat] = open_notional_by_strategy.get(strat, 0) + delta
            if open_positions_by_strategy.get(strat, 0) <= 0:
                open_positions_by_strategy.pop(strat, None)
                open_notional_by_strategy.pop(strat, None)
            # Update current equity to balance after this exit (realized)
            if bal_after is not None and not pd.isna(bal_after):
                current_equity = float(bal_after)

        # Track absolute peak notional
        if running_exposure > peak_exposure:
            peak_exposure = running_exposure
            peak_time_abs = ts

        # Track peak LEVERAGE (the meaningful risk metric)
        if current_equity > 0 and running_exposure > 0:
            lev = running_exposure / current_equity
            leverage_samples.append((ts, lev, running_exposure, current_equity))
            if lev > peak_leverage:
                peak_leverage = lev
                peak_time = ts
                peak_exposure_at_leverage = running_exposure
                peak_equity_at_leverage = current_equity
                peak_strategies_at_leverage = {
                    s: (open_positions_by_strategy.get(s, 0), open_notional_by_strategy.get(s, 0.0))
                    for s in open_positions_by_strategy
                    if open_positions_by_strategy.get(s, 0) > 0
                }

    # Peak LEVERAGE stats (the real risk metric)
    print("\n=== PEAK LEVERAGE (exposure / equity) ===")
    print(f"Maximum effective leverage: {peak_leverage:.1f}x")
    print(f"Reached at: {peak_time}")
    print(f"  Exposure at peak: ${peak_exposure_at_leverage:,.0f}")
    print(f"  Equity at peak:   ${peak_equity_at_leverage:,.0f}")
    adverse_wipe = 100.0 / peak_leverage if peak_leverage > 0 else 0
    print(f"  Adverse move for full wipe: {adverse_wipe:.3f}%")
    print("\nOpen positions at peak leverage:")
    total_pos = 0
    for s, (cnt, notl) in sorted(peak_strategies_at_leverage.items(), key=lambda x: -x[1][1]):
        print(f"  {s:<28} {cnt:>3} positions, ${notl:>14,.0f}")
        total_pos += cnt
    print(f"  {'TOTAL':<28} {total_pos:>3} positions, ${peak_exposure_at_leverage:>14,.0f}")

    # Leverage distribution
    if leverage_samples:
        df_lev = pd.DataFrame(leverage_samples, columns=["time", "leverage", "exposure", "equity"])
        df_lev = df_lev.set_index("time")
        minute_series = df_lev["leverage"].resample("1min").last().ffill()
        samples = minute_series.dropna().to_numpy()
        samples = samples[samples > 0]

        if len(samples):
            print("\n=== LEVERAGE DISTRIBUTION (minutes with open positions) ===")
            print(f"Sampled minutes: {len(samples):,}")

            def p(q):
                return np.percentile(samples, q)

            print(f"  p50 (median): {p(50):>6.1f}x  → wipe at {100/p(50):.3f}% adverse")
            print(f"  p75:           {p(75):>6.1f}x  → wipe at {100/p(75):.3f}% adverse")
            print(f"  p90:           {p(90):>6.1f}x  → wipe at {100/p(90):.3f}% adverse")
            print(f"  p95:           {p(95):>6.1f}x  → wipe at {100/p(95):.3f}% adverse")
            print(f"  p99:           {p(99):>6.1f}x  → wipe at {100/p(99):.3f}% adverse")
            print(
                f"  max:           {samples.max():>6.1f}x  → wipe at {100/samples.max():.3f}% adverse"
            )

            # Count minutes above broker leverage limit (500x)
            over_500 = (samples > 500).sum()
            over_1000 = (samples > 1000).sum()
            print(
                f"\n  Minutes with effective leverage > 500x: {over_500:,} ({over_500/len(samples)*100:.2f}%)"
            )
            print(
                f"  Minutes with effective leverage > 1000x: {over_1000:,} ({over_1000/len(samples)*100:.2f}%)"
            )

    print("\n=== ABSOLUTE NOTIONAL PEAK ===")
    print(f"Max concurrent notional: ${peak_exposure:,.0f}")
    print(f"Reached at: {peak_time_abs}")
    print(
        f"(Scales with balance growth — at peak, account equity was ~${peak_equity_at_leverage:,.0f})"
    )

    print("\nFor context, typical 1m XAUUSD moves:")
    print("  Normal candle range: 0.02-0.10%")
    print("  Strong impulse: 0.15-0.30%")
    print("  News spike: 0.30-1.00%")
    print("=" * 78)


if __name__ == "__main__":
    main()
