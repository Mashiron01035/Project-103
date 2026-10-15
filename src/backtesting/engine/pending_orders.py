"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : __init__.py
    Project     : Project-103
    Description : Simulates limit order entry with realistic fill logic

    SPDX-License-Identifier: AGPL-3.0

"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PendingOrderState(Enum):
    """State of a pending limit order."""

    PENDING = "pending"  # Waiting for fill
    FILLED = "filled"  # Limit order filled
    TIMEOUT = "timeout"  # Timed out, used market order
    CANCELLED = "cancelled"  # Cancelled (e.g., signal expired)


@dataclass
class PendingOrder:
    """Represents a pending limit order in backtest."""

    # Identifiers
    order_id: str
    signal_id: str

    # Order details
    symbol: str
    side: str  # BUY or SELL
    quantity: float
    limit_price: float  # Target limit price

    # Original signal data (for position creation)
    signal_data: Dict[str, Any] = field(default_factory=dict)

    # Timing
    created_at: datetime = None
    timeout_at: datetime = None
    timeout_candles: int = 2  # Number of candles to wait

    # Candle tracking
    candles_waited: int = 0
    candle_created_on: int = 0  # Index of candle when order was created

    # State
    state: PendingOrderState = PendingOrderState.PENDING

    # Fill info
    fill_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    used_market: bool = False

    def is_expired(self, current_candle_idx: int) -> bool:
        """Check if order has expired based on candle count."""
        return (current_candle_idx - self.candle_created_on) >= self.timeout_candles


@dataclass
class LimitEntryConfig:
    """Configuration for limit entry in backtest."""

    enabled: bool = True
    timeout_candles: int = 2  # Wait up to 2 candles for fill
    price_offset_percent: float = 0.02  # 0.02% offset from signal price

    # For price calculation
    use_orderbook_simulation: bool = False  # If True, simulate spread
    simulated_spread_percent: float = 0.02  # 0.02% spread

    # Fee comparison (maker vs taker)
    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.00055  # 0.055%


class PendingOrdersManager:
    """
    Manages pending limit orders during backtest.

    For each signal:
    1. Creates a pending limit order instead of immediate market
    2. On each candle, checks if limit would be filled
    3. If filled: use limit price, charge maker fee
    4. If timeout: fall back to market, charge taker fee
    """

    def __init__(
        self,
        config: LimitEntryConfig,
        on_order_filled: Optional[Callable] = None,
        on_order_timeout: Optional[Callable] = None,
    ):
        """
        Initialize PendingOrdersManager.

        Args:
            config: Limit entry configuration
            on_order_filled: Callback(pending_order) when order is filled
            on_order_timeout: Callback(pending_order) when order times out
        """
        self.config = config
        self.on_order_filled = on_order_filled
        self.on_order_timeout = on_order_timeout

        # Active pending orders: order_id -> PendingOrder
        self.pending_orders: Dict[str, PendingOrder] = {}

        # Completed orders for statistics
        self.filled_orders: List[PendingOrder] = []
        self.timeout_orders: List[PendingOrder] = []

        # Statistics
        self.stats = {
            "total_orders": 0,
            "limit_fills": 0,
            "market_fallbacks": 0,
            "total_fee_savings": 0.0,
        }

        logger.info(f"PendingOrdersManager initialized (timeout={config.timeout_candles} candles)")

    def create_pending_order(
        self, signal: Dict[str, Any], signal_id: str, current_candle_idx: int, timestamp: datetime
    ) -> PendingOrder:
        """
        Create a pending limit order from a signal.

        Args:
            signal: Trading signal dict
            signal_id: Unique signal identifier
            current_candle_idx: Current candle index (for timeout tracking)
            timestamp: Current timestamp

        Returns:
            PendingOrder object
        """
        symbol = signal["symbol"]
        side = signal["side"]
        price = signal["price"]
        quantity = signal.get("quantity", 0)

        # Offset: per-signal absolute $ override (metadata.limit_offset_price) wins,
        # else global percent. Absolute $ keeps a fixed dip depth across price levels.
        meta = signal.get("metadata", {}) or {}
        offset_price = meta.get("limit_offset_price")
        offset = (
            float(offset_price)
            if offset_price
            else price * (self.config.price_offset_percent / 100)
        )

        if side in ("BUY", "LONG"):
            limit_price = price - offset  # Buy lower
        else:
            limit_price = price + offset  # Sell higher

        # Per-signal timeout override (in candles)
        timeout_candles = int(meta.get("limit_fill_timeout_candles", self.config.timeout_candles))

        order_id = f"LMT_{signal_id}"

        pending = PendingOrder(
            order_id=order_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            signal_data=signal.copy(),
            created_at=timestamp,
            timeout_candles=timeout_candles,
            candle_created_on=current_candle_idx,
            state=PendingOrderState.PENDING,
        )

        self.pending_orders[order_id] = pending
        self.stats["total_orders"] += 1

        logger.debug(
            f"Pending limit order created: {side} {symbol} @ {limit_price:.4f} "
            f"(signal price: {price:.4f}, timeout: {self.config.timeout_candles} candles)"
        )

        return pending

    def process_candle(
        self, symbol: str, candle: Dict[str, Any], current_candle_idx: int, timestamp: datetime
    ) -> List[PendingOrder]:
        """
        Process a candle to check for limit order fills.

        Args:
            symbol: Trading pair
            candle: Candle data with 'open', 'high', 'low', 'close'
            current_candle_idx: Current candle index
            timestamp: Current timestamp

        Returns:
            List of orders that were filled or timed out
        """
        completed_orders = []

        # Check all pending orders for this symbol
        orders_to_process = [
            order
            for order in self.pending_orders.values()
            if order.symbol == symbol and order.state == PendingOrderState.PENDING
        ]

        for order in orders_to_process:
            # Check for timeout first
            if order.is_expired(current_candle_idx):
                self._handle_timeout(order, candle, timestamp)
                completed_orders.append(order)
                continue

            # Check if limit would be filled
            if self._check_limit_fill(order, candle):
                self._handle_fill(order, candle, timestamp)
                completed_orders.append(order)

        return completed_orders

    def _check_limit_fill(self, order: PendingOrder, candle: Dict[str, Any]) -> bool:
        """
        Check if a limit order would be filled on this candle.

        Logic:
        - BUY limit fills if candle low <= limit_price (price dropped to our buy level)
        - SELL limit fills if candle high >= limit_price (price rose to our sell level)
        """
        high = candle.get("high", candle.get("close", 0))
        low = candle.get("low", candle.get("close", 0))

        if order.side in ("BUY", "LONG"):
            # Buy limit fills when price drops to or below limit
            return low <= order.limit_price
        else:
            # Sell limit fills when price rises to or above limit
            return high >= order.limit_price

    def _handle_fill(self, order: PendingOrder, candle: Dict[str, Any], timestamp: datetime):
        """Handle limit order fill."""
        order.state = PendingOrderState.FILLED
        order.fill_price = order.limit_price  # Filled at limit price
        order.filled_at = timestamp
        order.used_market = False

        # Update signal data with actual fill price
        order.signal_data["executed_price"] = order.fill_price
        order.signal_data["fee_type"] = "maker"

        # Move to filled list
        del self.pending_orders[order.order_id]
        self.filled_orders.append(order)
        self.stats["limit_fills"] += 1

        # Calculate fee savings
        trade_value = order.quantity * order.fill_price
        savings = trade_value * (self.config.taker_fee - self.config.maker_fee)
        self.stats["total_fee_savings"] += savings

        logger.debug(
            f"Limit order FILLED: {order.side} {order.symbol} @ {order.fill_price:.4f} "
            f"(saved ${savings:.4f} in fees)"
        )

        # Callback
        if self.on_order_filled:
            self.on_order_filled(order)

    def _handle_timeout(self, order: PendingOrder, candle: Dict[str, Any], timestamp: datetime):
        """Handle limit order timeout — fall back to market, or cancel if signal
        opts out via metadata['cancel_on_limit_timeout']=True."""
        order.state = PendingOrderState.TIMEOUT
        order.filled_at = timestamp

        # Per-signal opt-out: skip market fallback entirely.
        signal_meta = order.signal_data.get("metadata", {}) if order.signal_data else {}
        if signal_meta.get("cancel_on_limit_timeout"):
            order.state = PendingOrderState.CANCELLED
            del self.pending_orders[order.order_id]
            self.stats["cancellations"] = self.stats.get("cancellations", 0) + 1
            logger.debug(
                f"Limit order CANCELLED (no market fallback): {order.side} "
                f"{order.symbol} | limit was {order.limit_price:.4f}"
            )
            return

        order.used_market = True
        # For market order, use current candle close price
        market_price = candle.get("close", order.limit_price)
        order.fill_price = market_price

        # Update signal data with market fill
        order.signal_data["executed_price"] = market_price
        order.signal_data["fee_type"] = "taker"

        # Move to timeout list
        del self.pending_orders[order.order_id]
        self.timeout_orders.append(order)
        self.stats["market_fallbacks"] += 1

        logger.debug(
            f"Limit order TIMEOUT: {order.side} {order.symbol} | "
            f"limit was {order.limit_price:.4f}, market fill @ {market_price:.4f}"
        )

        # Callback
        if self.on_order_timeout:
            self.on_order_timeout(order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        if order_id in self.pending_orders:
            order = self.pending_orders[order_id]
            order.state = PendingOrderState.CANCELLED
            del self.pending_orders[order_id]
            return True
        return False

    def get_pending_orders(self, symbol: Optional[str] = None) -> List[PendingOrder]:
        """Get all pending orders, optionally filtered by symbol."""
        orders = list(self.pending_orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_statistics(self) -> Dict[str, Any]:
        """Get manager statistics."""
        total = self.stats["total_orders"]
        return {
            **self.stats,
            "limit_fill_rate": self.stats["limit_fills"] / total if total > 0 else 0,
            "avg_savings_per_trade": (
                self.stats["total_fee_savings"] / self.stats["limit_fills"]
                if self.stats["limit_fills"] > 0
                else 0
            ),
        }

    def reset(self):
        """Reset manager state for new backtest."""
        self.pending_orders.clear()
        self.filled_orders.clear()
        self.timeout_orders.clear()
        self.stats = {
            "total_orders": 0,
            "limit_fills": 0,
            "market_fallbacks": 0,
            "total_fee_savings": 0.0,
        }
