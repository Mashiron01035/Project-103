"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : aggregator
    Project     : Project-103
    Description : Data aggregator for multiple sources. This module aggregates data from multiple providers.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.data.providers.base_provider import BaseDataProvider, DataSubscription
from src.models import Candle, OrderBook, Ticker

logger = logging.getLogger(__name__)


@dataclass
class AggregatorConfig:
    """Aggregator configuration."""

    # Aggregation settings
    consensus_threshold: float = 0.7  # 70% agreement required
    outlier_threshold: float = 3.0  # Standard deviations

    # Timing
    aggregation_window: float = 1.0  # seconds
    max_latency: int = 5000  # milliseconds

    # Weighting
    use_weighted_average: bool = True
    provider_weights: Dict[str, float] = field(default_factory=dict)

    # Failover
    min_providers: int = 1
    failover_enabled: bool = True

    # Caching
    cache_enabled: bool = True
    cache_ttl: int = 60  # seconds


@dataclass
class ProviderMetrics:
    """Metrics for a data provider."""

    provider_name: str

    # Reliability
    uptime: float = 100.0  # percentage
    last_update: Optional[int] = None
    consecutive_errors: int = 0
    total_errors: int = 0

    # Latency
    avg_latency: float = 0
    latencies: List[float] = field(default_factory=list)

    # Data quality
    outlier_count: int = 0
    data_points: int = 0

    def add_latency(self, latency: float) -> None:
        """Add latency measurement."""
        self.latencies.append(latency)
        if len(self.latencies) > 100:
            self.latencies.pop(0)
        self.avg_latency = statistics.mean(self.latencies)

    def record_success(self) -> None:
        """Record successful data point."""
        self.consecutive_errors = 0
        self.data_points += 1
        self.last_update = int(time.time() * 1000)
        self.update_uptime()

    def record_error(self) -> None:
        """Record error."""
        self.consecutive_errors += 1
        self.total_errors += 1
        self.update_uptime()

    def record_outlier(self) -> None:
        """Record outlier detection."""
        self.outlier_count += 1

    def update_uptime(self) -> None:
        """Update uptime percentage."""
        total = self.data_points + self.total_errors
        if total > 0:
            self.uptime = (self.data_points / total) * 100

    def get_weight(self, base_weight: float = 1.0) -> float:
        """Calculate provider weight based on metrics."""
        # Start with base weight
        weight = base_weight

        # Adjust for uptime
        weight *= self.uptime / 100

        # Penalize consecutive errors
        if self.consecutive_errors > 0:
            weight *= 0.5**self.consecutive_errors

        # Penalize high outlier rate
        if self.data_points > 0:
            outlier_rate = self.outlier_count / self.data_points
            weight *= max(0.1, 1 - outlier_rate)

        return max(0.01, weight)  # Minimum weight


class DataAggregator:
    """Aggregates data from multiple providers."""

    def __init__(self, config: AggregatorConfig):
        """
        Initialize data aggregator.

        Args:
            config: Aggregator configuration
        """
        self.config = config

        # Providers
        self.providers: Dict[str, BaseDataProvider] = {}
        self.provider_metrics: Dict[str, ProviderMetrics] = {}

        # Data buffers for aggregation
        self.candle_buffer: Dict[str, List[Tuple[str, Candle, float]]] = defaultdict(list)
        self.ticker_buffer: Dict[str, List[Tuple[str, Ticker, float]]] = defaultdict(list)
        self.orderbook_buffer: Dict[str, List[Tuple[str, OrderBook, float]]] = defaultdict(list)

        # Callbacks
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)

        # Cache
        self.cache: Dict[str, Tuple[Any, int]] = {}

        # Tasks
        self.aggregation_task: Optional[asyncio.Task] = None
        self.is_running = False

        logger.info("DataAggregator initialized")

    def add_provider(self, name: str, provider: BaseDataProvider) -> None:
        """
        Add data provider.

        Args:
            name: Provider name
            provider: Data provider instance
        """
        self.providers[name] = provider
        self.provider_metrics[name] = ProviderMetrics(provider_name=name)

        # Set default weight if not configured
        if name not in self.config.provider_weights:
            self.config.provider_weights[name] = 1.0

        logger.info(f"Added provider: {name}")

    def remove_provider(self, name: str) -> None:
        """
        Remove data provider.

        Args:
            name: Provider name to remove
        """
        if name in self.providers:
            del self.providers[name]
            del self.provider_metrics[name]
            logger.info(f"Removed provider: {name}")

    async def start(self) -> None:
        """Start aggregator."""
        if self.is_running:
            logger.warning("Aggregator already running")
            return

        self.is_running = True

        # Start all providers
        for name, provider in self.providers.items():
            try:
                await provider.start()
                logger.info(f"Started provider: {name}")
            except Exception as e:
                logger.error(f"Failed to start provider {name}: {e}")
                self.provider_metrics[name].record_error()

        # Start aggregation task
        self.aggregation_task = asyncio.create_task(self._aggregation_loop())

        logger.info("DataAggregator started")

    async def stop(self) -> None:
        """Stop aggregator."""
        if not self.is_running:
            return

        self.is_running = False

        # Cancel aggregation task
        if self.aggregation_task:
            self.aggregation_task.cancel()
            await asyncio.gather(self.aggregation_task, return_exceptions=True)

        # Stop all providers
        for name, provider in self.providers.items():
            try:
                await provider.stop()
                logger.info(f"Stopped provider: {name}")
            except Exception as e:
                logger.error(f"Error stopping provider {name}: {e}")

        logger.info("DataAggregator stopped")

    async def subscribe(
        self, symbol: str, data_type: str, callback: Optional[Callable] = None, **params
    ) -> bool:
        """
        Subscribe to aggregated data.

        Args:
            symbol: Trading symbol
            data_type: Data type (candle, ticker, orderbook)
            callback: Callback function
            **params: Additional parameters

        Returns:
            bool: Success status
        """
        # Register callback
        if callback:
            key = f"{symbol}_{data_type}"
            self.callbacks[key].append(callback)

        # Subscribe on all providers
        subscription = DataSubscription(
            symbol=symbol,
            data_type=data_type,
            params=params,
            callback=lambda data: asyncio.create_task(
                self._handle_provider_data(symbol, data_type, data)
            ),
        )

        results = {}
        for name, provider in self.providers.items():
            try:
                success = await provider.subscribe(subscription)
                results[name] = success
                if success:
                    logger.info(f"Subscribed to {symbol} {data_type} on {name}")
            except Exception as e:
                logger.error(f"Failed to subscribe on {name}: {e}")
                results[name] = False

        # Check if minimum providers succeeded
        success_count = sum(1 for s in results.values() if s)
        return success_count >= self.config.min_providers

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
        """
        Get aggregated candles.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            limit: Number of candles

        Returns:
            List[Candle]: Aggregated candles
        """
        # Check cache
        cache_key = f"candles_{symbol}_{timeframe}_{limit}"
        if self.config.cache_enabled:
            cached = self._get_cached(cache_key)
            if cached:
                return cached

        # Collect from all providers
        all_candles = {}

        for name, provider in self.providers.items():
            try:
                start_time = time.time()
                candles = await provider.get_candles(symbol, timeframe, limit)
                latency = (time.time() - start_time) * 1000

                if candles:
                    all_candles[name] = candles
                    self.provider_metrics[name].add_latency(latency)
                    self.provider_metrics[name].record_success()
                else:
                    self.provider_metrics[name].record_error()

            except Exception as e:
                logger.error(f"Error getting candles from {name}: {e}")
                self.provider_metrics[name].record_error()

        # Aggregate candles
        if not all_candles:
            return []

        aggregated = self._aggregate_candles(all_candles)

        # Cache result
        if self.config.cache_enabled:
            self._cache_data(cache_key, aggregated)

        return aggregated

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get aggregated ticker.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Aggregated ticker
        """
        # Check cache
        cache_key = f"ticker_{symbol}"
        if self.config.cache_enabled:
            cached = self._get_cached(cache_key)
            if cached:
                return cached

        # Collect from all providers
        all_tickers = {}

        for name, provider in self.providers.items():
            try:
                start_time = time.time()
                ticker = await provider.get_ticker(symbol)
                latency = (time.time() - start_time) * 1000

                if ticker:
                    all_tickers[name] = ticker
                    self.provider_metrics[name].add_latency(latency)
                    self.provider_metrics[name].record_success()
                else:
                    self.provider_metrics[name].record_error()

            except Exception as e:
                logger.error(f"Error getting ticker from {name}: {e}")
                self.provider_metrics[name].record_error()

        # Aggregate tickers
        if not all_tickers:
            return None

        aggregated = self._aggregate_tickers(all_tickers)

        # Cache result
        if self.config.cache_enabled:
            self._cache_data(cache_key, aggregated, ttl=5)  # Short TTL for tickers

        return aggregated

    # ==================== Private Methods ====================

    async def _handle_provider_data(self, symbol: str, data_type: str, data: Any) -> None:
        """Handle data from provider."""
        timestamp = time.time()
        provider_name = data.source if hasattr(data, "source") else "unknown"

        # Add to buffer
        if data_type == "candle":
            self.candle_buffer[symbol].append((provider_name, data, timestamp))
        elif data_type == "ticker":
            self.ticker_buffer[symbol].append((provider_name, data, timestamp))
        elif data_type == "orderbook":
            self.orderbook_buffer[symbol].append((provider_name, data, timestamp))

    async def _aggregation_loop(self) -> None:
        """Main aggregation loop."""
        while self.is_running:
            try:
                # Process buffers
                await self._process_buffers()

                # Clean old cache entries
                self._clean_cache()

                await asyncio.sleep(self.config.aggregation_window)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Aggregation loop error: {e}")
                await asyncio.sleep(1)

    async def _process_buffers(self) -> None:
        """Process data buffers."""
        current_time = time.time()

        # Process candle buffer
        for symbol, buffer in list(self.candle_buffer.items()):
            if buffer:
                # Filter recent data
                recent = [
                    (p, d, t)
                    for p, d, t in buffer
                    if (current_time - t) * 1000 < self.config.max_latency
                ]

                if recent:
                    # Aggregate and notify
                    aggregated = self._aggregate_candle_buffer(recent)
                    if aggregated:
                        await self._notify_callbacks(f"{symbol}_candle", aggregated)

                # Clear processed data
                self.candle_buffer[symbol] = [
                    (p, d, t)
                    for p, d, t in buffer
                    if (current_time - t) * 1000 < self.config.max_latency * 2
                ]

        # Process ticker buffer
        for symbol, buffer in list(self.ticker_buffer.items()):
            if buffer:
                recent = [
                    (p, d, t)
                    for p, d, t in buffer
                    if (current_time - t) * 1000 < self.config.max_latency
                ]

                if recent:
                    aggregated = self._aggregate_ticker_buffer(recent)
                    if aggregated:
                        await self._notify_callbacks(f"{symbol}_ticker", aggregated)

                self.ticker_buffer[symbol] = [
                    (p, d, t)
                    for p, d, t in buffer
                    if (current_time - t) * 1000 < self.config.max_latency * 2
                ]

    def _aggregate_candles(self, provider_candles: Dict[str, List[Candle]]) -> List[Candle]:
        """Aggregate candles from multiple providers."""
        if len(provider_candles) == 1:
            return list(provider_candles.values())[0]

        # Group by timestamp
        candles_by_time = defaultdict(list)

        for provider, candles in provider_candles.items():
            weight = self.provider_metrics[provider].get_weight(
                self.config.provider_weights.get(provider, 1.0)
            )

            for candle in candles:
                candles_by_time[candle.timestamp].append((candle, provider, weight))

        # Aggregate each timestamp
        aggregated = []

        for timestamp, candle_group in sorted(candles_by_time.items()):
            if len(candle_group) >= self.config.min_providers:
                agg_candle = self._aggregate_single_candle(candle_group)
                if agg_candle:
                    aggregated.append(agg_candle)

        return aggregated

    def _aggregate_single_candle(
        self, candle_group: List[Tuple[Candle, str, float]]
    ) -> Optional[Candle]:
        """Aggregate a single candle from multiple sources."""
        if not candle_group:
            return None

        # Extract values and weights
        opens = [(c.open, w) for c, _, w in candle_group]
        highs = [(c.high, w) for c, _, w in candle_group]
        lows = [(c.low, w) for c, _, w in candle_group]
        closes = [(c.close, w) for c, _, w in candle_group]
        volumes = [(c.volume, w) for c, _, w in candle_group]

        # Remove outliers
        opens = self._remove_outliers(opens)
        highs = self._remove_outliers(highs)
        lows = self._remove_outliers(lows)
        closes = self._remove_outliers(closes)

        if not opens or not closes:
            return None

        # Calculate aggregated values
        first_candle = candle_group[0][0]

        return Candle(
            symbol=first_candle.symbol,
            timeframe=first_candle.timeframe,
            timestamp=first_candle.timestamp,
            open=self._weighted_average(opens)
            if self.config.use_weighted_average
            else statistics.mean([v for v, _ in opens]),
            high=max([v for v, _ in highs]),
            low=min([v for v, _ in lows]),
            close=self._weighted_average(closes)
            if self.config.use_weighted_average
            else statistics.mean([v for v, _ in closes]),
            volume=sum([v for v, _ in volumes]),
            is_closed=first_candle.is_closed,
            source="aggregated",
            exchange="multiple",
        )

    def _aggregate_tickers(self, provider_tickers: Dict[str, Ticker]) -> Optional[Ticker]:
        """Aggregate tickers from multiple providers."""
        if not provider_tickers:
            return None

        if len(provider_tickers) == 1:
            return list(provider_tickers.values())[0]

        # Extract values with weights
        prices = []
        bids = []
        asks = []

        for provider, ticker in provider_tickers.items():
            weight = self.provider_metrics[provider].get_weight(
                self.config.provider_weights.get(provider, 1.0)
            )

            prices.append((ticker.last_price, weight))
            if ticker.bid_price:
                bids.append((ticker.bid_price, weight))
            if ticker.ask_price:
                asks.append((ticker.ask_price, weight))

        # Remove outliers
        prices = self._remove_outliers(prices)
        bids = self._remove_outliers(bids) if bids else []
        asks = self._remove_outliers(asks) if asks else []

        if not prices:
            return None

        # Use first ticker as template
        first_ticker = list(provider_tickers.values())[0]

        return Ticker(
            symbol=first_ticker.symbol,
            last_price=self._weighted_average(prices)
            if self.config.use_weighted_average
            else statistics.mean([v for v, _ in prices]),
            timestamp=int(time.time() * 1000),
            bid_price=self._weighted_average(bids) if bids else None,
            ask_price=self._weighted_average(asks) if asks else None,
            source="aggregated",
            exchange="multiple",
        )

    def _aggregate_candle_buffer(self, buffer: List[Tuple[str, Candle, float]]) -> Optional[Candle]:
        """Aggregate candles from buffer."""
        if not buffer:
            return None

        # Group by provider with weights
        candle_group = []

        for provider, candle, _ in buffer:
            weight = self.provider_metrics[provider].get_weight(
                self.config.provider_weights.get(provider, 1.0)
            )
            candle_group.append((candle, provider, weight))

        return self._aggregate_single_candle(candle_group)

    def _aggregate_ticker_buffer(self, buffer: List[Tuple[str, Ticker, float]]) -> Optional[Ticker]:
        """Aggregate tickers from buffer."""
        if not buffer:
            return None

        tickers = {provider: ticker for provider, ticker, _ in buffer}
        return self._aggregate_tickers(tickers)

    def _remove_outliers(self, values: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Remove outliers from values."""
        if len(values) <= 2:
            return values

        # Calculate mean and std
        raw_values = [v for v, _ in values]
        mean = statistics.mean(raw_values)
        std = statistics.stdev(raw_values)

        # Filter outliers
        filtered = []

        for value, weight in values:
            z_score = abs((value - mean) / std) if std > 0 else 0

            if z_score <= self.config.outlier_threshold:
                filtered.append((value, weight))
            else:
                # Record outlier for provider metrics
                for provider, metrics in self.provider_metrics.items():
                    if weight == metrics.get_weight(
                        self.config.provider_weights.get(provider, 1.0)
                    ):
                        metrics.record_outlier()
                        break

        return filtered if filtered else values

    def _weighted_average(self, values: List[Tuple[float, float]]) -> float:
        """Calculate weighted average."""
        if not values:
            return 0

        total_weight = sum(w for _, w in values)

        if total_weight == 0:
            return statistics.mean([v for v, _ in values])

        return sum(v * w for v, w in values) / total_weight

    async def _notify_callbacks(self, key: str, data: Any) -> None:
        """Notify callbacks with aggregated data."""
        if key in self.callbacks:
            for callback in self.callbacks[key]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Callback error for {key}: {e}")

    def _cache_data(self, key: str, data: Any, ttl: Optional[int] = None) -> None:
        """Cache data."""
        ttl = ttl or self.config.cache_ttl
        expiry = int(time.time()) + ttl
        self.cache[key] = (data, expiry)

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached data."""
        if key in self.cache:
            data, expiry = self.cache[key]
            if time.time() < expiry:
                return data
            else:
                del self.cache[key]
        return None

    def _clean_cache(self) -> None:
        """Clean expired cache entries."""
        current_time = time.time()
        expired = [key for key, (_, expiry) in self.cache.items() if current_time >= expiry]

        for key in expired:
            del self.cache[key]

    def get_provider_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get provider statistics."""
        stats = {}

        for name, metrics in self.provider_metrics.items():
            stats[name] = {
                "uptime": metrics.uptime,
                "last_update": metrics.last_update,
                "consecutive_errors": metrics.consecutive_errors,
                "total_errors": metrics.total_errors,
                "avg_latency": metrics.avg_latency,
                "outlier_count": metrics.outlier_count,
                "data_points": metrics.data_points,
                "weight": metrics.get_weight(self.config.provider_weights.get(name, 1.0)),
            }

        return stats
