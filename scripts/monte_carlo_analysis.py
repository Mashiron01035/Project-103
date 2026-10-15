"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 16.04.2026.
    File        : monte_carlo_analysis.py
    Project     : Project-103
    Description : Monte Carlo robustness analysis of backtest trades — permutation/bootstrap stress tests, worst-case, loss-streak and recovery distributions, rolling stability, and streak-stop simulation.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_VAULT = Path(os.environ.get("TRADING_VAULT_PATH", "notes"))


# Lines of markdown to emit into the vault note. Populated in parallel with stdout.
note_lines: list = []


def log(line: str = "") -> None:
    """Print to stdout and collect for the vault note."""
    print(line)
    note_lines.append(line)


def load_trades(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, sep=";", decimal=",")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
    if "Net_PnL_$" not in df.columns:
        raise ValueError(f"Expected column 'Net_PnL_$' not found. Columns: {list(df.columns)}")
    df = df[df["Net_PnL_$"].notna()].reset_index(drop=True)
    return df


def equity_curve(pnls: np.ndarray, starting_balance: float) -> np.ndarray:
    return starting_balance + np.cumsum(pnls)


def max_drawdown_pct(equity: np.ndarray, starting_balance: float) -> float:
    full = np.concatenate([[starting_balance], equity])
    running_peak = np.maximum.accumulate(full)
    drawdown = (full - running_peak) / running_peak * 100.0
    return float(-drawdown.min())


def max_consecutive_losses(pnls: np.ndarray) -> int:
    current = 0
    best = 0
    for p in pnls:
        if p < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def recovery_time(pnls: np.ndarray, starting_balance: float) -> int:
    """Trades needed to recover from max DD trough back to the prior peak.

    Returns 0 if no drawdown. Returns -1 if the strategy never recovers
    from its worst drawdown by the end of the sample.
    """
    equity = equity_curve(pnls, starting_balance)
    full = np.concatenate([[starting_balance], equity])
    running_peak = np.maximum.accumulate(full)
    drawdown = full - running_peak
    trough_idx = int(np.argmin(drawdown))
    if drawdown[trough_idx] >= 0:
        return 0
    peak_before = running_peak[trough_idx]
    for i in range(trough_idx, len(full)):
        if full[i] >= peak_before:
            return i - trough_idx
    return -1


def compute_stats(pnls: np.ndarray, starting_balance: float) -> dict:
    equity = equity_curve(pnls, starting_balance)
    final_balance = equity[-1] if len(equity) else starting_balance
    total_return_pct = (final_balance - starting_balance) / starting_balance * 100.0
    max_dd = max_drawdown_pct(equity, starting_balance)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    wr = len(wins) / len(pnls) * 100.0 if len(pnls) else 0.0
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() != 0 else float("inf")
    return {
        "total_return_pct": total_return_pct,
        "max_dd_pct": max_dd,
        "win_rate_pct": wr,
        "profit_factor": pf,
        "final_balance": final_balance,
    }


def percentile_row(values: np.ndarray, label: str, fmt: str = "8.2f") -> str:
    def p(q):
        return np.percentile(values, q)

    return (
        f"  {label:<20} "
        f"min={values.min():>{fmt}}  "
        f"p5={p(5):>{fmt}}  "
        f"p50={p(50):>{fmt}}  "
        f"p95={p(95):>{fmt}}  "
        f"max={values.max():>{fmt}}  "
        f"mean={values.mean():>{fmt}}"
    )


def percentile_md_row(values: np.ndarray, label: str, fmt: str = ".2f") -> str:
    def p(q):
        return np.percentile(values, q)

    return (
        f"| {label} | {values.min():{fmt}} | {p(5):{fmt}} | {p(50):{fmt}} | "
        f"{p(95):{fmt}} | {values.max():{fmt}} | {values.mean():{fmt}} |"
    )


def _sharpe(pnls: np.ndarray) -> float:
    """Per-trade Sharpe (mean / std of trade PnL). Not annualized — comparative."""
    if len(pnls) < 2:
        return 0.0
    sd = pnls.std(ddof=1)
    return float(pnls.mean() / sd) if sd > 0 else 0.0


def _profit_factor(pnls: np.ndarray) -> float:
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def block_bootstrap_sample(
    pnls: np.ndarray, rng: np.random.Generator, expected_block: int
) -> np.ndarray:
    """Stationary (circular) block bootstrap.

    Resamples contiguous BLOCKS of random geometric length (mean = expected_block)
    so serial dependence — losing streaks, regime clusters — is PRESERVED. Plain
    IID bootstrap shuffles trades independently and destroys that structure, which
    understates tail risk (DD/streaks) on autocorrelated trade sequences.
    """
    n = len(pnls)
    p = 1.0 / max(1, expected_block)
    out = np.empty(n)
    i = 0
    while i < n:
        start = int(rng.integers(0, n))
        block_len = int(rng.geometric(p))
        for j in range(block_len):
            if i >= n:
                break
            out[i] = pnls[(start + j) % n]  # circular wrap
            i += 1
    return out


def ruin_probabilities(dds: np.ndarray, thresholds: list) -> dict:
    """P(max drawdown >= threshold%) for each threshold."""
    return {t: float((dds >= t).mean() * 100.0) for t in thresholds}


def run_simulations(
    pnls: np.ndarray,
    starting_balance: float,
    runs: int,
    mode: str,
    rng: np.random.Generator,
    expected_block: int = 4,
    keep_paths: int = 0,
) -> dict:
    n = len(pnls)
    returns = np.empty(runs)
    dds = np.empty(runs)
    streaks = np.empty(runs, dtype=int)
    recoveries = np.full(runs, -1, dtype=int)
    pfs = np.empty(runs)
    sharpes = np.empty(runs)
    paths = []  # first `keep_paths` equity curves, for the fan chart
    for i in range(runs):
        if mode == "shuffle":
            sample = rng.permutation(pnls)
        elif mode == "bootstrap":
            sample = rng.choice(pnls, size=n, replace=True)
        elif mode == "block":
            sample = block_bootstrap_sample(pnls, rng, expected_block)
        else:
            raise ValueError(f"Unknown mode: {mode}")
        eq = equity_curve(sample, starting_balance)
        returns[i] = (eq[-1] - starting_balance) / starting_balance * 100.0
        dds[i] = max_drawdown_pct(eq, starting_balance)
        streaks[i] = max_consecutive_losses(sample)
        recoveries[i] = recovery_time(sample, starting_balance)
        pfs[i] = _profit_factor(sample)
        sharpes[i] = _sharpe(sample)
        if keep_paths and i < keep_paths:
            paths.append(eq)
    return {
        "returns": returns,
        "dds": dds,
        "streaks": streaks,
        "recoveries": recoveries,
        "pfs": pfs,
        "sharpes": sharpes,
        "paths": paths,
    }


def profit_probability(returns: np.ndarray) -> float:
    return float((returns > 0).mean() * 100.0)


def make_plots(
    sim: dict,
    baseline: dict,
    starting_balance: float,
    out_path: Path,
    title: str,
) -> Path:
    """Equity fan chart (P5/P50/P95 of sampled paths) + return / drawdown /
    loss-streak histograms. One PNG, intended for the README / write-up."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))

    ax = axes[0, 0]
    paths = sim.get("paths") or []
    if paths:
        arr = np.array(paths)
        x = np.arange(arr.shape[1])
        ax.fill_between(
            x,
            np.percentile(arr, 5, axis=0),
            np.percentile(arr, 95, axis=0),
            alpha=0.25,
            label="P5–P95",
        )
        ax.plot(x, np.percentile(arr, 50, axis=0), lw=1.6, label="median")
        ax.axhline(starting_balance, color="grey", ls="--", lw=0.8)
        ax.set_title("Equity fan chart (block bootstrap)")
        ax.set_xlabel("trade #")
        ax.set_ylabel("equity $")
        ax.legend()

    ax = axes[0, 1]
    ax.hist(sim["returns"], bins=50, alpha=0.75)
    ax.axvline(baseline["total_return_pct"], color="red", ls="--", label="as-traded")
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_title("Return % distribution")
    ax.set_xlabel("return %")
    ax.legend()

    ax = axes[1, 0]
    ax.hist(sim["dds"], bins=50, alpha=0.75, color="darkorange")
    ax.axvline(baseline["max_dd_pct"], color="red", ls="--", label="as-traded")
    ax.set_title("Max drawdown % distribution")
    ax.set_xlabel("max DD %")
    ax.legend()

    ax = axes[1, 1]
    smax = int(sim["streaks"].max()) if len(sim["streaks"]) else 1
    ax.hist(sim["streaks"], bins=range(smax + 2), alpha=0.75, color="firebrick")
    ax.set_title("Max consecutive losses")
    ax.set_xlabel("streak length")

    fig.suptitle(title, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def worst_case_drop_top_n(pnls: np.ndarray, starting_balance: float, n_drops: list) -> dict:
    log("\nWORST-CASE — drop top-N winning trades")
    log("  Tests: if the biggest winners hadn't happened, would the edge survive?")
    sorted_desc = np.sort(pnls)[::-1]
    results = []
    for n in n_drops:
        if n >= len(pnls):
            continue
        remaining = sorted_desc[n:]
        stats = compute_stats(remaining, starting_balance)
        dropped_sum = sorted_desc[:n].sum()
        log(
            f"  drop top {n:>2}: Return={stats['total_return_pct']:+7.2f}%  "
            f"DD={stats['max_dd_pct']:5.2f}%  "
            f"WR={stats['win_rate_pct']:5.2f}%  "
            f"PF={stats['profit_factor']:5.2f}  "
            f"(removed ${dropped_sum:+.2f})"
        )
        results.append({"n": n, "stats": stats, "dropped_sum": dropped_sum})
    return {"results": results}


def rolling_windows(pnls: np.ndarray, starting_balance: float, n_windows: int = 3) -> dict:
    log(f"\nROLLING WINDOWS — split into {n_windows} sequential periods")
    log("  Tests: are the metrics consistent through time, or concentrated?")
    if len(pnls) < n_windows:
        log(f"  Not enough trades ({len(pnls)}) for {n_windows} windows.")
        return {"windows": []}
    chunks = np.array_split(pnls, n_windows)
    cumulative_balance = starting_balance
    windows = []
    for i, chunk in enumerate(chunks, 1):
        stats = compute_stats(chunk, cumulative_balance)
        log(
            f"  window {i}/{n_windows} ({len(chunk):>2} trades): "
            f"Return={stats['total_return_pct']:+7.2f}%  "
            f"DD={stats['max_dd_pct']:5.2f}%  "
            f"WR={stats['win_rate_pct']:5.2f}%  "
            f"PF={stats['profit_factor']:5.2f}  "
            f"(start ${cumulative_balance:,.2f} → end ${stats['final_balance']:,.2f})"
        )
        windows.append(
            {
                "idx": i,
                "n_trades": len(chunk),
                "start": cumulative_balance,
                "end": stats["final_balance"],
                "stats": stats,
            }
        )
        cumulative_balance = stats["final_balance"]
    return {"windows": windows}


def streak_stop_simulation(
    pnls: np.ndarray,
    starting_balance: float,
    runs: int,
    rng: np.random.Generator,
    streak_limits: list,
) -> dict:
    log("\nSTREAK-STOP — what if you stopped after N consecutive losses?")
    log(f"  Bootstrap {runs:,} runs. A 'stop' ends trading at the N-th consecutive loss.")
    n = len(pnls)
    rows = []
    for limit in streak_limits:
        returns = np.empty(runs)
        stop_count = 0
        trades_taken = np.empty(runs)
        for i in range(runs):
            sample = rng.choice(pnls, size=n, replace=True)
            streak = 0
            eq = starting_balance
            trades_used = 0
            stopped = False
            for p in sample:
                eq += p
                trades_used += 1
                if p < 0:
                    streak += 1
                    if streak >= limit:
                        stopped = True
                        break
                else:
                    streak = 0
            returns[i] = (eq - starting_balance) / starting_balance * 100.0
            trades_taken[i] = trades_used
            if stopped:
                stop_count += 1
        row = {
            "limit": limit,
            "p5": float(np.percentile(returns, 5)),
            "p50": float(np.percentile(returns, 50)),
            "p95": float(np.percentile(returns, 95)),
            "stop_rate": stop_count / runs * 100,
            "avg_trades": float(trades_taken.mean()),
        }
        rows.append(row)
        log(
            f"  stop@{limit} losses: "
            f"Return p5={row['p5']:+7.2f}%  "
            f"p50={row['p50']:+7.2f}%  "
            f"p95={row['p95']:+7.2f}%  "
            f"| stopped in {row['stop_rate']:5.1f}% of runs  "
            f"| avg trades taken={row['avg_trades']:.1f}/{n}"
        )
    return {"rows": rows}


def infer_strategy_name(df: pd.DataFrame, csv_name: str) -> str:
    if "Strategy" in df.columns and len(df) > 0:
        val = str(df["Strategy"].iloc[0])
        if val and val.lower() != "nan":
            return val
    return csv_name.replace(".csv", "")


def detect_strategies(df: pd.DataFrame) -> list:
    """Return unique strategy names from the CSV, sorted by trade count descending."""
    if "Strategy" not in df.columns:
        return []
    counts = df["Strategy"].value_counts()
    return list(counts.index)


def per_strategy_breakdown(df: pd.DataFrame, pool_final_balance: float) -> list:
    """For a combined CSV, compute stats for each strategy as it contributed to the pool.

    Pool contribution metrics use the actual PnL values from the combined run
    (position sizes were determined by pool balance at time of entry, so compounding
    is already baked in). Solo PF/WR are computed on the strategy's own trade set.
    """
    rows = []
    total_pnl = df["Net_PnL_$"].sum()
    for name in detect_strategies(df):
        sub = df[df["Strategy"] == name]
        pnls = sub["Net_PnL_$"].to_numpy(dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        wr = len(wins) / len(pnls) * 100.0 if len(pnls) else 0.0
        pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() != 0 else float("inf")
        pnl_sum = float(pnls.sum())
        rows.append(
            {
                "name": name,
                "trades": len(pnls),
                "pnl": pnl_sum,
                "wr": wr,
                "pf": pf,
                "share_of_total_pnl": pnl_sum / total_pnl * 100 if total_pnl else 0.0,
                "avg_win": float(wins.mean()) if len(wins) else 0.0,
                "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            }
        )
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def print_per_strategy(rows: list, pool_starting_balance: float) -> None:
    log("\nPER-STRATEGY BREAKDOWN (pool contribution)")
    log("  Each row: trades the strategy took in the combined run, with actual position")
    log("  sizes (sized on pool balance at trade time — compounding from others is baked in).")
    log("")
    header = (
        f"  {'Strategy':<28} {'Trades':>7} {'PnL $':>12} "
        f"{'PnL % init':>11} {'WR %':>7} {'PF':>6} {'Share':>7}"
    )
    log(header)
    log("  " + "-" * (len(header) - 2))
    for r in rows:
        pnl_pct = r["pnl"] / pool_starting_balance * 100
        log(
            f"  {r['name']:<28} {r['trades']:>7} {r['pnl']:>+12.2f} "
            f"{pnl_pct:>+10.2f}% {r['wr']:>6.2f}% {r['pf']:>6.2f} "
            f"{r['share_of_total_pnl']:>6.1f}%"
        )


def build_verdict(
    baseline: dict,
    boot: dict,
    worst_case: dict,
    rolling: dict,
    streak_stop: dict,
) -> list:
    lines = []
    if profit_probability(boot["returns"]) >= 95:
        lines.append(
            f"- **Bootstrap:** {profit_probability(boot['returns']):.1f}% of resamples "
            f"finish in profit — edge is robust, not outlier-driven."
        )
    else:
        lines.append(
            f"- **Bootstrap:** only {profit_probability(boot['returns']):.1f}% of resamples "
            f"profit — edge is fragile, depends on specific trades."
        )

    if worst_case["results"]:
        last = worst_case["results"][-1]
        if last["stats"]["total_return_pct"] > 0:
            lines.append(
                f"- **Worst-case:** even after removing top {last['n']} winners, "
                f"returns stay at {last['stats']['total_return_pct']:+.2f}% — "
                f"edge is well-distributed."
            )
        else:
            lines.append(
                f"- **Worst-case:** removing top {last['n']} winners kills PnL "
                f"({last['stats']['total_return_pct']:+.2f}%) — edge concentrated in outliers."
            )

    if rolling["windows"]:
        wins_all_profitable = all(w["stats"]["total_return_pct"] > 0 for w in rolling["windows"])
        if wins_all_profitable:
            lines.append(
                "- **Rolling windows:** every period profitable — consistent through time."
            )
        else:
            bad = [w["idx"] for w in rolling["windows"] if w["stats"]["total_return_pct"] <= 0]
            lines.append(
                f"- **Rolling windows:** window(s) {bad} are unprofitable — "
                f"edge is time-concentrated."
            )

    if streak_stop["rows"]:
        tightest = streak_stop["rows"][0]
        if tightest["stop_rate"] < 10:
            lines.append(
                f"- **Streak-stop:** circuit breaker at {tightest['limit']} losses "
                f"triggers in only {tightest['stop_rate']:.1f}% of runs — "
                f"safety rule adds no cost."
            )
        else:
            lines.append(
                f"- **Streak-stop:** circuit breaker at {tightest['limit']} losses "
                f"would trigger in {tightest['stop_rate']:.1f}% of runs — "
                f"use a looser threshold."
            )

    return lines


def sanitize_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", s).strip(". ")


def save_vault_note(
    vault_path: Path,
    strategy: str,
    baseline: dict,
    boot: dict,
    verdict_lines: list,
    csv_path: Path,
    runs: int,
    strategies_in_csv: list,
    per_strategy_rows: list,
    starting_balance: float,
) -> Path:
    """Write a markdown note with the full MC output to the Obsidian vault."""
    now = datetime.now()
    month_dir = now.strftime("%Y-%m")
    probability = profit_probability(boot["returns"])
    is_pool = len(strategies_in_csv) > 1

    if is_pool:
        title = (
            f"monte carlo pool of {len(strategies_in_csv)} strategies "
            f"{probability:.0f}pct profit probability "
            f"{baseline['total_return_pct']:.0f}pct return"
        )
    else:
        title = (
            f"monte carlo {strategy} {probability:.0f}pct profit probability "
            f"{baseline['win_rate_pct']:.0f}pct wr"
        )
    safe_title = sanitize_filename(title)
    note_dir = vault_path / "monte_carlo" / month_dir
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{safe_title}.md"

    tags = "[monte_carlo, robustness_test"
    if is_pool:
        tags += ", pool"
    tags += "]"

    frontmatter = [
        "---",
        f"tags: {tags}",
    ]
    if is_pool:
        frontmatter.append(f"strategies: {strategies_in_csv}")
        frontmatter.append(f"pool_size: {len(strategies_in_csv)}")
    else:
        frontmatter.append(f"strategy: {strategy}")
    frontmatter += [
        f"source_csv: {csv_path.name}",
        f"baseline_return_pct: {baseline['total_return_pct']:.2f}",
        f"baseline_max_dd_pct: {baseline['max_dd_pct']:.2f}",
        f"baseline_win_rate_pct: {baseline['win_rate_pct']:.2f}",
        f"baseline_profit_factor: {baseline['profit_factor']:.2f}",
        f"bootstrap_profit_probability: {probability:.1f}",
        f"bootstrap_return_p5: {float(np.percentile(boot['returns'], 5)):.2f}",
        f"bootstrap_return_p95: {float(np.percentile(boot['returns'], 95)):.2f}",
        f"bootstrap_dd_p95: {float(np.percentile(boot['dds'], 95)):.2f}",
        f"runs_per_mode: {runs}",
        f"created: {now.strftime('%Y-%m-%d %H:%M')}",
        "---",
        "",
    ]

    verdict_md = ["## Verdict", ""] + verdict_lines + [""]

    breakdown_md = []
    if is_pool and per_strategy_rows:
        breakdown_md = [
            "## Per-Strategy Breakdown (Pool Contribution)",
            "",
            "Each row shows the trades that strategy took within the combined pool run. "
            "Position sizes were scaled on the pool balance at trade time, so the compounding "
            "boost from other strategies is already reflected in the PnL column.",
            "",
            "| Strategy | Trades | PnL $ | PnL % init | WR % | PF | Share of pool PnL |",
            "|----------|-------:|------:|-----------:|-----:|---:|------------------:|",
        ]
        for r in per_strategy_rows:
            pnl_pct = r["pnl"] / starting_balance * 100
            pf_cell = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "∞"
            breakdown_md.append(
                f"| {r['name']} | {r['trades']} | {r['pnl']:+.2f} | "
                f"{pnl_pct:+.2f}% | {r['wr']:.2f}% | {pf_cell} | "
                f"{r['share_of_total_pnl']:.1f}% |"
            )
        breakdown_md.append("")

    raw_output = ["## Raw Output", "", "```"] + note_lines + ["```", ""]

    extra = [
        "## Source",
        f"- Trades CSV: `{csv_path}`",
        f"- Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    note_path.write_text(
        "\n".join(frontmatter + verdict_md + breakdown_md + raw_output + extra),
        encoding="utf-8",
    )
    return note_path


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo analysis of backtest trades")
    parser.add_argument("csv", type=Path, help="Path to trades CSV from run_backtest.py")
    parser.add_argument("--runs", type=int, default=10000, help="Number of simulations per mode")
    parser.add_argument(
        "--starting-balance",
        type=float,
        default=None,
        help="Starting balance (default: infer from first Balance_After - Net_PnL_$)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--note", action="store_true", help="Write a markdown note to the notes directory (--vault)"
    )
    parser.add_argument(
        "--vault", type=Path, default=DEFAULT_VAULT, help=f"Vault path (default: {DEFAULT_VAULT})"
    )
    parser.add_argument(
        "--block-len",
        type=int,
        default=0,
        help="Mean block length for block bootstrap (0 = auto ~sqrt(n))",
    )
    parser.add_argument(
        "--ruin-thresholds",
        type=str,
        default="20,30,50",
        help="Comma-separated max-DD%% thresholds for risk-of-ruin. For a "
        "cap<balance run set the cap-relative liquidation %% here.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save a Monte Carlo PNG (fan chart + distributions) next to the CSV",
    )
    parser.add_argument(
        "--pct-mode",
        action="store_true",
        help="Resample the PnL_%% column instead of Net_PnL_$ — order-independent, "
        "avoids compound/sizing inflation of $ PnL",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: file not found: {args.csv}")
        sys.exit(1)

    df = load_trades(args.csv)
    if args.pct_mode:
        if "PnL_%" not in df.columns:
            print("ERROR: --pct-mode needs a 'PnL_%' column in the CSV")
            sys.exit(1)
        pnls = df["PnL_%"].to_numpy(dtype=float)
    else:
        pnls = df["Net_PnL_$"].to_numpy(dtype=float)

    if args.pct_mode:
        starting_balance = 100.0  # equity in % terms; per-trade PnL_% are summed
    elif args.starting_balance is not None:
        starting_balance = args.starting_balance
    elif "Balance_After" in df.columns and len(df) > 0:
        starting_balance = float(df["Balance_After"].iloc[0]) - float(pnls[0])
    else:
        starting_balance = 1500.0

    baseline = compute_stats(pnls, starting_balance)
    baseline_streak = max_consecutive_losses(pnls)
    baseline_recovery = recovery_time(pnls, starting_balance)
    strategies_in_csv = detect_strategies(df)
    is_combined = len(strategies_in_csv) > 1
    strategy = (
        "pool:" + ",".join(strategies_in_csv)
        if is_combined
        else infer_strategy_name(df, args.csv.name)
    )

    log("=" * 78)
    log(f"FILE: {args.csv.name}")
    if is_combined:
        log(f"MODE: COMBINED POOL ({len(strategies_in_csv)} strategies)")
        log(f"STRATEGIES: {', '.join(strategies_in_csv)}")
    else:
        log(f"STRATEGY: {strategy}")
    log(f"TRADES: {len(pnls)}   STARTING BALANCE: ${starting_balance:,.2f}")
    log("=" * 78)
    log("\nBASELINE (as-traded)")
    log(
        f"  Total Return     : {baseline['total_return_pct']:+.2f}%  "
        f"(final ${baseline['final_balance']:,.2f})"
    )
    log(f"  Max Drawdown     : {baseline['max_dd_pct']:.2f}%")
    log(f"  Win Rate         : {baseline['win_rate_pct']:.2f}%")
    log(f"  Profit Factor    : {baseline['profit_factor']:.2f}")
    log(f"  Max Loss Streak  : {baseline_streak}")
    recovery_label = "never recovered" if baseline_recovery == -1 else f"{baseline_recovery} trades"
    log(f"  Recovery Time    : {recovery_label}")

    per_strategy_rows = []
    if is_combined:
        per_strategy_rows = per_strategy_breakdown(df, baseline["final_balance"])
        print_per_strategy(per_strategy_rows, starting_balance)

    rng = np.random.default_rng(args.seed)

    log(f"\nSHUFFLE — {args.runs:,} runs (same trades, random order)")
    shuffle = run_simulations(pnls, starting_balance, args.runs, "shuffle", rng)
    log(percentile_row(shuffle["dds"], "Max DD %:"))
    recs_valid = shuffle["recoveries"][shuffle["recoveries"] >= 0]
    if len(recs_valid) > 0:
        log(percentile_row(recs_valid.astype(float), "Recovery trades:"))
    never_recovered = (shuffle["recoveries"] == -1).mean() * 100
    log(f"  Never recovered by end: {never_recovered:.1f}% of orderings")

    log(f"\nBOOTSTRAP (IID) — {args.runs:,} runs (resample with replacement)")
    boot = run_simulations(pnls, starting_balance, args.runs, "bootstrap", rng)
    log(percentile_row(boot["returns"], "Return %:"))
    log(percentile_row(boot["dds"], "Max DD %:"))
    log(percentile_row(boot["streaks"].astype(float), "Loss streak:", fmt="8.1f"))
    log(percentile_row(boot["pfs"], "Profit factor:"))
    log(percentile_row(boot["sharpes"], "Sharpe/trade:"))
    recs_valid_b = boot["recoveries"][boot["recoveries"] >= 0]
    if len(recs_valid_b) > 0:
        log(percentile_row(recs_valid_b.astype(float), "Recovery trades:", fmt="8.1f"))
    log(f"  Profit probability    : {profit_probability(boot['returns']):.1f}%")
    log(f"  Never recovered by end: {(boot['recoveries'] == -1).mean() * 100:.1f}% of resamples")

    # Block bootstrap — preserves serial dependence (streaks, regime clusters)
    block_len = args.block_len if args.block_len > 0 else max(2, int(round(len(pnls) ** 0.5)))
    log(f"\nBLOCK BOOTSTRAP — {args.runs:,} runs (mean block {block_len}, keeps autocorrelation)")
    log("  IID bootstrap shuffles trades independently and UNDERSTATES tail risk on")
    log("  autocorrelated sequences. Block keeps streaks/clusters intact -> honest DD.")
    block = run_simulations(
        pnls,
        starting_balance,
        args.runs,
        "block",
        rng,
        expected_block=block_len,
        keep_paths=min(300, args.runs),
    )
    log(percentile_row(block["returns"], "Return %:"))
    log(percentile_row(block["dds"], "Max DD %:"))
    log(percentile_row(block["streaks"].astype(float), "Loss streak:", fmt="8.1f"))
    log(percentile_row(block["pfs"], "Profit factor:"))
    log(percentile_row(block["sharpes"], "Sharpe/trade:"))
    log(f"  Profit probability    : {profit_probability(block['returns']):.1f}%")
    dd95_iid = float(np.percentile(boot["dds"], 95))
    dd95_blk = float(np.percentile(block["dds"], 95))
    log(
        f"  DD p95: IID={dd95_iid:.2f}% vs block={dd95_blk:.2f}%  "
        f"(block = the honest tail estimate)"
    )

    # Risk of ruin — P(max DD >= threshold)
    thresholds = [float(t) for t in args.ruin_thresholds.split(",") if t.strip()]
    log("\nRISK OF RUIN — P(max DD >= threshold), via block bootstrap")
    if args.pct_mode:
        log("  (pct-mode: DD relative to the PnL_% equity base, not account balance)")
    ruin = ruin_probabilities(block["dds"], thresholds)
    for t in thresholds:
        log(f"  P(DD >= {t:>5.1f}%) = {ruin[t]:5.1f}%")

    worst_case = worst_case_drop_top_n(pnls, starting_balance, n_drops=[1, 2, 3, 5])
    rolling = rolling_windows(pnls, starting_balance, n_windows=3)
    streak_stop = streak_stop_simulation(
        pnls, starting_balance, runs=min(args.runs, 5000), rng=rng, streak_limits=[2, 3, 5]
    )

    log("\n" + "=" * 78)

    verdict_lines = build_verdict(baseline, boot, worst_case, rolling, streak_stop)

    if args.plot:
        plot_path = args.csv.with_suffix(".mc.png")
        try:
            make_plots(
                block, baseline, starting_balance, plot_path, title=f"Monte Carlo — {strategy}"
            )
            log(f"\nPlot saved: {plot_path}")
        except Exception as e:
            log(f"\nPlot failed: {e}")

    if args.note:
        note_path = save_vault_note(
            args.vault,
            strategy,
            baseline,
            boot,
            verdict_lines,
            args.csv,
            args.runs,
            strategies_in_csv,
            per_strategy_rows,
            starting_balance,
        )
        print(f"\nMonte Carlo note saved: {note_path}")


if __name__ == "__main__":
    main()
