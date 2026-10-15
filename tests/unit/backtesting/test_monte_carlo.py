"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_monte_carlo.py
    Project     : Project-103
    Description : Correctness of the Monte Carlo robustness primitives — drawdown,
                  loss streaks, recovery time, profit factor, Sharpe, block
                  bootstrap and risk-of-ruin. The headline test asserts the
                  permutation invariant: reshuffling trades preserves total
                  return but moves path-dependent risk (drawdown).

    SPDX-License-Identifier: AGPL-3.0

"""

import numpy as np
import pytest

from scripts.monte_carlo_analysis import (
    _profit_factor,
    _sharpe,
    block_bootstrap_sample,
    compute_stats,
    equity_curve,
    max_consecutive_losses,
    max_drawdown_pct,
    profit_probability,
    recovery_time,
    ruin_probabilities,
    run_simulations,
)


class TestEquityAndDrawdown:
    def test_equity_curve_is_cumulative_sum(self):
        eq = equity_curve(np.array([10.0, -5.0, 20.0]), starting_balance=100.0)
        assert np.allclose(eq, [110.0, 105.0, 125.0])

    def test_max_drawdown_known_curve(self):
        # 100 -> 150 -> 120: peak 150, trough 120 => 20% drawdown.
        eq = equity_curve(np.array([50.0, -30.0]), 100.0)
        assert max_drawdown_pct(eq, 100.0) == pytest.approx(20.0)

    def test_monotonic_up_has_no_drawdown(self):
        eq = equity_curve(np.array([10.0, 10.0, 10.0]), 100.0)
        assert max_drawdown_pct(eq, 100.0) == pytest.approx(0.0)


class TestStreaksAndRecovery:
    def test_max_consecutive_losses(self):
        assert max_consecutive_losses(np.array([-1.0, -2.0, 3.0, -1.0, -1.0, -1.0, 5.0])) == 3

    def test_no_losses_means_zero_streak(self):
        assert max_consecutive_losses(np.array([1.0, 2.0, 3.0])) == 0

    def test_recovery_time_counts_trades_back_to_peak(self):
        # -20 (trough) then +5, +20 -> regains the 100 peak two trades later.
        assert recovery_time(np.array([-20.0, 5.0, 20.0]), 100.0) == 2

    def test_recovery_time_zero_when_no_drawdown(self):
        assert recovery_time(np.array([10.0, 10.0]), 100.0) == 0

    def test_recovery_time_minus_one_when_never_recovers(self):
        assert recovery_time(np.array([-50.0]), 100.0) == -1


class TestProfitFactorAndSharpe:
    def test_profit_factor_known(self):
        assert _profit_factor(np.array([10.0, 10.0, -5.0])) == pytest.approx(4.0)

    def test_profit_factor_infinite_without_losses(self):
        assert _profit_factor(np.array([10.0, 5.0])) == float("inf")

    def test_sharpe_needs_at_least_two_trades(self):
        assert _sharpe(np.array([5.0])) == 0.0

    def test_sharpe_zero_for_constant_pnl(self):
        # No dispersion -> undefined risk-adjusted return -> defined as 0.
        assert _sharpe(np.array([5.0, 5.0, 5.0])) == 0.0


class TestBlockBootstrap:
    """The resampler must keep length and only ever draw from the real trade pool."""

    def test_preserves_length(self):
        pnls = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        out = block_bootstrap_sample(pnls, np.random.default_rng(0), expected_block=2)
        assert len(out) == len(pnls)

    def test_only_resamples_real_trades(self):
        pnls = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        out = block_bootstrap_sample(pnls, np.random.default_rng(0), expected_block=2)
        assert np.isin(out, pnls).all()

    def test_is_deterministic_under_fixed_seed(self):
        pnls = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        a = block_bootstrap_sample(pnls, np.random.default_rng(7), expected_block=2)
        b = block_bootstrap_sample(pnls, np.random.default_rng(7), expected_block=2)
        assert np.array_equal(a, b)


class TestRiskOfRuin:
    def test_probabilities_known_values(self):
        dds = np.array([10.0, 20.0, 30.0, 40.0])
        probs = ruin_probabilities(dds, [15, 25, 35])
        assert probs[15] == pytest.approx(75.0)
        assert probs[25] == pytest.approx(50.0)
        assert probs[35] == pytest.approx(25.0)

    def test_probability_is_monotonic_in_threshold(self):
        dds = np.array([5.0, 12.0, 18.0, 25.0, 33.0])
        thresholds = [10, 20, 30]
        probs = ruin_probabilities(dds, thresholds)
        values = [probs[t] for t in thresholds]
        # A higher drawdown bar can only be cleared less often.
        assert values == sorted(values, reverse=True)


class TestPermutationInvariant:
    """The signature property of a permutation test: order is the only thing changed."""

    def test_shuffle_preserves_total_return_but_moves_drawdown(self):
        pnls = np.array([5.0, -3.0, 8.0, -2.0, 4.0])  # net +12
        sim = run_simulations(
            pnls, starting_balance=1000.0, runs=50, mode="shuffle", rng=np.random.default_rng(0)
        )
        # Reordering the same trades always ends at the same equity -> identical return.
        assert np.allclose(sim["returns"], sim["returns"][0])
        assert sim["returns"][0] == pytest.approx(1.2)  # 12 / 1000
        # But the path differs, so drawdown is genuinely distributed.
        assert sim["dds"].max() > sim["dds"].min()


class TestComputeStats:
    def test_summary_metrics(self):
        stats = compute_stats(np.array([10.0, -5.0, 10.0, -5.0]), starting_balance=100.0)
        assert stats["total_return_pct"] == pytest.approx(10.0)
        assert stats["win_rate_pct"] == pytest.approx(50.0)
        assert stats["profit_factor"] == pytest.approx(2.0)
        assert stats["final_balance"] == pytest.approx(110.0)

    def test_profit_probability(self):
        assert profit_probability(np.array([1.0, -1.0, 2.0, -3.0, 5.0])) == pytest.approx(60.0)
