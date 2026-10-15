"""Tests for one-day VaR/ES models and their backtests."""

import numpy as np
import pytest
from scipy import integrate, stats

from src.research.var import (
    ForecastConfig,
    backtest_table,
    christoffersen_independence,
    empirical_var_es,
    es_exceedance_test,
    fit_garch_t,
    garch_variance,
    kupiec_pof,
    normal_var_es,
    rolling_forecasts,
    student_t_var_es,
    traffic_light_red_share,
)


class TestDistributions:
    def test_normal_matches_textbook(self):
        var, es = normal_var_es(0.975)
        assert var == pytest.approx(1.95996, abs=1e-4)
        assert es == pytest.approx(2.33780, abs=1e-4)

    def test_student_t_es_matches_numerical_integral(self):
        nu, q = 5.0, 0.975
        var, es = student_t_var_es(q, nu)
        scale = np.sqrt((nu - 2) / nu)
        tail, _ = integrate.quad(lambda x: x * stats.t.pdf(x, nu), var / scale, np.inf)
        assert es == pytest.approx(tail / (1 - q) * scale, rel=1e-6)

    def test_student_t_converges_to_normal(self):
        assert student_t_var_es(0.99, 1e6)[0] == pytest.approx(normal_var_es(0.99)[0], rel=1e-4)

    def test_empirical_es_at_least_var(self):
        x = np.random.default_rng(1).standard_t(4, 5000)
        var, es = empirical_var_es(x, 0.99)
        assert es >= var
        assert var == pytest.approx(np.quantile(x, 0.99))


class TestCoverageTests:
    def test_kupiec_accepts_exact_rate(self):
        hits = np.zeros(1000, dtype=bool)
        hits[::100] = True  # exactly 1%
        lr, p = kupiec_pof(hits, 0.99)
        assert lr == pytest.approx(0.0, abs=1e-9)
        assert p == pytest.approx(1.0)

    def test_kupiec_rejects_double_rate(self):
        hits = np.zeros(2000, dtype=bool)
        hits[::50] = True  # 2% instead of 1%
        assert kupiec_pof(hits, 0.99)[1] < 0.001

    def test_kupiec_zero_exceptions(self):
        lr, p = kupiec_pof(np.zeros(1000, dtype=bool), 0.99)
        assert lr == pytest.approx(-2 * 1000 * np.log(0.99))
        assert 0 < p < 0.01

    def test_independence_flags_clustered_exceptions(self):
        spread = np.zeros(1000, dtype=bool)
        spread[::100] = True
        clustered = np.zeros(1000, dtype=bool)
        clustered[500:510] = True
        assert christoffersen_independence(spread)[1] > 0.5
        _, p, pairs = christoffersen_independence(clustered)
        assert p < 0.001
        assert pairs == 9

    def test_traffic_light(self):
        hits = np.zeros(500, dtype=bool)
        hits[100:110] = True  # 10 exceptions inside every window covering them
        share = traffic_light_red_share(hits)
        assert 0 < share < 1
        assert traffic_light_red_share(np.zeros(500, dtype=bool)) == 0.0


class TestEsExceedance:
    def test_correct_es_not_rejected(self):
        rng = np.random.default_rng(3)
        loss = rng.standard_normal(20000)
        var, es = normal_var_es(0.975)
        ratio, p = es_exceedance_test(loss, np.full_like(loss, var), np.full_like(loss, es))
        assert ratio == pytest.approx(1.0, abs=0.03)
        assert p > 0.05

    def test_understated_es_rejected(self):
        rng = np.random.default_rng(4)
        loss = rng.standard_t(3, 20000) / np.sqrt(3)  # fat tails, unit variance
        var, es = normal_var_es(0.975)  # normal model underestimates the tail
        ratio, p = es_exceedance_test(loss, np.full_like(loss, var), np.full_like(loss, es))
        assert ratio > 1.0
        assert p < 0.01


class TestGarch:
    def test_recovers_parameters(self):
        rng = np.random.default_rng(7)
        omega, alpha, beta, nu, n = 0.02, 0.08, 0.90, 6.0, 4000
        h, x = omega / (1 - alpha - beta), np.empty(n)
        for t in range(n):
            x[t] = np.sqrt(h * (nu - 2) / nu) * rng.standard_t(nu)
            h = omega + alpha * x[t] ** 2 + beta * h
        est = fit_garch_t(x)
        assert est[1] == pytest.approx(alpha, abs=0.03)
        assert est[2] == pytest.approx(beta, abs=0.04)
        assert est[1] + est[2] == pytest.approx(alpha + beta, abs=0.02)

    def test_variance_recursion(self):
        h = garch_variance(np.array([1.0, -2.0]), 0.1, 0.1, 0.8, 1.0)
        assert h.tolist() == pytest.approx([1.0, 0.1 + 0.1 + 0.8, 0.1 + 0.4 + 0.8 * 1.0])


class TestRollingForecasts:
    @pytest.fixture(scope="class")
    def forecasts(self):
        rng = np.random.default_rng(11)
        n = 900
        vol = np.where(np.arange(n) < 600, 1.0, 2.0)  # volatility regime shift
        r = vol * rng.standard_t(6, n) / np.sqrt(1.5)
        rv = (vol**2) * np.exp(rng.normal(0, 0.3, n))
        cfg = ForecastConfig(warmup=300, refit=50, window=300)
        return rolling_forecasts(r, rv, "long", cfg), r

    def test_shape_and_columns(self, forecasts):
        fc, r = forecasts
        assert len(fc) == len(r) - 300
        assert {"HS250_VaR99", "GARCH-t_ES975", "HAR-FHS_VaR975", "loss"} <= set(fc.columns)
        assert not fc.isna().any().any()

    def test_es_beyond_var(self, forecasts):
        fc, _ = forecasts
        for m in ("HS250", "EWMA-N", "GARCH-t", "FHS-GARCH", "HAR-FHS"):
            assert (fc[f"{m}_ES975"] >= fc[f"{m}_VaR975"] - 1e-9).all()
            assert (fc[f"{m}_VaR99"] >= fc[f"{m}_VaR975"] - 1e-9).all()

    def test_forecasts_are_causal(self):
        rng = np.random.default_rng(5)
        r = rng.standard_normal(700)
        cfg = ForecastConfig(warmup=300, refit=50, window=300)
        base = rolling_forecasts(r, None, "long", cfg)
        shocked = r.copy()
        shocked[650] = -50.0  # a crash on day 650 must not move forecasts up to day 650
        after = rolling_forecasts(shocked, None, "long", cfg)
        cols = [c for c in base.columns if c != "loss"]
        upto = slice(0, 650 - 300 + 1)
        assert np.allclose(base[cols].iloc[upto], after[cols].iloc[upto])

    def test_conditional_models_react_to_regime_shift(self, forecasts):
        fc, _ = forecasts
        late = fc.iloc[-100:]
        assert late["EWMA-N_VaR99"].mean() > fc.iloc[:200]["EWMA-N_VaR99"].mean() * 1.5

    def test_backtest_table(self, forecasts):
        fc, _ = forecasts
        tab = backtest_table(fc)
        assert set(tab.index) == {"HS250", "HS500", "EWMA-N", "GARCH-t", "FHS-GARCH", "HAR-FHS"}
        assert (tab["exc99"] <= tab["exc975"]).all()
