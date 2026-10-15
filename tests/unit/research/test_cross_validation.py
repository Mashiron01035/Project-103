"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_cross_validation.py
    Project     : Project-103
    Description : Tests for leakage-free cross-validation — train and test stay
                  disjoint, overlapping-label samples are purged from training,
                  and walk-forward test windows always lie in the future.

    SPDX-License-Identifier: AGPL-3.0

"""

import numpy as np
import pandas as pd
import pytest

from src.research.cross_validation import PurgedKFold, purged_walk_forward_cv, walk_forward_splits


def _t1(n: int = 40, label_days: int = 2) -> pd.Series:
    """Events at daily frequency; each label window ends `label_days` later."""
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.Series(idx + pd.Timedelta(days=label_days), index=idx)


def _no_train_overlaps_test(train_idx, test_idx, t1: pd.Series) -> bool:
    test_start = t1.index[test_idx[0]]
    t1_test_max = t1.iloc[test_idx].max()
    for i in train_idx:
        if t1.iloc[i] >= test_start and t1.index[i] <= t1_test_max:
            return False
    return True


class TestPurgedKFold:
    def test_train_and_test_are_disjoint(self):
        t1 = _t1()
        x = pd.DataFrame(np.arange(len(t1)), index=t1.index)
        for train_idx, test_idx in PurgedKFold(4, t1, embargo_pct=0.05).split(x):
            assert set(train_idx).isdisjoint(set(test_idx))

    def test_overlapping_labels_are_purged(self):
        t1 = _t1()
        x = pd.DataFrame(np.arange(len(t1)), index=t1.index)
        for train_idx, test_idx in PurgedKFold(4, t1).split(x):
            assert _no_train_overlaps_test(train_idx, test_idx, t1)

    def test_unsorted_t1_raises(self):
        idx = pd.DatetimeIndex(["2026-01-03", "2026-01-01", "2026-01-02"])
        t1 = pd.Series(idx, index=idx)
        with pytest.raises(ValueError):
            PurgedKFold(2, t1)


class TestWalkForward:
    def test_test_window_is_in_the_future(self):
        idx = pd.date_range("2026-01-01", periods=120, freq="D")
        splits = list(walk_forward_splits(idx, pd.Timedelta(days=40), pd.Timedelta(days=20)))
        assert len(splits) > 0
        for train_idx, test_idx in splits:
            assert max(train_idx) < min(test_idx)  # never train on the future


class TestPurgedWalkForward:
    def test_disjoint_future_and_purged(self):
        t1 = _t1(n=120, label_days=3)
        splits = list(
            purged_walk_forward_cv(
                t1, pd.Timedelta(days=40), pd.Timedelta(days=20), embargo_pct=0.02
            )
        )
        assert len(splits) > 0
        for train_idx, test_idx in splits:
            assert set(train_idx).isdisjoint(set(test_idx))
            assert max(train_idx) < min(test_idx)
            assert _no_train_overlaps_test(train_idx, test_idx, t1)
