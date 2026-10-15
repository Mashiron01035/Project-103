"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 15.10.2026.
    File        : var.py
    Project     : Project-103
    Description : One-day Value-at-Risk / Expected Shortfall forecasting and
                  backtesting. Six rolling models (historical simulation 250/500,
                  EWMA-normal, GARCH(1,1)-t, filtered HS on GARCH, filtered HS on a
                  HAR model of realised variance) and the standard coverage tests
                  (Kupiec, Christoffersen), an exceedance test for ES and the Basel
                  traffic-light share. Everything is causal: a forecast for day t
                  uses data up to day t-1 only.

    SPDX-License-Identifier: AGPL-3.0

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import optimize, stats

MODELS = ("HS250", "HS500", "EWMA-N", "GARCH-t", "FHS-GARCH", "HAR-FHS")


# ----------------------------------------------------------------------------- distributions


def empirical_var_es(losses: np.ndarray, q: float) -> Tuple[float, float]:
    """Empirical VaR (the q-quantile of losses) and ES (mean loss at or beyond it)."""
    var = float(np.quantile(losses, q))
    return var, float(losses[losses >= var].mean())


def student_t_var_es(q: float, nu: float) -> Tuple[float, float]:
    """VaR and ES at level q of a Student-t scaled to unit variance."""
    scale = np.sqrt((nu - 2) / nu)
    z = stats.t.ppf(q, nu)
    es = stats.t.pdf(z, nu) / (1 - q) * (nu + z**2) / (nu - 1)
    return float(z * scale), float(es * scale)


def normal_var_es(q: float) -> Tuple[float, float]:
    z = stats.norm.ppf(q)
    return float(z), float(stats.norm.pdf(z) / (1 - q))


# ----------------------------------------------------------------------------- GARCH(1,1)-t


def garch_variance(x: np.ndarray, omega: float, alpha: float, beta: float, h0: float):
    """Conditional variance path; element t is the forecast for x[t] (len = len(x) + 1)."""
    h = np.empty(len(x) + 1)
    h[0] = h0
    for t in range(len(x)):
        h[t + 1] = omega + alpha * x[t] ** 2 + beta * h[t]
    return h


def _garch_t_nll(params: np.ndarray, x: np.ndarray) -> float:
    omega, alpha, beta, nu = params
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.999 or nu <= 2.05:
        return 1e10
    h = garch_variance(x, omega, alpha, beta, x.var())[:-1]
    scale = np.sqrt(h * (nu - 2) / nu)
    return float(-np.sum(stats.t.logpdf(x / scale, nu) - np.log(scale)))


def fit_garch_t(x: np.ndarray, start: Optional[np.ndarray] = None) -> np.ndarray:
    """Maximum-likelihood GARCH(1,1) with Student-t innovations: (omega, alpha, beta, nu)."""
    if start is None:
        start = np.array([0.05 * x.var(), 0.05, 0.90, 6.0])
    res = optimize.minimize(
        _garch_t_nll,
        start,
        args=(x,),
        method="Nelder-Mead",
        options={"maxiter": 4000, "xatol": 1e-6, "fatol": 1e-6},
    )
    return res.x


# ----------------------------------------------------------------------------- backtests


def kupiec_pof(hits: np.ndarray, q: float) -> Tuple[float, float]:
    """Kupiec proportion-of-failures LR test: is the exception rate equal to 1 - q?"""
    p = 1 - q
    n, x = len(hits), int(np.sum(hits))
    if x == 0:
        lr = -2 * n * np.log(1 - p)
    elif x == n:
        lr = -2 * n * np.log(p)
    else:
        ph = x / n
        lr = -2 * (
            (n - x) * np.log(1 - p) + x * np.log(p) - (n - x) * np.log(1 - ph) - x * np.log(ph)
        )
    return float(lr), float(1 - stats.chi2.cdf(lr, 1))


def christoffersen_independence(hits: np.ndarray) -> Tuple[float, float, int]:
    """Christoffersen Markov test: does an exception make another one tomorrow more likely?

    Returns (LR, p-value, number of back-to-back exceptions).
    """
    h = np.asarray(hits, dtype=int)
    a, b = h[:-1], h[1:]
    n00 = int(np.sum((a == 0) & (b == 0)))
    n01 = int(np.sum((a == 0) & (b == 1)))
    n10 = int(np.sum((a == 1) & (b == 0)))
    n11 = int(np.sum((a == 1) & (b == 1)))
    p01 = n01 / max(1, n00 + n01)
    p11 = n11 / max(1, n10 + n11)
    p = (n01 + n11) / len(a)

    def ll(k: int, prob: float) -> float:
        return k * np.log(prob) if k > 0 else 0.0

    l0 = ll(n00 + n10, 1 - p) + ll(n01 + n11, p)
    l1 = ll(n00, 1 - p01) + ll(n01, p01) + ll(n10, 1 - p11) + ll(n11, p11)
    lr = -2 * (l0 - l1)
    return float(lr), float(1 - stats.chi2.cdf(lr, 1)), n11


def es_exceedance_test(
    losses: np.ndarray,
    var: np.ndarray,
    es: np.ndarray,
    n_boot: int = 5000,
    rng: Optional[np.random.Generator] = None,
) -> Tuple[float, float]:
    """McNeil–Frey style ES test on VaR exceptions.

    On days the loss exceeds VaR, loss / ES should average 1. Returns that mean
    ratio and a one-sided bootstrap p-value for "ES is understated" (ratio > 1).
    """
    if rng is None:
        rng = np.random.default_rng(0)
    hit = losses > var
    if hit.sum() < 2:
        return float("nan"), float("nan")
    ratio = losses[hit] / es[hit]
    excess = ratio - 1.0
    boot = np.array([rng.choice(excess, len(excess)).mean() for _ in range(n_boot)])
    p = float(np.mean(boot - excess.mean() >= excess.mean()))
    return float(ratio.mean()), p


def traffic_light_red_share(hits: np.ndarray, window: int = 250, red: int = 10) -> float:
    """Share of rolling windows in the Basel red zone (>= ``red`` 99% exceptions in 250 days)."""
    counts = pd.Series(np.asarray(hits, dtype=int)).rolling(window).sum().dropna()
    return float((counts >= red).mean()) if len(counts) else float("nan")


# ----------------------------------------------------------------------------- forecasts


@dataclass(frozen=True)
class ForecastConfig:
    warmup: int = 500  # first forecast day
    refit: int = 20  # re-estimate parameters every N days
    window: int = 1000  # estimation window for GARCH / HAR
    ewma_lambda: float = 0.94


def rolling_forecasts(
    returns: np.ndarray,
    realised_var: Optional[np.ndarray] = None,
    side: str = "long",
    cfg: ForecastConfig = ForecastConfig(),
) -> pd.DataFrame:
    """One-day-ahead VaR99, VaR97.5 and ES97.5 of every model, as positive losses.

    Args:
        returns: daily log returns in percent.
        realised_var: intraday realised variance in percent^2 (enables HAR-FHS).
        side: 'long' (loss = -return) or 'short' (loss = +return).

    Returns:
        DataFrame indexed by day (warmup .. end) with columns ``<model>_<measure>``
        and ``loss``.
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    sign = -1.0 if side == "long" else 1.0
    loss = sign * r
    out: Dict[str, np.ndarray] = {
        f"{m}_{k}": np.full(n, np.nan) for m in MODELS for k in ("VaR99", "VaR975", "ES975")
    }

    def put(model: str, t: int, v99: float, v975: float, e975: float) -> None:
        out[f"{model}_VaR99"][t] = v99
        out[f"{model}_VaR975"][t] = v975
        out[f"{model}_ES975"][t] = e975

    # historical simulation and EWMA-normal: no fitting
    ew = np.empty(n + 1)
    ew[0] = r[: cfg.warmup].var()
    for t in range(n):
        ew[t + 1] = cfg.ewma_lambda * ew[t] + (1 - cfg.ewma_lambda) * r[t] ** 2
    z99, _ = normal_var_es(0.99)
    z975, e975n = normal_var_es(0.975)
    for t in range(cfg.warmup, n):
        for name, w in (("HS250", 250), ("HS500", 500)):
            past = loss[max(0, t - w) : t]
            v99, _ = empirical_var_es(past, 0.99)
            v975, e975 = empirical_var_es(past, 0.975)
            put(name, t, v99, v975, e975)
        sd = np.sqrt(ew[t])
        put("EWMA-N", t, z99 * sd, z975 * sd, e975n * sd)

    # GARCH-t and filtered HS on GARCH residuals
    params = None
    for start in range(cfg.warmup, n, cfg.refit):
        stop = min(n, start + cfg.refit)
        lo = max(0, start - cfg.window)
        mu = r[lo:start].mean()
        x = r[lo:start] - mu
        params = fit_garch_t(x, params)
        omega, alpha, beta, nu = params
        h = garch_variance(r[:stop] - mu, omega, alpha, beta, x.var())
        z = sign * x / np.sqrt(h[lo:start])
        f99, _ = empirical_var_es(z, 0.99)
        f975, fe975 = empirical_var_es(z, 0.975)
        t99, _ = student_t_var_es(0.99, nu)
        t975, te975 = student_t_var_es(0.975, nu)
        drift = sign * mu
        for t in range(start, stop):
            sd = np.sqrt(h[t])
            put("GARCH-t", t, t99 * sd + drift, t975 * sd + drift, te975 * sd + drift)
            put("FHS-GARCH", t, f99 * sd + drift, f975 * sd + drift, fe975 * sd + drift)

    # HAR on log realised variance, scaled to close-to-close by its own residuals
    if realised_var is not None:
        lrv = np.log(np.maximum(np.asarray(realised_var, dtype=float), 1e-8))
        s = pd.Series(lrv)
        d1, w5, w22 = lrv, s.rolling(5).mean().values, s.rolling(22).mean().values
        design = np.column_stack([np.ones(n - 1), d1[:-1], w5[:-1], w22[:-1]])
        for start in range(cfg.warmup, n, cfg.refit):
            stop = min(n, start + cfg.refit)
            lo = max(22, start - cfg.window)
            b, *_ = np.linalg.lstsq(design[lo : start - 1], d1[lo + 1 : start], rcond=None)
            resid_var = np.var(d1[lo + 1 : start] - design[lo : start - 1] @ b)
            sigma = np.concatenate([[np.nan], np.sqrt(np.exp(design @ b + resid_var / 2))])
            z = sign * r[lo + 1 : start] / sigma[lo + 1 : start]
            z = z[np.isfinite(z)]
            f99, _ = empirical_var_es(z, 0.99)
            f975, fe975 = empirical_var_es(z, 0.975)
            for t in range(start, stop):
                put("HAR-FHS", t, f99 * sigma[t], f975 * sigma[t], fe975 * sigma[t])

    df = pd.DataFrame(out)
    df["loss"] = loss
    return df.iloc[cfg.warmup :].dropna(axis=1, how="all")


def backtest_table(fc: pd.DataFrame) -> pd.DataFrame:
    """Coverage and ES tests for every model in a ``rolling_forecasts`` frame."""
    loss = fc["loss"].values
    rows = []
    for m in [c[: -len("_VaR99")] for c in fc.columns if c.endswith("_VaR99")]:
        v99, v975, es = (fc[f"{m}_{k}"].values for k in ("VaR99", "VaR975", "ES975"))
        h99, h975 = loss > v99, loss > v975
        lr_pof, p_pof = kupiec_pof(h99, 0.99)
        lr_ind, p_ind, pairs = christoffersen_independence(h99)
        ratio, p_es = es_exceedance_test(loss, v975, es)
        rows.append(
            {
                "model": m,
                "exc99": int(h99.sum()),
                "rate99_%": 100 * h99.mean(),
                "p_kupiec": p_pof,
                "p_indep": p_ind,
                "p_cc": float(1 - stats.chi2.cdf(lr_pof + lr_ind, 2)),
                "pairs": pairs,
                "exc975": int(h975.sum()),
                "p_kupiec975": kupiec_pof(h975, 0.975)[1],
                "loss/ES": ratio,
                "p_es": p_es,
                "red_%": 100 * traffic_light_red_share(h99),
                "mean_VaR99": float(v99.mean()),
            }
        )
    return pd.DataFrame(rows).set_index("model")
