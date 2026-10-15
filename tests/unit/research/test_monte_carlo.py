"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_monte_carlo.py
    Project     : Project-103
    Description : Tests for the Monte Carlo drawdown engine — all-winner series
                  show no drawdown, the drawdown percentiles are ordered, risk-of-
                  ruin is monotone in the threshold, and runs are reproducible.

    SPDX-License-Identifier: AGPL-3.0

"""

import numpy as np
import pytest

from src.research.monte_carlo import monte_carlo_dd

LOSSY = np.array([10.0, -8.0, 12.0, -9.0, 7.0, -5.0, 11.0, -10.0])


class TestMonteCarloDrawdown:
    def test_all_winners_have_no_drawdown(self):
        res = monte_carlo_dd(
            np.array([5.0, 5.0, 5.0, 5.0]),
            100.0,
            runs=200,
            mode="iid",
            rng=np.random.default_rng(0),
        )
        assert res.dd_max == pytest.approx(0.0)

    def test_percentiles_are_ordered(self):
        res = monte_carlo_dd(LOSSY, 1000.0, runs=500, mode="block", rng=np.random.default_rng(1))
        assert res.dd_p50 <= res.dd_p95 <= res.dd_p99 <= res.dd_max

    def test_risk_of_ruin_is_monotone_in_threshold(self):
        res = monte_carlo_dd(
            LOSSY,
            1000.0,
            runs=500,
            mode="block",
            ruin_thresholds=[1, 2, 3],
            rng=np.random.default_rng(2),
        )
        vals = [res.ruin[1.0], res.ruin[2.0], res.ruin[3.0]]
        assert vals == sorted(vals, reverse=True)  # a higher bar is cleared less often
        assert all(0.0 <= v <= 100.0 for v in vals)

    def test_reproducible_under_seed(self):
        a = monte_carlo_dd(LOSSY, 1000.0, runs=200, rng=np.random.default_rng(7))
        b = monte_carlo_dd(LOSSY, 1000.0, runs=200, rng=np.random.default_rng(7))
        assert np.array_equal(a.samples, b.samples)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            monte_carlo_dd(LOSSY, 1000.0, runs=10, mode="bogus")

    def test_empty_pnls_raises(self):
        with pytest.raises(ValueError):
            monte_carlo_dd(np.array([]), 1000.0)
