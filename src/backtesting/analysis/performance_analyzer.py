"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : performance_analyzer.py
    Project     : Project-103
    Description : Performance analyzer for backtesting. Calculates comprehensive
                  metrics including returns, risk (Sharpe, Sortino, VaR), trade
                  statistics, and drawdown analysis.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from datetime import timedelta
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.backtesting.models import TradeResult

logger = logging.getLogger(__name__)


class PerformanceAnalyzer:
    """
    Analyzes trading performance and calculates metrics.
    """

    def __init__(self, risk_free_rate: float = 0.02):
        """
        Initialize performance analyzer.

        Args:
            risk_free_rate: Annual risk-free rate for Sharpe calculation
        """
        self.risk_free_rate = risk_free_rate
        self.daily_rf_rate = risk_free_rate / 252

    def analyze(self, trades: List[TradeResult], equity_curve: pd.DataFrame) -> Dict[str, Any]:
        """
        Perform comprehensive performance analysis.

        Args:
            trades: List of completed trades
            equity_curve: DataFrame with equity curve data

        Returns:
            Dictionary with all performance metrics
        """
        metrics = {}

        # Return metrics
        metrics.update(self._calculate_return_metrics(equity_curve))

        # Risk metrics
        metrics.update(self._calculate_risk_metrics(equity_curve))

        # Trade metrics
        metrics.update(self._calculate_trade_metrics(trades))

        # Drawdown metrics
        metrics.update(self._calculate_drawdown_metrics(equity_curve))

        # Time metrics
        metrics.update(self._calculate_time_metrics(trades, equity_curve))

        return metrics

    def _calculate_return_metrics(self, equity_curve: pd.DataFrame) -> Dict[str, float]:
        """Calculate return-based metrics."""
        if equity_curve.empty or "total_equity" not in equity_curve.columns:
            return {"total_return_pct": 0.0, "annualized_return": 0.0, "volatility": 0.0}

        equity = equity_curve["total_equity"]
        initial = equity.iloc[0]
        final = equity.iloc[-1]

        # Total return
        total_return = (final - initial) / initial
        total_return_pct = total_return * 100

        # Annualized return
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        if days > 0:
            annualized = ((1 + total_return) ** (365 / days)) - 1
        else:
            annualized = 0

        # Volatility
        if "returns" in equity_curve.columns:
            returns = equity_curve["returns"].dropna()
            volatility = returns.std() * np.sqrt(252)
        else:
            volatility = 0

        return {
            "total_return_pct": total_return_pct,
            "annualized_return": annualized * 100,
            "volatility": volatility * 100,
        }

    def _calculate_risk_metrics(self, equity_curve: pd.DataFrame) -> Dict[str, float]:
        """Calculate risk-adjusted metrics."""
        if equity_curve.empty or "returns" not in equity_curve.columns:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "var_95": 0.0,
                "cvar_95": 0.0,
            }

        returns = equity_curve["returns"].dropna()

        if len(returns) < 2:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "var_95": 0.0,
                "cvar_95": 0.0,
            }

        # Sharpe Ratio
        sharpe = self._calculate_sharpe_ratio(returns)

        # Sortino Ratio
        sortino = self._calculate_sortino_ratio(returns)

        # Calmar Ratio
        calmar = self._calculate_calmar_ratio(equity_curve)

        # VaR and CVaR
        var_95 = np.percentile(returns, 5) * 100
        cvar_95 = returns[returns <= np.percentile(returns, 5)].mean() * 100

        return {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "var_95": var_95,
            "cvar_95": cvar_95,
        }

    def _calculate_sharpe_ratio(self, returns: pd.Series) -> float:
        """
        Calculate Sharpe Ratio.

        Sharpe = (Mean Return - Risk Free Rate) / Std Dev
        """
        if returns.std() == 0:
            return 0.0

        excess_returns = returns - self.daily_rf_rate
        sharpe = np.sqrt(252) * excess_returns.mean() / returns.std()

        return float(sharpe)

    def _calculate_sortino_ratio(self, returns: pd.Series, target_return: float = 0.0) -> float:
        """
        Calculate Sortino Ratio.

        Sortino = (Mean Return - Target) / Downside Deviation
        """
        downside_returns = returns[returns < target_return]

        if len(downside_returns) == 0:
            return float("inf") if returns.mean() > target_return else 0.0

        downside_std = np.sqrt(np.mean(downside_returns**2))

        if downside_std == 0:
            return float("inf") if returns.mean() > target_return else 0.0

        sortino = np.sqrt(252) * (returns.mean() - target_return) / downside_std

        return float(sortino)

    def _calculate_calmar_ratio(self, equity_curve: pd.DataFrame) -> float:
        """
        Calculate Calmar Ratio.

        Calmar = Annualized Return / Max Drawdown
        """
        if equity_curve.empty:
            return 0.0

        equity = equity_curve["total_equity"]
        initial = equity.iloc[0]
        final = equity.iloc[-1]

        # Annualized return
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        if days > 0:
            total_return = (final - initial) / initial
            annualized_return = ((1 + total_return) ** (365 / days)) - 1
        else:
            return 0.0

        # Max drawdown
        if "drawdown_pct" in equity_curve.columns:
            max_dd = equity_curve["drawdown_pct"].max() / 100
        else:
            running_max = equity.expanding().max()
            drawdown = (equity - running_max) / running_max
            max_dd = abs(drawdown.min())

        if max_dd == 0:
            return float("inf") if annualized_return > 0 else 0.0

        calmar = annualized_return / max_dd

        return float(calmar)

    def _calculate_trade_metrics(self, trades: List[TradeResult]) -> Dict[str, float]:
        """Calculate trade-based metrics."""
        if not trades:
            return {
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "avg_trade": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "avg_win_loss_ratio": 0.0,
            }

        winners = [t for t in trades if t.net_pnl > 0]
        losers = [t for t in trades if t.net_pnl <= 0]

        # Profit Factor
        gross_profit = sum(t.net_pnl for t in winners)
        gross_loss = abs(sum(t.net_pnl for t in losers))

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        # Expectancy
        win_rate = len(winners) / len(trades)
        avg_win = gross_profit / len(winners) if winners else 0
        avg_loss = abs(gross_loss / len(losers)) if losers else 0

        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Average trade
        avg_trade = sum(t.net_pnl for t in trades) / len(trades)

        # Best/worst trades
        largest_win = max(t.net_pnl for t in trades) if trades else 0
        largest_loss = min(t.net_pnl for t in trades) if trades else 0

        # Average win/loss ratio
        if avg_loss > 0:
            avg_win_loss_ratio = avg_win / avg_loss
        else:
            avg_win_loss_ratio = float("inf") if avg_win > 0 else 0

        return {
            "profit_factor": float(profit_factor),
            "expectancy": float(expectancy),
            "avg_trade": float(avg_trade),
            "largest_win": float(largest_win),
            "largest_loss": float(largest_loss),
            "avg_win_loss_ratio": float(avg_win_loss_ratio),
        }

    def _calculate_drawdown_metrics(self, equity_curve: pd.DataFrame) -> Dict[str, Any]:
        """Calculate drawdown metrics."""
        if equity_curve.empty or "total_equity" not in equity_curve.columns:
            return {
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_drawdown": 0.0,
                "drawdown_duration": timedelta(0),
            }

        equity = equity_curve["total_equity"]

        # Calculate running maximum and drawdown
        running_max = equity.expanding().max()
        drawdown = (equity - running_max) / running_max

        # Max drawdown
        max_dd = abs(drawdown.min())
        max_dd_pct = max_dd * 100

        # Average drawdown
        avg_dd = abs(drawdown[drawdown < 0].mean()) * 100 if (drawdown < 0).any() else 0

        # Drawdown duration
        max_dd_idx = drawdown.idxmin()
        peak_idx = equity[:max_dd_idx].idxmax()

        if pd.notna(max_dd_idx) and pd.notna(peak_idx):
            dd_duration = max_dd_idx - peak_idx
        else:
            dd_duration = timedelta(0)

        return {
            "max_drawdown": float(max_dd),
            "max_drawdown_pct": float(max_dd_pct),
            "avg_drawdown": float(avg_dd),
            "drawdown_duration": dd_duration,
        }

    def drawdown_episodes(
        self, equity_curve: pd.DataFrame, min_pct: float = 1.0
    ) -> List[Dict[str, Any]]:
        """Split the equity curve into individual drawdowns.

        A portfolio drawdown is not a single number but a set of episodes, and
        whether it is tolerable depends on duration as much as on depth. 15% over
        two days and the same 15% over six weeks are different experiences with an
        identical metric.

        Episode = from a peak until the return to that peak; the trough is marked
        inside. The last episode may remain open — and is flagged as such, because
        an unfinished drawdown is not the same as one that was survived.

        min_pct filters out noise: without a threshold every one-cent wiggle would
        count as a drawdown, and there would be thousands of them.
        """
        if equity_curve.empty or "total_equity" not in equity_curve.columns:
            return []

        equity = equity_curve["total_equity"].astype(float)
        peak = equity.cummax()
        under = equity < peak

        episodes: List[Dict[str, Any]] = []
        start_idx = None
        for i in range(len(equity)):
            if under.iloc[i] and start_idx is None:
                start_idx = i
            elif not under.iloc[i] and start_idx is not None:
                episodes.append((start_idx, i - 1, True))
                start_idx = None
        if start_idx is not None:
            episodes.append((start_idx, len(equity) - 1, False))

        out: List[Dict[str, Any]] = []
        idx = equity.index
        for a, b, recovered in episodes:
            seg = equity.iloc[a : b + 1]
            peak_val = float(peak.iloc[a])
            trough_i = int(seg.values.argmin()) + a
            trough_val = float(equity.iloc[trough_i])
            depth = peak_val - trough_val
            depth_pct = depth / peak_val * 100 if peak_val else 0.0
            if depth_pct < min_pct:
                continue
            # peak — the last point ABOVE water before the dip,
            # end — the FIRST point where equity reaches the peak again (b is the last
            # bar under water, so the recovery happens on the next one)
            peak_i = a - 1 if a > 0 else a
            end_i = b + 1 if recovered else b
            out.append(
                {
                    "peak_time": idx[peak_i],
                    "trough_time": idx[trough_i],
                    "end_time": idx[end_i] if recovered else None,
                    "peak_equity": peak_val,
                    "trough_equity": trough_val,
                    "depth": depth,
                    "depth_pct": depth_pct,
                    "to_trough": idx[trough_i] - idx[peak_i],
                    "to_recover": (idx[end_i] - idx[trough_i]) if recovered else None,
                    "total": (idx[end_i] - idx[peak_i]) if recovered else (idx[-1] - idx[peak_i]),
                    "recovered": recovered,
                }
            )
        out.sort(key=lambda e: -e["depth_pct"])
        return out

    def _calculate_time_metrics(
        self, trades: List[TradeResult], equity_curve: pd.DataFrame
    ) -> Dict[str, Any]:
        """Calculate time-based metrics."""
        if not trades:
            return {
                "avg_hold_time": timedelta(0),
                "max_hold_time": timedelta(0),
                "time_in_market": 0.0,
            }

        # Hold times
        hold_times = [t.hold_time for t in trades if t.hold_time]
        avg_hold = sum(hold_times, timedelta()) / len(hold_times) if hold_times else timedelta()
        max_hold = max(hold_times) if hold_times else timedelta()

        # Time in market
        if not equity_curve.empty and "returns" in equity_curve.columns:
            total_periods = len(equity_curve)
            # Count periods where we had positions (non-zero returns or position count > 0)
            in_market = 0
            if "positions_count" in equity_curve.columns:
                in_market = (equity_curve["positions_count"] > 0).sum()
            else:
                # Approximate from non-zero returns
                in_market = (equity_curve["returns"] != 0).sum()

            time_in_market = (in_market / total_periods * 100) if total_periods > 0 else 0
        else:
            time_in_market = 0

        return {
            "avg_hold_time": avg_hold,
            "max_hold_time": max_hold,
            "time_in_market": float(time_in_market),
        }

    def calculate_monthly_returns(self, equity_curve: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate monthly returns table.

        Returns:
            DataFrame with years as rows and months as columns
        """
        if equity_curve.empty or "total_equity" not in equity_curve.columns:
            return pd.DataFrame()

        # Resample to monthly
        monthly = equity_curve["total_equity"].resample("M").last()
        monthly_returns = monthly.pct_change().dropna()

        # Create year-month matrix
        years = monthly_returns.index.year.unique()
        months = range(1, 13)

        result = pd.DataFrame(index=years, columns=months)

        for date, ret in monthly_returns.items():
            result.loc[date.year, date.month] = ret * 100

        return result.fillna("")

    def calculate_rolling_metrics(
        self, equity_curve: pd.DataFrame, window: int = 30
    ) -> pd.DataFrame:
        """
        Calculate rolling performance metrics.

        Args:
            equity_curve: DataFrame with equity curve
            window: Rolling window size in days

        Returns:
            DataFrame with rolling metrics
        """
        if equity_curve.empty or "returns" not in equity_curve.columns:
            return pd.DataFrame()

        returns = equity_curve["returns"].dropna()

        rolling = pd.DataFrame(index=returns.index)

        # Rolling Sharpe
        rolling["sharpe"] = returns.rolling(window).apply(
            lambda x: np.sqrt(252) * (x.mean() - self.daily_rf_rate) / x.std() if x.std() > 0 else 0
        )

        # Rolling volatility
        rolling["volatility"] = returns.rolling(window).std() * np.sqrt(252) * 100

        # Rolling return
        rolling["return"] = returns.rolling(window).apply(lambda x: (1 + x).prod() - 1) * 100

        return rolling
