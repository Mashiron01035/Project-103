"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_labeling.py
    Project     : Project-103
    Description : Tests for triple-barrier labelling and its helpers — a rising
                  path must label take-profit, a falling path stop-loss, a flat
                  path a neutral timeout; CUSUM fires on trends and stays silent
                  when flat.

    SPDX-License-Identifier: AGPL-3.0

"""

import numpy as np
import pandas as pd

from src.research.labeling import cusum_events, ewm_vol, triple_barrier


def _series(values, start="2026-01-01", freq="min") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


class TestTripleBarrier:
    def test_rising_price_hits_take_profit(self):
        close = _series(100.0 + np.arange(50))  # +1 per bar
        vol = pd.Series(0.01, index=close.index)
        out = triple_barrier(close, close.index[[0, 5, 10]], vol, pt_sl=(1.0, 1.0), horizon=20)
        assert (out["barrier"] == "pt").all()
        assert (out["label"] == 1).all()

    def test_falling_price_hits_stop(self):
        close = _series(100.0 - np.arange(50))  # -1 per bar
        vol = pd.Series(0.01, index=close.index)
        out = triple_barrier(close, close.index[[0, 5, 10]], vol, pt_sl=(1.0, 1.0), horizon=20)
        assert (out["barrier"] == "sl").all()
        assert (out["label"] == -1).all()

    def test_flat_price_times_out_neutral(self):
        close = _series(np.full(50, 100.0))
        vol = pd.Series(0.01, index=close.index)
        out = triple_barrier(close, close.index[[0, 5, 10]], vol, pt_sl=(1.0, 1.0), horizon=20)
        assert (out["barrier"] == "vert").all()
        assert (out["label"] == 0).all()


class TestVolAndEvents:
    def test_ewm_vol_is_nonnegative(self):
        close = _series(100.0 + np.cumsum(np.random.default_rng(0).normal(size=200)))
        vol = ewm_vol(close, span=50).dropna()
        assert len(vol) > 0
        assert (vol >= 0).all()

    def test_cusum_fires_on_a_trend(self):
        close = _series(100.0 + np.arange(200) * 0.5)
        assert len(cusum_events(close, threshold=0.02)) > 0

    def test_cusum_silent_when_flat(self):
        close = _series(np.full(200, 100.0))
        assert len(cusum_events(close, threshold=0.02)) == 0
