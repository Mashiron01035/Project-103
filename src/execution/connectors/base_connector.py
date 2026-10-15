"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : base_connector
    Project     : Project-103
    Description : Base connector abstract class. This module defines the abstract base class for all exchange connectors.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """Abstract base class for exchange connectors."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        """
        Initialize base connector.

        Args:
            api_key: API key
            api_secret: API secret
            testnet: Whether to use testnet
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.is_connected = False

        # Rate limiting
        self.last_request_time = 0
        self.request_count = 0
        self.rate_limit = 50  # requests per second

        logger.info(f"BaseConnector initialized (testnet: {testnet})")

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to exchange.

        Returns:
            bool: Connection success status
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """
        Disconnect from exchange.

        Returns:
            bool: Disconnection success status
        """
        pass

    @abstractmethod
    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order on the exchange.

        Args:
            order_params: Order parameters

        Returns:
            Dict: Order placement result
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            order_params: Order cancellation parameters

        Returns:
            Dict: Cancellation result
        """
        pass

    @abstractmethod
    async def modify_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modify an existing order.

        Args:
            order_params: Order modification parameters

        Returns:
            Dict: Modification result
        """
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Get order status.

        Args:
            order_id: Order ID
            symbol: Trading symbol

        Returns:
            Dict: Order status
        """
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open orders
        """
        pass

    @abstractmethod
    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get order history.

        Args:
            symbol: Optional symbol filter
            limit: Maximum number of orders

        Returns:
            List[Dict]: Historical orders
        """
        pass

    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open positions.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open positions
        """
        pass

    @abstractmethod
    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information.

        Returns:
            Dict: Account information
        """
        pass

    @abstractmethod
    async def get_balance(self) -> Dict[str, float]:
        """
        Get account balance.

        Returns:
            Dict: Asset balances
        """
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Ticker data
        """
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get order book for symbol.

        Args:
            symbol: Trading symbol
            limit: Order book depth

        Returns:
            Dict: Order book data
        """
        pass

    @abstractmethod
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades.

        Args:
            symbol: Trading symbol
            limit: Number of trades

        Returns:
            List[Dict]: Recent trades
        """
        pass

    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List]:
        """
        Get kline/candle data.

        Args:
            symbol: Trading symbol
            interval: Kline interval (1m, 5m, 1h, etc.)
            limit: Number of klines
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[List]: Kline data
        """
        pass

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol trading rules and information.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Symbol information
        """
        pass

    @abstractmethod
    async def subscribe_orderbook(self, symbol: str, callback: callable) -> bool:
        """
        Subscribe to order book updates.

        Args:
            symbol: Trading symbol
            callback: Callback function for updates

        Returns:
            bool: Subscription success status
        """
        pass

    @abstractmethod
    async def subscribe_trades(self, symbol: str, callback: callable) -> bool:
        """
        Subscribe to trade updates.

        Args:
            symbol: Trading symbol
            callback: Callback function for updates

        Returns:
            bool: Subscription success status
        """
        pass

    @abstractmethod
    async def subscribe_klines(self, symbol: str, interval: str, callback: callable) -> bool:
        """
        Subscribe to kline updates.

        Args:
            symbol: Trading symbol
            interval: Kline interval
            callback: Callback function for updates

        Returns:
            bool: Subscription success status
        """
        pass

    @abstractmethod
    async def subscribe_account(self, callback: callable) -> bool:
        """
        Subscribe to account updates.

        Args:
            callback: Callback function for updates

        Returns:
            bool: Subscription success status
        """
        pass

    async def ping(self) -> bool:
        """
        Ping exchange to check connection.

        Returns:
            bool: Connection status
        """
        try:
            # Default implementation - get server time
            response = await self.get_server_time()
            return response is not None
        except Exception:
            return False

    async def get_server_time(self) -> Optional[int]:
        """
        Get exchange server time.

        Returns:
            Optional[int]: Server timestamp
        """
        # To be overridden by specific implementations
        return None

    def _check_rate_limit(self) -> bool:
        """
        Check if rate limit allows new request.

        Returns:
            bool: Whether request is allowed
        """
        import time

        current_time = time.time()

        # Reset counter every second
        if current_time - self.last_request_time > 1:
            self.request_count = 0
            self.last_request_time = current_time

        # Check if under limit
        if self.request_count < self.rate_limit:
            self.request_count += 1
            return True

        return False

    async def _wait_for_rate_limit(self) -> None:
        """Wait if rate limited."""
        import asyncio

        while not self._check_rate_limit():
            await asyncio.sleep(0.01)  # Wait 10ms
