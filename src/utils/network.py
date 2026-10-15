"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : network.py
    Project     : Project-103
    Description : Robust HTTP networking helpers — aiohttp sessions with DNS caching, connection pooling, timeouts, and retry on transient failures.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector

logger = logging.getLogger(__name__)


def create_robust_session(
    total_timeout: float = 30.0,
    connect_timeout: float = 10.0,
    sock_read_timeout: float = 15.0,
    connection_limit: int = 100,
    dns_cache_ttl: int = 300,
) -> ClientSession:
    """
    Create a properly configured aiohttp ClientSession.

    Args:
        total_timeout: Total timeout for the entire request (seconds)
        connect_timeout: Timeout for establishing connection (seconds)
        sock_read_timeout: Timeout for reading from socket (seconds)
        connection_limit: Maximum number of concurrent connections
        dns_cache_ttl: DNS cache time-to-live (seconds)

    Returns:
        Configured aiohttp.ClientSession
    """
    timeout = ClientTimeout(
        total=total_timeout, connect=connect_timeout, sock_read=sock_read_timeout
    )

    connector = TCPConnector(
        limit=connection_limit,
        ttl_dns_cache=dns_cache_ttl,
        use_dns_cache=True,
        force_close=False,
        enable_cleanup_closed=True,
    )

    return ClientSession(
        timeout=timeout,
        connector=connector,
    )


async def retry_on_network_error(
    func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    **kwargs,
) -> Any:
    """
    Retry a function on network errors with exponential backoff.

    Args:
        func: Async function to call
        *args: Arguments for the function
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        exponential_base: Base for exponential backoff
        **kwargs: Keyword arguments for the function

    Returns:
        Result of the function call

    Raises:
        Last exception if all retries fail
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (
            aiohttp.ClientConnectorError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientOSError,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            last_exception = e

            if attempt < max_retries:
                delay = min(base_delay * (exponential_base**attempt), max_delay)
                logger.warning(
                    f"Network error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Network error after {max_retries + 1} attempts: {e}")

    raise last_exception


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
):
    """
    Decorator to add retry logic to async functions.

    Usage:
        @with_retry(max_retries=3)
        async def fetch_data():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await retry_on_network_error(
                func,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                **kwargs,
            )

        return wrapper

    return decorator


class NetworkHealthMonitor:
    """
    Monitors network health and pauses operations during outages.
    """

    def __init__(
        self,
        check_url: str = "https://api.bybit.com/v5/market/time",
        check_interval: float = 30.0,
        unhealthy_threshold: int = 3,
        healthy_threshold: int = 2,
    ):
        self.check_url = check_url
        self.check_interval = check_interval
        self.unhealthy_threshold = unhealthy_threshold
        self.healthy_threshold = healthy_threshold

        self._is_healthy = True
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._session: Optional[ClientSession] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_healthy(self) -> bool:
        """Returns True if network is considered healthy."""
        return self._is_healthy

    async def start(self):
        """Start monitoring network health."""
        if self._running:
            return

        self._running = True
        self._session = create_robust_session(total_timeout=10.0)
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Network health monitor started")

    async def stop(self):
        """Stop monitoring network health."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        logger.info("Network health monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Health check error: {e}")

            await asyncio.sleep(self.check_interval)

    async def _check_health(self):
        """Perform a single health check."""
        try:
            async with self._session.get(self.check_url) as response:
                if response.status == 200:
                    self._consecutive_failures = 0
                    self._consecutive_successes += 1

                    if (
                        not self._is_healthy
                        and self._consecutive_successes >= self.healthy_threshold
                    ):
                        self._is_healthy = True
                        logger.info("Network health restored")
                else:
                    self._handle_failure()
        except Exception as e:
            logger.debug(f"Network health check failed: {e}")
            self._handle_failure()

    def _handle_failure(self):
        """Handle a failed health check."""
        self._consecutive_successes = 0
        self._consecutive_failures += 1

        if self._is_healthy and self._consecutive_failures >= self.unhealthy_threshold:
            self._is_healthy = False
            logger.warning(
                f"Network unhealthy after {self._consecutive_failures} failed checks. "
                "Pausing network-dependent operations."
            )


# Global network health monitor instance
_network_monitor: Optional[NetworkHealthMonitor] = None


def get_network_monitor() -> NetworkHealthMonitor:
    """Get or create the global network health monitor."""
    global _network_monitor
    if _network_monitor is None:
        _network_monitor = NetworkHealthMonitor()
    return _network_monitor
