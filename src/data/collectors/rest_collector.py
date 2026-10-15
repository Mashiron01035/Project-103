"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : rest_collector
    Project     : Project-103
    Description : REST API data collector. This module handles REST API requests for data collection.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from src.core.exceptions import DataProviderError

logger = logging.getLogger(__name__)


@dataclass
class RestConfig:
    """REST API configuration."""

    base_url: str
    name: str = "rest_api"

    # Request settings
    timeout: int = 30  # seconds
    retry_count: int = 3
    retry_delay: int = 1  # seconds

    # Rate limiting
    rate_limit: int = 50  # requests per second
    rate_limit_window: int = 1  # seconds

    # Authentication
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    auth_type: str = "header"  # header, query, body

    # Headers
    headers: Dict[str, str] = field(default_factory=dict)

    # SSL
    ssl_verify: bool = True


@dataclass
class RestStats:
    """REST API statistics."""

    requests_sent: int = 0
    requests_success: int = 0
    requests_failed: int = 0

    bytes_sent: int = 0
    bytes_received: int = 0

    total_latency: float = 0
    avg_latency: float = 0

    errors: List[str] = field(default_factory=list)
    last_error: Optional[str] = None
    last_error_time: Optional[int] = None

    # Rate limiting
    rate_limit_hits: int = 0

    def record_request(
        self, success: bool, latency: float, bytes_sent: int = 0, bytes_received: int = 0
    ) -> None:
        """Record request statistics."""
        self.requests_sent += 1

        if success:
            self.requests_success += 1
        else:
            self.requests_failed += 1

        self.bytes_sent += bytes_sent
        self.bytes_received += bytes_received

        self.total_latency += latency
        self.avg_latency = self.total_latency / self.requests_sent

    def record_error(self, error: str) -> None:
        """Record error."""
        self.errors.append(error)
        if len(self.errors) > 100:
            self.errors.pop(0)
        self.last_error = error
        self.last_error_time = int(time.time() * 1000)


class RestCollector:
    """REST API data collector."""

    def __init__(self, config: RestConfig):
        """
        Initialize REST collector.

        Args:
            config: REST configuration
        """
        self.config = config
        self.stats = RestStats()

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None

        # Rate limiting
        self.request_times: List[float] = []
        self.rate_limit_lock = asyncio.Lock()

        # Default headers
        self.default_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **config.headers,
        }

        # Add auth headers if configured
        if config.api_key and config.auth_type == "header":
            self.default_headers["X-API-KEY"] = config.api_key
            if config.api_secret:
                self.default_headers["X-API-SECRET"] = config.api_secret

        logger.info(f"RestCollector initialized for {config.name}")

    async def start(self) -> None:
        """Start REST collector."""
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout, headers=self.default_headers)
        logger.info(f"REST collector started for {self.config.name}")

    async def stop(self) -> None:
        """Stop REST collector."""
        if self.session:
            await self.session.close()
            self.session = None
        logger.info(f"REST collector stopped for {self.config.name}")

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Any], int]:
        """
        Make GET request.

        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Additional headers

        Returns:
            Tuple[response_data, status_code]
        """
        return await self._request("GET", endpoint, params=params, headers=headers)

    async def post(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Any], int]:
        """
        Make POST request.

        Args:
            endpoint: API endpoint
            data: Request body
            params: Query parameters
            headers: Additional headers

        Returns:
            Tuple[response_data, status_code]
        """
        return await self._request("POST", endpoint, data=data, params=params, headers=headers)

    async def put(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Any], int]:
        """
        Make PUT request.

        Args:
            endpoint: API endpoint
            data: Request body
            params: Query parameters
            headers: Additional headers

        Returns:
            Tuple[response_data, status_code]
        """
        return await self._request("PUT", endpoint, data=data, params=params, headers=headers)

    async def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Any], int]:
        """
        Make DELETE request.

        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Additional headers

        Returns:
            Tuple[response_data, status_code]
        """
        return await self._request("DELETE", endpoint, params=params, headers=headers)

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Any], int]:
        """
        Make HTTP request with retries.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request body
            params: Query parameters
            headers: Additional headers

        Returns:
            Tuple[response_data, status_code]
        """
        if not self.session:
            await self.start()

        # Build URL
        url = f"{self.config.base_url}{endpoint}"

        # Add auth to params if configured
        if self.config.api_key and self.config.auth_type == "query":
            if params is None:
                params = {}
            params["api_key"] = self.config.api_key
            if self.config.api_secret:
                params["api_secret"] = self.config.api_secret

        # Merge headers
        request_headers = {**self.default_headers}
        if headers:
            request_headers.update(headers)

        # Rate limiting
        await self._check_rate_limit()

        # Retry logic
        last_error = None

        for attempt in range(self.config.retry_count):
            try:
                start_time = time.time()

                # Make request
                async with self.session.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=request_headers,
                    ssl=self.config.ssl_verify,
                ) as response:
                    latency = time.time() - start_time

                    # Get response data
                    if response.content_type == "application/json":
                        response_data = await response.json()
                    else:
                        response_data = await response.text()

                    # Record statistics
                    self.stats.record_request(
                        success=response.status < 400,
                        latency=latency,
                        bytes_sent=len(str(data)) if data else 0,
                        bytes_received=len(str(response_data)),
                    )

                    # Check status
                    if response.status >= 400:
                        error = f"HTTP {response.status}: {response_data}"
                        logger.error(f"Request failed: {error}")
                        self.stats.record_error(error)

                        # Don't retry on client errors
                        if response.status < 500:
                            return response_data, response.status

                        last_error = error
                    else:
                        logger.debug(f"Request successful: {method} {endpoint}")
                        return response_data, response.status

            except asyncio.TimeoutError as e:
                last_error = f"Request timeout: {e}"
                logger.error(last_error)
                self.stats.record_error(last_error)

            except Exception as e:
                last_error = f"Request error: {e}"
                logger.error(last_error)
                self.stats.record_error(last_error)

            # Wait before retry
            if attempt < self.config.retry_count - 1:
                await asyncio.sleep(self.config.retry_delay * (attempt + 1))

        # All retries failed
        raise DataProviderError(
            f"Request failed after {self.config.retry_count} attempts: {last_error}"
        )

    async def _check_rate_limit(self) -> None:
        """Check and enforce rate limiting."""
        async with self.rate_limit_lock:
            current_time = time.time()

            # Remove old request times
            self.request_times = [
                t for t in self.request_times if current_time - t < self.config.rate_limit_window
            ]

            # Check if over limit
            if len(self.request_times) >= self.config.rate_limit:
                # Calculate wait time
                oldest_request = min(self.request_times)
                wait_time = self.config.rate_limit_window - (current_time - oldest_request)

                if wait_time > 0:
                    logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                    self.stats.rate_limit_hits += 1
                    await asyncio.sleep(wait_time)

                    # Clean up after waiting
                    current_time = time.time()
                    self.request_times = [
                        t
                        for t in self.request_times
                        if current_time - t < self.config.rate_limit_window
                    ]

            # Record this request
            self.request_times.append(current_time)

    def get_stats(self) -> Dict[str, Any]:
        """Get collector statistics."""
        return {
            "name": self.config.name,
            "requests_sent": self.stats.requests_sent,
            "requests_success": self.stats.requests_success,
            "requests_failed": self.stats.requests_failed,
            "success_rate": self.stats.requests_success / self.stats.requests_sent
            if self.stats.requests_sent > 0
            else 0,
            "bytes_sent": self.stats.bytes_sent,
            "bytes_received": self.stats.bytes_received,
            "avg_latency": self.stats.avg_latency,
            "rate_limit_hits": self.stats.rate_limit_hits,
            "last_error": self.stats.last_error,
            "error_count": len(self.stats.errors),
        }
