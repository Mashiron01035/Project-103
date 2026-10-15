"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : orderbook
    Project     : Project-103
    Description : Order book model for market depth data. This module provides order book data models for bid/ask levels.

    SPDX-License-Identifier: AGPL-3.0

"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.models.base import BaseModel, PriceMixin, SerializableMixin, TimestampMixin


@dataclass
class OrderBookLevel:
    """Single order book level (price and size)."""

    price: float
    size: float
    orders_count: Optional[int] = None  # Number of orders at this level

    @property
    def total_value(self) -> float:
        """Get total value at this level."""
        return self.price * self.size

    def to_list(self) -> List[float]:
        """Convert to list format [price, size]."""
        return [self.price, self.size]

    def __repr__(self) -> str:
        """String representation."""
        return f"Level({self.price:.4f} x {self.size:.4f})"


@dataclass
class OrderBook(BaseModel, TimestampMixin, PriceMixin, SerializableMixin):
    """Order book model with bid/ask levels."""

    # Required fields
    symbol: str
    timestamp: int  # Unix timestamp in milliseconds
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)

    # Metadata
    exchange: str = "unknown"
    source: str = "unknown"
    sequence: Optional[int] = None  # Sequence number for updates
    is_snapshot: bool = True  # True if full snapshot, False if update

    def validate(self) -> List[str]:
        """
        Validate order book data.

        Returns:
            List[str]: Validation errors
        """
        errors = []

        if not self.symbol:
            errors.append("Symbol is required")
        if self.timestamp <= 0:
            errors.append("Invalid timestamp")

        # Check bid prices are descending
        for i in range(1, len(self.bids)):
            if self.bids[i].price >= self.bids[i - 1].price:
                errors.append("Bid prices must be in descending order")
                break

        # Check ask prices are ascending
        for i in range(1, len(self.asks)):
            if self.asks[i].price <= self.asks[i - 1].price:
                errors.append("Ask prices must be in ascending order")
                break

        # Check bid/ask overlap
        if self.bids and self.asks:
            if self.bids[0].price >= self.asks[0].price:
                errors.append("Best bid cannot be >= best ask")

        return errors

    @property
    def best_bid(self) -> Optional[OrderBookLevel]:
        """Get best (highest) bid."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderBookLevel]:
        """Get best (lowest) ask."""
        return self.asks[0] if self.asks else None

    @property
    def best_bid_price(self) -> Optional[float]:
        """Get best bid price."""
        return self.best_bid.price if self.best_bid else None

    @property
    def best_ask_price(self) -> Optional[float]:
        """Get best ask price."""
        return self.best_ask.price if self.best_ask else None

    @property
    def best_bid_size(self) -> Optional[float]:
        """Get best bid size."""
        return self.best_bid.size if self.best_bid else None

    @property
    def best_ask_size(self) -> Optional[float]:
        """Get best ask size."""
        return self.best_ask.size if self.best_ask else None

    @property
    def spread(self) -> Optional[float]:
        """Get bid-ask spread."""
        if self.best_bid_price and self.best_ask_price:
            return self.best_ask_price - self.best_bid_price
        return None

    @property
    def spread_percentage(self) -> Optional[float]:
        """Get spread as percentage of mid price."""
        if self.spread is not None and self.mid_price:
            return (self.spread / self.mid_price) * 100
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Get mid price between best bid and ask."""
        if self.best_bid_price and self.best_ask_price:
            return (self.best_bid_price + self.best_ask_price) / 2
        return None

    @property
    def weighted_mid_price(self) -> Optional[float]:
        """Get size-weighted mid price."""
        if self.best_bid and self.best_ask:
            total_size = self.best_bid_size + self.best_ask_size
            if total_size > 0:
                return (
                    self.best_bid_price * self.best_ask_size
                    + self.best_ask_price * self.best_bid_size
                ) / total_size
        return self.mid_price

    @property
    def depth(self) -> int:
        """Get order book depth (number of levels)."""
        return max(len(self.bids), len(self.asks))

    def get_bid_depth(self, levels: int = None) -> float:
        """
        Get total bid depth (volume).

        Args:
            levels: Number of levels to include (None for all)

        Returns:
            float: Total bid volume
        """
        if levels is None:
            return sum(level.size for level in self.bids)
        return sum(level.size for level in self.bids[:levels])

    def get_ask_depth(self, levels: int = None) -> float:
        """
        Get total ask depth (volume).

        Args:
            levels: Number of levels to include (None for all)

        Returns:
            float: Total ask volume
        """
        if levels is None:
            return sum(level.size for level in self.asks)
        return sum(level.size for level in self.asks[:levels])

    def get_total_bid_value(self, levels: int = None) -> float:
        """
        Get total bid value.

        Args:
            levels: Number of levels to include (None for all)

        Returns:
            float: Total bid value in quote currency
        """
        if levels is None:
            return sum(level.total_value for level in self.bids)
        return sum(level.total_value for level in self.bids[:levels])

    def get_total_ask_value(self, levels: int = None) -> float:
        """
        Get total ask value.

        Args:
            levels: Number of levels to include (None for all)

        Returns:
            float: Total ask value in quote currency
        """
        if levels is None:
            return sum(level.total_value for level in self.asks)
        return sum(level.total_value for level in self.asks[:levels])

    def get_imbalance(self, levels: int = 5) -> float:
        """
        Get order book imbalance.

        Positive = more bids, Negative = more asks

        Args:
            levels: Number of levels to consider

        Returns:
            float: Imbalance ratio (-1 to 1)
        """
        bid_depth = self.get_bid_depth(levels)
        ask_depth = self.get_ask_depth(levels)

        total = bid_depth + ask_depth
        if total == 0:
            return 0

        return (bid_depth - ask_depth) / total

    def get_volume_imbalance(self, levels: int = 5) -> float:
        """
        Get volume imbalance ratio.

        Args:
            levels: Number of levels to consider

        Returns:
            float: Bid volume / Ask volume
        """
        bid_depth = self.get_bid_depth(levels)
        ask_depth = self.get_ask_depth(levels)

        if ask_depth == 0:
            return float("inf") if bid_depth > 0 else 1.0

        return bid_depth / ask_depth

    def get_price_levels(self, depth_percentage: float) -> Tuple[float, float]:
        """
        Get price levels at certain depth percentage.

        Args:
            depth_percentage: Percentage of total depth (0-1)

        Returns:
            Tuple[float, float]: (bid_price, ask_price) at depth
        """
        bid_target = self.get_bid_depth() * depth_percentage
        ask_target = self.get_ask_depth() * depth_percentage

        bid_price = self.best_bid_price
        ask_price = self.best_ask_price

        # Find bid price at depth
        cumulative = 0
        for level in self.bids:
            cumulative += level.size
            if cumulative >= bid_target:
                bid_price = level.price
                break

        # Find ask price at depth
        cumulative = 0
        for level in self.asks:
            cumulative += level.size
            if cumulative >= ask_target:
                ask_price = level.price
                break

        return bid_price, ask_price

    def get_market_impact(self, size: float, is_buy: bool) -> Optional[float]:
        """
        Calculate market impact for a given order size.

        Args:
            size: Order size
            is_buy: True for buy order, False for sell

        Returns:
            Optional[float]: Average execution price
        """
        levels = self.asks if is_buy else self.bids

        if not levels:
            return None

        remaining = size
        total_cost = 0

        for level in levels:
            if remaining <= 0:
                break

            fill_size = min(remaining, level.size)
            total_cost += fill_size * level.price
            remaining -= fill_size

        if remaining > 0:
            # Not enough liquidity
            return None

        return total_cost / size

    def get_slippage(self, size: float, is_buy: bool) -> Optional[float]:
        """
        Calculate slippage for a given order size.

        Args:
            size: Order size
            is_buy: True for buy order, False for sell

        Returns:
            Optional[float]: Slippage percentage
        """
        avg_price = self.get_market_impact(size, is_buy)
        if avg_price is None:
            return None

        reference_price = self.best_ask_price if is_buy else self.best_bid_price
        if reference_price is None or reference_price == 0:
            return None

        return abs((avg_price - reference_price) / reference_price) * 100

    def get_liquidity_score(self, price_range: float = 0.01) -> float:
        """
        Calculate liquidity score within price range.

        Args:
            price_range: Price range as percentage (e.g., 0.01 = 1%)

        Returns:
            float: Liquidity score
        """
        if not self.mid_price:
            return 0

        lower_bound = self.mid_price * (1 - price_range)
        upper_bound = self.mid_price * (1 + price_range)

        bid_liquidity = sum(level.total_value for level in self.bids if level.price >= lower_bound)

        ask_liquidity = sum(level.total_value for level in self.asks if level.price <= upper_bound)

        return bid_liquidity + ask_liquidity

    def trim(self, max_levels: int) -> "OrderBook":
        """
        Trim order book to maximum levels.

        Args:
            max_levels: Maximum number of levels per side

        Returns:
            OrderBook: Trimmed order book
        """
        trimmed = self.copy()
        trimmed.bids = trimmed.bids[:max_levels]
        trimmed.asks = trimmed.asks[:max_levels]
        return trimmed

    def aggregate(self, price_step: float) -> "OrderBook":
        """
        Aggregate order book by price steps.

        Args:
            price_step: Price step for aggregation

        Returns:
            OrderBook: Aggregated order book
        """

        def aggregate_levels(levels: List[OrderBookLevel], ascending: bool) -> List[OrderBookLevel]:
            if not levels:
                return []

            aggregated = {}
            for level in levels:
                price_bucket = round(level.price / price_step) * price_step
                if price_bucket not in aggregated:
                    aggregated[price_bucket] = 0
                aggregated[price_bucket] += level.size

            result = [OrderBookLevel(price=price, size=size) for price, size in aggregated.items()]

            return sorted(result, key=lambda x: x.price, reverse=not ascending)

        return OrderBook(
            symbol=self.symbol,
            timestamp=self.timestamp,
            bids=aggregate_levels(self.bids, False),
            asks=aggregate_levels(self.asks, True),
            exchange=self.exchange,
            source=self.source,
            sequence=self.sequence,
            is_snapshot=self.is_snapshot,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderBook":
        """Create order book from dictionary."""
        bids = []
        for bid_data in data.get("bids", []):
            if isinstance(bid_data, list):
                bids.append(
                    OrderBookLevel(
                        price=float(bid_data[0]),
                        size=float(bid_data[1]),
                        orders_count=int(bid_data[2]) if len(bid_data) > 2 else None,
                    )
                )
            else:
                bids.append(
                    OrderBookLevel(
                        price=float(bid_data["price"]),
                        size=float(bid_data["size"]),
                        orders_count=bid_data.get("orders_count"),
                    )
                )

        asks = []
        for ask_data in data.get("asks", []):
            if isinstance(ask_data, list):
                asks.append(
                    OrderBookLevel(
                        price=float(ask_data[0]),
                        size=float(ask_data[1]),
                        orders_count=int(ask_data[2]) if len(ask_data) > 2 else None,
                    )
                )
            else:
                asks.append(
                    OrderBookLevel(
                        price=float(ask_data["price"]),
                        size=float(ask_data["size"]),
                        orders_count=ask_data.get("orders_count"),
                    )
                )

        return cls(
            symbol=data["symbol"],
            timestamp=int(data["timestamp"]),
            bids=bids,
            asks=asks,
            exchange=data.get("exchange", "unknown"),
            source=data.get("source", "unknown"),
            sequence=data.get("sequence"),
            is_snapshot=data.get("is_snapshot", True),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "bids": [[level.price, level.size] for level in self.bids],
            "asks": [[level.price, level.size] for level in self.asks],
            "exchange": self.exchange,
            "source": self.source,
            "sequence": self.sequence,
            "is_snapshot": self.is_snapshot,
        }

    def __repr__(self) -> str:
        """String representation."""
        spread_info = f" Spread:{self.spread:.4f}" if self.spread else ""
        imbalance = self.get_imbalance(5)
        imb_str = "↑" if imbalance > 0.2 else "↓" if imbalance < -0.2 else "→"

        return (
            f"OrderBook({self.symbol} "
            f"Bids:{len(self.bids)} Asks:{len(self.asks)}"
            f"{spread_info} {imb_str})"
        )


@dataclass
class OrderBookSnapshot:
    """Snapshot of order book at specific time."""

    orderbook: OrderBook
    metrics: Dict[str, float] = field(default_factory=dict)

    def calculate_metrics(self) -> None:
        """Calculate and store order book metrics."""
        self.metrics = {
            "spread": self.orderbook.spread or 0,
            "spread_percentage": self.orderbook.spread_percentage or 0,
            "mid_price": self.orderbook.mid_price or 0,
            "weighted_mid_price": self.orderbook.weighted_mid_price or 0,
            "imbalance_5": self.orderbook.get_imbalance(5),
            "imbalance_10": self.orderbook.get_imbalance(10),
            "bid_depth_5": self.orderbook.get_bid_depth(5),
            "ask_depth_5": self.orderbook.get_ask_depth(5),
            "bid_value_5": self.orderbook.get_total_bid_value(5),
            "ask_value_5": self.orderbook.get_total_ask_value(5),
            "liquidity_1pct": self.orderbook.get_liquidity_score(0.01),
            "liquidity_2pct": self.orderbook.get_liquidity_score(0.02),
        }

    def compare_with(self, other: "OrderBookSnapshot") -> Dict[str, float]:
        """
        Compare with another snapshot.

        Args:
            other: Another order book snapshot

        Returns:
            Dict[str, float]: Comparison metrics
        """
        if not self.metrics:
            self.calculate_metrics()
        if not other.metrics:
            other.calculate_metrics()

        comparison = {}
        for key in self.metrics:
            if key in other.metrics:
                comparison[f"{key}_change"] = self.metrics[key] - other.metrics[key]
                if other.metrics[key] != 0:
                    comparison[f"{key}_change_pct"] = (
                        (self.metrics[key] - other.metrics[key]) / abs(other.metrics[key]) * 100
                    )

        return comparison
