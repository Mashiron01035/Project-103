"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : candle
    Project     : Project-103
    Description : Candle (OHLCV) model. This module provides the candle data model with pattern recognition.

    SPDX-License-Identifier: AGPL-3.0

"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.base import BaseModel, PriceMixin, SerializableMixin, TimestampMixin, ValidatorMixin


class CandlePattern(Enum):
    """Candle pattern types."""

    NONE = "none"
    DOJI = "doji"
    HAMMER = "hammer"
    INVERTED_HAMMER = "inverted_hammer"
    SHOOTING_STAR = "shooting_star"
    HANGING_MAN = "hanging_man"
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"
    THREE_WHITE_SOLDIERS = "three_white_soldiers"
    THREE_BLACK_CROWS = "three_black_crows"
    PIERCING_LINE = "piercing_line"
    DARK_CLOUD_COVER = "dark_cloud_cover"
    HARAMI = "harami"
    MARUBOZU = "marubozu"


@dataclass
class Candle(BaseModel, TimestampMixin, PriceMixin, SerializableMixin, ValidatorMixin):
    """Candle (OHLCV) data model."""

    # Required fields
    symbol: str
    timeframe: str
    timestamp: int  # Unix timestamp in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    # Optional fields
    trades_count: Optional[int] = None
    buy_volume: Optional[float] = None
    sell_volume: Optional[float] = None
    quote_volume: Optional[float] = None  # Volume in quote currency (USDT)
    taker_buy_volume: Optional[float] = None
    taker_sell_volume: Optional[float] = None

    # Spread (average spread in price units, e.g. 0.20 for XAUUSD)
    spread: Optional[float] = None

    # Metadata
    is_closed: bool = True  # Is candle closed
    source: str = "unknown"  # Data source
    exchange: str = "unknown"  # Exchange name

    # Calculated fields (set after initialization)
    patterns: List[CandlePattern] = field(default_factory=list)
    strength: Optional[float] = None  # Pattern strength 0-1

    def __post_init__(self):
        """Validate after initialization."""
        errors = self.validate()
        if errors:
            error_messages = [str(e) for e in errors]
            raise ValueError(f"Candle validation failed: {', '.join(error_messages)}")

    def validate(self) -> List[str]:
        """
        Validate candle data.

        Returns:
            List[str]: Validation errors
        """
        errors = []

        # Check price relationships
        if self.high < self.low:
            errors.append("High price cannot be less than low price")
        if self.open < self.low or self.open > self.high:
            errors.append("Open price must be between low and high")
        if self.close < self.low or self.close > self.high:
            errors.append("Close price must be between low and high")

        # Check volume
        if self.volume < 0:
            errors.append("Volume cannot be negative")

        # Check timestamp
        if self.timestamp <= 0:
            errors.append("Invalid timestamp")

        # Check optional volumes
        if self.buy_volume is not None and self.buy_volume < 0:
            errors.append("Buy volume cannot be negative")
        if self.sell_volume is not None and self.sell_volume < 0:
            errors.append("Sell volume cannot be negative")

        # Check volume consistency
        if self.buy_volume and self.sell_volume:
            total = self.buy_volume + self.sell_volume
            if abs(total - self.volume) > self.volume * 0.01:  # 1% tolerance
                errors.append("Buy + Sell volume doesn't match total volume")

        return errors

    # ==================== Price Properties ====================

    @property
    def is_bullish(self) -> bool:
        """Check if candle is bullish (green)."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Check if candle is bearish (red)."""
        return self.close < self.open

    @property
    def is_neutral(self) -> bool:
        """Check if candle is neutral (doji-like)."""
        return abs(self.close - self.open) < self.average_price * 0.001  # 0.1% threshold

    @property
    def body_size(self) -> float:
        """Get candle body size."""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        """Get upper shadow size."""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """Get lower shadow size."""
        return min(self.open, self.close) - self.low

    @property
    def total_range(self) -> float:
        """Get total price range."""
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        """Get body to total range ratio."""
        if self.total_range == 0:
            return 0
        return self.body_size / self.total_range

    @property
    def upper_shadow_ratio(self) -> float:
        """Get upper shadow to total range ratio."""
        if self.total_range == 0:
            return 0
        return self.upper_shadow / self.total_range

    @property
    def lower_shadow_ratio(self) -> float:
        """Get lower shadow to total range ratio."""
        if self.total_range == 0:
            return 0
        return self.lower_shadow / self.total_range

    # ==================== Calculated Prices ====================

    @property
    def typical_price(self) -> float:
        """Get typical price (HLC/3)."""
        return (self.high + self.low + self.close) / 3

    @property
    def weighted_close(self) -> float:
        """Get weighted close price (HLCC/4)."""
        return (self.high + self.low + self.close * 2) / 4

    @property
    def average_price(self) -> float:
        """Get average price (OHLC/4)."""
        return (self.open + self.high + self.low + self.close) / 4

    @property
    def midpoint(self) -> float:
        """Get midpoint price (HL/2)."""
        return (self.high + self.low) / 2

    @property
    def pivot_point(self) -> float:
        """Get pivot point."""
        return self.typical_price

    @property
    def resistance1(self) -> float:
        """Get first resistance level."""
        return 2 * self.pivot_point - self.low

    @property
    def support1(self) -> float:
        """Get first support level."""
        return 2 * self.pivot_point - self.high

    @property
    def vwap(self) -> float:
        """Get VWAP (Volume Weighted Average Price)."""
        if self.quote_volume and self.volume > 0:
            return self.quote_volume / self.volume
        return self.typical_price

    # ==================== Volume Properties ====================

    @property
    def buy_sell_ratio(self) -> Optional[float]:
        """Get buy/sell volume ratio."""
        if self.buy_volume and self.sell_volume and self.sell_volume > 0:
            return self.buy_volume / self.sell_volume
        return None

    @property
    def buy_volume_percentage(self) -> Optional[float]:
        """Get buy volume percentage."""
        if self.buy_volume and self.volume > 0:
            return (self.buy_volume / self.volume) * 100
        return None

    @property
    def volume_trend(self) -> str:
        """Get volume trend based on buy/sell ratio."""
        ratio = self.buy_sell_ratio
        if ratio is None:
            return "unknown"
        if ratio > 1.5:
            return "strong_buy"
        elif ratio > 1.1:
            return "buy"
        elif ratio < 0.67:
            return "strong_sell"
        elif ratio < 0.9:
            return "sell"
        return "neutral"

    # ==================== Pattern Detection ====================

    def is_doji(self, threshold: float = 0.1) -> bool:
        """
        Check if candle is a doji.

        Args:
            threshold: Body ratio threshold

        Returns:
            bool: True if doji
        """
        return self.body_ratio < threshold

    def is_hammer(self, min_shadow_ratio: float = 2.0) -> bool:
        """
        Check if candle is a hammer.

        Args:
            min_shadow_ratio: Minimum lower shadow to body ratio

        Returns:
            bool: True if hammer
        """
        if not self.is_bullish or self.body_size == 0:
            return False
        return (
            self.lower_shadow >= self.body_size * min_shadow_ratio
            and self.upper_shadow < self.body_size * 0.3
        )

    def is_inverted_hammer(self, min_shadow_ratio: float = 2.0) -> bool:
        """
        Check if candle is an inverted hammer.

        Args:
            min_shadow_ratio: Minimum upper shadow to body ratio

        Returns:
            bool: True if inverted hammer
        """
        if not self.is_bullish or self.body_size == 0:
            return False
        return (
            self.upper_shadow >= self.body_size * min_shadow_ratio
            and self.lower_shadow < self.body_size * 0.3
        )

    def is_shooting_star(self, min_shadow_ratio: float = 2.0) -> bool:
        """
        Check if candle is a shooting star.

        Args:
            min_shadow_ratio: Minimum upper shadow to body ratio

        Returns:
            bool: True if shooting star
        """
        if not self.is_bearish or self.body_size == 0:
            return False
        return (
            self.upper_shadow >= self.body_size * min_shadow_ratio
            and self.lower_shadow < self.body_size * 0.3
        )

    def is_hanging_man(self, min_shadow_ratio: float = 2.0) -> bool:
        """
        Check if candle is a hanging man.

        Args:
            min_shadow_ratio: Minimum lower shadow to body ratio

        Returns:
            bool: True if hanging man
        """
        if not self.is_bearish or self.body_size == 0:
            return False
        return (
            self.lower_shadow >= self.body_size * min_shadow_ratio
            and self.upper_shadow < self.body_size * 0.3
        )

    def is_marubozu(self, shadow_threshold: float = 0.01) -> bool:
        """
        Check if candle is a marubozu (no shadows).

        Args:
            shadow_threshold: Maximum shadow ratio

        Returns:
            bool: True if marubozu
        """
        return (
            self.upper_shadow_ratio < shadow_threshold
            and self.lower_shadow_ratio < shadow_threshold
        )

    def is_engulfing(self, previous: "Candle") -> Optional[str]:
        """
        Check if candle forms an engulfing pattern.

        Args:
            previous: Previous candle

        Returns:
            Optional[str]: 'bullish', 'bearish', or None
        """
        if not previous:
            return None

        # Bullish engulfing
        if (
            self.is_bullish
            and previous.is_bearish
            and self.open <= previous.close
            and self.close >= previous.open
        ):
            return "bullish"

        # Bearish engulfing
        if (
            self.is_bearish
            and previous.is_bullish
            and self.open >= previous.close
            and self.close <= previous.open
        ):
            return "bearish"

        return None

    def detect_patterns(self, previous: Optional["Candle"] = None) -> List[CandlePattern]:
        """
        Detect all patterns in this candle.

        Args:
            previous: Previous candle for multi-candle patterns

        Returns:
            List[CandlePattern]: Detected patterns
        """
        patterns = []

        # Single candle patterns
        if self.is_doji():
            patterns.append(CandlePattern.DOJI)
        if self.is_hammer():
            patterns.append(CandlePattern.HAMMER)
        if self.is_inverted_hammer():
            patterns.append(CandlePattern.INVERTED_HAMMER)
        if self.is_shooting_star():
            patterns.append(CandlePattern.SHOOTING_STAR)
        if self.is_hanging_man():
            patterns.append(CandlePattern.HANGING_MAN)
        if self.is_marubozu():
            patterns.append(CandlePattern.MARUBOZU)

        # Multi-candle patterns
        if previous:
            engulfing = self.is_engulfing(previous)
            if engulfing == "bullish":
                patterns.append(CandlePattern.BULLISH_ENGULFING)
            elif engulfing == "bearish":
                patterns.append(CandlePattern.BEARISH_ENGULFING)

        self.patterns = patterns
        return patterns

    # ==================== Conversion Methods ====================

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Candle":
        """
        Create candle from dictionary.

        Args:
            data: Dictionary with candle data

        Returns:
            Candle: Candle instance
        """
        return cls(
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            timestamp=int(data["timestamp"]),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=float(data["volume"]),
            trades_count=data.get("trades_count"),
            buy_volume=data.get("buy_volume"),
            sell_volume=data.get("sell_volume"),
            quote_volume=data.get("quote_volume"),
            taker_buy_volume=data.get("taker_buy_volume"),
            taker_sell_volume=data.get("taker_sell_volume"),
            is_closed=data.get("is_closed", True),
            source=data.get("source", "unknown"),
            exchange=data.get("exchange", "unknown"),
            patterns=[CandlePattern(p) for p in data.get("patterns", [])]
            if "patterns" in data
            else [],
            strength=data.get("strength"),
        )

    @classmethod
    def from_bybit(cls, data: Dict[str, Any], symbol: str, timeframe: str) -> "Candle":
        """
        Create candle from Bybit API data.

        Args:
            data: Bybit kline data
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Candle: Candle instance
        """
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=int(data.get("start_at", data.get("start", 0))) * 1000,
            open=float(data.get("open", 0)),
            high=float(data.get("high", 0)),
            low=float(data.get("low", 0)),
            close=float(data.get("close", 0)),
            volume=float(data.get("volume", 0)),
            quote_volume=float(data.get("turnover", 0)),
            is_closed=True,
            source="bybit",
            exchange="bybit",
        )

    @classmethod
    def from_binance(cls, data: List, symbol: str, timeframe: str) -> "Candle":
        """
        Create candle from Binance API data.

        Args:
            data: Binance kline data (list format)
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Candle: Candle instance
        """
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=int(data[0]),
            open=float(data[1]),
            high=float(data[2]),
            low=float(data[3]),
            close=float(data[4]),
            volume=float(data[5]),
            quote_volume=float(data[7]) if len(data) > 7 else None,
            trades_count=int(data[8]) if len(data) > 8 else None,
            taker_buy_volume=float(data[9]) if len(data) > 9 else None,
            is_closed=bool(data[6]) if len(data) > 6 else True,
            source="binance",
            exchange="binance",
        )

    def to_trading_view(self) -> Dict[str, Any]:
        """
        Convert to TradingView format.

        Returns:
            Dict: TradingView compatible format
        """
        return {
            "time": self.timestamp // 1000,  # TradingView uses seconds
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    def __repr__(self) -> str:
        """String representation."""
        direction = "↑" if self.is_bullish else "↓" if self.is_bearish else "→"
        return (
            f"Candle({self.symbol} {self.timeframe} "
            f"{direction} O:{self.open:.2f} H:{self.high:.2f} "
            f"L:{self.low:.2f} C:{self.close:.2f} V:{self.volume:.0f})"
        )
