"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : labeling.py
    Project     : Project-103
    Description : Triple-barrier labelling (López de Prado, Advances in Financial
                  ML, ch. 2-3). Labels an event by whichever of three barriers it
                  touches first — volatility-scaled take-profit, stop-loss, or a
                  vertical timeout — so labels stay path-aware and comparable
                  across volatility regimes. Includes EWM volatility and a CUSUM
                  event filter for selecting events.

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

import logging
from typing import Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def ewm_vol(close: pd.Series, span: int = 100) -> pd.Series:
    """EWM estimate of 1-bar log-return volatility.

    Serves as the barrier scale (``vol[t0]``) and as the CUSUM threshold so that
    barriers are wide in turbulent regimes and tight in calm ones.
    """
    log_ret = np.log(close).diff()
    return log_ret.ewm(span=span).std()


def cusum_events(close: pd.Series, threshold: Union[float, pd.Series]) -> pd.DatetimeIndex:
    """Symmetric CUSUM filter — selects bars of significant cumulative move.

    Accumulates log-returns in an up and a down counter; when either breaks
    ``threshold`` it records an event and resets that counter. ``threshold`` may
    be a scalar or a Series (adaptive, e.g. a multiple of ``ewm_vol``). Sampling
    events this way — rather than every bar — cuts overfitting to noise and
    sharply reduces label overlap (which matters for purged cross-validation).
    """
    log_ret = np.log(close).diff().to_numpy()
    if isinstance(threshold, (int, float)):
        thr = np.full(len(close), float(threshold))
    else:
        thr = threshold.reindex(close.index).to_numpy()
    idx = close.index

    events: list[pd.Timestamp] = []
    s_pos = 0.0
    s_neg = 0.0
    for i in range(1, len(log_ret)):
        r = log_ret[i]
        t = thr[i]
        if not (np.isfinite(r) and np.isfinite(t)) or t <= 0:
            continue
        s_pos = max(0.0, s_pos + r)
        s_neg = min(0.0, s_neg + r)
        if s_pos > t:
            s_pos = 0.0
            events.append(idx[i])
        elif s_neg < -t:
            s_neg = 0.0
            events.append(idx[i])

    logger.info(
        "CUSUM: %d events from %d bars (%.1f%%)",
        len(events),
        len(close),
        100.0 * len(events) / max(len(close), 1),
    )
    return pd.DatetimeIndex(events)


def triple_barrier(
    close: pd.Series,
    event_times: pd.DatetimeIndex,
    vol: pd.Series,
    pt_sl: tuple[float, float],
    horizon: int,
    min_ret: float = 0.0,
) -> pd.DataFrame:
    """Label events by the first of three barriers touched.

    For each event ``t0`` the barriers are::

        take-profit : close[t0] * (1 + pt * vol[t0])
        stop-loss   : close[t0] * (1 - sl * vol[t0])
        timeout     : ``horizon`` bars after t0

    Because the barriers scale with ``vol[t0]`` rather than a fixed number of
    points, labels are comparable across price regimes — overfit protection at
    the labelling layer itself.

    Args:
        close: close-price series (DatetimeIndex, 1m).
        event_times: candidate entry times (usually from ``cusum_events``).
        vol: barrier scale at t0 (usually ``ewm_vol``).
        pt_sl: (take-profit multiple, stop-loss multiple) of vol.
        horizon: vertical barrier, in bars.
        min_ret: dead zone — |ret| <= min_ret at timeout -> label 0.

    Returns:
        DataFrame indexed by ``t0`` with columns:
            t1       — time the first barrier was touched
            ret      — return t0 -> t1 (fraction)
            barrier  — which barrier hit: 'pt' | 'sl' | 'vert'
            label    — +1 / -1 / 0 (0 = timeout inside the dead zone)
            usd      — ret in price dollars (swing-size sanity check)
    """
    close = close.sort_index()
    arr = close.to_numpy(dtype=float)
    idx = close.index
    pos = {t: i for i, t in enumerate(idx)}
    n = len(arr)

    records: list[dict] = []
    for t0 in event_times:
        i0 = pos.get(t0)
        if i0 is None:
            continue
        v = vol.get(t0, np.nan)
        if not np.isfinite(v) or v <= 0:
            continue

        i1 = min(i0 + horizon, n - 1)
        p0 = arr[i0]
        up = p0 * (1.0 + pt_sl[0] * v)
        dn = p0 * (1.0 - pt_sl[1] * v)

        path = arr[i0 + 1 : i1 + 1]
        hit_up = int(np.argmax(path >= up)) if (path >= up).any() else None
        hit_dn = int(np.argmax(path <= dn)) if (path <= dn).any() else None

        if hit_up is not None and (hit_dn is None or hit_up <= hit_dn):
            j, barrier = i0 + 1 + hit_up, "pt"
        elif hit_dn is not None:
            j, barrier = i0 + 1 + hit_dn, "sl"
        else:
            j, barrier = i1, "vert"

        ret = arr[j] / p0 - 1.0
        if barrier == "pt":
            label = 1
        elif barrier == "sl":
            label = -1
        else:
            label = 0 if abs(ret) <= min_ret else int(np.sign(ret))

        records.append(
            {
                "t0": t0,
                "t1": idx[j],
                "ret": ret,
                "barrier": barrier,
                "label": label,
                "usd": arr[j] - p0,
            }
        )

    if not records:
        return pd.DataFrame(columns=["t1", "ret", "barrier", "label", "usd"])

    out = pd.DataFrame.from_records(records).set_index("t0")
    out.index.name = "t0"
    dist = out["label"].value_counts().to_dict()
    logger.info(
        "Labelled %d events | label distribution %s | median |usd| $%.2f",
        len(out),
        dist,
        out["usd"].abs().median(),
    )
    return out
