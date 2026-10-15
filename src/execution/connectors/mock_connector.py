"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : mock_connector
    Project     : Project-103
    Description : Mock connector for testing. This module implements a mock exchange connector for testing without real trading.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import random
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.execution.connectors.base_connector import BaseConnector

logger = logging.getLogger(__name__)

# History size limits to prevent memory leaks
MAX_ORDER_HISTORY_SIZE = 1000


class MockConnector(BaseConnector):
    """Mock exchange connector for testing."""

    def __init__(
        self,
        api_key: str = "mock_key",
        api_secret: str = "mock_secret",
        testnet: bool = True,
        initial_balance: float = 10000.0,
        latency: float = 0.01,
        failure_rate: float = 0.0,
    ):
        """
        Initialize mock connector.

        Args:
            api_key: Mock API key
            api_secret: Mock API secret
            testnet: Always True for mock
            initial_balance: Initial account balance
            latency: Simulated network latency in seconds
            failure_rate: Probability of request failure (0-1)
        """
        super().__init__(api_key, api_secret, True)

        # Mock settings
        self.initial_balance = initial_balance
        self.latency = latency
        self.failure_rate = failure_rate

        # Mock data storage
        self.balance = {"USDT": initial_balance}
        self.orders = {}
        self.order_history = []
        self.positions = {}
        self.trades = defaultdict(list)

        # Market data simulation
        self.prices = {
            "BTCUSDT": 50000.0,
            "ETHUSDT": 3000.0,
            "SOLUSDT": 100.0,
            "BNBUSDT": 500.0,
        }
        self.last_update = time.time()

        # Order ID counter
        self.order_counter = 1000

        # WebSocket simulation
        self.subscriptions = {
            "orderbook": {},
            "trades": {},
            "klines": {},
            "account": None,
        }
        self.is_streaming = False

        logger.info(f"MockConnector initialized with balance: {initial_balance}")

    async def connect(self) -> bool:
        """
        Simulate connection to exchange.

        Returns:
            bool: Connection success status
        """
        await self._simulate_latency()

        if random.random() < self.failure_rate:
            logger.error("Mock connection failed (simulated)")
            return False

        self.is_connected = True
        self.is_streaming = True

        # Start market data simulation
        asyncio.create_task(self._simulate_market_data())

        logger.info("Connected to MockExchange successfully")
        return True

    async def disconnect(self) -> bool:
        """
        Simulate disconnection from exchange.

        Returns:
            bool: Disconnection success status
        """
        await self._simulate_latency()

        self.is_connected = False
        self.is_streaming = False

        logger.info("Disconnected from MockExchange")
        return True

    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulate placing an order.

        Args:
            order_params: Order parameters

        Returns:
            Dict: Order placement result
        """
        await self._simulate_latency()

        # Simulate failure
        if random.random() < self.failure_rate:
            return {
                "success": False,
                "error": "Simulated order placement failure",
                "error_code": "MOCK_ERROR",
            }

        # Generate order ID
        order_id = f"MOCK_{self.order_counter}"
        self.order_counter += 1

        # Get current price
        symbol = order_params["symbol"]
        current_price = self.prices.get(symbol, 100.0)

        # Create mock order
        order = {
            "id": order_id,
            "symbol": symbol,
            "side": order_params["side"],
            "type": order_params["type"],
            "quantity": order_params["quantity"],
            "price": order_params.get("price", current_price),
            "status": "NEW",
            "filled_quantity": 0,
            "average_price": 0,
            "created_time": int(time.time() * 1000),
            "updated_time": int(time.time() * 1000),
        }

        # Check balance for buy orders
        if order_params["side"] == "BUY":
            required_balance = order["quantity"] * order["price"]
            if required_balance > self.balance.get("USDT", 0):
                return {
                    "success": False,
                    "error": "Insufficient balance",
                    "error_code": "INSUFFICIENT_FUNDS",
                }

        # Store order
        self.orders[order_id] = order

        # Simulate immediate fill for market orders
        if order_params["type"] == "MARKET":
            await self._fill_order(order_id, current_price)

        # Start order simulation
        asyncio.create_task(self._simulate_order_execution(order_id))

        return {
            "success": True,
            "order_id": order_id,
            "status": order["status"],
            "price": order["price"],
            "quantity": order["quantity"],
            "timestamp": order["created_time"],
        }

    async def cancel_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulate cancelling an order.

        Args:
            order_params: Order cancellation parameters

        Returns:
            Dict: Cancellation result
        """
        await self._simulate_latency()

        order_id = order_params.get("order_id")

        if order_id not in self.orders:
            return {
                "success": False,
                "error": f"Order {order_id} not found",
                "error_code": "ORDER_NOT_FOUND",
            }

        order = self.orders[order_id]

        if order["status"] in ["FILLED", "CANCELLED"]:
            return {
                "success": False,
                "error": f"Cannot cancel order with status {order['status']}",
                "error_code": "INVALID_STATUS",
            }

        # Cancel order
        order["status"] = "CANCELLED"
        order["updated_time"] = int(time.time() * 1000)

        # Move to history
        self.order_history.append(order)
        del self.orders[order_id]

        # Trim history to prevent memory leak
        if len(self.order_history) > MAX_ORDER_HISTORY_SIZE:
            self.order_history = self.order_history[-MAX_ORDER_HISTORY_SIZE:]

        return {
            "success": True,
            "order_id": order_id,
        }

    async def modify_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulate modifying an order.

        Args:
            order_params: Order modification parameters

        Returns:
            Dict: Modification result
        """
        await self._simulate_latency()

        order_id = order_params.get("order_id")

        if order_id not in self.orders:
            return {
                "success": False,
                "error": f"Order {order_id} not found",
                "error_code": "ORDER_NOT_FOUND",
            }

        order = self.orders[order_id]

        if order["status"] != "NEW":
            return {
                "success": False,
                "error": f"Cannot modify order with status {order['status']}",
                "error_code": "INVALID_STATUS",
            }

        # Modify order
        if "price" in order_params:
            order["price"] = order_params["price"]

        if "quantity" in order_params:
            order["quantity"] = order_params["quantity"]

        order["updated_time"] = int(time.time() * 1000)

        return {
            "success": True,
            "order_id": order_id,
        }

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Get mock order status.

        Args:
            order_id: Order ID
            symbol: Trading symbol

        Returns:
            Dict: Order status
        """
        await self._simulate_latency()

        # Check active orders
        if order_id in self.orders:
            return self.orders[order_id].copy()

        # Check history
        for order in self.order_history:
            if order["id"] == order_id:
                return order.copy()

        return {}

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get mock open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open orders
        """
        await self._simulate_latency()

        orders = []
        for order in self.orders.values():
            if symbol is None or order["symbol"] == symbol:
                orders.append(order.copy())

        return orders

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get mock positions.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open positions
        """
        await self._simulate_latency()

        positions = []
        for position in self.positions.values():
            if symbol is None or position["symbol"] == symbol:
                # Update unrealized PnL
                current_price = self.prices.get(position["symbol"], position["entry_price"])
                if position["side"] == "LONG":
                    position["unrealized_pnl"] = (
                        current_price - position["entry_price"]
                    ) * position["size"]
                else:
                    position["unrealized_pnl"] = (
                        position["entry_price"] - current_price
                    ) * position["size"]

                position["mark_price"] = current_price
                positions.append(position.copy())

        return positions

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get mock account information.

        Returns:
            Dict: Account information
        """
        await self._simulate_latency()

        # Calculate total equity
        total_equity = self.balance.get("USDT", 0)
        total_pnl = 0

        for position in self.positions.values():
            current_price = self.prices.get(position["symbol"], position["entry_price"])
            if position["side"] == "LONG":
                pnl = (current_price - position["entry_price"]) * position["size"]
            else:
                pnl = (position["entry_price"] - current_price) * position["size"]
            total_pnl += pnl

        total_equity += total_pnl

        return {
            "account_type": "MOCK",
            "currency": "USDT",
            "balance": self.balance.get("USDT", 0),
            "equity": total_equity,
            "unrealized_pnl": total_pnl,
            "margin_used": sum(p.get("margin", 0) for p in self.positions.values()),
            "margin_free": total_equity - sum(p.get("margin", 0) for p in self.positions.values()),
            "leverage": 10,
            "positions_count": len(self.positions),
            "orders_count": len(self.orders),
        }

    async def get_balance(self) -> Dict[str, float]:
        """
        Get mock balance.

        Returns:
            Dict: Asset balances
        """
        await self._simulate_latency()
        return self.balance.copy()

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get mock ticker data.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Ticker data
        """
        await self._simulate_latency()

        price = self.prices.get(symbol, 100.0)
        spread = price * 0.0001  # 0.01% spread

        return {
            "symbol": symbol,
            "last_price": price,
            "bid_price": price - spread / 2,
            "ask_price": price + spread / 2,
            "volume_24h": random.uniform(1000000, 10000000),
            "high_24h": price * 1.02,
            "low_24h": price * 0.98,
            "price_change_24h": random.uniform(-0.05, 0.05),
            "timestamp": int(time.time() * 1000),
        }

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get mock order book.

        Args:
            symbol: Trading symbol
            limit: Order book depth

        Returns:
            Dict: Order book data
        """
        await self._simulate_latency()

        price = self.prices.get(symbol, 100.0)
        spread = price * 0.0001

        # Generate mock bids and asks
        bids = []
        asks = []

        for i in range(min(limit, 20)):
            bid_price = price - spread * (i + 1)
            ask_price = price + spread * (i + 1)

            bid_size = random.uniform(10, 1000)
            ask_size = random.uniform(10, 1000)

            bids.append([bid_price, bid_size])
            asks.append([ask_price, ask_size])

        return {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "bids": bids,
            "asks": asks,
        }

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get mock recent trades.

        Args:
            symbol: Trading symbol
            limit: Number of trades

        Returns:
            List[Dict]: Recent trades
        """
        await self._simulate_latency()

        trades = []
        current_price = self.prices.get(symbol, 100.0)
        current_time = int(time.time() * 1000)

        for i in range(min(limit, 100)):
            trade = {
                "id": f"TRADE_{uuid.uuid4().hex[:8]}",
                "symbol": symbol,
                "price": current_price * random.uniform(0.999, 1.001),
                "quantity": random.uniform(0.01, 10),
                "side": random.choice(["BUY", "SELL"]),
                "time": current_time - (i * 1000),
            }
            trades.append(trade)

        return trades

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List]:
        """
        Get mock kline data.

        Args:
            symbol: Trading symbol
            interval: Kline interval
            limit: Number of klines
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[List]: Kline data
        """
        await self._simulate_latency()

        # Parse interval to minutes
        interval_minutes = self._parse_interval(interval)

        # Generate mock klines
        klines = []
        current_price = self.prices.get(symbol, 100.0)

        if end_time:
            current_time = end_time
        else:
            current_time = int(time.time())

        for i in range(limit):
            timestamp = current_time - (i * interval_minutes * 60)

            # Generate OHLCV data with some randomness
            open_price = current_price * random.uniform(0.98, 1.02)
            high = open_price * random.uniform(1.0, 1.01)
            low = open_price * random.uniform(0.99, 1.0)
            close = random.uniform(low, high)
            volume = random.uniform(100, 10000)

            klines.append(
                [
                    timestamp * 1000,  # timestamp in ms
                    open_price,
                    high,
                    low,
                    close,
                    volume,
                    volume * close,  # quote volume
                ]
            )

            current_price = close

        # Reverse to get chronological order
        klines.reverse()

        return klines

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get mock symbol information.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Symbol information
        """
        await self._simulate_latency()

        # Mock symbol info
        base_asset = symbol[:-4] if symbol.endswith("USDT") else symbol[:-3]
        quote_asset = "USDT"

        return {
            "symbol": symbol,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "status": "TRADING",
            "min_quantity": 0.001,
            "max_quantity": 10000,
            "quantity_step": 0.001,
            "min_price": 0.01,
            "max_price": 1000000,
            "price_tick": 0.01,
            "min_notional": 10,
            "max_leverage": 125,
            "contract_size": 1,
        }

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get mock order history.

        Args:
            symbol: Optional symbol filter
            limit: Maximum number of orders

        Returns:
            List[Dict]: Historical orders
        """
        await self._simulate_latency()

        history = []
        for order in self.order_history[-limit:]:
            if symbol is None or order["symbol"] == symbol:
                history.append(order.copy())

        return history

    async def subscribe_orderbook(self, symbol: str, callback: callable) -> bool:
        """Subscribe to mock orderbook updates."""
        self.subscriptions["orderbook"][symbol] = callback

        # Start sending mock updates
        asyncio.create_task(self._stream_orderbook(symbol, callback))

        return True

    async def subscribe_trades(self, symbol: str, callback: callable) -> bool:
        """Subscribe to mock trade updates."""
        self.subscriptions["trades"][symbol] = callback

        # Start sending mock updates
        asyncio.create_task(self._stream_trades(symbol, callback))

        return True

    async def subscribe_klines(self, symbol: str, interval: str, callback: callable) -> bool:
        """Subscribe to mock kline updates."""
        key = f"{symbol}_{interval}"
        self.subscriptions["klines"][key] = callback

        # Start sending mock updates
        asyncio.create_task(self._stream_klines(symbol, interval, callback))

        return True

    async def subscribe_account(self, callback: callable) -> bool:
        """Subscribe to mock account updates."""
        self.subscriptions["account"] = callback

        # Start sending mock updates
        asyncio.create_task(self._stream_account(callback))

        return True

    # ==================== Private Methods ====================

    async def _simulate_latency(self) -> None:
        """Simulate network latency."""
        if self.latency > 0:
            await asyncio.sleep(self.latency)

    async def _simulate_market_data(self) -> None:
        """Simulate market price movements."""
        while self.is_streaming:
            try:
                # Update prices with random walk
                for symbol in self.prices:
                    change = random.uniform(-0.001, 0.001)  # ±0.1% change
                    self.prices[symbol] *= 1 + change

                self.last_update = time.time()

                # Wait before next update
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error simulating market data: {e}")
                break

    async def _simulate_order_execution(self, order_id: str) -> None:
        """Simulate order execution."""
        try:
            order = self.orders.get(order_id)
            if not order:
                return

            # Wait for execution
            await asyncio.sleep(random.uniform(0.5, 2))

            # Check if order still exists (not cancelled)
            if order_id not in self.orders:
                return

            symbol = order["symbol"]
            current_price = self.prices.get(symbol, 100.0)

            # Check if limit order can be filled
            if order["type"] == "LIMIT":
                if order["side"] == "BUY" and current_price > order["price"]:
                    return  # Price too high for buy limit
                elif order["side"] == "SELL" and current_price < order["price"]:
                    return  # Price too low for sell limit

            # Fill order
            await self._fill_order(order_id, current_price)

        except Exception as e:
            logger.error(f"Error simulating order execution: {e}")

    async def _fill_order(self, order_id: str, fill_price: float) -> None:
        """Fill an order."""
        if order_id not in self.orders:
            return

        order = self.orders[order_id]

        # Update order
        order["status"] = "FILLED"
        order["filled_quantity"] = order["quantity"]
        order["average_price"] = fill_price
        order["updated_time"] = int(time.time() * 1000)

        # Update balance
        if order["side"] == "BUY":
            self.balance["USDT"] -= order["quantity"] * fill_price

            # Create or update position
            position_id = f"{order['symbol']}_LONG"
            if position_id in self.positions:
                position = self.positions[position_id]
                # Average entry price
                total_size = position["size"] + order["quantity"]
                total_cost = (position["size"] * position["entry_price"]) + (
                    order["quantity"] * fill_price
                )
                position["entry_price"] = total_cost / total_size
                position["size"] = total_size
            else:
                self.positions[position_id] = {
                    "symbol": order["symbol"],
                    "side": "LONG",
                    "size": order["quantity"],
                    "entry_price": fill_price,
                    "mark_price": fill_price,
                    "unrealized_pnl": 0,
                    "realized_pnl": 0,
                    "margin": order["quantity"] * fill_price / 10,  # 10x leverage
                }
        else:  # SELL
            # Check if closing a long position
            position_id = f"{order['symbol']}_LONG"
            if position_id in self.positions:
                position = self.positions[position_id]

                if order["quantity"] >= position["size"]:
                    # Close entire position
                    realized_pnl = (fill_price - position["entry_price"]) * position["size"]
                    self.balance["USDT"] += position["size"] * fill_price + realized_pnl
                    del self.positions[position_id]
                else:
                    # Reduce position
                    realized_pnl = (fill_price - position["entry_price"]) * order["quantity"]
                    self.balance["USDT"] += order["quantity"] * fill_price + realized_pnl
                    position["size"] -= order["quantity"]
            else:
                # Open short position
                position_id = f"{order['symbol']}_SHORT"
                self.positions[position_id] = {
                    "symbol": order["symbol"],
                    "side": "SHORT",
                    "size": order["quantity"],
                    "entry_price": fill_price,
                    "mark_price": fill_price,
                    "unrealized_pnl": 0,
                    "realized_pnl": 0,
                    "margin": order["quantity"] * fill_price / 10,
                }
                self.balance["USDT"] += order["quantity"] * fill_price

        # Move to history
        self.order_history.append(order)
        del self.orders[order_id]

        # Trim history to prevent memory leak
        if len(self.order_history) > MAX_ORDER_HISTORY_SIZE:
            self.order_history = self.order_history[-MAX_ORDER_HISTORY_SIZE:]

        # Trigger account callback if subscribed
        if self.subscriptions["account"]:
            await self.subscriptions["account"](
                {
                    "type": "ORDER_FILLED",
                    "order": order,
                    "timestamp": order["updated_time"],
                }
            )

    async def _stream_orderbook(self, symbol: str, callback: callable) -> None:
        """Stream mock orderbook updates."""
        while self.is_streaming and symbol in self.subscriptions["orderbook"]:
            try:
                orderbook = await self.get_orderbook(symbol, 20)
                await callback(
                    {
                        "type": "orderbook",
                        "symbol": symbol,
                        "data": orderbook,
                    }
                )
                await asyncio.sleep(0.5)  # Update every 500ms
            except Exception as e:
                logger.error(f"Error streaming orderbook: {e}")
                break

    async def _stream_trades(self, symbol: str, callback: callable) -> None:
        """Stream mock trade updates."""
        while self.is_streaming and symbol in self.subscriptions["trades"]:
            try:
                # Generate a new trade
                trade = {
                    "id": f"TRADE_{uuid.uuid4().hex[:8]}",
                    "symbol": symbol,
                    "price": self.prices.get(symbol, 100.0) * random.uniform(0.999, 1.001),
                    "quantity": random.uniform(0.01, 10),
                    "side": random.choice(["BUY", "SELL"]),
                    "time": int(time.time() * 1000),
                }

                await callback(
                    {
                        "type": "trade",
                        "symbol": symbol,
                        "data": trade,
                    }
                )

                await asyncio.sleep(random.uniform(0.5, 2))
            except Exception as e:
                logger.error(f"Error streaming trades: {e}")
                break

    async def _stream_klines(self, symbol: str, interval: str, callback: callable) -> None:
        """Stream mock kline updates."""
        interval_seconds = self._parse_interval(interval) * 60

        while self.is_streaming:
            try:
                klines = await self.get_klines(symbol, interval, 1)
                if klines:
                    await callback(
                        {
                            "type": "kline",
                            "symbol": symbol,
                            "interval": interval,
                            "data": klines[0],
                        }
                    )

                await asyncio.sleep(min(interval_seconds, 60))
            except Exception as e:
                logger.error(f"Error streaming klines: {e}")
                break

    async def _stream_account(self, callback: callable) -> None:
        """Stream mock account updates."""
        while self.is_streaming and self.subscriptions["account"]:
            try:
                account_info = await self.get_account_info()
                await callback(
                    {
                        "type": "account",
                        "data": account_info,
                        "timestamp": int(time.time() * 1000),
                    }
                )

                await asyncio.sleep(5)  # Update every 5 seconds
            except Exception as e:
                logger.error(f"Error streaming account: {e}")
                break

    def _parse_interval(self, interval: str) -> int:
        """Parse interval string to minutes."""
        mapping = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "12h": 720,
            "1d": 1440,
            "1w": 10080,
        }
        return mapping.get(interval, 1)
