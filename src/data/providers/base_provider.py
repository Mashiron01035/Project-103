"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : base_provider
    Project     : Project-103
    Description : Base data provider abstract class. This module defines the abstract base class for all data providers.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from src.core.exceptions import DataProviderError
from src.models import Candle, OrderBook, Ticker

logger = logging.getLogger(__name__)


@dataclass
class DataSubscription:
    """Data subscription configuration."""

    symbol: str
    data_type: str  # "candle", "ticker", "orderbook", "trades"
    params: Dict[str, Any] = field(default_factory=dict)  # Additional parameters
    callback: Optional[Callable] = None  # Callback for data processing

    # Subscription metadata
    subscribed_at: Optional[int] = None
    last_update: Optional[int] = None
    update_count: int = 0
    error_count: int = 0

    def __hash__(self) -> int:
        """Make subscription hashable for use in sets."""
        return hash(f"{self.symbol}_{self.data_type}")

    def __eq__(self, other) -> bool:
        """Check equality by symbol and data type."""
        if not isinstance(other, DataSubscription):
            return False
        return self.symbol == other.symbol and self.data_type == other.data_type


@dataclass
class ConnectionStatus:
    """Connection status information."""

    is_connected: bool = False
    latency_ms: int = 0
    last_update: int = 0  # timestamp
    error_message: Optional[str] = None
    reconnect_count: int = 0

    # Statistics
    messages_received: int = 0
    messages_sent: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0

    # Connection health
    last_heartbeat: Optional[int] = None
    missed_heartbeats: int = 0

    def update_heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self.last_heartbeat = int(time.time() * 1000)
        self.missed_heartbeats = 0

    def check_heartbeat(self, timeout_ms: int = 30000) -> bool:
        """
        Check if heartbeat is within timeout.

        Args:
            timeout_ms: Heartbeat timeout in milliseconds

        Returns:
            bool: True if healthy
        """
        if self.last_heartbeat is None:
            return True  # No heartbeat tracking

        elapsed = int(time.time() * 1000) - self.last_heartbeat
        if elapsed > timeout_ms:
            self.missed_heartbeats += 1
            return False

        return True


@dataclass
class DataProviderConfig:
    """Data provider configuration."""

    exchange: str
    testnet: bool = False

    # Authentication
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

    # WebSocket settings
    ws_enabled: bool = True
    ws_url: Optional[str] = None
    ws_ping_interval: int = 30  # seconds
    ws_reconnect_delay: int = 5  # seconds
    ws_max_reconnect_attempts: int = 10
    ws_reconnect_enabled: bool = True  # Set to False to disable auto-reconnect

    # REST settings
    rest_enabled: bool = True
    rest_url: Optional[str] = None
    rest_timeout: int = 30  # seconds
    rest_retry_count: int = 3
    rest_rate_limit: int = 50  # requests per second

    # Data settings
    max_candles_per_request: int = 1000
    max_orderbook_depth: int = 50

    # Cache settings
    cache_enabled: bool = True
    cache_ttl: int = 60  # seconds

    # Validation
    validate_data: bool = True
    clean_outliers: bool = True

    # Performance
    batch_size: int = 100
    queue_size: int = 10000


class BaseDataProvider(ABC):
    """Abstract base class for data providers."""

    def __init__(self, config: DataProviderConfig):
        """
        Initialize base data provider.

        Args:
            config: Provider configuration
        """
        self.config = config
        self.connection_status = ConnectionStatus()
        self.subscriptions: Set[DataSubscription] = set()

        # Event queues
        self.data_queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_size)
        self.error_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # Tasks
        self.tasks: List[asyncio.Task] = []
        self.is_running = False

        # Rate limiting
        self.last_request_time = 0
        self.request_count = 0

        logger.info(f"BaseDataProvider initialized for {config.exchange}")

    # ==================== Abstract Methods ====================

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to data source.

        Returns:
            bool: Connection success status
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """
        Disconnect from data source.

        Returns:
            bool: Disconnection success status
        """
        pass

    @abstractmethod
    async def subscribe(self, subscription: DataSubscription) -> bool:
        """
        Subscribe to data stream.

        Args:
            subscription: Subscription configuration

        Returns:
            bool: Subscription success status
        """
        pass

    @abstractmethod
    async def unsubscribe(self, subscription: DataSubscription) -> bool:
        """
        Unsubscribe from data stream.

        Args:
            subscription: Subscription to remove

        Returns:
            bool: Unsubscription success status
        """
        pass

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """
        Get historical candles.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (1m, 5m, 1h, etc.)
            limit: Number of candles
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[Candle]: Historical candles
        """
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get current ticker data.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Ticker data
        """
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[OrderBook]:
        """
        Get current order book.

        Args:
            symbol: Trading symbol
            depth: Order book depth

        Returns:
            Optional[OrderBook]: Order book data
        """
        pass

    # ==================== Common Methods ====================

    async def start(self) -> None:
        """Start data provider."""
        if self.is_running:
            logger.warning("Data provider already running")
            return

        self.is_running = True

        # Connect
        if not await self.connect():
            raise DataProviderError("Failed to connect to data source")

        # Start background tasks
        self.tasks.append(asyncio.create_task(self._heartbeat_task()))
        self.tasks.append(asyncio.create_task(self._process_queue()))

        if self.config.ws_enabled:
            self.tasks.append(asyncio.create_task(self._reconnection_task()))

        logger.info(f"Data provider started for {self.config.exchange}")

    async def stop(self) -> None:
        """Stop data provider."""
        if not self.is_running:
            return

        self.is_running = False

        # Cancel tasks
        for task in self.tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

        # Disconnect
        await self.disconnect()

        logger.info(f"Data provider stopped for {self.config.exchange}")

    async def subscribe_multiple(self, subscriptions: List[DataSubscription]) -> Dict[str, bool]:
        """
        Subscribe to multiple data streams.

        Args:
            subscriptions: List of subscriptions

        Returns:
            Dict[str, bool]: Results for each subscription
        """
        results = {}

        for sub in subscriptions:
            key = f"{sub.symbol}_{sub.data_type}"
            try:
                success = await self.subscribe(sub)
                results[key] = success
                if success:
                    self.subscriptions.add(sub)
                    sub.subscribed_at = int(time.time() * 1000)
            except Exception as e:
                logger.error(f"Failed to subscribe to {key}: {e}")
                results[key] = False

        return results

    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all data streams."""
        for sub in list(self.subscriptions):
            try:
                await self.unsubscribe(sub)
                self.subscriptions.remove(sub)
            except Exception as e:
                logger.error(f"Failed to unsubscribe from {sub.symbol}_{sub.data_type}: {e}")

    def get_subscription(self, symbol: str, data_type: str) -> Optional[DataSubscription]:
        """
        Get subscription by symbol and data type.

        Args:
            symbol: Trading symbol
            data_type: Data type

        Returns:
            Optional[DataSubscription]: Subscription if found
        """
        for sub in self.subscriptions:
            if sub.symbol == symbol and sub.data_type == data_type:
                return sub
        return None

    async def push_data(self, data_type: str, symbol: str, data: Any) -> None:
        """
        Push data to queue and trigger callbacks.

        Args:
            data_type: Type of data
            symbol: Trading symbol
            data: Data object
        """
        # Update subscription metadata
        sub = self.get_subscription(symbol, data_type)
        if sub:
            sub.last_update = int(time.time() * 1000)
            sub.update_count += 1

            # Trigger callback if exists
            if sub.callback:
                try:
                    if asyncio.iscoroutinefunction(sub.callback):
                        await sub.callback(data)
                    else:
                        sub.callback(data)
                except Exception as e:
                    logger.error(f"Callback error for {symbol}_{data_type}: {e}")
                    sub.error_count += 1

        # Add to queue
        try:
            await self.data_queue.put(
                {
                    "type": data_type,
                    "symbol": symbol,
                    "data": data,
                    "timestamp": int(time.time() * 1000),
                }
            )
        except asyncio.QueueFull:
            logger.warning(f"Data queue full, dropping {symbol}_{data_type}")

    async def get_data(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Get data from queue.

        Args:
            timeout: Timeout in seconds

        Returns:
            Optional[Dict]: Data item or None if timeout
        """
        try:
            if timeout:
                return await asyncio.wait_for(self.data_queue.get(), timeout=timeout)
            else:
                return await self.data_queue.get()
        except asyncio.TimeoutError:
            return None

    def data_generator(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Async generator for data stream.

        Yields:
            Dict: Data items from queue
        """

        async def _generator():
            while self.is_running:
                data = await self.get_data(timeout=1.0)
                if data:
                    yield data

        return _generator()

    # ==================== Background Tasks ====================

    async def _heartbeat_task(self) -> None:
        """Send periodic heartbeat."""
        while self.is_running:
            try:
                # Update heartbeat
                self.connection_status.update_heartbeat()

                # Check connection health
                if not self.connection_status.check_heartbeat():
                    logger.warning(f"Heartbeat timeout for {self.config.exchange}")

                    # Trigger reconnection if needed
                    if self.connection_status.missed_heartbeats > 3:
                        self.connection_status.is_connected = False

                # Update statistics
                self.connection_status.last_update = int(time.time() * 1000)

                await asyncio.sleep(self.config.ws_ping_interval)

            except Exception as e:
                logger.error(f"Heartbeat task error: {e}")
                await asyncio.sleep(5)

    async def _reconnection_task(self) -> None:
        """Handle automatic reconnection."""
        while self.is_running:
            try:
                if not self.connection_status.is_connected:
                    logger.info(f"Attempting reconnection for {self.config.exchange}")

                    # Wait before reconnection
                    await asyncio.sleep(self.config.ws_reconnect_delay)

                    # Try to reconnect
                    if await self.connect():
                        self.connection_status.reconnect_count += 1

                        # Resubscribe to all streams
                        for sub in self.subscriptions:
                            await self.subscribe(sub)

                        logger.info(f"Reconnected to {self.config.exchange}")
                    else:
                        logger.error(f"Reconnection failed for {self.config.exchange}")

                        # Check max attempts
                        if (
                            self.connection_status.reconnect_count
                            >= self.config.ws_max_reconnect_attempts
                        ):
                            logger.error(
                                f"Max reconnection attempts reached for {self.config.exchange}"
                            )
                            self.is_running = False

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Reconnection task error: {e}")
                await asyncio.sleep(30)

    async def _process_queue(self) -> None:
        """Process data queue."""
        while self.is_running:
            try:
                # Get batch of data
                batch = []
                deadline = time.time() + 0.1  # 100ms batch window

                while time.time() < deadline and len(batch) < self.config.batch_size:
                    try:
                        data = await asyncio.wait_for(self.data_queue.get(), timeout=0.01)
                        batch.append(data)
                    except asyncio.TimeoutError:
                        break

                if batch:
                    # Process batch
                    await self._process_batch(batch)

                await asyncio.sleep(0.01)

            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                await asyncio.sleep(1)

    async def _process_batch(self, batch: List[Dict[str, Any]]) -> None:
        """
        Process batch of data.

        Args:
            batch: List of data items
        """
        # Override in subclasses for custom processing
        pass

    # ==================== Rate Limiting ====================

    async def check_rate_limit(self) -> None:
        """Check and enforce rate limiting."""
        current_time = time.time()

        # Reset counter every second
        if current_time - self.last_request_time > 1:
            self.request_count = 0
            self.last_request_time = current_time

        # Check if over limit
        if self.request_count >= self.config.rest_rate_limit:
            # Wait until next second
            sleep_time = 1 - (current_time - self.last_request_time)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()

        self.request_count += 1

    # ==================== Utility Methods ====================

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            "exchange": self.config.exchange,
            "testnet": self.config.testnet,
            "is_connected": self.connection_status.is_connected,
            "latency_ms": self.connection_status.latency_ms,
            "reconnect_count": self.connection_status.reconnect_count,
            "subscriptions": len(self.subscriptions),
            "queue_size": self.data_queue.qsize(),
            "messages_received": self.connection_status.messages_received,
            "messages_sent": self.connection_status.messages_sent,
        }

    def get_subscription_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get subscription statistics."""
        stats = {}

        for sub in self.subscriptions:
            key = f"{sub.symbol}_{sub.data_type}"
            stats[key] = {
                "subscribed_at": sub.subscribed_at,
                "last_update": sub.last_update,
                "update_count": sub.update_count,
                "error_count": sub.error_count,
                "updates_per_minute": self._calculate_update_rate(sub),
            }

        return stats

    def _calculate_update_rate(self, subscription: DataSubscription) -> float:
        """Calculate update rate for subscription."""
        if not subscription.subscribed_at or subscription.update_count == 0:
            return 0.0

        elapsed_minutes = (time.time() * 1000 - subscription.subscribed_at) / 60000
        if elapsed_minutes > 0:
            return subscription.update_count / elapsed_minutes

        return 0.0
