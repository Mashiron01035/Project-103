"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : ticker
    Project     : Project-103
    Description : Ticker model for market data. This module provides ticker data models for real-time price information.

    SPDX-License-Identifier: AGPL-3.0

"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.models.base import BaseModel, PriceMixin, SerializableMixin, Timestamped, TimestampMixin


@dataclass
class Ticker(BaseModel, TimestampMixin, PriceMixin, SerializableMixin):
    """Real-time ticker data model."""

    # Required fields
    symbol: str
    last_price: float
    timestamp: int  # Unix timestamp in milliseconds

    # Bid/Ask
    bid_price: Optional[float] = None
    bid_size: Optional[float] = None
    ask_price: Optional[float] = None
    ask_size: Optional[float] = None

    # 24h stats
    open_24h: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    close_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    quote_volume_24h: Optional[float] = None
    trades_24h: Optional[int] = None

    # Price changes
    price_change_24h: Optional[float] = None
    price_change_percentage_24h: Optional[float] = None

    # Additional data
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    funding_rate: Optional[float] = None
    next_funding_time: Optional[int] = None
    open_interest: Optional[float] = None
    open_interest_value: Optional[float] = None

    # Metadata
    exchange: str = "unknown"
    source: str = "unknown"

    def validate(self) -> List[str]:
        """
        Validate ticker data.

        Returns:
            List[str]: Validation errors
        """
        errors = []

        # Check required fields
        if not self.symbol:
            errors.append("Symbol is required")
        if self.last_price <= 0:
            errors.append("Last price must be positive")
        if self.timestamp <= 0:
            errors.append("Invalid timestamp")

        # Check bid/ask relationship
        if self.bid_price and self.ask_price:
            if self.bid_price >= self.ask_price:
                errors.append("Bid price must be less than ask price")

        # Check 24h high/low
        if self.high_24h and self.low_24h:
            if self.high_24h < self.low_24h:
                errors.append("24h high must be greater than 24h low")

        # Check volumes
        if self.volume_24h is not None and self.volume_24h < 0:
            errors.append("Volume cannot be negative")

        return errors

    @property
    def spread(self) -> Optional[float]:
        """Get bid-ask spread."""
        if self.bid_price and self.ask_price:
            return self.ask_price - self.bid_price
        return None

    @property
    def spread_percentage(self) -> Optional[float]:
        """Get spread as percentage of mid price."""
        if self.bid_price and self.ask_price:
            mid_price = self.mid_price
            if mid_price > 0:
                return (self.spread / mid_price) * 100
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Get mid price between bid and ask."""
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / 2
        return self.last_price

    @property
    def is_liquid(self, min_volume: float = 100000) -> bool:
        """
        Check if market is liquid.

        Args:
            min_volume: Minimum 24h volume for liquidity

        Returns:
            bool: True if liquid
        """
        if self.volume_24h:
            return self.volume_24h >= min_volume
        return False

    @property
    def price_range_24h(self) -> Optional[float]:
        """Get 24h price range."""
        if self.high_24h and self.low_24h:
            return self.high_24h - self.low_24h
        return None

    @property
    def price_range_percentage_24h(self) -> Optional[float]:
        """Get 24h price range as percentage."""
        if self.high_24h and self.low_24h and self.low_24h > 0:
            return ((self.high_24h - self.low_24h) / self.low_24h) * 100
        return None

    @property
    def is_trending_up(self) -> bool:
        """Check if price is trending up (24h)."""
        if self.price_change_percentage_24h:
            return self.price_change_percentage_24h > 0
        return False

    @property
    def is_trending_down(self) -> bool:
        """Check if price is trending down (24h)."""
        if self.price_change_percentage_24h:
            return self.price_change_percentage_24h < 0
        return False

    @property
    def funding_rate_annual(self) -> Optional[float]:
        """Get annualized funding rate."""
        if self.funding_rate is not None:
            # Assuming 8-hour funding periods (3 per day)
            return self.funding_rate * 3 * 365 * 100
        return None

    @property
    def time_to_funding(self) -> Optional[int]:
        """Get time to next funding in seconds."""
        if self.next_funding_time:
            return max(0, (self.next_funding_time - self.timestamp) // 1000)
        return None

    def get_price_level(self) -> str:
        """
        Get price level relative to 24h range.

        Returns:
            str: 'high', 'mid', 'low', or 'unknown'
        """
        if not (self.high_24h and self.low_24h):
            return "unknown"

        range_size = self.high_24h - self.low_24h
        if range_size == 0:
            return "mid"

        position = (self.last_price - self.low_24h) / range_size

        if position > 0.7:
            return "high"
        elif position < 0.3:
            return "low"
        return "mid"

    def calculate_volatility(self) -> Optional[float]:
        """
        Calculate simple volatility metric.

        Returns:
            Optional[float]: Volatility percentage
        """
        if self.high_24h and self.low_24h and self.last_price > 0:
            return ((self.high_24h - self.low_24h) / self.last_price) * 100
        return None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ticker":
        """Create ticker from dictionary."""
        return cls(
            symbol=data["symbol"],
            last_price=float(data["last_price"]),
            timestamp=int(data["timestamp"]),
            bid_price=data.get("bid_price"),
            bid_size=data.get("bid_size"),
            ask_price=data.get("ask_price"),
            ask_size=data.get("ask_size"),
            open_24h=data.get("open_24h"),
            high_24h=data.get("high_24h"),
            low_24h=data.get("low_24h"),
            close_24h=data.get("close_24h"),
            volume_24h=data.get("volume_24h"),
            quote_volume_24h=data.get("quote_volume_24h"),
            trades_24h=data.get("trades_24h"),
            price_change_24h=data.get("price_change_24h"),
            price_change_percentage_24h=data.get("price_change_percentage_24h"),
            mark_price=data.get("mark_price"),
            index_price=data.get("index_price"),
            funding_rate=data.get("funding_rate"),
            next_funding_time=data.get("next_funding_time"),
            open_interest=data.get("open_interest"),
            open_interest_value=data.get("open_interest_value"),
            exchange=data.get("exchange", "unknown"),
            source=data.get("source", "unknown"),
        )

    @classmethod
    def from_bybit(cls, data: Dict[str, Any]) -> "Ticker":
        """Create ticker from Bybit API data."""
        return cls(
            symbol=data.get("symbol"),
            last_price=float(data.get("lastPrice", 0)),
            timestamp=int(data.get("time", 0)),
            bid_price=float(data.get("bid1Price", 0)) if data.get("bid1Price") else None,
            bid_size=float(data.get("bid1Size", 0)) if data.get("bid1Size") else None,
            ask_price=float(data.get("ask1Price", 0)) if data.get("ask1Price") else None,
            ask_size=float(data.get("ask1Size", 0)) if data.get("ask1Size") else None,
            open_24h=float(data.get("prevPrice24h", 0)) if data.get("prevPrice24h") else None,
            high_24h=float(data.get("highPrice24h", 0)) if data.get("highPrice24h") else None,
            low_24h=float(data.get("lowPrice24h", 0)) if data.get("lowPrice24h") else None,
            volume_24h=float(data.get("volume24h", 0)) if data.get("volume24h") else None,
            quote_volume_24h=float(data.get("turnover24h", 0)) if data.get("turnover24h") else None,
            price_change_percentage_24h=float(data.get("price24hPcnt", 0)) * 100
            if data.get("price24hPcnt")
            else None,
            mark_price=float(data.get("markPrice", 0)) if data.get("markPrice") else None,
            index_price=float(data.get("indexPrice", 0)) if data.get("indexPrice") else None,
            funding_rate=float(data.get("fundingRate", 0)) if data.get("fundingRate") else None,
            next_funding_time=int(data.get("nextFundingTime", 0))
            if data.get("nextFundingTime")
            else None,
            open_interest=float(data.get("openInterest", 0)) if data.get("openInterest") else None,
            open_interest_value=float(data.get("openInterestValue", 0))
            if data.get("openInterestValue")
            else None,
            exchange="bybit",
            source="bybit_api",
        )

    def __repr__(self) -> str:
        """String representation."""
        change = ""
        if self.price_change_percentage_24h:
            arrow = "↑" if self.price_change_percentage_24h > 0 else "↓"
            change = f" {arrow}{abs(self.price_change_percentage_24h):.2f}%"

        spread_info = ""
        if self.spread_percentage:
            spread_info = f" Spread:{self.spread_percentage:.3f}%"

        return f"Ticker({self.symbol} ${self.last_price:.4f}{change}{spread_info})"


@dataclass
class TickerSnapshot(Timestamped):
    """Snapshot of multiple tickers at a point in time."""

    tickers: Dict[str, Ticker] = field(default_factory=dict)
    exchange: str = "unknown"

    def add_ticker(self, ticker: Ticker) -> None:
        """
        Add ticker to snapshot.

        Args:
            ticker: Ticker to add
        """
        self.tickers[ticker.symbol] = ticker
        self.touch()

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get ticker by symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Ticker if found
        """
        return self.tickers.get(symbol)

    def get_symbols(self) -> List[str]:
        """Get list of symbols in snapshot."""
        return list(self.tickers.keys())

    def get_top_gainers(self, limit: int = 10) -> List[Ticker]:
        """
        Get top gaining tickers.

        Args:
            limit: Maximum number of tickers

        Returns:
            List[Ticker]: Top gainers
        """
        tickers_with_change = [
            t for t in self.tickers.values() if t.price_change_percentage_24h is not None
        ]

        sorted_tickers = sorted(
            tickers_with_change, key=lambda t: t.price_change_percentage_24h, reverse=True
        )

        return sorted_tickers[:limit]

    def get_top_losers(self, limit: int = 10) -> List[Ticker]:
        """
        Get top losing tickers.

        Args:
            limit: Maximum number of tickers

        Returns:
            List[Ticker]: Top losers
        """
        tickers_with_change = [
            t for t in self.tickers.values() if t.price_change_percentage_24h is not None
        ]

        sorted_tickers = sorted(tickers_with_change, key=lambda t: t.price_change_percentage_24h)

        return sorted_tickers[:limit]

    def get_most_volatile(self, limit: int = 10) -> List[Ticker]:
        """
        Get most volatile tickers.

        Args:
            limit: Maximum number of tickers

        Returns:
            List[Ticker]: Most volatile tickers
        """
        tickers_with_vol = []
        for ticker in self.tickers.values():
            vol = ticker.calculate_volatility()
            if vol is not None:
                tickers_with_vol.append((ticker, vol))

        sorted_tickers = sorted(tickers_with_vol, key=lambda x: x[1], reverse=True)

        return [t for t, _ in sorted_tickers[:limit]]

    def get_highest_volume(self, limit: int = 10) -> List[Ticker]:
        """
        Get highest volume tickers.

        Args:
            limit: Maximum number of tickers

        Returns:
            List[Ticker]: Highest volume tickers
        """
        tickers_with_volume = [t for t in self.tickers.values() if t.volume_24h is not None]

        sorted_tickers = sorted(tickers_with_volume, key=lambda t: t.volume_24h, reverse=True)

        return sorted_tickers[:limit]

    def filter_by_criteria(
        self,
        min_volume: Optional[float] = None,
        min_price_change: Optional[float] = None,
        max_spread: Optional[float] = None,
    ) -> List[Ticker]:
        """
        Filter tickers by criteria.

        Args:
            min_volume: Minimum 24h volume
            min_price_change: Minimum price change percentage
            max_spread: Maximum spread percentage

        Returns:
            List[Ticker]: Filtered tickers
        """
        filtered = list(self.tickers.values())

        if min_volume is not None:
            filtered = [t for t in filtered if t.volume_24h and t.volume_24h >= min_volume]

        if min_price_change is not None:
            filtered = [
                t
                for t in filtered
                if t.price_change_percentage_24h
                and abs(t.price_change_percentage_24h) >= min_price_change
            ]

        if max_spread is not None:
            filtered = [
                t for t in filtered if t.spread_percentage and t.spread_percentage <= max_spread
            ]

        return filtered

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "exchange": self.exchange,
            "tickers": {symbol: ticker.to_dict() for symbol, ticker in self.tickers.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TickerSnapshot":
        """Create from dictionary."""
        snapshot = cls(exchange=data.get("exchange", "unknown"))
        snapshot.created_at = data.get("created_at", snapshot.created_at)
        snapshot.updated_at = data.get("updated_at", snapshot.updated_at)

        for symbol, ticker_data in data.get("tickers", {}).items():
            ticker = Ticker.from_dict(ticker_data)
            snapshot.add_ticker(ticker)

        return snapshot
