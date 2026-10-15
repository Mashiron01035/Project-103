"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : enums
    Project     : Project-103
    Description : Enumeration classes for the trading bot system.This module defines all enum types used for type safety and code clarity.

    SPDX-License-Identifier: AGPL-3.0

"""

from enum import Enum, IntEnum
from typing import Optional


class TimeFrame(Enum):
    """Trading timeframes."""

    M1 = "1m"  # 1 minute
    M2 = "2m"  # 2 minutes
    M3 = "3m"  # 3 minutes
    M5 = "5m"  # 5 minutes
    M15 = "15m"  # 15 minutes
    M30 = "30m"  # 30 minutes
    H1 = "1h"  # 1 hour
    H2 = "2h"  # 2 hours
    H4 = "4h"  # 4 hours
    H6 = "6h"  # 6 hours
    H12 = "12h"  # 12 hours
    D1 = "1d"  # 1 day
    W1 = "1w"  # 1 week
    MN = "1M"  # 1 month

    @property
    def seconds(self) -> int:
        """Get timeframe duration in seconds."""
        mapping = {
            self.M1: 60,
            self.M2: 120,
            self.M3: 180,
            self.M5: 300,
            self.M15: 900,
            self.M30: 1800,
            self.H1: 3600,
            self.H2: 7200,
            self.H4: 14400,
            self.H6: 21600,
            self.H12: 43200,
            self.D1: 86400,
            self.W1: 604800,
            self.MN: 2592000,
        }
        return mapping[self]

    @classmethod
    def from_string(cls, value: str) -> Optional["TimeFrame"]:
        """Create TimeFrame from string value."""
        for member in cls:
            if member.value == value:
                return member
        return None


class OrderType(Enum):
    """Order types."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"

    def is_stop_order(self) -> bool:
        """Check if this is a stop order."""
        return self in [
            self.STOP_LOSS,
            self.STOP_LOSS_LIMIT,
            self.TRAILING_STOP,
        ]

    def is_limit_order(self) -> bool:
        """Check if this is a limit order."""
        return self in [
            self.LIMIT,
            self.STOP_LOSS_LIMIT,
            self.TAKE_PROFIT_LIMIT,
        ]


class OrderSide(Enum):
    """Order side (buy/sell)."""

    BUY = "BUY"
    SELL = "SELL"

    def opposite(self) -> "OrderSide":
        """Get opposite side."""
        return self.SELL if self == self.BUY else self.BUY


class OrderStatus(Enum):
    """Order status."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PENDING_CANCEL = "PENDING_CANCEL"

    def is_active(self) -> bool:
        """Check if order is active."""
        return self in [
            self.NEW,
            self.PARTIALLY_FILLED,
            self.PENDING_CANCEL,
        ]

    def is_terminal(self) -> bool:
        """Check if order is in terminal state."""
        return self in [
            self.FILLED,
            self.CANCELLED,
            self.REJECTED,
            self.EXPIRED,
        ]


class PositionSide(Enum):
    """Position side (long/short)."""

    LONG = "LONG"
    SHORT = "SHORT"

    def opposite(self) -> "PositionSide":
        """Get opposite side."""
        return self.SHORT if self == self.LONG else self.LONG

    def to_order_side(self, closing: bool = False) -> OrderSide:
        """Convert to order side."""
        if closing:
            return OrderSide.SELL if self == self.LONG else OrderSide.BUY
        else:
            return OrderSide.BUY if self == self.LONG else OrderSide.SELL


class PositionStatus(Enum):
    """Position status."""

    PENDING = "PENDING"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"

    def is_active(self) -> bool:
        """Check if position is active."""
        return self in [
            self.PENDING,
            self.OPENING,
            self.OPEN,
            self.CLOSING,
        ]


class SignalType(Enum):
    """Trading signal types."""

    BUY = "BUY"
    SELL = "SELL"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    HOLD = "HOLD"

    def is_entry(self) -> bool:
        """Check if this is an entry signal."""
        return self in [self.BUY, self.SELL]

    def is_exit(self) -> bool:
        """Check if this is an exit signal."""
        return self in [self.CLOSE_LONG, self.CLOSE_SHORT]

    def to_position_side(self) -> Optional[PositionSide]:
        """Convert to position side."""
        if self == self.BUY:
            return PositionSide.LONG
        elif self == self.SELL:
            return PositionSide.SHORT
        return None


class SignalStrength(IntEnum):
    """Signal strength levels."""

    WEAK = 1
    MODERATE = 2
    STRONG = 3
    VERY_STRONG = 4

    @classmethod
    def from_confidence(cls, confidence: float) -> "SignalStrength":
        """Create from confidence value (0-1)."""
        if confidence < 0.4:
            return cls.WEAK
        elif confidence < 0.6:
            return cls.MODERATE
        elif confidence < 0.8:
            return cls.STRONG
        else:
            return cls.VERY_STRONG


class RiskLevel(IntEnum):
    """Risk levels."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def risk_percentage(self) -> float:
        """Get risk percentage."""
        mapping = {
            self.LOW: 0.01,  # 1%
            self.MEDIUM: 0.02,  # 2%
            self.HIGH: 0.03,  # 3%
        }
        return mapping[self]

    @property
    def risk_reward_ratio(self) -> float:
        """Get risk:reward ratio."""
        mapping = {
            self.LOW: 2.0,  # 1:2
            self.MEDIUM: 2.5,  # 1:2.5
            self.HIGH: 3.0,  # 1:3
        }
        return mapping[self]


class MarketCondition(Enum):
    """Market conditions."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    QUIET = "QUIET"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"

    def is_trending(self) -> bool:
        """Check if market is trending."""
        return self in [
            self.TRENDING_UP,
            self.TRENDING_DOWN,
            self.BREAKOUT,
            self.BREAKDOWN,
        ]

    def is_sideways(self) -> bool:
        """Check if market is moving sideways."""
        return self in [self.RANGING, self.QUIET]


class IndicatorType(Enum):
    """Technical indicator types."""

    # Trend indicators
    SMA = "SMA"
    EMA = "EMA"
    WMA = "WMA"
    MACD = "MACD"
    ADX = "ADX"
    SUPERTREND = "SUPERTREND"
    ICHIMOKU = "ICHIMOKU"

    # Oscillators
    RSI = "RSI"
    STOCHASTIC = "STOCHASTIC"
    CCI = "CCI"
    WILLIAMS_R = "WILLIAMS_R"
    MFI = "MFI"

    # Volatility indicators
    BOLLINGER_BANDS = "BOLLINGER_BANDS"
    ATR = "ATR"
    KELTNER_CHANNEL = "KELTNER_CHANNEL"
    DONCHIAN_CHANNEL = "DONCHIAN_CHANNEL"

    # Volume indicators
    OBV = "OBV"
    VOLUME_PROFILE = "VOLUME_PROFILE"
    VWAP = "VWAP"
    CMF = "CMF"

    # Custom indicators
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    FIBONACCI = "FIBONACCI"
    PIVOT_POINTS = "PIVOT_POINTS"

    def is_oscillator(self) -> bool:
        """Check if this is an oscillator indicator."""
        return self in [
            self.RSI,
            self.STOCHASTIC,
            self.CCI,
            self.WILLIAMS_R,
            self.MFI,
        ]

    def is_trend(self) -> bool:
        """Check if this is a trend indicator."""
        return self in [
            self.SMA,
            self.EMA,
            self.WMA,
            self.MACD,
            self.ADX,
            self.SUPERTREND,
            self.ICHIMOKU,
        ]


class StrategyType(Enum):
    """Trading strategy types."""

    TREND_FOLLOWING = "TREND_FOLLOWING"
    MEAN_REVERSION = "MEAN_REVERSION"
    MOMENTUM = "MOMENTUM"
    BREAKOUT = "BREAKOUT"
    SCALPING = "SCALPING"
    SWING = "SWING"
    ARBITRAGE = "ARBITRAGE"
    MARKET_MAKING = "MARKET_MAKING"
    GRID = "GRID"
    DCA = "DCA"
    HYBRID = "HYBRID"

    @property
    def default_timeframes(self) -> list[TimeFrame]:
        """Get default timeframes for strategy."""
        mapping = {
            self.SCALPING: [TimeFrame.M1, TimeFrame.M5],
            self.TREND_FOLLOWING: [TimeFrame.H1, TimeFrame.H4],
            self.SWING: [TimeFrame.H4, TimeFrame.D1],
            self.MEAN_REVERSION: [TimeFrame.M15, TimeFrame.H1],
            self.MOMENTUM: [TimeFrame.M15, TimeFrame.M30],
            self.BREAKOUT: [TimeFrame.M15, TimeFrame.H1],
        }
        return mapping.get(self, [TimeFrame.M15])


class ExchangeType(Enum):
    """Exchange types."""

    BYBIT = "BYBIT"
    BINANCE = "BINANCE"
    OKX = "OKX"
    COINBASE = "COINBASE"
    KRAKEN = "KRAKEN"
    HUOBI = "HUOBI"
    GATEIO = "GATEIO"

    @property
    def supports_testnet(self) -> bool:
        """Check if exchange supports testnet."""
        return self in [
            self.BYBIT,
            self.BINANCE,
            self.OKX,
        ]

    @property
    def supports_futures(self) -> bool:
        """Check if exchange supports futures trading."""
        return self in [
            self.BYBIT,
            self.BINANCE,
            self.OKX,
            self.HUOBI,
        ]


class LogLevel(IntEnum):
    """Logging levels."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class NotificationType(Enum):
    """Notification types."""

    SIGNAL = "SIGNAL"
    TRADE_OPEN = "TRADE_OPEN"
    TRADE_CLOSE = "TRADE_CLOSE"
    ALERT = "ALERT"
    ERROR = "ERROR"
    REPORT = "REPORT"
    SYSTEM = "SYSTEM"

    @property
    def priority(self) -> int:
        """Get notification priority (1-5, 5 being highest)."""
        mapping = {
            self.ERROR: 5,
            self.TRADE_OPEN: 4,
            self.TRADE_CLOSE: 4,
            self.ALERT: 3,
            self.SIGNAL: 2,
            self.REPORT: 1,
            self.SYSTEM: 1,
        }
        return mapping[self]


class BacktestMode(Enum):
    """Backtest modes."""

    QUICK = "QUICK"  # Fast, less accurate
    STANDARD = "STANDARD"  # Normal accuracy
    DETAILED = "DETAILED"  # High accuracy, slower

    @property
    def use_tick_data(self) -> bool:
        """Check if mode uses tick data."""
        return self == self.DETAILED

    @property
    def commission_rate(self) -> float:
        """Get commission rate for mode."""
        mapping = {
            self.QUICK: 0.001,  # 0.1%
            self.STANDARD: 0.0007,  # 0.07%
            self.DETAILED: 0.0005,  # 0.05%
        }
        return mapping[self]


class DatabaseOperation(Enum):
    """Database operation types."""

    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    BULK_INSERT = "BULK_INSERT"
    BULK_UPDATE = "BULK_UPDATE"
    QUERY = "QUERY"
    TRANSACTION = "TRANSACTION"


class CacheType(Enum):
    """Cache types."""

    MEMORY = "MEMORY"
    REDIS = "REDIS"
    DISK = "DISK"
    HYBRID = "HYBRID"

    @property
    def default_ttl(self) -> int:
        """Get default TTL in seconds."""
        mapping = {
            self.MEMORY: 60,  # 1 minute
            self.REDIS: 300,  # 5 minutes
            self.DISK: 3600,  # 1 hour
            self.HYBRID: 300,  # 5 minutes
        }
        return mapping[self]
