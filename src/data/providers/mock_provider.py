"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : mock_provider
    Project     : Project-103
    Description : Mock data provider for testing. This module implements a mock data provider for testing purposes.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.core.exceptions import DataProviderError
from src.data.providers.base_provider import BaseDataProvider, DataProviderConfig, DataSubscription
from src.models import Candle, OrderBook, OrderBookLevel, Ticker

logger = logging.getLogger(__name__)


class MockDataProvider(BaseDataProvider):
    """Mock data provider for testing."""

    def __init__(self, config: DataProviderConfig):
        """
        Initialize mock data provider.

        Args:
            config: Provider configuration
        """
        super().__init__(config)

        # Mock data generation settings
        self.base_prices = {
            "BTCUSDT": 50000.0,
            "ETHUSDT": 3000.0,
            "SOLUSDT": 100.0,
            "BNBUSDT": 500.0,
        }

        self.current_prices = self.base_prices.copy()
        self.price_volatility = 0.001  # 0.1% volatility

        # Data generation tasks
        self.generation_tasks = {}

        # Historical data cache
        self.candle_cache = {}

        logger.info("MockDataProvider initialized")

    async def connect(self) -> bool:
        """
        Simulate connection.

        Returns:
            bool: Always returns True
        """
        await asyncio.sleep(0.1)  # Simulate connection delay

        self.connection_status.is_connected = True
        self.connection_status.last_update = int(time.time() * 1000)
        self.connection_status.latency_ms = random.randint(10, 50)

        logger.info("Connected to MockDataProvider")
        return True

    async def disconnect(self) -> bool:
        """
        Simulate disconnection.

        Returns:
            bool: Always returns True
        """
        # Cancel generation tasks
        for task in self.generation_tasks.values():
            task.cancel()
        self.generation_tasks.clear()

        self.connection_status.is_connected = False

        logger.info("Disconnected from MockDataProvider")
        return True

    async def subscribe(self, subscription: DataSubscription) -> bool:
        """
        Subscribe to mock data stream.

        Args:
            subscription: Subscription configuration

        Returns:
            bool: Always returns True
        """
        try:
            key = f"{subscription.symbol}_{subscription.data_type}"

            if key in self.generation_tasks:
                logger.warning(f"Already subscribed to {key}")
                return True

            # Initialize price if not exists
            if subscription.symbol not in self.current_prices:
                self.current_prices[subscription.symbol] = 100.0

            # Start generation task
            if subscription.data_type == "ticker":
                task = asyncio.create_task(self._generate_ticker(subscription))
            elif subscription.data_type == "candle":
                task = asyncio.create_task(self._generate_candles(subscription))
            elif subscription.data_type == "orderbook":
                task = asyncio.create_task(self._generate_orderbook(subscription))
            else:
                logger.warning(f"Unsupported data type: {subscription.data_type}")
                return False

            self.generation_tasks[key] = task
            logger.info(f"Subscribed to mock {key}")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {subscription.symbol}: {e}")
            return False

    async def unsubscribe(self, subscription: DataSubscription) -> bool:
        """
        Unsubscribe from mock data stream.

        Args:
            subscription: Subscription to remove

        Returns:
            bool: Always returns True
        """
        try:
            key = f"{subscription.symbol}_{subscription.data_type}"

            if key in self.generation_tasks:
                self.generation_tasks[key].cancel()
                del self.generation_tasks[key]
                logger.info(f"Unsubscribed from mock {key}")

            return True

        except Exception as e:
            logger.error(f"Error unsubscribing from {subscription.symbol}: {e}")
            return False

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """
        Get mock historical candles.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            limit: Number of candles
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[Candle]: Mock historical candles
        """
        try:
            # Generate mock candles
            candles = []

            # Determine time range
            if end_time:
                end_dt = datetime.fromtimestamp(end_time / 1000)
            else:
                end_dt = datetime.now()

            # Calculate interval in minutes
            interval_minutes = self._get_interval_minutes(timeframe)

            # Generate candles backwards from end time
            current_time = end_dt
            base_price = self.current_prices.get(symbol, 100.0)

            for i in range(limit):
                timestamp = int(current_time.timestamp() * 1000)

                # Generate OHLCV with some randomness
                open_price = base_price * (1 + random.uniform(-0.01, 0.01))
                close_price = base_price * (1 + random.uniform(-0.01, 0.01))
                high = max(open_price, close_price) * (1 + random.uniform(0, 0.005))
                low = min(open_price, close_price) * (1 - random.uniform(0, 0.005))
                volume = random.uniform(100, 10000)

                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close_price,
                    volume=volume,
                    quote_volume=volume * (open_price + close_price) / 2,
                    is_closed=True,
                    source="mock",
                    exchange="mock",
                )

                candles.append(candle)

                # Move to previous candle time
                current_time -= timedelta(minutes=interval_minutes)

                # Update base price for continuity
                base_price = open_price

            # Reverse to get chronological order
            candles.reverse()

            return candles

        except Exception as e:
            logger.error(f"Error generating mock candles: {e}")
            return []

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get mock ticker data.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Mock ticker data
        """
        try:
            # Get or generate current price
            if symbol not in self.current_prices:
                self.current_prices[symbol] = 100.0

            current_price = self.current_prices[symbol]

            # Generate ticker data
            ticker = Ticker(
                symbol=symbol,
                last_price=current_price,
                timestamp=int(time.time() * 1000),
                bid_price=current_price * 0.9999,
                bid_size=random.uniform(10, 1000),
                ask_price=current_price * 1.0001,
                ask_size=random.uniform(10, 1000),
                open_24h=current_price * (1 - random.uniform(0, 0.05)),
                high_24h=current_price * (1 + random.uniform(0, 0.05)),
                low_24h=current_price * (1 - random.uniform(0, 0.05)),
                volume_24h=random.uniform(100000, 10000000),
                quote_volume_24h=random.uniform(1000000, 100000000),
                price_change_percentage_24h=random.uniform(-5, 5),
                exchange="mock",
                source="mock",
            )

            return ticker

        except Exception as e:
            logger.error(f"Error generating mock ticker: {e}")
            return None

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[OrderBook]:
        """
        Get mock order book.

        Args:
            symbol: Trading symbol
            depth: Order book depth

        Returns:
            Optional[OrderBook]: Mock order book
        """
        try:
            # Get current price
            if symbol not in self.current_prices:
                self.current_prices[symbol] = 100.0

            current_price = self.current_prices[symbol]
            spread = current_price * 0.0001  # 0.01% spread

            # Generate bid levels
            bids = []
            bid_price = current_price - spread / 2

            for i in range(depth):
                price = bid_price * (1 - i * 0.0001)
                size = random.uniform(10, 1000) * (1 + i * 0.1)  # Increase size with depth
                bids.append(OrderBookLevel(price=price, size=size))

            # Generate ask levels
            asks = []
            ask_price = current_price + spread / 2

            for i in range(depth):
                price = ask_price * (1 + i * 0.0001)
                size = random.uniform(10, 1000) * (1 + i * 0.1)
                asks.append(OrderBookLevel(price=price, size=size))

            orderbook = OrderBook(
                symbol=symbol,
                timestamp=int(time.time() * 1000),
                bids=bids,
                asks=asks,
                exchange="mock",
                source="mock",
                is_snapshot=True,
            )

            return orderbook

        except Exception as e:
            logger.error(f"Error generating mock orderbook: {e}")
            return None

    # ==================== Data Generation Tasks ====================

    async def _generate_ticker(self, subscription: DataSubscription) -> None:
        """Generate ticker data stream."""
        interval = subscription.params.get("interval", 1.0)

        while self.is_running:
            try:
                # Update price with random walk
                self._update_price(subscription.symbol)

                # Generate ticker
                ticker = await self.get_ticker(subscription.symbol)
                if ticker:
                    await self.push_data("ticker", subscription.symbol, ticker)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error generating ticker for {subscription.symbol}: {e}")
                await asyncio.sleep(interval)

    async def _generate_candles(self, subscription: DataSubscription) -> None:
        """Generate candle data stream."""
        timeframe = subscription.params.get("timeframe", "1m")
        interval_minutes = self._get_interval_minutes(timeframe)
        interval_seconds = interval_minutes * 60

        # Track current candle
        current_candle_start = None
        current_candle = None

        while self.is_running:
            try:
                now = datetime.now()
                candle_start = self._get_candle_start(now, interval_minutes)
                candle_start_ms = int(candle_start.timestamp() * 1000)

                if current_candle_start != candle_start_ms:
                    # New candle period
                    if current_candle:
                        # Close previous candle
                        current_candle.is_closed = True
                        await self.push_data("candle", subscription.symbol, current_candle)

                    # Start new candle
                    price = self.current_prices.get(subscription.symbol, 100.0)
                    current_candle = Candle(
                        symbol=subscription.symbol,
                        timeframe=timeframe,
                        timestamp=candle_start_ms,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=0,
                        quote_volume=0,
                        is_closed=False,
                        source="mock",
                        exchange="mock",
                    )
                    current_candle_start = candle_start_ms

                # Update current candle
                if current_candle:
                    price = self._update_price(subscription.symbol)
                    current_candle.close = price
                    current_candle.high = max(current_candle.high, price)
                    current_candle.low = min(current_candle.low, price)
                    current_candle.volume += random.uniform(0.1, 10)
                    current_candle.quote_volume = current_candle.volume * price

                    # Send update
                    await self.push_data("candle", subscription.symbol, current_candle)

                # Wait for next update
                await asyncio.sleep(min(1, interval_seconds / 60))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error generating candles for {subscription.symbol}: {e}")
                await asyncio.sleep(1)

    async def _generate_orderbook(self, subscription: DataSubscription) -> None:
        """Generate order book data stream."""
        interval = subscription.params.get("interval", 0.5)
        depth = subscription.params.get("depth", 20)

        while self.is_running:
            try:
                # Update price
                self._update_price(subscription.symbol)

                # Generate orderbook
                orderbook = await self.get_orderbook(subscription.symbol, depth)
                if orderbook:
                    await self.push_data("orderbook", subscription.symbol, orderbook)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error generating orderbook for {subscription.symbol}: {e}")
                await asyncio.sleep(interval)

    # ==================== Helper Methods ====================

    def _update_price(self, symbol: str) -> float:
        """Update price with random walk."""
        if symbol not in self.current_prices:
            self.current_prices[symbol] = 100.0

        # Random walk with mean reversion
        current = self.current_prices[symbol]
        base = self.base_prices.get(symbol, current)

        # Calculate change
        random_change = random.gauss(0, self.price_volatility)
        reversion = (base - current) / base * 0.01  # Mean reversion

        # Apply change
        change_factor = 1 + random_change + reversion
        new_price = current * change_factor

        # Add some momentum
        if random.random() < 0.1:  # 10% chance of trend
            new_price *= 1 + random.choice([-0.005, 0.005])

        self.current_prices[symbol] = max(new_price, 0.01)  # Ensure positive

        return self.current_prices[symbol]

    def _get_interval_minutes(self, timeframe: str) -> int:
        """Get interval in minutes from timeframe string."""
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
        return mapping.get(timeframe, 1)

    def _get_candle_start(self, dt: datetime, interval_minutes: int) -> datetime:
        """Get candle start time."""
        # Round down to nearest interval
        total_minutes = dt.hour * 60 + dt.minute
        candle_minutes = (total_minutes // interval_minutes) * interval_minutes

        candle_hour = candle_minutes // 60
        candle_minute = candle_minutes % 60

        return dt.replace(hour=candle_hour, minute=candle_minute, second=0, microsecond=0)
