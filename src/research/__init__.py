"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : __init__.py
    Project     : Project-103
    Description : Research-rigor toolkit — the statistical machinery used to tell
                  a real, exploitable edge from overfit. Permutation/surrogate
                  tests, triple-barrier labelling, leakage-free (purged) cross-
                  validation, and Monte Carlo drawdown / risk-of-ruin.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.research.cross_validation import PurgedKFold, purged_walk_forward_cv, walk_forward_splits
from src.research.labeling import cusum_events, ewm_vol, triple_barrier
from src.research.monte_carlo import DrawdownMC, equity_curve, max_drawdown_pct, monte_carlo_dd
from src.research.permutation import PermutationResult, permutation_test

__all__ = [
    # permutation / surrogate
    "permutation_test",
    "PermutationResult",
    # labelling
    "triple_barrier",
    "ewm_vol",
    "cusum_events",
    # leakage-free cross-validation
    "PurgedKFold",
    "walk_forward_splits",
    "purged_walk_forward_cv",
    # monte carlo drawdown
    "monte_carlo_dd",
    "DrawdownMC",
    "max_drawdown_pct",
    "equity_curve",
]
