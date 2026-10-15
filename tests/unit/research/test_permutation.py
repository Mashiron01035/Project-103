"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_permutation.py
    Project     : Project-103
    Description : Tests for the permutation / surrogate test — it must flag real
                  serial structure and clear genuine IID noise, with a smoothed,
                  reproducible p-value.

    SPDX-License-Identifier: AGPL-3.0

"""

import numpy as np
import pytest

from src.research.permutation import permutation_test


def acf1(x: np.ndarray) -> float:
    """Lag-1 autocorrelation — a statistic the ordering genuinely drives."""
    x = x - x.mean()
    denom = float(np.sum(x * x))
    return float(np.sum(x[:-1] * x[1:]) / denom) if denom > 0 else 0.0


def _ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal()
    return x


class TestDetectsStructure:
    def test_autocorrelation_is_flagged(self):
        x = _ar1(500, phi=0.7, rng=np.random.default_rng(0))
        res = permutation_test(x, acf1, n_permutations=1000, rng=np.random.default_rng(1))
        assert res.observed > 0.3  # real AR(1) memory
        assert res.z_score > 5  # far outside the shuffled band
        assert res.is_significant(0.01)

    def test_iid_noise_is_cleared(self):
        x = np.random.default_rng(2).normal(size=500)
        res = permutation_test(
            x, acf1, n_permutations=1000, alternative="two-sided", rng=np.random.default_rng(3)
        )
        assert abs(res.z_score) < 3  # indistinguishable from its own shuffles
        assert not res.is_significant(0.01)


class TestMechanics:
    def test_p_value_is_smoothed(self):
        x = _ar1(300, phi=0.9, rng=np.random.default_rng(4))
        res = permutation_test(x, acf1, n_permutations=200, rng=np.random.default_rng(5))
        # +1 smoothing -> p can never be exactly 0.
        assert res.p_value >= 1 / (200 + 1)

    def test_reproducible_under_seed(self):
        x = np.random.default_rng(6).normal(size=200)
        a = permutation_test(x, acf1, n_permutations=200, rng=np.random.default_rng(9))
        b = permutation_test(x, acf1, n_permutations=200, rng=np.random.default_rng(9))
        assert a == b

    def test_invalid_alternative_raises(self):
        with pytest.raises(ValueError):
            permutation_test(np.arange(10.0), acf1, alternative="bogus")
