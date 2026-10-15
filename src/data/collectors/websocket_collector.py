"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : websocket_collector
    Project     : Project-103
    Description : WebSocket data collector. This module handles WebSocket connections for real-time data collection.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

import aiohttp

from src.core.exceptions import DataProviderError

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """WebSocket message types."""

    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    AUTH = "auth"
    PING = "ping"
    PONG = "pong"
    DATA = "data"
    ERROR = "error"
    INFO = "info"


@dataclass
class WebSocketConfig:
    """WebSocket connection configuration."""

    url: str
    name: str = "websocket"

    # Connection settings
    ping_interval: int = 30  # seconds
    ping_timeout: int = 10  # seconds
    reconnect_delay: int = 5  # seconds
    max_reconnect_attempts: int = 10
    connection_timeout: int = 30  # seconds

    # Message handling
    max_message_size: int = 10 * 1024 * 1024  # 10MB
    queue_size: int = 10000
    batch_size: int = 100

    # Authentication
    auth_required: bool = False
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    auth_type: str = "header"  # header, message, query

    # SSL
    ssl_verify: bool = True

    # Compression
    compress: int = 0  # 0-9, 0 = disabled

    # Custom headers
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class WebSocketStats:
    """WebSocket connection statistics."""

    connected_at: Optional[int] = None
    disconnected_at: Optional[int] = None
    reconnect_count: int = 0
    connection_attempts: int = 0

    messages_sent: int = 0
    messages_received: int = 0
    messages_processed: int = 0
    messages_dropped: int = 0

    bytes_sent: int = 0
    bytes_received: int = 0

    errors: List[str] = field(default_factory=list)
    last_error: Optional[str] = None
    last_error_time: Optional[int] = None

    # Latency tracking
    ping_times: List[float] = field(default_factory=list)
    avg_latency: float = 0
    min_latency: float = float("inf")
    max_latency: float = 0

    # Message type counts
    message_types: Dict[str, int] = field(default_factory=dict)

    def add_ping_time(self, latency: float) -> None:
        """Add ping time and update statistics."""
        self.ping_times.append(latency)
        if len(self.ping_times) > 100:
            self.ping_times.pop(0)

        self.avg_latency = sum(self.ping_times) / len(self.ping_times)
        self.min_latency = min(self.min_latency, latency)
        self.max_latency = max(self.max_latency, latency)

    def record_error(self, error: str) -> None:
        """Record error."""
        self.errors.append(error)
        if len(self.errors) > 100:
            self.errors.pop(0)
        self.last_error = error
        self.last_error_time = int(time.time() * 1000)

    def record_message(self, msg_type: str, size: int, direction: str = "received") -> None:
        """Record message statistics."""
        if direction == "received":
            self.messages_received += 1
            self.bytes_received += size
        else:
            self.messages_sent += 1
            self.bytes_sent += size

        if msg_type not in self.message_types:
            self.message_types[msg_type] = 0
        self.message_types[msg_type] += 1


class WebSocketCollector:
    """WebSocket data collector with advanced features."""

    def __init__(self, config: WebSocketConfig):
        """
        Initialize WebSocket collector.

        Args:
            config: WebSocket configuration
        """
        self.config = config
        self.stats = WebSocketStats()

        # Connection
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.is_connected = False
        self.is_running = False
        self.is_authenticated = False

        # Message handling
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_size)
        self.priority_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.handlers: Dict[str, List[Callable]] = {}
        self.middleware: List[Callable] = []

        # Subscriptions
        self.subscriptions: Set[str] = set()
        self.pending_subscriptions: Set[str] = set()

        # Tasks
        self.tasks: List[asyncio.Task] = []

        # Rate limiting
        self.message_timestamps: List[float] = []
        self.rate_limit = 100  # messages per second

        logger.info(f"WebSocketCollector initialized for {config.name}")

    async def connect(self) -> bool:
        """
        Connect to WebSocket.

        Returns:
            bool: Connection success status
        """
        try:
            if self.is_connected:
                logger.warning(f"Already connected to {self.config.name}")
                return True

            self.stats.connection_attempts += 1

            # Create session if needed
            if not self.session:
                timeout = aiohttp.ClientTimeout(
                    total=self.config.connection_timeout,
                    sock_connect=self.config.connection_timeout,
                )

                connector = aiohttp.TCPConnector(
                    ssl=self.config.ssl_verify, limit=100, ttl_dns_cache=300
                )

                self.session = aiohttp.ClientSession(
                    timeout=timeout, connector=connector, headers=self.config.headers
                )

            # Add authentication to URL if needed
            url = self.config.url
            if self.config.auth_type == "query" and self.config.api_key:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}api_key={self.config.api_key}"

            # Connect to WebSocket
            self.ws = await self.session.ws_connect(
                url,
                heartbeat=self.config.ping_interval,
                max_msg_size=self.config.max_message_size,
                compress=self.config.compress,
            )

            self.is_connected = True
            self.stats.connected_at = int(time.time() * 1000)

            # Authenticate if required
            if self.config.auth_required and self.config.auth_type == "message":
                await self._authenticate()
            else:
                self.is_authenticated = True

            logger.info(f"Connected to {self.config.name} WebSocket")
            return True

        except aiohttp.ClientError as e:
            logger.error(f"Client error connecting to {self.config.name}: {e}")
            self.stats.record_error(f"Client error: {e}")
            return False
        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to {self.config.name}")
            self.stats.record_error("Connection timeout")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to {self.config.name}: {e}")
            self.stats.record_error(str(e))
            return False

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        try:
            self.is_connected = False
            self.is_authenticated = False
            self.stats.disconnected_at = int(time.time() * 1000)

            if self.ws:
                await self.ws.close()
                self.ws = None

            if self.session:
                await self.session.close()
                self.session = None

            logger.info(f"Disconnected from {self.config.name}")

        except Exception as e:
            logger.error(f"Error disconnecting from {self.config.name}: {e}")

    async def start(self) -> None:
        """Start WebSocket collector."""
        if self.is_running:
            logger.warning(f"WebSocket collector already running for {self.config.name}")
            return

        self.is_running = True

        # Connect
        if not await self.connect():
            raise DataProviderError(f"Failed to connect to {self.config.name}")

        # Start tasks
        self.tasks = [
            asyncio.create_task(self._receive_messages()),
            asyncio.create_task(self._process_messages()),
            asyncio.create_task(self._process_priority_messages()),
            asyncio.create_task(self._ping_task()),
            asyncio.create_task(self._reconnect_task()),
            asyncio.create_task(self._subscription_manager()),
        ]

        logger.info(f"WebSocket collector started for {self.config.name}")

    async def stop(self) -> None:
        """Stop WebSocket collector."""
        if not self.is_running:
            return

        self.is_running = False

        # Cancel tasks
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

        # Clear queues
        while not self.message_queue.empty():
            self.message_queue.get_nowait()

        while not self.priority_queue.empty():
            self.priority_queue.get_nowait()

        # Disconnect
        await self.disconnect()

        logger.info(f"WebSocket collector stopped for {self.config.name}")

    async def send_message(
        self, message: Union[Dict[str, Any], str], priority: bool = False
    ) -> None:
        """
        Send message through WebSocket.

        Args:
            message: Message to send (dict or string)
            priority: Send with high priority
        """
        if not self.ws or not self.is_connected:
            raise DataProviderError("WebSocket not connected")

        # Rate limiting
        if not await self._check_rate_limit():
            logger.warning(f"Rate limit exceeded for {self.config.name}")
            self.stats.messages_dropped += 1
            return

        try:
            # Convert to JSON if dict
            if isinstance(message, dict):
                json_msg = json.dumps(message)
            else:
                json_msg = message

            # Send message
            await self.ws.send_str(json_msg)

            # Update statistics
            self.stats.record_message(
                message.get("op", "unknown") if isinstance(message, dict) else "text",
                len(json_msg),
                "sent",
            )

            logger.debug(f"Sent message to {self.config.name}: {json_msg[:100]}")

        except Exception as e:
            logger.error(f"Error sending message to {self.config.name}: {e}")
            self.stats.record_error(str(e))
            self.is_connected = False
            raise

    async def subscribe(
        self, channel: str, params: Optional[Dict] = None, callback: Optional[Callable] = None
    ) -> bool:
        """
        Subscribe to channel.

        Args:
            channel: Channel name
            params: Additional parameters
            callback: Channel-specific callback

        Returns:
            bool: Success status
        """
        try:
            # Add to pending if not connected
            if not self.is_connected or not self.is_authenticated:
                self.pending_subscriptions.add(channel)
                logger.info(f"Added {channel} to pending subscriptions")
                return True

            # Build subscription message
            sub_msg = self._build_subscription_message(channel, params, "subscribe")

            # Send subscription
            await self.send_message(sub_msg)

            # Track subscription
            self.subscriptions.add(channel)

            # Register callback if provided
            if callback:
                self.register_handler(f"channel:{channel}", callback)

            logger.info(f"Subscribed to {channel} on {self.config.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}")
            return False

    async def unsubscribe(self, channel: str) -> bool:
        """
        Unsubscribe from channel.

        Args:
            channel: Channel name

        Returns:
            bool: Success status
        """
        try:
            if channel not in self.subscriptions:
                return True

            # Build unsubscription message
            unsub_msg = self._build_subscription_message(channel, None, "unsubscribe")

            # Send unsubscription
            await self.send_message(unsub_msg)

            # Remove subscription
            self.subscriptions.discard(channel)
            self.pending_subscriptions.discard(channel)

            # Remove channel handlers
            channel_key = f"channel:{channel}"
            if channel_key in self.handlers:
                del self.handlers[channel_key]

            logger.info(f"Unsubscribed from {channel} on {self.config.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to unsubscribe from {channel}: {e}")
            return False

    def register_handler(
        self, message_type: str, handler: Callable, priority: bool = False
    ) -> None:
        """
        Register message handler.

        Args:
            message_type: Type of message to handle
            handler: Handler function
            priority: High priority handler
        """
        key = f"priority:{message_type}" if priority else message_type

        if key not in self.handlers:
            self.handlers[key] = []

        self.handlers[key].append(handler)
        logger.debug(f"Registered {'priority ' if priority else ''}handler for {message_type}")

    def unregister_handler(self, message_type: str, handler: Callable) -> None:
        """
        Unregister message handler.

        Args:
            message_type: Type of message
            handler: Handler function to remove
        """
        for key in [message_type, f"priority:{message_type}"]:
            if key in self.handlers:
                try:
                    self.handlers[key].remove(handler)
                    logger.debug(f"Unregistered handler for {message_type}")
                except ValueError:
                    pass

    def add_middleware(self, middleware: Callable) -> None:
        """
        Add message processing middleware.

        Args:
            middleware: Middleware function
        """
        self.middleware.append(middleware)
        logger.debug("Added message middleware")

    async def get_message(
        self, timeout: Optional[float] = None, priority: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Get message from queue.

        Args:
            timeout: Timeout in seconds
            priority: Get from priority queue

        Returns:
            Optional[Dict]: Message or None if timeout
        """
        try:
            queue = self.priority_queue if priority else self.message_queue

            if timeout:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            else:
                return await queue.get()

        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error getting message: {e}")
            return None

    # ==================== Private Methods ====================

    async def _authenticate(self) -> None:
        """Authenticate WebSocket connection."""
        if not self.config.api_key:
            self.is_authenticated = True
            return

        try:
            # Generate authentication signature
            timestamp = str(int(time.time() * 1000))
            sign_str = f"{timestamp}{self.config.api_key}"

            if self.config.api_secret:
                signature = hmac.new(
                    self.config.api_secret.encode(), sign_str.encode(), hashlib.sha256
                ).hexdigest()
            else:
                signature = ""

            # Build auth message
            auth_msg = {
                "op": "auth",
                "args": [
                    {"api_key": self.config.api_key, "timestamp": timestamp, "sign": signature}
                ],
            }

            # Send auth
            await self.send_message(auth_msg, priority=True)

            # Wait for auth response (with timeout)
            auth_timeout = 5.0
            start_time = time.time()

            while time.time() - start_time < auth_timeout:
                msg = await self.get_message(timeout=0.1, priority=True)

                if msg and msg.get("op") == "auth":
                    if msg.get("success"):
                        self.is_authenticated = True
                        logger.info(f"Authenticated with {self.config.name}")
                        return
                    else:
                        raise DataProviderError(f"Authentication failed: {msg.get('ret_msg')}")

            raise DataProviderError("Authentication timeout")

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            self.is_authenticated = False
            raise

    async def _receive_messages(self) -> None:
        """Receive messages from WebSocket."""
        while self.is_running:
            try:
                if not self.ws or not self.is_connected:
                    await asyncio.sleep(1)
                    continue

                # Receive message
                msg = await self.ws.receive()

                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Parse and queue message
                    try:
                        data = json.loads(msg.data)

                        # Apply middleware
                        for mw in self.middleware:
                            data = await mw(data) if asyncio.iscoroutinefunction(mw) else mw(data)
                            if data is None:
                                break

                        if data:
                            # Determine priority
                            is_priority = self._is_priority_message(data)
                            queue = self.priority_queue if is_priority else self.message_queue

                            # Add to queue if not full
                            if not queue.full():
                                await queue.put(data)
                            else:
                                self.stats.messages_dropped += 1
                                logger.warning(
                                    f"Queue full, dropping message from {self.config.name}"
                                )

                        # Update statistics
                        self.stats.record_message(
                            data.get("op", data.get("type", "data")), len(msg.data), "received"
                        )

                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse message: {e}")
                        self.stats.record_error(f"JSON decode error: {e}")
                    except Exception as e:
                        logger.error(f"Error processing received message: {e}")
                        self.stats.record_error(str(e))

                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Handle binary messages if needed
                    logger.debug(f"Received binary message: {len(msg.data)} bytes")

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error = f"WebSocket error: {self.ws.exception()}"
                    logger.error(error)
                    self.stats.record_error(error)
                    self.is_connected = False

                elif msg.type in [aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED]:
                    logger.warning(f"WebSocket closed: {msg.data}")
                    self.is_connected = False
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in receive loop: {e}")
                self.stats.record_error(str(e))
                self.is_connected = False
                await asyncio.sleep(1)

    async def _process_messages(self) -> None:
        """Process regular messages from queue."""
        while self.is_running:
            try:
                # Get batch of messages
                messages = []
                deadline = time.time() + 0.1  # 100ms batch window

                while time.time() < deadline and len(messages) < self.config.batch_size:
                    msg = await self.get_message(timeout=0.01)
                    if msg:
                        messages.append(msg)
                    else:
                        break

                # Process batch
                for message in messages:
                    await self._handle_message(message)
                    self.stats.messages_processed += 1

                if not messages:
                    await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing messages: {e}")
                await asyncio.sleep(1)

    async def _process_priority_messages(self) -> None:
        """Process priority messages immediately."""
        while self.is_running:
            try:
                message = await self.get_message(timeout=1.0, priority=True)

                if message:
                    await self._handle_message(message, priority=True)
                    self.stats.messages_processed += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing priority messages: {e}")
                await asyncio.sleep(0.1)

    async def _handle_message(self, message: Dict[str, Any], priority: bool = False) -> None:
        """
        Handle individual message.

        Args:
            message: Message to handle
            priority: Priority message
        """
        try:
            # Determine message type
            msg_type = self._get_message_type(message)

            # Check for channel-specific handlers
            channel = self._extract_channel(message)
            if channel:
                channel_key = f"channel:{channel}"
                if channel_key in self.handlers:
                    for handler in self.handlers[channel_key]:
                        await self._call_handler(handler, message)

            # Call type-specific handlers
            handler_key = f"priority:{msg_type}" if priority else msg_type

            if handler_key in self.handlers:
                for handler in self.handlers[handler_key]:
                    await self._call_handler(handler, message)

            # Call generic handlers
            generic_key = "priority:*" if priority else "*"
            if generic_key in self.handlers:
                for handler in self.handlers[generic_key]:
                    await self._call_handler(handler, message)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _call_handler(self, handler: Callable, message: Dict[str, Any]) -> None:
        """
        Call message handler safely.

        Args:
            handler: Handler function
            message: Message to pass
        """
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(message)
            else:
                handler(message)
        except Exception as e:
            logger.error(f"Handler error: {e}")

    async def _ping_task(self) -> None:
        """Send periodic pings and measure latency."""
        while self.is_running:
            try:
                await asyncio.sleep(self.config.ping_interval)

                if self.ws and self.is_connected:
                    start_time = time.time()

                    # Send ping
                    pong_waiter = await self.ws.ping()

                    try:
                        await asyncio.wait_for(pong_waiter, timeout=self.config.ping_timeout)
                        latency = (time.time() - start_time) * 1000  # ms

                        self.stats.add_ping_time(latency)
                        logger.debug(f"{self.config.name} ping latency: {latency:.2f}ms")

                    except asyncio.TimeoutError:
                        logger.warning(f"Ping timeout for {self.config.name}")
                        self.is_connected = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ping task error: {e}")
                await asyncio.sleep(5)

    async def _reconnect_task(self) -> None:
        """Handle automatic reconnection."""
        consecutive_failures = 0

        while self.is_running:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds

                if not self.is_connected:
                    consecutive_failures += 1

                    if consecutive_failures > self.config.max_reconnect_attempts:
                        logger.error(f"Max reconnection attempts reached for {self.config.name}")
                        self.is_running = False
                        break

                    # Calculate backoff delay
                    delay = min(
                        self.config.reconnect_delay * (2 ** (consecutive_failures - 1)),
                        300,  # Max 5 minutes
                    )

                    logger.info(
                        f"Reconnecting to {self.config.name} in {delay}s (attempt {consecutive_failures})"
                    )
                    await asyncio.sleep(delay)

                    # Try to reconnect
                    if await self.connect():
                        self.stats.reconnect_count += 1
                        consecutive_failures = 0

                        # Process pending subscriptions
                        await self._restore_subscriptions()

                        logger.info(f"Successfully reconnected to {self.config.name}")
                    else:
                        logger.error(f"Reconnection failed for {self.config.name}")
                else:
                    consecutive_failures = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reconnection task error: {e}")
                await asyncio.sleep(30)

    async def _subscription_manager(self) -> None:
        """Manage subscriptions and resubscriptions."""
        while self.is_running:
            try:
                await asyncio.sleep(1)

                # Process pending subscriptions
                if self.is_connected and self.is_authenticated and self.pending_subscriptions:
                    for channel in list(self.pending_subscriptions):
                        if await self.subscribe(channel):
                            self.pending_subscriptions.discard(channel)
                        await asyncio.sleep(0.1)  # Rate limit subscriptions

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Subscription manager error: {e}")
                await asyncio.sleep(5)

    async def _restore_subscriptions(self) -> None:
        """Restore subscriptions after reconnection."""
        if not self.subscriptions:
            return

        logger.info(f"Restoring {len(self.subscriptions)} subscriptions")

        for channel in list(self.subscriptions):
            try:
                # Build and send subscription message
                sub_msg = self._build_subscription_message(channel, None, "subscribe")
                await self.send_message(sub_msg)
                await asyncio.sleep(0.05)  # Small delay between subscriptions

            except Exception as e:
                logger.error(f"Failed to restore subscription {channel}: {e}")
                self.pending_subscriptions.add(channel)

    async def _check_rate_limit(self) -> bool:
        """
        Check if message can be sent within rate limit.

        Returns:
            bool: True if within rate limit
        """
        current_time = time.time()

        # Remove timestamps older than 1 second
        self.message_timestamps = [ts for ts in self.message_timestamps if current_time - ts < 1.0]

        # Check rate limit
        if len(self.message_timestamps) >= self.rate_limit:
            return False

        self.message_timestamps.append(current_time)
        return True

    def _build_subscription_message(
        self, channel: str, params: Optional[Dict], operation: str
    ) -> Dict[str, Any]:
        """
        Build subscription message.

        Args:
            channel: Channel name
            params: Additional parameters
            operation: subscribe or unsubscribe

        Returns:
            Dict: Subscription message
        """
        # Default format (can be overridden in subclasses)
        msg = {"op": operation, "args": [channel]}

        if params:
            if isinstance(msg["args"][0], str):
                # Add params as additional arguments
                msg["args"].extend(params.values())
            else:
                # Merge params into args
                msg["args"] = [{**{"channel": channel}, **params}]

        return msg

    def _get_message_type(self, message: Dict[str, Any]) -> str:
        """
        Determine message type.

        Args:
            message: Message dict

        Returns:
            str: Message type
        """
        # Common patterns
        if "op" in message:
            return message["op"]
        elif "type" in message:
            return message["type"]
        elif "event" in message:
            return message["event"]
        elif "action" in message:
            return message["action"]
        elif "topic" in message:
            return "data"
        else:
            return "unknown"

    def _extract_channel(self, message: Dict[str, Any]) -> Optional[str]:
        """
        Extract channel from message.

        Args:
            message: Message dict

        Returns:
            Optional[str]: Channel name
        """
        # Try common channel fields
        for field in ["channel", "topic", "stream", "subject"]:
            if field in message:
                return message[field]

        # Try to extract from args
        if "args" in message and isinstance(message["args"], list):
            if message["args"] and isinstance(message["args"][0], str):
                return message["args"][0]

        return None

    def _is_priority_message(self, message: Dict[str, Any]) -> bool:
        """
        Check if message is high priority.

        Args:
            message: Message dict

        Returns:
            bool: True if priority message
        """
        msg_type = self._get_message_type(message)

        # Priority message types
        priority_types = {
            "error",
            "auth",
            "subscribe",
            "unsubscribe",
            "ping",
            "pong",
            "disconnect",
            "reconnect",
        }

        return msg_type in priority_types

    # ==================== Public Utility Methods ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive connection statistics."""
        uptime = 0
        if self.stats.connected_at:
            if self.stats.disconnected_at and self.stats.disconnected_at > self.stats.connected_at:
                uptime = self.stats.disconnected_at - self.stats.connected_at
            elif self.is_connected:
                uptime = int(time.time() * 1000) - self.stats.connected_at

        return {
            "name": self.config.name,
            "url": self.config.url,
            "connected": self.is_connected,
            "authenticated": self.is_authenticated,
            "uptime_ms": uptime,
            "connected_at": self.stats.connected_at,
            "disconnected_at": self.stats.disconnected_at,
            "connection_attempts": self.stats.connection_attempts,
            "reconnect_count": self.stats.reconnect_count,
            "messages": {
                "sent": self.stats.messages_sent,
                "received": self.stats.messages_received,
                "processed": self.stats.messages_processed,
                "dropped": self.stats.messages_dropped,
                "types": self.stats.message_types,
            },
            "bytes": {"sent": self.stats.bytes_sent, "received": self.stats.bytes_received},
            "latency": {
                "avg": round(self.stats.avg_latency, 2),
                "min": round(self.stats.min_latency, 2)
                if self.stats.min_latency != float("inf")
                else 0,
                "max": round(self.stats.max_latency, 2),
            },
            "queues": {
                "message_queue": self.message_queue.qsize(),
                "priority_queue": self.priority_queue.qsize(),
            },
            "subscriptions": {
                "active": len(self.subscriptions),
                "pending": len(self.pending_subscriptions),
                "list": list(self.subscriptions),
            },
            "errors": {
                "count": len(self.stats.errors),
                "last": self.stats.last_error,
                "last_time": self.stats.last_error_time,
            },
        }

    def is_healthy(self) -> bool:
        """
        Check if connection is healthy.

        Returns:
            bool: True if healthy
        """
        if not self.is_connected:
            return False

        # Check if authenticated when required
        if self.config.auth_required and not self.is_authenticated:
            return False

        # Check latency
        if self.stats.avg_latency > 1000:  # > 1 second
            return False

        # Check error rate
        if self.stats.messages_received > 100:
            error_rate = len(self.stats.errors) / self.stats.messages_received
            if error_rate > 0.1:  # > 10% error rate
                return False

        # Check message drop rate
        total_messages = self.stats.messages_processed + self.stats.messages_dropped
        if total_messages > 100:
            drop_rate = self.stats.messages_dropped / total_messages
            if drop_rate > 0.05:  # > 5% drop rate
                return False

        return True
