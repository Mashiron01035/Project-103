"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : cross_validation.py
    Project     : Project-103
    Description : Leakage-free cross-validation — purged K-Fold with embargo and
                  purged walk-forward (López de Prado, AFML ch. 7). Overlapping
                  labels let a test sample's [t0, t1] window leak into training;
                  purging drops those train samples and the embargo kills residual
                  autocorrelation after each test block. Plain KFold ignores this
                  and reports fantasy out-of-sample scores.

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

import logging
from typing import Iterator

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PurgedKFold:
    """K-Fold that purges overlapping labels and applies an embargo.

    Args:
        n_splits: number of folds.
        t1: Series whose index is the event start t0 and whose value is t1
            (the end of the label window).
        embargo_pct: fraction of the sample held out as an embargo gap after
            each test block.
    """

    def __init__(self, n_splits: int, t1: pd.Series, embargo_pct: float = 0.01) -> None:
        if not t1.index.is_monotonic_increasing:
            raise ValueError("t1 must be sorted by t0")
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        self.n_splits = n_splits
        self.t1 = t1
        self.embargo_pct = embargo_pct

    def split(self, x: pd.DataFrame) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        if not x.index.equals(self.t1.index):
            raise ValueError("X index must match t1 index")

        indices = np.arange(len(x))
        embargo = int(len(x) * self.embargo_pct)

        for fold in np.array_split(indices, self.n_splits):
            test_idx = fold
            t0_test = self.t1.index[test_idx[0]]
            t1_test_max = self.t1.iloc[test_idx].max()

            # train = everything except the test block, minus any sample whose
            # label window [t0, t1] overlaps the test span, minus the embargo tail.
            train_mask = np.ones(len(x), dtype=bool)
            train_mask[test_idx] = False

            ends_in_test = self.t1 >= t0_test  # label still open at/after test start
            starts_in_test = self.t1.index <= t1_test_max  # event began before test end
            overlap = (ends_in_test & starts_in_test).to_numpy()
            train_mask &= ~overlap

            embargo_end = min(test_idx[-1] + 1 + embargo, len(x))
            train_mask[test_idx[-1] + 1 : embargo_end] = False

            train_idx = indices[train_mask]
            logger.debug(
                "fold: train=%d test=%d (purged=%d)",
                len(train_idx),
                len(test_idx),
                int(overlap.sum()),
            )
            yield train_idx, test_idx


def walk_forward_splits(
    index: pd.DatetimeIndex,
    train_span: pd.Timedelta,
    test_span: pd.Timedelta,
    anchored: bool = False,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward split by calendar windows — train on the past, test the future.

    Args:
        index: DatetimeIndex of the events.
        train_span: length of the training window.
        test_span: length of the test window (and the roll step).
        anchored: True grows train from the start; False slides a fixed length.

    Yields:
        (train_idx, test_idx) positional indices. Test windows must span
        different regimes for the validation to mean anything.
    """
    index = pd.DatetimeIndex(index)
    pos = np.arange(len(index))
    start = index[0]
    end = index[-1]

    train_end = start + train_span
    while train_end + test_span <= end:
        test_end = train_end + test_span
        train_start = start if anchored else train_end - train_span

        train_idx = pos[(index >= train_start) & (index < train_end)]
        test_idx = pos[(index >= train_end) & (index < test_end)]
        if len(train_idx) and len(test_idx):
            yield train_idx, test_idx
        train_end = test_end


def purged_walk_forward_cv(
    t1: pd.Series,
    train_span: pd.Timedelta,
    test_span: pd.Timedelta,
    embargo_pct: float = 0.01,
    anchored: bool = False,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward CV with label purging and an embargo at each boundary.

    Combines the realism of walk-forward (always test the future) with the
    leakage protection of purging: in each window, training samples whose label
    window [t0, t1] reaches into the test span are dropped, as is an embargo gap
    immediately after the test block.

    Args:
        t1: Series indexed by event start t0, value = label end t1 (sorted).
        train_span: training window length.
        test_span: test window length / roll step.
        embargo_pct: embargo gap as a fraction of all events.
        anchored: True grows train from the start; False slides fixed length.

    Yields:
        (train_idx, test_idx) positional indices, train already purged.
    """
    if not t1.index.is_monotonic_increasing:
        raise ValueError("t1 must be sorted by t0")

    index = pd.DatetimeIndex(t1.index)
    t0 = index
    t1_vals = t1.to_numpy()
    n = len(index)
    embargo = int(n * embargo_pct)

    for train_idx, test_idx in walk_forward_splits(index, train_span, test_span, anchored):
        test_start = index[test_idx[0]]
        test_end_label = max(index[test_idx[-1]], t1.iloc[test_idx].max())

        # Drop train samples whose label window overlaps the test span.
        overlap = (t1_vals >= test_start) & (t0 <= test_end_label)
        keep = np.ones(n, dtype=bool)
        keep[overlap] = False

        # Embargo: silence the bars right after the test block.
        last = int(test_idx[-1])
        keep[last + 1 : min(last + 1 + embargo, n)] = False

        train_purged = np.array([i for i in train_idx if keep[i]], dtype=int)
        if len(train_purged) and len(test_idx):
            logger.debug(
                "WF fold: train=%d (purged %d) test=%d",
                len(train_purged),
                len(train_idx) - len(train_purged),
                len(test_idx),
            )
            yield train_purged, test_idx
