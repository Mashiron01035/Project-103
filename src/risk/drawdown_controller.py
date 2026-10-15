"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : drawdown_controller.py
    Project     : Project-103
    Description : Drawdown control and monitoring to protect capital during losing streaks.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DrawdownStatus(Enum):
    """Drawdown status levels."""

    NORMAL = "normal"  # Within acceptable limits
    WARNING = "warning"  # Approaching limits
    CRITICAL = "critical"  # At or exceeding limits
    EMERGENCY = "emergency"  # Emergency stop trading


@dataclass
class DrawdownMetrics:
    """Drawdown metrics."""

    current_drawdown: float  # Current drawdown %
    current_drawdown_amount: float  # Current drawdown amount
    max_drawdown: float  # Maximum drawdown seen %
    max_drawdown_amount: float  # Maximum drawdown amount
    peak_balance: float  # Peak account balance
    current_balance: float  # Current balance
    status: DrawdownStatus
    days_in_drawdown: int
    recovery_needed: float  # % gain needed to recover


class DrawdownController:
    """
    Controls and monitors drawdown to protect capital.

    Implements progressive trading restrictions as drawdown increases.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize DrawdownController.

        Args:
            config: Controller configuration
        """
        self.config = config or {}

        # Drawdown limits
        self.max_daily_drawdown = self.config.get("max_daily_drawdown", 0.05)  # 5%
        self.max_weekly_drawdown = self.config.get("max_weekly_drawdown", 0.10)  # 10%
        self.max_monthly_drawdown = self.config.get("max_monthly_drawdown", 0.20)  # 20%

        # Warning thresholds
        self.warning_threshold = self.config.get("warning_threshold", 0.70)  # 70% of max
        self.critical_threshold = self.config.get("critical_threshold", 0.90)  # 90% of max

        # State
        self.peak_balance = 0.0
        self.peak_daily_balance = 0.0
        self.peak_weekly_balance = 0.0
        self.peak_monthly_balance = 0.0
        self.drawdown_start_time: Optional[int] = None

        logger.info("DrawdownController initialized")

    def update(
        self, current_balance: float, period_start_balance: Optional[Dict[str, float]] = None
    ) -> DrawdownMetrics:
        """
        Update drawdown metrics.

        Args:
            current_balance: Current account balance
            period_start_balance: Starting balances for periods (daily, weekly, monthly)

        Returns:
            DrawdownMetrics object
        """
        # Update peak balance
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
            self.drawdown_start_time = None

        # Calculate current drawdown from peak
        if self.peak_balance > 0:
            current_dd = (self.peak_balance - current_balance) / self.peak_balance
            current_dd_amount = self.peak_balance - current_balance
        else:
            current_dd = 0.0
            current_dd_amount = 0.0

        # Track drawdown duration
        if current_dd > 0 and self.drawdown_start_time is None:
            self.drawdown_start_time = int(time.time())

        days_in_dd = 0
        if self.drawdown_start_time:
            days_in_dd = (int(time.time()) - self.drawdown_start_time) // 86400

        # Calculate recovery needed
        if current_balance > 0:
            recovery_needed = ((self.peak_balance / current_balance) - 1) * 100
        else:
            recovery_needed = 0

        # Determine status
        status = self._determine_status(current_dd, period_start_balance or {}, current_balance)

        metrics = DrawdownMetrics(
            current_drawdown=current_dd * 100,
            current_drawdown_amount=current_dd_amount,
            max_drawdown=current_dd * 100,  # Track separately if needed
            max_drawdown_amount=current_dd_amount,
            peak_balance=self.peak_balance,
            current_balance=current_balance,
            status=status,
            days_in_drawdown=days_in_dd,
            recovery_needed=recovery_needed,
        )

        self._log_status(metrics)

        return metrics

    def _determine_status(
        self, current_dd: float, period_balances: Dict[str, float], current_balance: float
    ) -> DrawdownStatus:
        """
        Determine drawdown status based on current metrics.

        Args:
            current_dd: Current drawdown from peak
            period_balances: Period starting balances
            current_balance: Current balance

        Returns:
            DrawdownStatus
        """
        # Check daily drawdown
        daily_start = period_balances.get("daily", current_balance)
        if daily_start > 0:
            daily_dd = (daily_start - current_balance) / daily_start

            if daily_dd >= self.max_daily_drawdown:
                return DrawdownStatus.EMERGENCY

        # Check overall drawdown against limits
        if current_dd >= self.max_monthly_drawdown * self.critical_threshold:
            return DrawdownStatus.CRITICAL
        elif current_dd >= self.max_monthly_drawdown * self.warning_threshold:
            return DrawdownStatus.WARNING

        return DrawdownStatus.NORMAL

    def can_trade(self, metrics: DrawdownMetrics) -> bool:
        """
        Check if trading is allowed based on drawdown.

        Args:
            metrics: Current drawdown metrics

        Returns:
            True if trading allowed
        """
        if metrics.status == DrawdownStatus.EMERGENCY:
            logger.critical("Trading halted: Emergency drawdown level reached")
            return False

        return True

    def get_risk_multiplier(self, metrics: DrawdownMetrics) -> float:
        """
        Get risk multiplier based on drawdown status.

        Reduce risk as drawdown increases.

        Args:
            metrics: Drawdown metrics

        Returns:
            Risk multiplier (0-1)
        """
        if metrics.status == DrawdownStatus.EMERGENCY:
            return 0.0
        elif metrics.status == DrawdownStatus.CRITICAL:
            return 0.25  # 25% of normal risk
        elif metrics.status == DrawdownStatus.WARNING:
            return 0.50  # 50% of normal risk
        else:
            return 1.0  # Normal risk

    def _log_status(self, metrics: DrawdownMetrics) -> None:
        """Log drawdown status."""
        if metrics.status != DrawdownStatus.NORMAL:
            logger.warning(
                f"Drawdown status: {metrics.status.value}, "
                f"current DD: {metrics.current_drawdown:.2f}%, "
                f"days in DD: {metrics.days_in_drawdown}"
            )
