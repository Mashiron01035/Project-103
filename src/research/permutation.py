"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : permutation.py
    Project     : Project-103
    Description : Permutation / surrogate test — separates a real, exploitable
                  signal from one that is an artefact of a particular ordering.
                  Shuffling destroys temporal structure while preserving the
                  return distribution; if the observed statistic sits far outside
                  the shuffled null band, the structure is real (not noise).

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np


@dataclass(frozen=True)
class PermutationResult:
    """Outcome of a permutation test.

    Attributes:
        observed: the statistic on the real, ordered series.
        null_mean: mean of the statistic across shuffled surrogates.
        null_std: std of the statistic across surrogates.
        z_score: (observed - null_mean) / null_std — how many sigmas out.
        p_value: probability of a surrogate as extreme as observed (smoothed).
        n_permutations: number of surrogates drawn.
    """

    observed: float
    null_mean: float
    null_std: float
    z_score: float
    p_value: float
    n_permutations: int

    def is_significant(self, alpha: float = 0.05) -> bool:
        """True if the observed statistic beats the surrogate null at ``alpha``."""
        return self.p_value < alpha


def permutation_test(
    x: np.ndarray,
    statistic: Callable[[np.ndarray], float],
    n_permutations: int = 2000,
    alternative: str = "greater",
    rng: Optional[np.random.Generator] = None,
) -> PermutationResult:
    """Shuffle-surrogate test for structure in an ordered series.

    The null hypothesis is "the ordering carries no information": any value of
    ``statistic`` is explained by the marginal distribution alone. We sample that
    null directly by shuffling ``x`` ``n_permutations`` times and recomputing the
    statistic on each surrogate, then locate the observed value in that band.

    This is the honest test for serial structure (autocorrelation, momentum,
    mean-reversion): a backtest edge that survives shuffling was never about
    order, and an edge that vanishes was path-luck, not signal.

    Args:
        x: 1-D array (e.g. returns) whose ordering is being tested.
        statistic: maps an array to a scalar (e.g. lag-1 autocorrelation).
        n_permutations: number of shuffled surrogates.
        alternative: 'greater', 'less', or 'two-sided'.
        rng: optional numpy Generator for reproducibility.

    Returns:
        PermutationResult with the observed value, null moments, z-score and
        a (+1-smoothed) p-value.
    """
    if rng is None:
        rng = np.random.default_rng()
    x = np.asarray(x, dtype=float)
    if x.ndim != 1:
        raise ValueError("permutation_test expects a 1-D array")
    if n_permutations < 1:
        raise ValueError("n_permutations must be >= 1")
    if alternative not in ("greater", "less", "two-sided"):
        raise ValueError("alternative must be 'greater', 'less' or 'two-sided'")

    observed = float(statistic(x))

    null = np.empty(n_permutations, dtype=float)
    surrogate = x.copy()
    for i in range(n_permutations):
        rng.shuffle(surrogate)
        null[i] = statistic(surrogate)

    null_mean = float(null.mean())
    null_std = float(null.std())
    z_score = (observed - null_mean) / null_std if null_std > 0 else 0.0

    # +1 smoothing avoids p=0 (an unobserved tail is not proof of impossibility).
    if alternative == "greater":
        hits = int(np.sum(null >= observed))
    elif alternative == "less":
        hits = int(np.sum(null <= observed))
    else:  # two-sided: distance from the null centre
        hits = int(np.sum(np.abs(null - null_mean) >= abs(observed - null_mean)))
    p_value = (hits + 1) / (n_permutations + 1)

    return PermutationResult(
        observed=observed,
        null_mean=null_mean,
        null_std=null_std,
        z_score=z_score,
        p_value=p_value,
        n_permutations=n_permutations,
    )
