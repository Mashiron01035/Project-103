"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : types
    Project     : Project-103
    Description : Type definitions for the trading bot system.This module defines TypedDict classes and generic types for type safety.

    SPDX-License-Identifier: AGPL-3.0

"""

from datetime import datetime
from decimal import Decimal
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    TypedDict,
    TypeVar,
    Union,
)

from src.core.enums import (
    IndicatorType,
    MarketCondition,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    RiskLevel,
    SignalStrength,
    SignalType,
    TimeFrame,
)

# ==================== Basic Types ====================

PriceData = Union[float, Decimal]
VolumeData = Union[float, Decimal]
TimestampData = Union[int, float, datetime]

# Type variables
T = TypeVar("T")
R = TypeVar("R")
P = TypeVar("P")

# Callback types
CallbackFunc = Callable[[Any], None]
AsyncCallbackFunc = Callable[[Any], Awaitable[None]]


# ==================== Market Data Types ====================


class Candle(TypedDict):
    """OHLCV candle data."""

    timestamp: int  # Unix timestamp in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: Optional[float]  # Volume in quote currency
    trades_count: Optional[int]  # Number of trades
    taker_buy_volume: Optional[float]  # Taker buy volume
    taker_buy_quote_volume: Optional[float]  # Taker buy volume in quote


class OrderBookLevel(TypedDict):
    """Single order book level."""

    price: float
    quantity: float
    orders_count: Optional[int]  # Number of orders at this level


class OrderBook(TypedDict):
    """Order book snapshot."""

    timestamp: int
    symbol: str
    bids: List[OrderBookLevel]  # Buy orders
    asks: List[OrderBookLevel]  # Sell orders
    sequence: Optional[int]  # Update sequence number


class Trade(TypedDict):
    """Individual trade/tick data."""

    id: str
    timestamp: int
    symbol: str
    price: float
    quantity: float
    side: Literal["BUY", "SELL"]
    is_maker: Optional[bool]


class Ticker(TypedDict):
    """24hr ticker statistics."""

    symbol: str
    timestamp: int
    last_price: float
    bid_price: float
    ask_price: float
    open_price: float
    high_price: float
    low_price: float
    volume: float
    quote_volume: float
    price_change: float
    price_change_percent: float
    weighted_avg_price: Optional[float]


# ==================== Account Types ====================


class Balance(TypedDict):
    """Account balance."""

    asset: str
    free: float  # Available balance
    locked: float  # In orders
    total: float  # free + locked
    borrowed: Optional[float]  # For margin trading
    interest: Optional[float]  # Accumulated interest
    net: Optional[float]  # total - borrowed - interest


class Position(TypedDict):
    """Open position."""

    id: str
    symbol: str
    side: PositionSide
    status: PositionStatus

    # Quantities
    quantity: float
    filled_quantity: float
    remaining_quantity: float

    # Prices
    entry_price: float
    mark_price: float
    liquidation_price: Optional[float]

    # P/L
    unrealized_pnl: float
    realized_pnl: float

    # Risk management
    stop_loss: Optional[float]
    take_profit: Optional[float]
    trailing_stop: Optional[float]

    # Leverage
    leverage: Optional[int]
    margin_mode: Optional[Literal["cross", "isolated"]]
    margin: Optional[float]

    # Timestamps
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]

    # Metadata
    strategy_id: Optional[str]
    notes: Optional[str]


class Order(TypedDict):
    """Trading order."""

    id: str
    client_id: Optional[str]
    symbol: str
    type: OrderType
    side: OrderSide
    status: OrderStatus

    # Quantities
    quantity: float
    filled_quantity: float
    remaining_quantity: float

    # Prices
    price: Optional[float]  # For limit orders
    stop_price: Optional[float]  # For stop orders
    avg_fill_price: Optional[float]

    # Execution
    time_in_force: Literal["GTC", "IOC", "FOK", "GTX"]
    post_only: Optional[bool]
    reduce_only: Optional[bool]
    close_position: Optional[bool]

    # Timestamps
    created_at: datetime
    updated_at: datetime
    filled_at: Optional[datetime]
    cancelled_at: Optional[datetime]

    # Metadata
    position_id: Optional[str]
    strategy_id: Optional[str]
    notes: Optional[str]


# ==================== Signal Types ====================


class Signal(TypedDict):
    """Trading signal."""

    id: str
    timestamp: datetime
    symbol: str
    type: SignalType
    strength: SignalStrength
    confidence: float  # 0-1

    # Price targets
    entry_price: float
    stop_loss: float
    take_profit: List[float]  # Multiple TP levels

    # Risk parameters
    risk_level: RiskLevel
    position_size: float
    risk_amount: float
    potential_profit: float
    risk_reward_ratio: float

    # Indicators that triggered signal
    indicators: Dict[str, Any]

    # Market context
    market_condition: MarketCondition
    trend_strength: float
    volatility: float
    volume_ratio: float

    # Strategy info
    strategy_name: str
    timeframe: TimeFrame

    # Metadata
    notes: Optional[str]
    expires_at: Optional[datetime]


# ==================== Risk Management Types ====================


class RiskParameters(TypedDict):
    """Risk management parameters."""

    # Position sizing
    account_balance: float
    risk_per_trade: float  # Percentage
    max_position_size: float

    # Risk limits
    max_daily_loss: float  # Percentage
    max_drawdown: float  # Percentage
    max_positions: int
    max_correlation: float

    # Stop loss
    stop_loss_type: Literal["fixed", "dynamic", "atr", "trailing"]
    stop_loss_percentage: float
    atr_multiplier: Optional[float]
    trailing_distance: Optional[float]

    # Take profit
    take_profit_levels: List[float]  # Percentage levels
    take_profit_amounts: List[float]  # Percentage of position to close

    # Risk:Reward
    min_risk_reward: float
    target_risk_reward: float

    # Filters
    min_volume: float
    max_spread: float
    max_slippage: float


class PositionSize(TypedDict):
    """Position size calculation result."""

    quantity: float
    notional_value: float
    risk_amount: float
    margin_required: Optional[float]
    leverage: Optional[int]
    stop_loss_price: float
    take_profit_prices: List[float]
    risk_reward_ratio: float


# ==================== Indicator Types ====================


class IndicatorResult(TypedDict):
    """Generic indicator result."""

    name: str
    type: IndicatorType
    timestamp: datetime
    timeframe: TimeFrame

    # Main value(s)
    value: Union[float, List[float], Dict[str, float]]

    # Additional lines/bands
    upper_band: Optional[float]
    lower_band: Optional[float]
    middle_band: Optional[float]

    # Signal interpretation
    signal: Optional[Literal["BUY", "SELL", "NEUTRAL"]]
    strength: Optional[float]  # 0-1

    # Metadata
    params: Dict[str, Any]  # Indicator parameters
    raw_data: Optional[Dict[str, Any]]  # Raw calculation data


class MACDResult(TypedDict):
    """MACD indicator result."""

    macd_line: float
    signal_line: float
    histogram: float
    crossover: Optional[Literal["bullish", "bearish"]]
    divergence: Optional[Literal["bullish", "bearish"]]


class BollingerBandsResult(TypedDict):
    """Bollinger Bands result."""

    upper_band: float
    middle_band: float
    lower_band: float
    bandwidth: float
    percent_b: float  # Position within bands


class RSIResult(TypedDict):
    """RSI indicator result."""

    value: float
    overbought: bool
    oversold: bool
    divergence: Optional[Literal["bullish", "bearish"]]


# ==================== Strategy Types ====================


class StrategySignal(TypedDict):
    """Strategy-generated signal."""

    strategy_name: str
    signal: Signal
    conditions_met: List[str]  # Which conditions triggered
    confidence_breakdown: Dict[str, float]  # Confidence per condition
    backtested_performance: Optional[Dict[str, float]]


class StrategyPerformance(TypedDict):
    """Strategy performance metrics."""

    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int

    # Returns
    total_return: float
    average_return: float
    best_trade: float
    worst_trade: float

    # Risk metrics
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float

    # Time metrics
    average_trade_duration: float
    longest_trade_duration: float
    shortest_trade_duration: float


# ==================== Backtest Types ====================


class BacktestResult(TypedDict):
    """Backtest result."""

    # Configuration
    start_date: datetime
    end_date: datetime
    initial_balance: float
    symbols: List[str]
    strategies: List[str]

    # Results
    final_balance: float
    total_return: float
    total_trades: int

    # Performance metrics
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float

    # Trade statistics
    average_trade_return: float
    best_trade: float
    worst_trade: float
    average_win: float
    average_loss: float
    largest_win: float
    largest_loss: float

    # Time statistics
    total_days: int
    trading_days: int
    average_trades_per_day: float
    longest_winning_streak: int
    longest_losing_streak: int

    # Risk statistics
    value_at_risk: float  # VaR
    conditional_value_at_risk: float  # CVaR

    # Detailed data
    equity_curve: List[float]
    trades: List[Dict[str, Any]]
    daily_returns: List[float]


class PerformanceMetrics(TypedDict):
    """System performance metrics."""

    timestamp: datetime

    # System metrics
    uptime_seconds: int
    cpu_usage: float
    memory_usage: float
    disk_usage: float

    # Trading metrics
    active_positions: int
    pending_orders: int
    total_trades_today: int

    # Financial metrics
    account_balance: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float

    # API metrics
    api_calls_count: int
    api_errors_count: int
    average_latency: float

    # Strategy metrics
    signals_generated: int
    signals_executed: int
    signal_success_rate: float


# ==================== Configuration Types ====================


class ExchangeCredentials(TypedDict):
    """Exchange API credentials."""

    api_key: str
    api_secret: str
    passphrase: Optional[str]  # For some exchanges
    testnet: bool


class TradingPairInfo(TypedDict):
    """Trading pair information."""

    symbol: str
    base_asset: str
    quote_asset: str

    # Limits
    min_quantity: float
    max_quantity: float
    min_notional: float

    # Precision
    price_precision: int
    quantity_precision: int
    base_precision: int
    quote_precision: int

    # Steps
    price_tick_size: float
    quantity_step_size: float

    # Status
    is_active: bool
    is_margin_trading: bool
    is_spot_trading: bool
    is_futures_trading: bool

    # Fees
    maker_fee: float
    taker_fee: float


# ==================== Notification Types ====================


class NotificationMessage(TypedDict):
    """Notification message."""

    type: Literal["signal", "trade", "alert", "error", "report"]
    priority: Literal["low", "medium", "high", "critical"]
    title: str
    message: str
    timestamp: datetime

    # Additional data
    symbol: Optional[str]
    strategy: Optional[str]
    trade_id: Optional[str]
    error_code: Optional[str]

    # Attachments
    chart_url: Optional[str]
    details: Optional[Dict[str, Any]]


# ==================== Event Types ====================


class Event(TypedDict):
    """System event."""

    id: str
    type: str
    timestamp: datetime
    source: str  # Component that generated event
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]]


class MarketEvent(Event):
    """Market data event."""

    symbol: str
    event_type: Literal["price", "volume", "orderbook", "trade"]


class TradingEvent(Event):
    """Trading event."""

    event_type: Literal[
        "order_placed",
        "order_filled",
        "order_cancelled",
        "position_opened",
        "position_closed",
        "stop_loss_hit",
    ]
    order_id: Optional[str]
    position_id: Optional[str]


class SystemEvent(Event):
    """System event."""

    event_type: Literal["startup", "shutdown", "error", "warning", "config_change"]
    severity: Literal["info", "warning", "error", "critical"]
