"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : monte_carlo.py
    Project     : Project-103
    Description : Monte Carlo drawdown analysis. Resamples a trade-PnL series many
                  times and reports the distribution of maximum drawdown plus
                  risk-of-ruin. The block-bootstrap mode preserves serial
                  dependence (losing streaks, regime clusters) that the IID
                  bootstrap destroys — and which is exactly what understates tail
                  risk on autocorrelated trade sequences.

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


def equity_curve(pnls: np.ndarray, starting_balance: float) -> np.ndarray:
    """Running balance = starting_balance + cumulative PnL."""
    return starting_balance + np.cumsum(pnls)


def max_drawdown_pct(equity: np.ndarray, starting_balance: float) -> float:
    """Largest peak-to-trough decline of the equity curve, in percent (positive)."""
    full = np.concatenate([[starting_balance], equity])
    running_peak = np.maximum.accumulate(full)
    drawdown = (full - running_peak) / running_peak * 100.0
    return float(-drawdown.min())


def block_bootstrap_sample(
    pnls: np.ndarray, rng: np.random.Generator, expected_block: int
) -> np.ndarray:
    """Stationary (circular) block bootstrap.

    Resamples contiguous blocks of random geometric length (mean = expected_block)
    so serial dependence — losing streaks, regime clusters — is preserved. Plain
    IID resampling draws trades independently and destroys that structure, which
    understates tail risk on autocorrelated trade sequences.
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


def _resample(pnls: np.ndarray, mode: str, rng: np.random.Generator, expected_block: int):
    if mode == "shuffle":
        return rng.permutation(pnls)
    if mode == "iid":
        return rng.choice(pnls, size=len(pnls), replace=True)
    if mode == "block":
        return block_bootstrap_sample(pnls, rng, expected_block)
    raise ValueError(f"Unknown mode: {mode!r} (use 'block', 'iid' or 'shuffle')")


@dataclass(frozen=True)
class DrawdownMC:
    """Distribution of maximum drawdown across Monte Carlo resamples.

    Drawdowns are percentages of equity (positive numbers). ``ruin`` maps each
    requested threshold to P(max drawdown >= threshold), in percent.
    """

    runs: int
    mode: str
    dd_mean: float
    dd_p50: float
    dd_p95: float
    dd_p99: float
    dd_max: float
    ruin: Dict[float, float] = field(default_factory=dict)
    samples: Optional[np.ndarray] = None


def monte_carlo_dd(
    pnls: np.ndarray,
    starting_balance: float,
    runs: int = 10_000,
    mode: str = "block",
    expected_block: Optional[int] = None,
    ruin_thresholds: Optional[List[float]] = None,
    rng: Optional[np.random.Generator] = None,
) -> DrawdownMC:
    """Monte Carlo distribution of maximum drawdown for a PnL series.

    Each run resamples the trade PnLs (preserving streaks under ``mode='block'``),
    rebuilds the equity curve and records its maximum drawdown. The spread of
    those drawdowns — not the single historical one — is what a position size
    must survive.

    Args:
        pnls: per-trade PnL in account currency.
        starting_balance: balance the equity curve starts from.
        runs: number of resamples.
        mode: 'block' (preserves streaks), 'iid', or 'shuffle'.
        expected_block: mean block length for block mode (default ~sqrt(n)).
        ruin_thresholds: drawdown % levels to report P(maxDD >= level) for.
        rng: optional numpy Generator for reproducibility.

    Returns:
        DrawdownMC with mean / p50 / p95 / p99 / max drawdown, risk-of-ruin per
        threshold, and the raw drawdown samples.
    """
    if rng is None:
        rng = np.random.default_rng()
    pnls = np.asarray(pnls, dtype=float)
    if pnls.ndim != 1 or len(pnls) == 0:
        raise ValueError("pnls must be a non-empty 1-D array")
    if runs < 1:
        raise ValueError("runs must be >= 1")
    if expected_block is None:
        expected_block = max(1, int(round(np.sqrt(len(pnls)))))

    dds = np.empty(runs, dtype=float)
    for i in range(runs):
        sample = _resample(pnls, mode, rng, expected_block)
        dds[i] = max_drawdown_pct(equity_curve(sample, starting_balance), starting_balance)

    ruin = {}
    if ruin_thresholds:
        ruin = {float(t): float((dds >= t).mean() * 100.0) for t in ruin_thresholds}

    return DrawdownMC(
        runs=runs,
        mode=mode,
        dd_mean=float(dds.mean()),
        dd_p50=float(np.percentile(dds, 50)),
        dd_p95=float(np.percentile(dds, 95)),
        dd_p99=float(np.percentile(dds, 99)),
        dd_max=float(dds.max()),
        ruin=ruin,
        samples=dds,
    )
