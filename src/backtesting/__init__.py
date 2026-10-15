"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : __init__.py
    Project     : Project-103
    Description : Backtesting module for trading strategies. Provides comprehensive
                  backtesting capabilities including historical data simulation,
                  realistic order execution, portfolio management, and performance analysis.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.backtesting.analysis.performance_analyzer import PerformanceAnalyzer
from src.backtesting.data.data_loader import DataLoader
from src.backtesting.engine.backtest_engine import BacktestEngine
from src.backtesting.models import (
    BacktestConfig,
    BacktestResult,
    CommissionConfig,
    DataConfig,
    DataSource,
    DrawdownInfo,
    EquityPoint,
    ExecutionMode,
    RiskConfig,
    SlippageConfig,
    StrategyConfig,
    StrategyMetrics,
    SymbolMetrics,
    TradeResult,
)
from src.backtesting.portfolio.commission_model import (
    CommissionModel,
    FixedCommission,
    TieredCommission,
    create_commission_model,
)
from src.backtesting.portfolio.portfolio_manager import PortfolioManager
from src.backtesting.portfolio.slippage_model import (
    FixedSlippage,
    MarketState,
    SlippageModel,
    VolatilityAdjustedSlippage,
    create_slippage_model,
)

__all__ = [
    # Config
    "BacktestConfig",
    "StrategyConfig",
    "SlippageConfig",
    "CommissionConfig",
    "RiskConfig",
    "DataConfig",
    "DataSource",
    "ExecutionMode",
    # Results
    "BacktestResult",
    "TradeResult",
    "EquityPoint",
    "StrategyMetrics",
    "SymbolMetrics",
    "DrawdownInfo",
    # Engine
    "BacktestEngine",
    "DataLoader",
    # Portfolio
    "PortfolioManager",
    "CommissionModel",
    "FixedCommission",
    "TieredCommission",
    "create_commission_model",
    "SlippageModel",
    "FixedSlippage",
    "VolatilityAdjustedSlippage",
    "create_slippage_model",
    "MarketState",
    # Analysis
    "PerformanceAnalyzer",
]
