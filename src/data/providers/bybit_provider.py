"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : bybit_provider
    Project     : Project-103
    Description : Bybit data provider implementation. This module implements the Bybit exchange data provider.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import aiohttp

from src.core.exceptions import DataProviderError
from src.data.providers.base_provider import (
    BaseDataProvider,
    ConnectionStatus,
    DataProviderConfig,
    DataSubscription,
)
from src.models import Candle, OrderBook, OrderBookLevel, Ticker
from src.utils.network import create_robust_session

logger = logging.getLogger(__name__)


@dataclass
class WebSocketConnection:
    """Represents a single WebSocket connection in the pool."""

    id: int
    ws: Optional[aiohttp.ClientWebSocketResponse] = None
    symbols: Set[str] = field(default_factory=set)
    handler_task: Optional[asyncio.Task] = None
    last_message_time: float = 0
    is_reconnecting: bool = False
    reconnect_attempt: int = 0


class BybitDataProvider(BaseDataProvider):
    """Bybit exchange data provider implementation."""

    def __init__(self, config: DataProviderConfig):
        """
        Initialize Bybit data provider.

        Args:
            config: Provider configuration
        """
        super().__init__(config)

        # Set Bybit-specific URLs
        if config.testnet:
            self.rest_url = config.rest_url or "https://api-testnet.bybit.com"
            self.ws_url = config.ws_url or "wss://stream-testnet.bybit.com"
        else:
            self.rest_url = config.rest_url or "https://api.bybit.com"
            self.ws_url = config.ws_url or "wss://stream.bybit.com"

        # API version
        self.api_version = "/v5"

        # WebSocket pool configuration
        # Bybit recommends max 10 subscriptions per connection for orderbook
        self.ws_pool_size: int = 4  # Number of WebSocket connections
        self.max_symbols_per_ws: int = 4  # Max symbols per connection for orderbook

        # WebSocket connection pool
        self.ws_pool: List[WebSocketConnection] = []
        self.ws_private: Optional[aiohttp.ClientWebSocketResponse] = None

        # Legacy single connection (for backward compatibility during transition)
        self.ws_public: Optional[aiohttp.ClientWebSocketResponse] = None

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None

        # Subscription mapping
        self.channel_map = {
            "candle": "kline",
            "ticker": "tickers",
            "orderbook": "orderbook",
            "trades": "publicTrade",
        }

        # Local orderbook storage for delta updates
        # Key: symbol, Value: OrderBook
        self._local_orderbooks: Dict[str, OrderBook] = {}

        # WebSocket reconnection settings
        # Set ws_reconnect_enabled=False to disable automatic reconnection (reduces log spam)
        self._reconnect_enabled: bool = (
            config.ws_reconnect_enabled if hasattr(config, "ws_reconnect_enabled") else True
        )
        self._reconnect_max_attempts: int = 0  # 0 = infinite retries (never give up)
        self._reconnect_base_delay: float = 2.0  # seconds
        self._reconnect_max_delay: float = 300.0  # 5 minutes max between retries
        self._reconnect_attempt: int = 0
        self._is_reconnecting: bool = False
        self._ws_handler_lock: asyncio.Lock = asyncio.Lock()  # Prevents concurrent handler restarts
        self._ws_handler_task: Optional[asyncio.Task] = None
        self._ws_watchdog_task: Optional[asyncio.Task] = None
        self._last_ws_message_time: float = 0

        # Timeframe mapping
        self.timeframe_map = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }

        logger.info(f"BybitDataProvider initialized (testnet: {config.testnet})")

    async def connect(self) -> bool:
        """
        Establish connection to Bybit.

        Returns:
            bool: Connection success status
        """
        try:
            # Create HTTP session
            if not self.session:
                self.session = create_robust_session()

            # Re-enable reconnection (in case of previous disconnect)
            self._reconnect_enabled = True
            self._reconnect_attempt = 0

            # Connect to WebSocket if enabled
            if self.config.ws_enabled:
                await self._connect_websocket()
                # Also create WebSocket pool for orderbook subscriptions
                await self._connect_ws_pool()

            # Test REST connection
            test_url = f"{self.rest_url}{self.api_version}/market/time"
            async with self.session.get(test_url) as response:
                if response.status == 200:
                    self.connection_status.is_connected = True
                    self.connection_status.last_update = int(time.time() * 1000)
                    logger.info("Connected to Bybit successfully")
                    return True

            logger.error("Failed to connect to Bybit REST API")
            return False

        except Exception as e:
            logger.error(f"Error connecting to Bybit: {e}")
            self.connection_status.error_message = str(e)
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from Bybit.

        Returns:
            bool: Disconnection success status
        """
        try:
            # Disable reconnection before closing
            self._reconnect_enabled = False

            # Cancel watchdog task
            if self._ws_watchdog_task and not self._ws_watchdog_task.done():
                self._ws_watchdog_task.cancel()
                try:
                    await self._ws_watchdog_task
                except asyncio.CancelledError:
                    pass

            # Cancel handler task
            if self._ws_handler_task and not self._ws_handler_task.done():
                self._ws_handler_task.cancel()
                try:
                    await self._ws_handler_task
                except asyncio.CancelledError:
                    pass

            # Close WebSocket connections
            if self.ws_public:
                await self.ws_public.close()
                self.ws_public = None

            if self.ws_private:
                await self.ws_private.close()
                self.ws_private = None

            # Close WebSocket pool connections
            for conn in self.ws_pool:
                if conn.handler_task and not conn.handler_task.done():
                    conn.handler_task.cancel()
                    try:
                        await conn.handler_task
                    except asyncio.CancelledError:
                        pass
                if conn.ws and not conn.ws.closed:
                    await conn.ws.close()
            self.ws_pool.clear()

            # Close HTTP session
            if self.session:
                await self.session.close()
                self.session = None

            self.connection_status.is_connected = False
            logger.info("Disconnected from Bybit")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from Bybit: {e}")
            return False

    async def subscribe(self, subscription: DataSubscription) -> bool:
        """
        Subscribe to Bybit data stream.

        Args:
            subscription: Subscription configuration

        Returns:
            bool: Subscription success status
        """
        try:
            # Validate symbol format for linear perpetual (must end with USDT)
            symbol = subscription.symbol
            if not symbol.endswith("USDT"):
                logger.warning(
                    f"Skipping subscription for {symbol}: invalid symbol format "
                    f"(linear perpetual symbols must end with USDT)"
                )
                return False

            # Use WebSocket pool for orderbook to distribute load
            if subscription.data_type == "orderbook" and self.ws_pool:
                return await self.subscribe_with_pool(subscription)

            if not self.ws_public:
                logger.error("WebSocket not connected")
                return False

            # Build subscription message
            channel = self.channel_map.get(subscription.data_type)
            if not channel:
                logger.error(f"Unknown data type: {subscription.data_type}")
                return False

            # Build arguments based on data type
            if subscription.data_type == "candle":
                interval = self.timeframe_map.get(subscription.params.get("timeframe", "1m"), "1")
                args = [f"{channel}.{interval}.{subscription.symbol}"]
            elif subscription.data_type == "orderbook":
                depth = subscription.params.get("depth", 50)
                args = [f"{channel}.{depth}.{subscription.symbol}"]
            else:
                args = [f"{channel}.{subscription.symbol}"]

            # Send subscription message
            sub_msg = {"op": "subscribe", "args": args}

            await self.ws_public.send_json(sub_msg)

            # Add to subscriptions set for callback handling
            subscription.subscribed_at = int(time.time() * 1000)
            self.subscriptions.add(subscription)

            logger.info(f"Subscribed to {subscription.symbol} {subscription.data_type}")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {subscription.symbol}: {e}")
            return False

    async def unsubscribe(self, subscription: DataSubscription) -> bool:
        """
        Unsubscribe from Bybit data stream.

        Args:
            subscription: Subscription to remove

        Returns:
            bool: Unsubscription success status
        """
        try:
            if not self.ws_public:
                return True

            # Build unsubscription message
            channel = self.channel_map.get(subscription.data_type)
            if not channel:
                return False

            # Build arguments
            if subscription.data_type == "candle":
                interval = self.timeframe_map.get(subscription.params.get("timeframe", "1m"), "1")
                args = [f"{channel}.{interval}.{subscription.symbol}"]
            elif subscription.data_type == "orderbook":
                depth = subscription.params.get("depth", 50)
                args = [f"{channel}.{depth}.{subscription.symbol}"]
            else:
                args = [f"{channel}.{subscription.symbol}"]

            # Send unsubscription message
            unsub_msg = {"op": "unsubscribe", "args": args}

            await self.ws_public.send_json(unsub_msg)

            logger.info(f"Unsubscribed from {subscription.symbol} {subscription.data_type}")
            return True

        except Exception as e:
            logger.error(f"Error unsubscribing from {subscription.symbol}: {e}")
            return False

    async def _make_request_with_retry(
        self, url: str, params: Optional[Dict] = None, max_retries: int = 3
    ) -> Optional[Dict]:
        """
        Make HTTP GET request with retry logic for network errors.

        Args:
            url: Request URL
            params: Query parameters
            max_retries: Maximum number of retry attempts

        Returns:
            Response JSON or None on failure
        """
        for attempt in range(max_retries + 1):
            try:
                if not self.session or self.session.closed:
                    self.session = create_robust_session()

                async with self.session.get(url, params=params) as response:
                    if response.status != 200:
                        logger.error(f"HTTP error: {response.status}")
                        return None
                    return await response.json()

            except (
                aiohttp.ClientConnectorError,
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientOSError,
                asyncio.TimeoutError,
                OSError,
            ) as e:
                if attempt < max_retries:
                    delay = min(1.0 * (2**attempt), 30.0)
                    logger.warning(
                        f"Network error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Network error after {max_retries + 1} attempts: {e}")
                    return None

            except Exception as e:
                logger.error(f"Request error: {e}")
                return None

        return None

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """
        Get historical candles from Bybit.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            limit: Number of candles
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[Candle]: Historical candles
        """
        try:
            await self.check_rate_limit()

            # Build request parameters
            params = {
                "category": "linear",
                "symbol": symbol,
                "interval": self.timeframe_map.get(timeframe, "1"),
                "limit": min(limit, self.config.max_candles_per_request),
            }

            if start_time:
                params["start"] = start_time
            if end_time:
                params["end"] = end_time

            # Make API request with retry
            url = f"{self.rest_url}{self.api_version}/market/kline"
            data = await self._make_request_with_retry(url, params)

            if not data:
                return []

            if data.get("retCode") != 0:
                logger.error(f"Bybit API error: {data.get('retMsg')}")
                return []

            # Parse candles
            candles = []
            klines = data.get("result", {}).get("list", [])

            # Bybit returns newest first, reverse for chronological order
            klines.reverse()

            for kline in klines:
                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=int(kline[0]),
                    open=float(kline[1]),
                    high=float(kline[2]),
                    low=float(kline[3]),
                    close=float(kline[4]),
                    volume=float(kline[5]),
                    quote_volume=float(kline[6]) if len(kline) > 6 else None,
                    is_closed=True,
                    source="bybit",
                    exchange="bybit",
                )
                candles.append(candle)

            return candles

        except Exception as e:
            logger.error(f"Error getting candles: {e}")
            return []

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get current ticker from Bybit.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Ticker data
        """
        try:
            await self.check_rate_limit()

            # Build request
            params = {"category": "linear", "symbol": symbol}

            url = f"{self.rest_url}{self.api_version}/market/tickers"

            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"Failed to get ticker: {response.status}")
                    return None

                data = await response.json()

                if data.get("retCode") != 0:
                    logger.error(f"Bybit API error: {data.get('retMsg')}")
                    return None

                # Parse ticker
                tickers = data.get("result", {}).get("list", [])
                if not tickers:
                    return None

                ticker_data = tickers[0]

                ticker = Ticker(
                    symbol=symbol,
                    last_price=float(ticker_data.get("lastPrice", 0)),
                    timestamp=int(data.get("time", time.time() * 1000)),
                    bid_price=float(ticker_data.get("bid1Price", 0))
                    if ticker_data.get("bid1Price")
                    else None,
                    bid_size=float(ticker_data.get("bid1Size", 0))
                    if ticker_data.get("bid1Size")
                    else None,
                    ask_price=float(ticker_data.get("ask1Price", 0))
                    if ticker_data.get("ask1Price")
                    else None,
                    ask_size=float(ticker_data.get("ask1Size", 0))
                    if ticker_data.get("ask1Size")
                    else None,
                    open_24h=float(ticker_data.get("prevPrice24h", 0))
                    if ticker_data.get("prevPrice24h")
                    else None,
                    high_24h=float(ticker_data.get("highPrice24h", 0))
                    if ticker_data.get("highPrice24h")
                    else None,
                    low_24h=float(ticker_data.get("lowPrice24h", 0))
                    if ticker_data.get("lowPrice24h")
                    else None,
                    volume_24h=float(ticker_data.get("volume24h", 0))
                    if ticker_data.get("volume24h")
                    else None,
                    quote_volume_24h=float(ticker_data.get("turnover24h", 0))
                    if ticker_data.get("turnover24h")
                    else None,
                    price_change_percentage_24h=float(ticker_data.get("price24hPcnt", 0)) * 100
                    if ticker_data.get("price24hPcnt")
                    else None,
                    mark_price=float(ticker_data.get("markPrice", 0))
                    if ticker_data.get("markPrice")
                    else None,
                    index_price=float(ticker_data.get("indexPrice", 0))
                    if ticker_data.get("indexPrice")
                    else None,
                    funding_rate=float(ticker_data.get("fundingRate", 0))
                    if ticker_data.get("fundingRate")
                    else None,
                    next_funding_time=int(ticker_data.get("nextFundingTime", 0))
                    if ticker_data.get("nextFundingTime")
                    else None,
                    open_interest=float(ticker_data.get("openInterest", 0))
                    if ticker_data.get("openInterest")
                    else None,
                    exchange="bybit",
                    source="bybit_rest",
                )

                return ticker

        except Exception as e:
            logger.error(f"Error getting ticker: {e}")
            return None

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[OrderBook]:
        """
        Get order book from Bybit.

        Args:
            symbol: Trading symbol
            depth: Order book depth

        Returns:
            Optional[OrderBook]: Order book data
        """
        try:
            await self.check_rate_limit()

            # Build request
            params = {
                "category": "linear",
                "symbol": symbol,
                "limit": min(depth, self.config.max_orderbook_depth),
            }

            url = f"{self.rest_url}{self.api_version}/market/orderbook"

            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"Failed to get orderbook: {response.status}")
                    return None

                data = await response.json()

                if data.get("retCode") != 0:
                    logger.error(f"Bybit API error: {data.get('retMsg')}")
                    return None

                # Parse order book
                result = data.get("result", {})

                # Create bid levels
                bids = []
                for bid in result.get("b", []):
                    bids.append(OrderBookLevel(price=float(bid[0]), size=float(bid[1])))

                # Create ask levels
                asks = []
                for ask in result.get("a", []):
                    asks.append(OrderBookLevel(price=float(ask[0]), size=float(ask[1])))

                orderbook = OrderBook(
                    symbol=symbol,
                    timestamp=int(result.get("ts", time.time() * 1000)),
                    bids=bids,
                    asks=asks,
                    exchange="bybit",
                    source="bybit_rest",
                    sequence=int(result.get("u", 0)),
                    is_snapshot=True,
                )

                return orderbook

        except Exception as e:
            logger.error(f"Error getting orderbook: {e}")
            return None

    # ==================== WebSocket Methods ====================

    async def _connect_websocket(self) -> None:
        """Connect to Bybit WebSocket."""
        try:
            # Connect to public WebSocket
            ws_url = f"{self.ws_url}/v5/public/linear"

            self.ws_public = await self.session.ws_connect(
                ws_url, heartbeat=self.config.ws_ping_interval
            )

            # Initialize message time
            self._last_ws_message_time = time.time()

            # Start message handler with error tracking
            self._ws_handler_task = asyncio.create_task(self._handle_ws_messages())
            self._ws_handler_task.add_done_callback(self._on_ws_handler_done)

            # Start watchdog task (only once)
            if self._ws_watchdog_task is None or self._ws_watchdog_task.done():
                self._ws_watchdog_task = asyncio.create_task(self._ws_watchdog())

            logger.info("Connected to Bybit WebSocket")

        except Exception as e:
            logger.error(f"Error connecting to WebSocket: {e}")
            raise DataProviderError(f"WebSocket connection failed: {e}")

    async def _connect_ws_pool(self) -> None:
        """Create a pool of WebSocket connections for distributing subscriptions."""
        try:
            ws_url = f"{self.ws_url}/v5/public/linear"

            for i in range(self.ws_pool_size):
                ws = await self.session.ws_connect(ws_url, heartbeat=self.config.ws_ping_interval)

                conn = WebSocketConnection(
                    id=i, ws=ws, symbols=set(), last_message_time=time.time()
                )

                # Start message handler for this connection
                conn.handler_task = asyncio.create_task(self._handle_pool_ws_messages(conn))

                self.ws_pool.append(conn)
                logger.info(f"WebSocket pool connection {i + 1}/{self.ws_pool_size} established")

            logger.info(f"WebSocket pool created with {len(self.ws_pool)} connections")

        except Exception as e:
            logger.error(f"Error creating WebSocket pool: {e}")
            raise DataProviderError(f"WebSocket pool creation failed: {e}")

    def _get_ws_for_symbol(self, symbol: str) -> Optional[WebSocketConnection]:
        """
        Get the WebSocket connection for a symbol.
        If symbol is already assigned, return that connection.
        Otherwise, assign to the connection with fewest symbols.
        """
        if not self.ws_pool:
            return None

        # Check if symbol is already assigned
        for conn in self.ws_pool:
            if symbol in conn.symbols:
                return conn

        # Find connection with fewest symbols
        available_conns = [c for c in self.ws_pool if c.ws and not c.ws.closed]
        if not available_conns:
            return None

        # Sort by number of symbols (least first)
        available_conns.sort(key=lambda c: len(c.symbols))
        return available_conns[0]

    async def _handle_pool_ws_messages(self, conn: WebSocketConnection) -> None:
        """Handle messages for a specific pool connection."""
        logger.info(f"WebSocket pool handler started for connection {conn.id}")
        message_count = 0
        last_log_count = 0

        while True:
            try:
                if not conn.ws or conn.ws.closed:
                    logger.debug(f"Pool connection {conn.id} closed, attempting reconnect")
                    await self._reconnect_pool_connection(conn)
                    continue

                async for msg in conn.ws:
                    message_count += 1
                    current_time = time.time()
                    conn.last_message_time = current_time
                    # Also update main last message time so watchdog doesn't trigger
                    self._last_ws_message_time = current_time
                    conn.reconnect_attempt = 0

                    # Log every 100 messages for debugging
                    if message_count - last_log_count >= 100:
                        logger.debug(
                            f"Pool connection {conn.id}: {message_count} messages received"
                        )
                        last_log_count = message_count

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._process_ws_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"Pool connection {conn.id} error: {conn.ws.exception()}")
                        break
                    elif msg.type in [aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED]:
                        logger.warning(f"Pool connection {conn.id} closed by server")
                        break

                # Connection closed, try to reconnect
                if self._reconnect_enabled:
                    await self._reconnect_pool_connection(conn)
                else:
                    break

            except asyncio.CancelledError:
                logger.info(f"Pool handler {conn.id} cancelled after {message_count} messages")
                break
            except Exception as e:
                logger.error(f"Error in pool handler {conn.id}: {e}")
                if self._reconnect_enabled:
                    await asyncio.sleep(2)
                else:
                    break

    async def _reconnect_pool_connection(self, conn: WebSocketConnection) -> bool:
        """Reconnect a specific pool connection."""
        if conn.is_reconnecting:
            return False

        conn.is_reconnecting = True
        conn.reconnect_attempt += 1

        try:
            delay = min(
                self._reconnect_base_delay * (2 ** min(conn.reconnect_attempt - 1, 10)),
                self._reconnect_max_delay,
            )
            # Log less frequently to reduce spam
            logger.debug(
                f"Reconnecting pool connection {conn.id} in {delay:.1f}s (attempt {conn.reconnect_attempt})"
            )
            await asyncio.sleep(delay)

            # Close old connection
            if conn.ws and not conn.ws.closed:
                await conn.ws.close()

            # Create new connection
            ws_url = f"{self.ws_url}/v5/public/linear"
            conn.ws = await self.session.ws_connect(ws_url, heartbeat=self.config.ws_ping_interval)
            conn.last_message_time = time.time()

            # Resubscribe to all symbols on this connection
            await self._resubscribe_pool_connection(conn)

            logger.info(
                f"Pool connection {conn.id} reconnected, resubscribed to {len(conn.symbols)} symbols"
            )
            conn.is_reconnecting = False
            return True

        except Exception as e:
            # Log only on DEBUG level to avoid spam
            logger.debug(f"Failed to reconnect pool connection {conn.id}: {e}")
            conn.is_reconnecting = False
            return False

    async def _resubscribe_pool_connection(self, conn: WebSocketConnection) -> None:
        """Resubscribe to all topics on a reconnected pool connection."""
        for sub in self.subscriptions:
            if sub.symbol in conn.symbols:
                channel = self.channel_map.get(sub.data_type)
                if not channel:
                    continue

                if sub.data_type == "candle":
                    interval = self.timeframe_map.get(sub.params.get("timeframe", "1m"), "1")
                    args = [f"{channel}.{interval}.{sub.symbol}"]
                elif sub.data_type == "orderbook":
                    depth = sub.params.get("depth", 50)
                    args = [f"{channel}.{depth}.{sub.symbol}"]
                else:
                    args = [f"{channel}.{sub.symbol}"]

                sub_msg = {"op": "subscribe", "args": args}
                await conn.ws.send_json(sub_msg)

    async def subscribe_with_pool(self, subscription: DataSubscription) -> bool:
        """Subscribe using the WebSocket pool (for orderbook)."""
        try:
            conn = self._get_ws_for_symbol(subscription.symbol)
            if not conn or not conn.ws:
                logger.error(f"No available pool connection for {subscription.symbol}")
                return False

            # Build subscription message
            channel = self.channel_map.get(subscription.data_type)
            if not channel:
                logger.error(f"Unknown data type: {subscription.data_type}")
                return False

            if subscription.data_type == "candle":
                interval = self.timeframe_map.get(subscription.params.get("timeframe", "1m"), "1")
                args = [f"{channel}.{interval}.{subscription.symbol}"]
            elif subscription.data_type == "orderbook":
                depth = subscription.params.get("depth", 50)
                args = [f"{channel}.{depth}.{subscription.symbol}"]
            else:
                args = [f"{channel}.{subscription.symbol}"]

            sub_msg = {"op": "subscribe", "args": args}
            await conn.ws.send_json(sub_msg)

            # Track symbol on this connection
            conn.symbols.add(subscription.symbol)

            # Add to subscriptions set
            subscription.subscribed_at = int(time.time() * 1000)
            self.subscriptions.add(subscription)

            logger.info(
                f"Subscribed to {subscription.symbol} {subscription.data_type} on pool connection {conn.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error subscribing {subscription.symbol} with pool: {e}")
            return False

    async def _handle_ws_messages(self) -> None:
        """Handle incoming WebSocket messages with automatic reconnection."""
        logger.debug("WebSocket message handler started")
        message_count = 0
        last_message_time = time.time()

        while True:
            try:
                # Check if WebSocket needs reconnection
                if not self.ws_public or self.ws_public.closed:
                    logger.debug(
                        f"WebSocket not connected (closed={getattr(self.ws_public, 'closed', 'N/A')})"
                    )
                    self.connection_status.is_connected = False

                    if self._reconnect_enabled:
                        # Reset reconnecting flag if stuck
                        if self._is_reconnecting:
                            logger.debug("Reconnecting flag was stuck, resetting...")
                            self._is_reconnecting = False

                        success = await self._reconnect_websocket()
                        if not success:
                            logger.debug("WebSocket reconnection failed, waiting before retry...")
                            await asyncio.sleep(5)
                            continue
                    else:
                        logger.info("WebSocket reconnection disabled, exiting handler")
                        break

                # Process messages
                async for msg in self.ws_public:
                    message_count += 1
                    current_time = time.time()
                    last_message_time = current_time
                    self._last_ws_message_time = current_time

                    # Reset reconnect counter on successful message
                    self._reconnect_attempt = 0

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._process_ws_message(msg.data)

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WebSocket error: {self.ws_public.exception()}")
                        self.connection_status.is_connected = False
                        break

                    elif msg.type in [aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED]:
                        logger.debug(
                            f"WebSocket connection closed by server (processed {message_count} messages)"
                        )
                        self.connection_status.is_connected = False
                        break

                # Async for loop exited - connection closed
                logger.debug(f"WebSocket message loop exited after {message_count} messages")
                self.connection_status.is_connected = False

                # Attempt reconnect
                if self._reconnect_enabled:
                    logger.debug("Attempting WebSocket reconnection...")
                    await self._reconnect_websocket()
                else:
                    logger.info("Reconnection disabled, exiting handler loop")
                    break

            except asyncio.CancelledError:
                logger.debug(
                    f"WebSocket handler cancelled after processing {message_count} messages"
                )
                break
            except Exception as e:
                logger.error(f"Error in WebSocket handler: {e}", exc_info=True)
                self.connection_status.is_connected = False

                # Reset reconnecting flag on error
                self._is_reconnecting = False

                if self._reconnect_enabled:
                    logger.debug("Attempting reconnection after error...")
                    await asyncio.sleep(1)  # Brief pause before reconnect
                    await self._reconnect_websocket()
                else:
                    logger.warning("Reconnection disabled, exiting after error")
                    break

        logger.debug(f"WebSocket handler exiting (total messages: {message_count})")

    def _on_ws_handler_done(self, task: asyncio.Task) -> None:
        """Callback when WebSocket handler task completes."""
        try:
            exc = task.exception()
            if exc:
                logger.error(f"❌ WebSocket handler crashed with exception: {exc}", exc_info=exc)
            else:
                logger.debug("WebSocket handler task completed unexpectedly")
        except asyncio.CancelledError:
            logger.debug("WebSocket handler task was cancelled")
            return  # Don't restart if cancelled intentionally
        except Exception as e:
            logger.error(f"Error in WebSocket handler callback: {e}")

        # Mark as disconnected
        self.connection_status.is_connected = False

        # Reset reconnecting flag
        self._is_reconnecting = False

        # Restart handler if reconnection is enabled
        if self._reconnect_enabled:
            logger.debug("Restarting WebSocket handler task...")
            try:
                # Schedule restart in event loop
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._restart_ws_handler())
            except Exception as e:
                logger.error(f"Failed to schedule handler restart: {e}")

    async def _restart_ws_handler(self) -> None:
        """Restart WebSocket handler after a brief delay."""
        await asyncio.sleep(2)  # Brief delay before restart

        if not self._reconnect_enabled:
            logger.info("Reconnection disabled, not restarting handler")
            return

        # Use lock to prevent concurrent restarts (avoids "Concurrent call to receive()")
        async with self._ws_handler_lock:
            try:
                # Cancel old handler if still running to prevent concurrent receive()
                if self._ws_handler_task and not self._ws_handler_task.done():
                    logger.debug("Cancelling old WebSocket handler before restart...")
                    self._ws_handler_task.cancel()
                    try:
                        await self._ws_handler_task
                    except asyncio.CancelledError:
                        pass

                # Reconnect WebSocket first
                if not self.ws_public or self.ws_public.closed:
                    logger.debug("Reconnecting WebSocket before restarting handler...")
                    success = await self._reconnect_websocket()
                    if not success:
                        logger.debug("Failed to reconnect, will retry in 10 seconds...")
                        await asyncio.sleep(10)
                        # Schedule retry outside the lock
                        asyncio.create_task(self._restart_ws_handler())
                        return

                # Start new handler task
                self._ws_handler_task = asyncio.create_task(self._handle_ws_messages())
                self._ws_handler_task.add_done_callback(self._on_ws_handler_done)
                logger.debug("WebSocket handler task restarted successfully")

            except Exception as e:
                logger.error(f"Error restarting WebSocket handler: {e}")

    async def _ws_watchdog(self) -> None:
        """
        Watchdog task to monitor WebSocket health and restart if needed.
        Checks every 60 seconds if messages are being received.
        """
        logger.info("WebSocket watchdog started")
        check_interval = 60  # seconds (increased from 30)
        stale_threshold = 180  # seconds without messages before considering stale (3 min)
        stale_warning_logged = False  # Only log warning once per stale period

        while self._reconnect_enabled:
            try:
                await asyncio.sleep(check_interval)

                # Skip check if not connected
                if not self.connection_status.is_connected:
                    stale_warning_logged = False
                    continue

                # Check if messages are being received
                current_time = time.time()
                time_since_last_message = current_time - self._last_ws_message_time

                # Also check pool connections - if any pool connection is active, consider WS healthy
                pool_active = False
                max_pool_time = 0
                if self.ws_pool:
                    for conn in self.ws_pool:
                        if conn.last_message_time > 0:
                            pool_time = current_time - conn.last_message_time
                            if pool_time < stale_threshold:
                                pool_active = True
                                # Update main timestamp from pool
                                self._last_ws_message_time = max(
                                    self._last_ws_message_time, conn.last_message_time
                                )
                                max_pool_time = max(max_pool_time, conn.last_message_time)

                # Skip stale check if pool is active
                if pool_active:
                    stale_warning_logged = False
                    continue

                if time_since_last_message > stale_threshold:
                    # Only log warning once per stale period
                    if not stale_warning_logged:
                        logger.warning(
                            f"⚠️ WebSocket stale: no messages for {time_since_last_message:.0f}s (threshold: {stale_threshold}s)"
                        )
                        stale_warning_logged = True

                    # Check if handler task is still running
                    if self._ws_handler_task is None or self._ws_handler_task.done():
                        logger.debug("WebSocket handler task is not running, restarting...")
                        await self._restart_ws_handler()
                    else:
                        # Handler is running but no messages - connection might be dead
                        logger.debug("Handler running but no messages - forcing reconnection")

                        # Cancel current handler
                        self._ws_handler_task.cancel()
                        try:
                            await self._ws_handler_task
                        except asyncio.CancelledError:
                            pass

                        # Force reconnection
                        self._is_reconnecting = False
                        await self._restart_ws_handler()

                elif time_since_last_message > check_interval:
                    # Log debug info if messages are slow
                    logger.debug(f"WebSocket: {time_since_last_message:.0f}s since last message")

            except asyncio.CancelledError:
                logger.info("WebSocket watchdog cancelled")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket watchdog: {e}")
                await asyncio.sleep(5)

        logger.info("WebSocket watchdog stopped")

    async def _reconnect_websocket(self) -> bool:
        """
        Attempt to reconnect WebSocket with exponential backoff.

        Returns:
            bool: True if reconnection successful
        """
        if self._is_reconnecting:
            logger.debug("Already reconnecting, skipping duplicate attempt")
            return False

        self._is_reconnecting = True

        try:
            # Infinite retry loop (max_attempts=0 means never give up)
            while True:
                self._reconnect_attempt += 1

                # Calculate delay with exponential backoff (caps at max_delay)
                delay = min(
                    self._reconnect_base_delay * (2 ** min(self._reconnect_attempt - 1, 10)),
                    self._reconnect_max_delay,
                )

                # Log reconnect attempts on DEBUG level to reduce spam
                logger.debug(
                    f"WebSocket reconnecting in {delay:.1f}s "
                    f"(attempt {self._reconnect_attempt})"
                )

                await asyncio.sleep(delay)

                try:
                    # Ensure session exists
                    if not self.session or self.session.closed:
                        logger.info("Creating new HTTP session for WebSocket...")
                        self.session = create_robust_session()

                    # Close old connection if exists
                    if self.ws_public:
                        try:
                            if not self.ws_public.closed:
                                await self.ws_public.close()
                        except Exception as close_err:
                            logger.debug(f"Error closing old WebSocket: {close_err}")
                        self.ws_public = None

                    # Reconnect
                    ws_url = f"{self.ws_url}/v5/public/linear"
                    self.ws_public = await self.session.ws_connect(
                        ws_url, heartbeat=self.config.ws_ping_interval
                    )

                    self.connection_status.is_connected = True
                    logger.debug(f"WebSocket reconnected (attempt {self._reconnect_attempt})")

                    # Resubscribe to all topics
                    await self._resubscribe_all()

                    # Reset counters on success
                    self._reconnect_attempt = 0
                    self._is_reconnecting = False
                    return True

                except Exception as e:
                    # Log every 10th failed attempt to reduce spam
                    logger.debug(f"Reconnection attempt {self._reconnect_attempt} failed: {e}")
                    continue

        except asyncio.CancelledError:
            logger.info("WebSocket reconnection was cancelled")
            self._is_reconnecting = False
            raise
        except Exception as e:
            logger.error(f"Error during reconnection: {e}", exc_info=True)
            self._reconnect_attempt = 0  # Reset for future retries
            self._is_reconnecting = False
            return False

    async def _resubscribe_all(self) -> None:
        """Resubscribe to all previously subscribed topics after reconnection."""
        if not self.subscriptions:
            logger.debug("No subscriptions to restore")
            return

        logger.debug(f"Resubscribing to {len(self.subscriptions)} topics...")
        successful = 0
        failed = 0

        # Copy subscriptions to avoid modification during iteration
        subs_to_restore = list(self.subscriptions)
        self.subscriptions.clear()

        for subscription in subs_to_restore:
            try:
                # Build subscription message
                channel = self.channel_map.get(subscription.data_type)
                if not channel:
                    failed += 1
                    continue

                if subscription.data_type == "candle":
                    interval = self.timeframe_map.get(
                        subscription.params.get("timeframe", "1m"), "1"
                    )
                    args = [f"{channel}.{interval}.{subscription.symbol}"]
                elif subscription.data_type == "orderbook":
                    depth = subscription.params.get("depth", 50)
                    args = [f"{channel}.{depth}.{subscription.symbol}"]
                else:
                    args = [f"{channel}.{subscription.symbol}"]

                sub_msg = {"op": "subscribe", "args": args}

                await self.ws_public.send_json(sub_msg)
                subscription.subscribed_at = int(time.time() * 1000)
                self.subscriptions.add(subscription)
                successful += 1

                # Small delay to avoid rate limiting
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error(
                    f"Failed to resubscribe {subscription.symbol} {subscription.data_type}: {e}"
                )
                failed += 1

        if failed > 0:
            logger.warning(f"Resubscription: {successful} ok, {failed} failed")
        else:
            logger.debug(f"Resubscription complete: {successful} topics")

    async def _process_ws_message(self, message: str) -> None:
        """
        Process WebSocket message.

        Args:
            message: Raw message string
        """
        try:
            data = json.loads(message)

            # Update connection statistics
            self.connection_status.messages_received += 1
            self.connection_status.bytes_received += len(message)

            # Handle different message types
            if data.get("op") == "pong":
                # Heartbeat response
                self.connection_status.update_heartbeat()

            elif data.get("op") == "subscribe":
                # Subscription confirmation
                if data.get("success"):
                    logger.debug(f"Subscription confirmed: {data.get('req_id')}")
                else:
                    logger.error(f"Subscription failed: {data.get('ret_msg')}")

            elif "topic" in data:
                # Data message
                await self._process_data_message(data)

            elif data.get("ret_code"):
                # Error message
                logger.error(f"Bybit error: {data.get('ret_msg')}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {e}")
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")

    async def _process_data_message(self, data: Dict[str, Any]) -> None:
        """
        Process data message from WebSocket.

        Args:
            data: Parsed message data
        """
        try:
            topic = data.get("topic", "")

            # Parse topic to get data type and symbol
            if "kline" in topic:
                await self._process_candle_data(data)
            elif "tickers" in topic:
                await self._process_ticker_data(data)
            elif "orderbook" in topic:
                await self._process_orderbook_data(data)
            elif "publicTrade" in topic:
                await self._process_trade_data(data)
            else:
                logger.debug(f"Unknown topic: {topic}")

        except Exception as e:
            logger.error(f"Error processing data message: {e}")

    async def _process_candle_data(self, data: Dict[str, Any]) -> None:
        """Process candle data from WebSocket."""
        try:
            # Parse topic: kline.{interval}.{symbol}
            topic_parts = data.get("topic", "").split(".")
            if len(topic_parts) != 3:
                return

            interval = topic_parts[1]
            symbol = topic_parts[2]

            # Find timeframe
            timeframe = None
            for tf, bybit_interval in self.timeframe_map.items():
                if bybit_interval == interval:
                    timeframe = tf
                    break

            if not timeframe:
                return

            # Parse candle data
            candle_data = data.get("data", [])
            if not candle_data:
                return

            for item in candle_data:
                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=int(item.get("start", 0)),
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=float(item.get("close", 0)),
                    volume=float(item.get("volume", 0)),
                    quote_volume=float(item.get("turnover", 0)) if "turnover" in item else None,
                    is_closed=item.get("confirm", False),
                    source="bybit_ws",
                    exchange="bybit",
                )

                await self.push_data("candle", symbol, candle)

        except Exception as e:
            logger.error(f"Error processing candle data: {e}")

    async def _process_ticker_data(self, data: Dict[str, Any]) -> None:
        """Process ticker data from WebSocket."""
        try:
            ticker_data = data.get("data", {})
            if not ticker_data:
                return

            symbol = ticker_data.get("symbol")
            if not symbol:
                return

            ticker = Ticker(
                symbol=symbol,
                last_price=float(ticker_data.get("lastPrice", 0)),
                timestamp=int(data.get("ts", time.time() * 1000)),
                bid_price=float(ticker_data.get("bid1Price", 0))
                if ticker_data.get("bid1Price")
                else None,
                bid_size=float(ticker_data.get("bid1Size", 0))
                if ticker_data.get("bid1Size")
                else None,
                ask_price=float(ticker_data.get("ask1Price", 0))
                if ticker_data.get("ask1Price")
                else None,
                ask_size=float(ticker_data.get("ask1Size", 0))
                if ticker_data.get("ask1Size")
                else None,
                volume_24h=float(ticker_data.get("volume24h", 0))
                if ticker_data.get("volume24h")
                else None,
                quote_volume_24h=float(ticker_data.get("turnover24h", 0))
                if ticker_data.get("turnover24h")
                else None,
                exchange="bybit",
                source="bybit_ws",
            )

            await self.push_data("ticker", symbol, ticker)

        except Exception as e:
            logger.error(f"Error processing ticker data: {e}")

    async def _process_orderbook_data(self, data: Dict[str, Any]) -> None:
        """
        Process order book data from WebSocket.

        Handles both snapshot (full orderbook) and delta (incremental updates).
        Maintains local orderbook state and applies deltas correctly.
        """
        try:
            # Parse topic: orderbook.{depth}.{symbol}
            topic_parts = data.get("topic", "").split(".")
            if len(topic_parts) != 3:
                return

            symbol = topic_parts[2]
            is_snapshot = data.get("type") == "snapshot"
            orderbook_data = data.get("data", {})

            if not orderbook_data:
                return

            timestamp = int(data.get("ts", time.time() * 1000))
            sequence = int(orderbook_data.get("u", 0))

            if is_snapshot:
                # Full snapshot - replace local orderbook
                bids = []
                for bid in orderbook_data.get("b", []):
                    bids.append(OrderBookLevel(price=float(bid[0]), size=float(bid[1])))

                asks = []
                for ask in orderbook_data.get("a", []):
                    asks.append(OrderBookLevel(price=float(ask[0]), size=float(ask[1])))

                orderbook = OrderBook(
                    symbol=symbol,
                    timestamp=timestamp,
                    bids=sorted(bids, key=lambda x: x.price, reverse=True),
                    asks=sorted(asks, key=lambda x: x.price),
                    exchange="bybit",
                    source="bybit_ws",
                    sequence=sequence,
                    is_snapshot=True,
                )

                # Store locally
                self._local_orderbooks[symbol] = orderbook

            else:
                # Delta update - apply to local orderbook
                local_ob = self._local_orderbooks.get(symbol)

                if local_ob is None:
                    # No local orderbook yet, skip delta (wait for snapshot)
                    logger.debug(f"Skipping delta for {symbol} - no snapshot yet")
                    return

                # Apply bid updates
                bid_dict = {level.price: level.size for level in local_ob.bids}
                for bid in orderbook_data.get("b", []):
                    price = float(bid[0])
                    size = float(bid[1])
                    if size == 0:
                        # Remove level
                        bid_dict.pop(price, None)
                    else:
                        # Add/update level
                        bid_dict[price] = size

                # Apply ask updates
                ask_dict = {level.price: level.size for level in local_ob.asks}
                for ask in orderbook_data.get("a", []):
                    price = float(ask[0])
                    size = float(ask[1])
                    if size == 0:
                        # Remove level
                        ask_dict.pop(price, None)
                    else:
                        # Add/update level
                        ask_dict[price] = size

                # Rebuild orderbook from dicts
                new_bids = [
                    OrderBookLevel(price=p, size=s)
                    for p, s in sorted(bid_dict.items(), reverse=True)
                ]
                new_asks = [OrderBookLevel(price=p, size=s) for p, s in sorted(ask_dict.items())]

                orderbook = OrderBook(
                    symbol=symbol,
                    timestamp=timestamp,
                    bids=new_bids,
                    asks=new_asks,
                    exchange="bybit",
                    source="bybit_ws",
                    sequence=sequence,
                    is_snapshot=False,
                )

                # Update local storage
                self._local_orderbooks[symbol] = orderbook

            # Push the complete orderbook (not just delta)
            await self.push_data("orderbook", symbol, orderbook)

        except Exception as e:
            logger.error(f"Error processing orderbook data: {e}")

    async def _process_trade_data(self, data: Dict[str, Any]) -> None:
        """
        Process trade data from WebSocket.

        Bybit publicTrade format:
        {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "ts": 1672304486868,
            "data": [
                {
                    "T": 1672304486865,      # Timestamp
                    "s": "BTCUSDT",           # Symbol
                    "S": "Buy",               # Side: Buy/Sell
                    "v": "0.001",             # Size
                    "p": "16578.50",          # Price
                    "L": "PlusTick",          # Tick direction
                    "i": "20f43950-d8dd-5b31-9112-a178eb6023af",  # Trade ID
                    "BT": false               # Block trade
                }
            ]
        }
        """
        try:
            # Parse topic: publicTrade.{symbol}
            topic_parts = data.get("topic", "").split(".")
            if len(topic_parts) != 2:
                return

            symbol = topic_parts[1]
            trades_data = data.get("data", [])

            if not trades_data:
                return

            for trade in trades_data:
                trade_dict = {
                    "symbol": symbol,
                    "timestamp": int(trade.get("T", 0)),
                    "trade_id": trade.get("i", ""),
                    "price": float(trade.get("p", 0)),
                    "size": float(trade.get("v", 0)),
                    "side": trade.get("S", "").upper(),  # BUY or SELL
                    "tick_direction": trade.get(
                        "L", ""
                    ),  # PlusTick, MinusTick, ZeroPlusTick, ZeroMinusTick
                    "is_block_trade": trade.get("BT", False),
                    "exchange": "bybit",
                    "source": "bybit_ws",
                }

                await self.push_data("trade", symbol, trade_dict)

        except Exception as e:
            logger.error(f"Error processing trade data: {e}")
