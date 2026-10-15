"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : constants.py
    Project     : Project-103
    Description : System constants and default values. This module defines all constants used throughout the trading bot system.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import Dict, List, Tuple

# ==================== Time Constants ====================

# Timeframes
TIMEFRAMES: List[str] = [
    "1m",  # 1 minute
    "2m",  # 2 minutes
    "3m",  # 3 minutes
    "5m",  # 5 minutes
    "15m",  # 15 minutes
    "30m",  # 30 minutes
    "1h",  # 1 hour
    "2h",  # 2 hours
    "4h",  # 4 hours
    "6h",  # 6 hours
    "12h",  # 12 hours
    "1d",  # 1 day
    "1w",  # 1 week
    "1M",  # 1 month
]

# Timeframe to seconds mapping
TIMEFRAME_SECONDS: Dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
    "1M": 2592000,
}

# Default timeframe
DEFAULT_TIMEFRAME: str = "15m"

# ==================== Trading Constants ====================

# Order types
ORDER_TYPES: List[str] = [
    "LIMIT",
    "MARKET",
    "STOP_LOSS",
    "STOP_LOSS_LIMIT",
    "TAKE_PROFIT",
    "TAKE_PROFIT_LIMIT",
    "TRAILING_STOP",
]

# Order sides
ORDER_SIDES: List[str] = [
    "BUY",
    "SELL",
]

# Position sides
POSITION_SIDES: List[str] = [
    "LONG",
    "SHORT",
]

# Trade states
TRADE_STATES: List[str] = [
    "PENDING",
    "ACTIVE",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "CLOSED",
]

# Signal types
SIGNAL_TYPES: List[str] = [
    "BUY",
    "SELL",
    "CLOSE",
    "HOLD",
]

# ==================== Exchange Constants ====================

# Default exchange
DEFAULT_EXCHANGE: str = "BYBIT"

# Supported exchanges
SUPPORTED_EXCHANGES: List[str] = [
    "BYBIT",
    "BINANCE",
    "OKX",
    "KRAKEN",
]

# Exchange endpoints
EXCHANGE_ENDPOINTS: Dict[str, Dict[str, str]] = {
    "BYBIT": {
        "MAINNET": "https://api.bybit.com",
        "TESTNET": "https://api-testnet.bybit.com",
        "WS_MAINNET": "wss://stream.bybit.com/v5/public",
        "WS_TESTNET": "wss://stream-testnet.bybit.com/v5/public",
    },
    "BINANCE": {
        "MAINNET": "https://api.binance.com",
        "TESTNET": "https://testnet.binance.vision",
        "WS_MAINNET": "wss://stream.binance.com:9443",
        "WS_TESTNET": "wss://testnet.binance.vision",
    },
}

# ==================== API Constants ====================

# Rate limits (requests per second)
API_RATE_LIMIT: int = 50

# WebSocket heartbeat interval (seconds)
WEBSOCKET_HEARTBEAT: int = 30

# Request timeout (seconds)
REQUEST_TIMEOUT: int = 30

# Max retries for failed requests
MAX_RETRIES: int = 3

# Retry delay (seconds)
RETRY_DELAY: float = 1.0

# Exponential backoff factor
BACKOFF_FACTOR: float = 2.0

# ==================== Trading Limits ====================

# Minimum order size (USDT)
MIN_ORDER_SIZE: float = 10.0

# Maximum leverage
MAX_LEVERAGE: int = 50

# Default leverage
DEFAULT_LEVERAGE: int = 1

# Maximum open positions
MAX_POSITIONS: int = 5

# Maximum daily trades
MAX_DAILY_TRADES: int = 100

# ==================== Risk Management Constants ====================

# Risk levels
RISK_LEVELS: List[str] = [
    "LOW",
    "MEDIUM",
    "HIGH",
]

# Default risk per trade (percentage)
DEFAULT_RISK_PER_TRADE: float = 0.01  # 1%

# Maximum drawdown (percentage)
MAX_DRAWDOWN: float = 0.20  # 20%

# Maximum daily loss (percentage)
MAX_DAILY_LOSS: float = 0.05  # 5%

# Stop loss multipliers for different risk levels
STOP_LOSS_MULTIPLIERS: Dict[str, float] = {
    "LOW": 0.01,  # 1%
    "MEDIUM": 0.02,  # 2%
    "HIGH": 0.03,  # 3%
}

# Risk:Reward ratios
RISK_REWARD_RATIOS: Dict[str, float] = {
    "LOW": 2.0,  # 1:2
    "MEDIUM": 2.5,  # 1:2.5
    "HIGH": 3.0,  # 1:3
}

# Shitcoin risk:reward ratios (higher due to increased volatility)
SHITCOIN_RISK_REWARD_RATIOS: Dict[str, float] = {
    "LOW": 3.0,  # 1:3
    "MEDIUM": 4.0,  # 1:4
    "HIGH": 5.0,  # 1:5
}

# ==================== Technical Analysis Constants ====================

# Default indicator periods
INDICATOR_PERIODS: Dict[str, int] = {
    "RSI": 14,
    "EMA_FAST": 9,
    "EMA_SLOW": 21,
    "SMA": 20,
    "MACD_FAST": 12,
    "MACD_SLOW": 26,
    "MACD_SIGNAL": 9,
    "BB_PERIOD": 20,
    "BB_STD": 2,
    "ATR": 14,
    "ADX": 14,
    "STOCH": 14,
}

# Overbought/Oversold levels
OVERSOLD_LEVEL: float = 30.0
OVERBOUGHT_LEVEL: float = 70.0

# Trend strength thresholds
TREND_WEAK: float = 25.0
TREND_STRONG: float = 50.0

# Volume thresholds
VOLUME_LOW: float = 0.5
VOLUME_HIGH: float = 2.0

# ==================== Database Constants ====================

# Default pool sizes
DB_POOL_SIZE: int = 10
DB_MAX_OVERFLOW: int = 20
DB_POOL_TIMEOUT: int = 30

# Redis settings
REDIS_MAX_CONNECTIONS: int = 50
REDIS_SOCKET_TIMEOUT: int = 5
REDIS_TTL_DEFAULT: int = 3600  # 1 hour

# ==================== Monitoring Constants ====================

# Dashboard settings
DASHBOARD_PORT: int = 8080
DASHBOARD_HOST: str = "0.0.0.0"

# Metrics collection interval (seconds)
METRICS_INTERVAL: int = 60

# Health check interval (seconds)
HEALTH_CHECK_INTERVAL: int = 30

# Alert cooldown period (seconds)
ALERT_COOLDOWN: int = 300  # 5 minutes

# ==================== Performance Constants ====================

# Thread pool settings
MAX_WORKERS: int = 4
THREAD_POOL_SIZE: int = 10

# Event loop interval (seconds)
EVENT_LOOP_INTERVAL: float = 0.1

# Cache settings
CACHE_TTL_SHORT: int = 60  # 1 minute
CACHE_TTL_MEDIUM: int = 300  # 5 minutes
CACHE_TTL_LONG: int = 3600  # 1 hour

# ==================== File System Constants ====================

# Directories
LOG_DIR: str = "logs"
DATA_DIR: str = "data"
CONFIG_DIR: str = "config"
CACHE_DIR: str = "cache"
BACKUP_DIR: str = "backups"

# File extensions
CONFIG_EXTENSIONS: Tuple[str, ...] = (".yaml", ".yml", ".json")
DATA_EXTENSIONS: Tuple[str, ...] = (".csv", ".parquet", ".feather")

# File size limits (bytes)
MAX_LOG_SIZE: int = 10 * 1024 * 1024  # 10 MB
MAX_CACHE_SIZE: int = 100 * 1024 * 1024  # 100 MB

# ==================== Validation Constants ====================

# Symbol patterns
SYMBOL_PATTERN: str = r"^[A-Z]{2,10}(USDT|BUSD|BTC|ETH)$"

# API key patterns
API_KEY_LENGTH: int = 64
API_SECRET_LENGTH: int = 64

# Price precision limits
MIN_PRICE_PRECISION: int = 0
MAX_PRICE_PRECISION: int = 8

# Quantity precision limits
MIN_QUANTITY_PRECISION: int = 0
MAX_QUANTITY_PRECISION: int = 8

# ==================== Message Templates ====================

# Error messages
ERROR_MESSAGES: Dict[str, str] = {
    "CONFIG_NOT_FOUND": "Configuration file not found: {file}",
    "CONFIG_INVALID": "Invalid configuration: {error}",
    "API_KEY_MISSING": "API key not configured for {exchange}",
    "INSUFFICIENT_BALANCE": "Insufficient balance: required {required}, available {available}",
    "ORDER_SIZE_TOO_SMALL": "Order size {size} is below minimum {minimum}",
    "POSITION_LIMIT_REACHED": "Maximum position limit reached: {limit}",
    "DAILY_LOSS_LIMIT": "Daily loss limit reached: {loss}",
}

# Success messages
SUCCESS_MESSAGES: Dict[str, str] = {
    "ORDER_PLACED": "Order placed successfully: {order_id}",
    "ORDER_CANCELLED": "Order cancelled: {order_id}",
    "POSITION_OPENED": "Position opened: {symbol} {side}",
    "POSITION_CLOSED": "Position closed: {symbol} P/L: {pnl}",
}

# ==================== Feature Flags ====================

FEATURE_FLAGS: Dict[str, bool] = {
    "PAPER_TRADING": True,
    "BACKTESTING": True,
    "MULTI_EXCHANGE": False,
    "MARGIN_TRADING": True,
    "FUTURES_TRADING": True,
    "OPTIONS_TRADING": False,
    "SOCIAL_TRADING": False,
    "AI_PREDICTIONS": False,
}

# ==================== Version Info ====================

VERSION: str = "1.0.0"
API_VERSION: str = "v1"
MIN_PYTHON_VERSION: Tuple[int, int] = (3, 9)
