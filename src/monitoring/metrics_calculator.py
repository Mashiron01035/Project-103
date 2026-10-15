"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : metrics_calculator
    Project     : Project-103
    Description : Metrics calculation module for trading bot. Calculates various trading and system metrics.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List, Tuple

import numpy as np
from scipy import stats

from src.models.trade import Trade


class MetricsCalculator:
    """Calculator for trading metrics"""

    @staticmethod
    def calculate_win_rate(trades: List[Trade]) -> float:
        """
        Calculate win rate percentage

        Args:
            trades: List of trades

        Returns:
            float: Win rate percentage
        """
        if not trades:
            return 0.0

        wins = sum(1 for t in trades if t.is_profitable)
        return (wins / len(trades)) * 100

    @staticmethod
    def calculate_expectancy(trades: List[Trade]) -> float:
        """
        Calculate expected value per trade

        Args:
            trades: List of trades

        Returns:
            float: Expected value
        """
        if not trades:
            return 0.0

        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        if not winning_trades and not losing_trades:
            return 0.0

        avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = (
            abs(sum(t.pnl for t in losing_trades)) / len(losing_trades) if losing_trades else 0
        )
        win_rate = len(winning_trades) / len(trades)

        return (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    @staticmethod
    def calculate_kelly_criterion(win_rate: float, payoff_ratio: float) -> float:
        """
        Calculate Kelly Criterion for optimal position sizing

        Args:
            win_rate: Win rate (0-1)
            payoff_ratio: Average win / Average loss

        Returns:
            float: Optimal position size percentage (capped at 25%)
        """
        if payoff_ratio <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0

        # f = (p * b - q) / b
        # where p = win_rate, q = 1 - win_rate, b = payoff_ratio
        q = 1 - win_rate
        f = (win_rate * payoff_ratio - q) / payoff_ratio

        # Cap at 25% for safety
        return max(0, min(f, 0.25))

    @staticmethod
    def calculate_calmar_ratio(
        trades: List[Trade], equity_curve: List[Tuple[int, float]], period_years: float = 1
    ) -> float:
        """
        Calculate Calmar ratio (Annual return / Max drawdown)

        Args:
            trades: List of trades
            equity_curve: Equity curve [(timestamp, equity), ...]
            period_years: Period in years

        Returns:
            float: Calmar ratio
        """
        if not trades or not equity_curve or period_years <= 0:
            return 0.0

        # Annual return
        total_return = (equity_curve[-1][1] - equity_curve[0][1]) / equity_curve[0][1]
        annual_return = total_return / period_years

        # Max drawdown
        max_dd = MetricsCalculator.calculate_max_drawdown(equity_curve)

        if max_dd == 0:
            return float("inf") if annual_return > 0 else 0

        return annual_return / (max_dd / 100)

    @staticmethod
    def calculate_max_drawdown(equity_curve: List[Tuple[int, float]]) -> float:
        """
        Calculate maximum drawdown percentage

        Args:
            equity_curve: Equity curve [(timestamp, equity), ...]

        Returns:
            float: Max drawdown percentage
        """
        if not equity_curve:
            return 0.0

        peak = equity_curve[0][1]
        max_dd = 0.0

        for _, equity in equity_curve:
            peak = max(peak, equity)
            drawdown = ((peak - equity) / peak) * 100 if peak > 0 else 0
            max_dd = max(max_dd, drawdown)

        return max_dd

    @staticmethod
    def calculate_var(returns: List[float], confidence: float = 0.95) -> float:
        """
        Calculate Value at Risk (VaR)

        Args:
            returns: List of returns
            confidence: Confidence level

        Returns:
            float: VaR
        """
        if not returns:
            return 0.0

        return float(np.percentile(returns, (1 - confidence) * 100))

    @staticmethod
    def calculate_cvar(returns: List[float], confidence: float = 0.95) -> float:
        """
        Calculate Conditional Value at Risk (CVaR)

        Args:
            returns: List of returns
            confidence: Confidence level

        Returns:
            float: CVaR (expected shortfall)
        """
        if not returns:
            return 0.0

        var = MetricsCalculator.calculate_var(returns, confidence)
        return float(np.mean([r for r in returns if r <= var]))

    @staticmethod
    def calculate_information_ratio(returns: List[float], benchmark_returns: List[float]) -> float:
        """
        Calculate Information Ratio

        Args:
            returns: Strategy returns
            benchmark_returns: Benchmark returns

        Returns:
            float: Information ratio
        """
        if not returns or not benchmark_returns or len(returns) != len(benchmark_returns):
            return 0.0

        excess_returns = [r - b for r, b in zip(returns, benchmark_returns)]

        if len(excess_returns) < 2:
            return 0.0

        avg_excess = np.mean(excess_returns)
        std_excess = np.std(excess_returns)

        if std_excess == 0:
            return 0.0

        return float(avg_excess / std_excess)

    @staticmethod
    def calculate_omega_ratio(returns: List[float], threshold: float = 0) -> float:
        """
        Calculate Omega Ratio

        Args:
            returns: List of returns
            threshold: Threshold return

        Returns:
            float: Omega ratio
        """
        if not returns:
            return 0.0

        gains = sum(max(r - threshold, 0) for r in returns)
        losses = sum(max(threshold - r, 0) for r in returns)

        if losses == 0:
            return float("inf") if gains > 0 else 0

        return gains / losses

    @staticmethod
    def calculate_recovery_factor(total_pnl: float, max_drawdown: float) -> float:
        """
        Calculate Recovery Factor

        Args:
            total_pnl: Total P&L
            max_drawdown: Maximum drawdown

        Returns:
            float: Recovery factor
        """
        if max_drawdown == 0:
            return float("inf") if total_pnl > 0 else 0

        return total_pnl / max_drawdown

    @staticmethod
    def calculate_profit_factor(trades: List[Trade]) -> float:
        """
        Calculate Profit Factor (Gross profit / Gross loss)

        Args:
            trades: List of trades

        Returns:
            float: Profit factor
        """
        if not trades:
            return 0.0

        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0

        return gross_profit / gross_loss

    @staticmethod
    def calculate_sharpe_ratio(
        returns: List[float], risk_free_rate: float = 0.02, periods_per_year: int = 252
    ) -> float:
        """
        Calculate Sharpe Ratio

        Args:
            returns: List of returns
            risk_free_rate: Annual risk-free rate
            periods_per_year: Number of periods per year (252 for daily)

        Returns:
            float: Sharpe ratio
        """
        if not returns or len(returns) < 2:
            return 0.0

        returns_array = np.array(returns)
        avg_return = np.mean(returns_array)
        std_return = np.std(returns_array, ddof=1)

        if std_return == 0:
            return 0.0

        # Annualized Sharpe ratio
        sharpe = (
            np.sqrt(periods_per_year)
            * (avg_return - risk_free_rate / periods_per_year)
            / std_return
        )

        return float(sharpe)

    @staticmethod
    def calculate_sortino_ratio(
        returns: List[float], target_return: float = 0, periods_per_year: int = 252
    ) -> float:
        """
        Calculate Sortino Ratio

        Args:
            returns: List of returns
            target_return: Target return threshold
            periods_per_year: Number of periods per year

        Returns:
            float: Sortino ratio
        """
        if not returns or len(returns) < 2:
            return 0.0

        returns_array = np.array(returns)
        avg_return = np.mean(returns_array)

        # Only negative deviations
        downside_returns = returns_array[returns_array < target_return]

        if len(downside_returns) == 0:
            return float("inf") if avg_return > target_return else 0

        downside_std = np.std(downside_returns, ddof=1)

        if downside_std == 0:
            return 0.0

        # Annualized Sortino ratio
        sortino = np.sqrt(periods_per_year) * (avg_return - target_return) / downside_std

        return float(sortino)

    @staticmethod
    def calculate_ulcer_index(equity_curve: List[Tuple[int, float]]) -> float:
        """
        Calculate Ulcer Index (measure of downside volatility)

        Args:
            equity_curve: Equity curve [(timestamp, equity), ...]

        Returns:
            float: Ulcer index
        """
        if not equity_curve:
            return 0.0

        equities = [e for _, e in equity_curve]
        peak = equities[0]
        drawdowns = []

        for equity in equities:
            peak = max(peak, equity)
            dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
            drawdowns.append(dd**2)

        if not drawdowns:
            return 0.0

        return float(np.sqrt(np.mean(drawdowns)))

    @staticmethod
    def calculate_sterling_ratio(
        total_return: float, equity_curve: List[Tuple[int, float]], periods: int = 3
    ) -> float:
        """
        Calculate Sterling Ratio

        Args:
            total_return: Total return
            equity_curve: Equity curve
            periods: Number of periods

        Returns:
            float: Sterling ratio
        """
        if not equity_curve:
            return 0.0

        avg_drawdown = MetricsCalculator.calculate_max_drawdown(equity_curve) / 100

        if avg_drawdown == 0:
            return float("inf") if total_return > 0 else 0

        return total_return / avg_drawdown

    @staticmethod
    def calculate_tail_ratio(returns: List[float], percentile: float = 95) -> float:
        """
        Calculate Tail Ratio (right tail / left tail)

        Args:
            returns: List of returns
            percentile: Percentile for tails

        Returns:
            float: Tail ratio
        """
        if not returns:
            return 0.0

        right_tail = np.percentile(returns, percentile)
        left_tail = abs(np.percentile(returns, 100 - percentile))

        if left_tail == 0:
            return float("inf") if right_tail > 0 else 0

        return float(right_tail / left_tail)

    @staticmethod
    def calculate_common_sense_ratio(
        total_return: float, tail_ratio: float, win_rate: float
    ) -> float:
        """
        Calculate Common Sense Ratio

        Args:
            total_return: Total return
            tail_ratio: Tail ratio
            win_rate: Win rate (0-1)

        Returns:
            float: Common sense ratio
        """
        if tail_ratio == 0 or win_rate == 0:
            return 0.0

        return total_return * tail_ratio * win_rate

    @staticmethod
    def calculate_skewness(returns: List[float]) -> float:
        """
        Calculate skewness of returns

        Args:
            returns: List of returns

        Returns:
            float: Skewness
        """
        if not returns or len(returns) < 3:
            return 0.0

        return float(stats.skew(returns))

    @staticmethod
    def calculate_kurtosis(returns: List[float]) -> float:
        """
        Calculate kurtosis of returns

        Args:
            returns: List of returns

        Returns:
            float: Kurtosis
        """
        if not returns or len(returns) < 4:
            return 0.0

        return float(stats.kurtosis(returns))

    @staticmethod
    def calculate_mar_ratio(annual_return: float, max_drawdown: float) -> float:
        """
        Calculate MAR Ratio (Managed Account Ratio)

        Args:
            annual_return: Annual return percentage
            max_drawdown: Maximum drawdown percentage

        Returns:
            float: MAR ratio
        """
        if max_drawdown == 0:
            return float("inf") if annual_return > 0 else 0

        return annual_return / max_drawdown

    @staticmethod
    def calculate_sharpe_confidence_interval(
        returns: List[float],
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252,
        confidence: float = 0.95,
        n_resamples: int = 10000,
    ) -> Tuple[float, float]:
        """
        Bootstrap confidence interval for the annualized Sharpe ratio.

        A point Sharpe hides estimation error; on noisy returns the CI shows how
        much of the ratio is signal vs sampling luck. Note: IID percentile
        bootstrap — for autocorrelated returns prefer a block bootstrap.

        Args:
            returns: List of period returns
            risk_free_rate: Annual risk-free rate
            periods_per_year: Periods per year (252 for daily)
            confidence: Confidence level (0-1)
            n_resamples: Bootstrap resamples

        Returns:
            (low, high): Sharpe ratio confidence interval
        """
        if not returns or len(returns) < 30:
            return (0.0, 0.0)

        data = np.asarray(returns, dtype=float)

        def _sharpe(x: np.ndarray) -> float:
            sd = np.std(x, ddof=1)
            if sd == 0:
                return 0.0
            return np.sqrt(periods_per_year) * (np.mean(x) - risk_free_rate / periods_per_year) / sd

        res = stats.bootstrap(
            (data,),
            _sharpe,
            confidence_level=confidence,
            n_resamples=n_resamples,
            method="percentile",
        )
        return (float(res.confidence_interval.low), float(res.confidence_interval.high))

    @staticmethod
    def calculate_normality(returns: List[float]) -> Tuple[float, float]:
        """
        Jarque-Bera normality test on returns.

        Args:
            returns: List of returns

        Returns:
            (statistic, p_value). p < 0.05 → returns are NOT normal (fat tails /
            skew) — the norm for financial data and a reason to prefer
            permutation/bootstrap over tests that assume normality.
        """
        if not returns or len(returns) < 8:
            return (0.0, 1.0)

        jb = stats.jarque_bera(np.asarray(returns, dtype=float))
        return (float(jb.statistic), float(jb.pvalue))
