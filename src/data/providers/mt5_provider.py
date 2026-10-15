"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : mt5_provider
    Project     : Project-103
    Description : MetaTrader 5 data provider implementation. This module implements the MT5 data provider.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from src.core.exceptions import DataProviderError
from src.data.providers.base_provider import BaseDataProvider, DataProviderConfig, DataSubscription
from src.models import Candle, OrderBook, OrderBookLevel, Ticker
from src.utils.mt5_bridge import MT5_AVAILABLE, mt5

logger = logging.getLogger(__name__)

# MT5 broker server timezone: EET (UTC+2), EEST (UTC+3) in summer.
# rate['time'] returns seconds in server time, not real UTC.
# Auto-detected at runtime; this is only a fallback.
# Server timestamps are converted to UTC BY THE TIMESTAMP ITSELF, not by the current
# offset: a bar may lie on the other side of a DST switch relative to "now"
# (history backfill, the first bar after the switch).
from src.backtesting.data.tick_data_loader import server_sec_to_utc_sec  # noqa: E402


def _detect_server_utc_offset() -> int:
    """Return current MT5 server UTC offset in seconds (DST-aware)."""
    try:
        from datetime import datetime, timezone

        from src.backtesting.data.tick_data_loader import get_mt5_offset_for_date

        return get_mt5_offset_for_date(datetime.now(timezone.utc))
    except Exception:
        return 3 * 3600  # Fallback to summer time


class MT5DataProvider(BaseDataProvider):
    """MetaTrader 5 data provider implementation."""

    def __init__(self, config: DataProviderConfig):
        """
        Initialize MT5 data provider.

        Args:
            config: Provider configuration
        """
        super().__init__(config)

        if not MT5_AVAILABLE:
            raise DataProviderError(
                "MT5 not available. Install MetaTrader5 (Windows) or mt5linux (Linux)."
            )

        # MT5 specific settings
        self.login = None
        self.password = None
        self.server = None
        self.path = None

        # Polling tasks for simulated real-time data
        self.polling_tasks = {}

        # Timeframe mapping
        self.timeframe_map = {
            "1m": mt5.TIMEFRAME_M1,
            "2m": mt5.TIMEFRAME_M2,
            "3m": mt5.TIMEFRAME_M3,
            "5m": mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,
            "4h": mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
            "1w": mt5.TIMEFRAME_W1,
            "1M": mt5.TIMEFRAME_MN1,
        }

        # The server offset is NOT cached: it changes twice a year, and the bot
        # lives for weeks. A value cached at startup would outlive a DST switch and
        # label every candle one hour off — and the strategies' session windows
        # would move with them. The computation is cheap, see the property below.

        # Cache of built candles: {(symbol, timeframe): (raw_ts of the last one, [Candle])}
        # Closed candles never change — there is no point rebuilding them every minute.
        self._candle_cache: Dict[tuple, tuple] = {}

        # Reconnection settings
        self._consecutive_errors = 0
        self._max_errors_before_reconnect = 3
        self._reconnecting = False

        offset_h = self._server_utc_offset_sec // 3600
        logger.info(f"MT5DataProvider initialized (server UTC offset: +{offset_h}h)")

    @property
    def _server_utc_offset_sec(self) -> int:
        """Current server offset, recomputed on every access."""
        return _detect_server_utc_offset()

    async def _ensure_connected(self) -> bool:
        """
        Check MT5 connection and reconnect if needed.
        Called automatically when IPC errors are detected.

        Returns:
            bool: True if connected
        """
        if self._reconnecting:
            return False

        try:
            # Quick check - try to get terminal info
            info = mt5.terminal_info()
            if info is not None:
                self._consecutive_errors = 0
                return True
        except Exception:
            pass

        # Connection lost - attempt reconnect
        self._reconnecting = True
        logger.warning("MT5 connection lost, attempting reconnect...")

        try:
            # Shutdown old connection
            try:
                mt5.shutdown()
            except Exception:
                pass

            await asyncio.sleep(2)

            # Re-initialize
            if self.path:
                result = mt5.initialize(self.path)
            else:
                result = mt5.initialize()

            if not result:
                logger.error(f"MT5 reconnect failed: {mt5.last_error()}")
                self._reconnecting = False
                return False

            # Re-login if needed
            if self.login and self.password and self.server:
                if not mt5.login(login=self.login, password=self.password, server=self.server):
                    logger.error(f"MT5 re-login failed: {mt5.last_error()}")
                    self._reconnecting = False
                    return False

            account_info = mt5.account_info()
            if account_info:
                self.connection_status.is_connected = True
                self.connection_status.last_update = int(time.time() * 1000)
                self._consecutive_errors = 0
                logger.info(f"MT5 reconnected successfully. Account: {account_info.login}")
                self._reconnecting = False
                return True
            else:
                logger.error("MT5 reconnect: failed to get account info")
                self._reconnecting = False
                return False

        except Exception as e:
            logger.error(f"MT5 reconnect error: {e}")
            self._reconnecting = False
            return False

    async def connect(self) -> bool:
        """
        Connect to MT5.

        Returns:
            bool: Connection success status
        """
        try:
            # Initialize MT5
            if self.path:
                if not mt5.initialize(self.path):
                    logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                    return False
            else:
                if not mt5.initialize():
                    logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                    return False

            # Login if credentials provided
            if self.login and self.password and self.server:
                if not mt5.login(login=self.login, password=self.password, server=self.server):
                    logger.error(f"MT5 login failed: {mt5.last_error()}")
                    mt5.shutdown()
                    return False

            # Verify connection
            account_info = mt5.account_info()
            if account_info:
                self.connection_status.is_connected = True
                self.connection_status.last_update = int(time.time() * 1000)
                logger.info(f"Connected to MT5. Account: {account_info.login}")
                return True
            else:
                logger.error("Failed to get MT5 account info")
                mt5.shutdown()
                return False

        except Exception as e:
            logger.error(f"Error connecting to MT5: {e}")
            self.connection_status.error_message = str(e)
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from MT5.

        Returns:
            bool: Disconnection success status
        """
        try:
            # Cancel polling tasks
            for task in self.polling_tasks.values():
                task.cancel()
            self.polling_tasks.clear()

            # Shutdown MT5
            mt5.shutdown()

            self.connection_status.is_connected = False
            logger.info("Disconnected from MT5")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from MT5: {e}")
            return False

    async def subscribe(self, subscription: DataSubscription) -> bool:
        """
        Subscribe to MT5 data (simulated with polling).

        Args:
            subscription: Subscription configuration

        Returns:
            bool: Subscription success status
        """
        try:
            # MT5 doesn't have real-time streaming, so we poll
            key = f"{subscription.symbol}_{subscription.data_type}"

            if key in self.polling_tasks:
                logger.warning(f"Already subscribed to {key}")
                return True

            # Start polling task
            if subscription.data_type == "ticker":
                task = asyncio.create_task(self._poll_ticker(subscription))
            elif subscription.data_type == "candle":
                task = asyncio.create_task(self._poll_candles(subscription))
            else:
                logger.warning(f"Unsupported data type for MT5: {subscription.data_type}")
                return False

            self.polling_tasks[key] = task
            logger.info(f"Started polling for {key}")
            return True

        except Exception as e:
            logger.error(f"Error subscribing to {subscription.symbol}: {e}")
            return False

    async def unsubscribe(self, subscription: DataSubscription) -> bool:
        """
        Unsubscribe from MT5 data.

        Args:
            subscription: Subscription to remove

        Returns:
            bool: Unsubscription success status
        """
        try:
            key = f"{subscription.symbol}_{subscription.data_type}"

            if key in self.polling_tasks:
                self.polling_tasks[key].cancel()
                del self.polling_tasks[key]
                logger.info(f"Stopped polling for {key}")

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
        Get historical candles from MT5.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            limit: Number of candles
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)

        Returns:
            List[Candle]: Historical candles
        """
        try:
            # Get MT5 timeframe
            mt5_timeframe = self.timeframe_map.get(timeframe, mt5.TIMEFRAME_M1)

            # Map TF to seconds for range fallback when caller didn't pass start_time
            _tf_seconds = {
                "1m": 60,
                "2m": 120,
                "3m": 180,
                "5m": 300,
                "15m": 900,
                "30m": 1800,
                "1h": 3600,
                "4h": 14400,
                "1d": 86400,
                "1w": 604800,
                "1M": 2592000,
            }

            # Auto-promote to copy_rates_range when limit is large.
            # Reason: copy_rates_from_pos on some brokers caps at ~2000 (MT5 buffer
            # tail). copy_rates_range pulls from history archive — no cap.
            # Threshold 1500 leaves headroom for the 2000-ish broker cap.
            _LARGE_LIMIT_THRESHOLD = 1500
            auto_range = (
                start_time is None
                and end_time is None
                and limit > _LARGE_LIMIT_THRESHOLD
                and timeframe in _tf_seconds
            )

            # Get rates (convert real UTC → server time for MT5 query)
            # Retry up to 2 times on IPC failures (high-latency connections)
            rates = None
            max_retries = 2
            for attempt in range(max_retries + 1):
                if start_time and end_time:
                    start_dt = datetime.fromtimestamp(
                        start_time / 1000 + self._server_utc_offset_sec
                    )
                    end_dt = datetime.fromtimestamp(end_time / 1000 + self._server_utc_offset_sec)
                    rates = mt5.copy_rates_range(symbol, mt5_timeframe, start_dt, end_dt)
                elif start_time:
                    start_dt = datetime.fromtimestamp(
                        start_time / 1000 + self._server_utc_offset_sec
                    )
                    rates = mt5.copy_rates_from(symbol, mt5_timeframe, start_dt, limit)
                elif auto_range:
                    # Pull from history archive: now → now - (limit * tf * 2.5 buffer)
                    # Buffer covers weekends (5/7 trading days) + gaps + market closures.
                    # 2.5× is conservative; oversized fetch is trimmed to last `limit`.
                    tf_sec = _tf_seconds[timeframe]
                    now_sec = int(datetime.now().timestamp()) + self._server_utc_offset_sec
                    span_sec = int(limit * tf_sec * 2.5)
                    start_dt = datetime.fromtimestamp(now_sec - span_sec)
                    end_dt = datetime.fromtimestamp(now_sec + 60)  # +60s headroom
                    rates = mt5.copy_rates_range(symbol, mt5_timeframe, start_dt, end_dt)
                    # Trim to last `limit` if range returned more
                    if rates is not None and len(rates) > limit:
                        rates = rates[-limit:]
                else:
                    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, limit)

                if rates is not None:
                    break

                error = mt5.last_error()
                error_code = error[0] if error else 0

                if error_code >= 0 or attempt == max_retries:
                    # Non-IPC error or last attempt — give up
                    logger.error(f"Failed to get rates: {error}")
                    if error_code < 0:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= self._max_errors_before_reconnect:
                            logger.warning(
                                f"MT5 IPC errors ({self._consecutive_errors}x), "
                                f"triggering reconnect..."
                            )
                            self.connection_status.is_connected = False
                            await self._ensure_connected()
                    return []

                # IPC error with retries left — brief pause and retry
                logger.debug(f"MT5 IPC error for {symbol}, retry {attempt + 1}/{max_retries}")
                await asyncio.sleep(0.3)

            # Reset error counter on success
            self._consecutive_errors = 0

            # Convert to Candle objects
            # Exclude the last (current/unclosed) candle — position 0 in MT5
            # is always the forming candle whose close changes every tick.
            # Using it would cause EMA/ATR to differ from backtest (which
            # only uses fully closed candles).
            closed_rates = rates[:-1] if len(rates) > 1 else rates

            # Closed candles do not change, so there is no point rebuilding them
            # every minute. Candle is a dataclass with 20 fields and validate() in
            # __post_init__; 60,000 objects cost ~1.9 s on a workstation and
            # several times more on a VPS. It was the MAIN source of entry latency:
            # one live trade entered $2.51 worse than the backtest because the
            # loop spent seconds before sending the order.
            #
            # The cache reuses the candles already built and converts only the new
            # ones. If history diverged (MT5 rebuilt bars after a reconnect) — the
            # cache is dropped entirely.
            cache_key = (symbol, timeframe)
            prev_raw_ts, prev_candles = self._candle_cache.get(cache_key, (None, None))
            rates_to_convert = closed_rates
            candles = []

            if prev_candles and prev_raw_ts is not None and len(closed_rates):
                times = closed_rates["time"]
                idx = int(np.searchsorted(times, prev_raw_ts))
                if (
                    idx < len(times)
                    and int(times[idx]) == prev_raw_ts
                    and abs(float(closed_rates[idx]["close"]) - prev_candles[-1].close) < 1e-9
                ):
                    # A copy, not the list itself: otherwise the result of the previous
                    # call would mutate under whoever still holds it. Copying 60,000
                    # references costs a fraction of a millisecond versus
                    # ~1900 ms to recreate the objects.
                    candles = list(prev_candles)
                    rates_to_convert = closed_rates[idx + 1 :]

            for rate in rates_to_convert:
                # MT5 spread is in points (integers), convert to price units
                # For XAUUSD: spread=20 means 0.20 in price
                raw_spread = int(rate["spread"]) if "spread" in rate.dtype.names else 0
                spread_price = raw_spread / 100.0  # points → price units

                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=server_sec_to_utc_sec(int(rate["time"]))
                    * 1000,  # Server time → real UTC ms
                    open=float(rate["open"]),
                    high=float(rate["high"]),
                    low=float(rate["low"]),
                    close=float(rate["close"]),
                    volume=float(rate["tick_volume"]),
                    quote_volume=float(rate["real_volume"]) if "real_volume" in rate else None,
                    spread=spread_price,
                    is_closed=True,
                    source="mt5",
                    exchange="mt5",
                )
                candles.append(candle)

            # The cache stores the FULL built list; only the returned slice is trimmed.
            # Otherwise a call with a small limit would shrink the cache, and the next
            # request for 60,000 would return a meagre remainder.
            if len(closed_rates):
                self._candle_cache[cache_key] = (int(closed_rates[-1]["time"]), candles)

            return candles[-limit:] if len(candles) > limit else candles

        except Exception as e:
            logger.error(f"Error getting candles: {e}")
            return []

    async def get_historical_ticks(
        self, symbol: str, date_from: datetime, date_to: datetime
    ) -> Optional[any]:
        """
        Get historical tick data from MT5.

        Uses mt5.copy_ticks_range() to fetch all ticks in the given time range.
        Timestamps are normalized from server time (EET) to real UTC.

        Args:
            symbol: Trading symbol
            date_from: Start datetime (UTC)
            date_to: End datetime (UTC)

        Returns:
            numpy structured array with fields: time, bid, ask, last, volume,
            time_msc, flags. Returns None on failure.
        """
        try:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to select symbol {symbol} for tick data")
                return None

            # Convert UTC → server time (EET/EEST) for MT5 query
            # Use date-specific offset to handle DST transitions
            from datetime import timedelta

            try:
                from src.backtesting.data.tick_data_loader import get_mt5_offset_for_date

                offset_from = get_mt5_offset_for_date(date_from)
                offset_to = get_mt5_offset_for_date(date_to)
            except ImportError:
                offset_from = offset_to = self._server_utc_offset_sec
            server_from = date_from + timedelta(seconds=offset_from)
            server_to = date_to + timedelta(seconds=offset_to)

            ticks = None
            max_retries = 2
            for attempt in range(max_retries + 1):
                ticks = mt5.copy_ticks_range(symbol, server_from, server_to, mt5.COPY_TICKS_ALL)

                if ticks is not None and len(ticks) > 0:
                    break

                error = mt5.last_error()
                error_code = error[0] if error else 0

                if error_code >= 0 or attempt == max_retries:
                    if ticks is not None and len(ticks) == 0:
                        logger.debug(
                            f"No ticks for {symbol} "
                            f"{date_from:%Y-%m-%d %H:%M} - {date_to:%Y-%m-%d %H:%M}"
                        )
                        return None
                    logger.error(f"Failed to get ticks for {symbol}: {error}")
                    if error_code < 0:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= self._max_errors_before_reconnect:
                            self.connection_status.is_connected = False
                            await self._ensure_connected()
                    return None

                await asyncio.sleep(0.3)

            self._consecutive_errors = 0

            logger.debug(
                f"Loaded {len(ticks)} ticks for {symbol} "
                f"{date_from:%Y-%m-%d} - {date_to:%Y-%m-%d}"
            )
            return ticks

        except Exception as e:
            logger.error(f"Error getting historical ticks for {symbol}: {e}")
            return None

    def last_bar_start_ms(self, symbol: str, timeframe: str) -> Optional[int]:
        """Start timestamp of the MOST RECENT bar (including the forming one), UTC ms.

        Used to wait for a new minute's bar to appear without building candles.
        Deliberately does NOT go through get_candles: that caches by symbol+TF, and
        a request for a couple of bars would overwrite the cached 60,000 — strategies
        would get three candles instead of sixty thousand and silently go quiet.
        """
        try:
            mt5_tf = self.timeframe_map.get(timeframe)
            if mt5_tf is None:
                return None
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, 1)
            if rates is None or len(rates) == 0:
                return None
            return server_sec_to_utc_sec(int(rates[-1]["time"])) * 1000
        except Exception:  # noqa: BLE001
            return None

    def symbol_exists(self, symbol: str) -> bool:
        """Whether the connected terminal knows this symbol.

        Without this check a missing symbol shows up as "Terminal: Call
        failed" — a message that gives no hint that the terminal is simply
        connected to the wrong account.
        """
        try:
            return mt5.symbol_info(symbol) is not None
        except Exception:  # noqa: BLE001
            return False

    def describe_connection(self) -> str:
        """Account and server on one line — for error messages."""
        try:
            a = mt5.account_info()
            return f"account {a.login} ({a.server})" if a else "account unknown"
        except Exception:  # noqa: BLE001
            return "account unknown"

    def similar_symbols(self, symbol: str, limit: int = 6) -> List[str]:
        """Similar names — most often the right symbol differs by a suffix."""
        try:
            stem = "".join(ch for ch in symbol if ch.isalnum())[:6].upper()
            names = [s.name for s in mt5.symbols_get() or []]
            return [n for n in names if stem[:3] in n.upper()][:limit]
        except Exception:  # noqa: BLE001
            return []

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get current ticker from MT5.

        Args:
            symbol: Trading symbol

        Returns:
            Optional[Ticker]: Ticker data
        """
        try:
            # Select symbol
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to select symbol {symbol}")
                return None

            # Get tick (retry on IPC failures for high-latency connections)
            tick = None
            for attempt in range(3):
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    break
                error = mt5.last_error()
                error_code = error[0] if error else 0
                if error_code >= 0 or attempt == 2:
                    logger.error(f"Failed to get tick: {error}")
                    if error_code < 0:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= self._max_errors_before_reconnect:
                            self.connection_status.is_connected = False
                            await self._ensure_connected()
                    return None
                await asyncio.sleep(0.3)

            # Get symbol info for 24h data
            symbol_info = mt5.symbol_info(symbol)

            ticker = Ticker(
                symbol=symbol,
                last_price=tick.last,
                timestamp=server_sec_to_utc_sec(int(tick.time)) * 1000,  # Server time → real UTC ms
                bid_price=tick.bid,
                bid_size=tick.volume if hasattr(tick, "volume") else None,
                ask_price=tick.ask,
                ask_size=tick.volume if hasattr(tick, "volume") else None,
                volume_24h=float(symbol_info.volume) if symbol_info else None,
                high_24h=float(symbol_info.bidhigh) if symbol_info else None,
                low_24h=float(symbol_info.bidlow) if symbol_info else None,
                exchange="mt5",
                source="mt5",
            )

            return ticker

        except Exception as e:
            logger.error(f"Error getting ticker: {e}")
            return None

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[OrderBook]:
        """
        Get order book from MT5 (limited to best bid/ask).

        Args:
            symbol: Trading symbol
            depth: Order book depth (ignored for MT5)

        Returns:
            Optional[OrderBook]: Order book data
        """
        try:
            # MT5 only provides best bid/ask
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logger.error(f"Failed to get tick: {mt5.last_error()}")
                return None

            # Create order book with single level
            orderbook = OrderBook(
                symbol=symbol,
                timestamp=server_sec_to_utc_sec(int(tick.time)) * 1000,  # Server time → real UTC ms
                bids=[
                    OrderBookLevel(
                        price=tick.bid, size=tick.volume if hasattr(tick, "volume") else 1.0
                    )
                ],
                asks=[
                    OrderBookLevel(
                        price=tick.ask, size=tick.volume if hasattr(tick, "volume") else 1.0
                    )
                ],
                exchange="mt5",
                source="mt5",
                is_snapshot=True,
            )

            return orderbook

        except Exception as e:
            logger.error(f"Error getting orderbook: {e}")
            return None

    # ==================== Polling Methods ====================

    async def _poll_ticker(self, subscription: DataSubscription) -> None:
        """Poll ticker data."""
        interval = subscription.params.get("interval", 1.0)  # Default 1 second

        while self.is_running:
            try:
                ticker = await self.get_ticker(subscription.symbol)
                if ticker:
                    await self.push_data("ticker", subscription.symbol, ticker)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling ticker for {subscription.symbol}: {e}")
                await asyncio.sleep(interval)

    async def _poll_candles(self, subscription: DataSubscription) -> None:
        """Poll candle data."""
        timeframe = subscription.params.get("timeframe", "1m")
        interval = self._get_poll_interval(timeframe)
        last_candle_time = None

        while self.is_running:
            try:
                candles = await self.get_candles(subscription.symbol, timeframe, limit=2)

                if candles:
                    # Check if we have a new candle
                    latest = candles[-1]
                    if last_candle_time != latest.timestamp:
                        await self.push_data("candle", subscription.symbol, latest)
                        last_candle_time = latest.timestamp

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling candles for {subscription.symbol}: {e}")
                await asyncio.sleep(interval)

    def _get_poll_interval(self, timeframe: str) -> float:
        """Get polling interval based on timeframe."""
        intervals = {"1m": 5, "5m": 10, "15m": 30, "30m": 60, "1h": 120, "4h": 300, "1d": 600}
        return intervals.get(timeframe, 10)
