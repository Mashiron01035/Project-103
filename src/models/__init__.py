"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Models module for Trading Bot. This module contains all data models for the trading system.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.models.account import (
    Account,
    AccountBalance,
    AccountMargin,
    AccountPerformance,
    AccountRisk,
)
from src.models.base import (
    BaseModel,
    PriceMixin,
    SerializableMixin,
    TimestampMixin,
    ValidationError,
)
from src.models.candle import Candle, CandlePattern
from src.models.order import (
    Order,
    OrderExecutionReport,
    OrderSide,
    OrderStatus,
    OrderTimeInForce,
    OrderType,
)
from src.models.orderbook import OrderBook, OrderBookLevel, OrderBookSnapshot
from src.models.position import (
    Position,
    PositionManager,
    PositionMetrics,
    PositionRisk,
    PositionSide,
    PositionStatus,
)
from src.models.signal import Signal, SignalMetrics, SignalStatus, SignalStrength, SignalType
from src.models.ticker import Ticker, TickerSnapshot
from src.models.trade import Trade, TradeResult, TradeStatistics

__all__ = [
    # Base
    "BaseModel",
    "TimestampMixin",
    "PriceMixin",
    "SerializableMixin",
    "ValidationError",
    # Candle
    "Candle",
    "CandlePattern",
    # Ticker
    "Ticker",
    "TickerSnapshot",
    # OrderBook
    "OrderBook",
    "OrderBookLevel",
    "OrderBookSnapshot",
    # Signal
    "Signal",
    "SignalType",
    "SignalStrength",
    "SignalStatus",
    "SignalMetrics",
    # Order
    "Order",
    "OrderType",
    "OrderSide",
    "OrderStatus",
    "OrderTimeInForce",
    "OrderExecutionReport",
    # Position
    "Position",
    "PositionSide",
    "PositionStatus",
    "PositionMetrics",
    "PositionRisk",
    "PositionManager",
    # Trade
    "Trade",
    "TradeResult",
    "TradeStatistics",
    # Account
    "Account",
    "AccountBalance",
    "AccountMargin",
    "AccountRisk",
    "AccountPerformance",
]
