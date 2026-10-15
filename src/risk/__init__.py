"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : __init__.py
    Project     : Project-103
    Description : Risk management module. Handles position sizing, risk calculation, portfolio management, and drawdown control.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.risk.correlation_analyzer import CorrelationAnalyzer
from src.risk.drawdown_controller import DrawdownController, DrawdownStatus
from src.risk.portfolio_manager import PortfolioManager, PortfolioMetrics
from src.risk.position_sizing import PositionSizer, PositionSizeResult
from src.risk.risk_manager import RiskCalculationRequest, RiskDecision, RiskManager
from src.risk.stop_loss_calculator import StopLossCalculator
from src.risk.take_profit_calculator import TakeProfitCalculator

__all__ = [
    "RiskManager",
    "RiskDecision",
    "RiskCalculationRequest",
    "PositionSizer",
    "PositionSizeResult",
    "StopLossCalculator",
    "TakeProfitCalculator",
    "PortfolioManager",
    "PortfolioMetrics",
    "CorrelationAnalyzer",
    "DrawdownController",
    "DrawdownStatus",
]
