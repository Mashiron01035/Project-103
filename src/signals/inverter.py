"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 12.12.2025.
    File        : inverter.py
    Project     : Project-103
    Description : Dynamic signal inversion based on market conditions.

                  Supports multiple inversion modes:
                  - btc_trend: Invert based on BTC trend direction
                  - schedule: Invert during specific hours/periods
                  - always: Always invert (contrarian mode)
                  - manual: Disabled (default)

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class InversionMode(Enum):
    """Signal inversion modes"""

    MANUAL = "manual"  # Disabled
    ALWAYS = "always"  # Always invert (contrarian)
    BTC_TREND = "btc_trend"  # Based on BTC trend
    SCHEDULE = "schedule"  # Based on time schedule
    COMBINED = "combined"  # BTC trend + schedule


class BTCTrend(Enum):
    """BTC trend states"""

    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGING = "RANGING"
    UNKNOWN = "UNKNOWN"


@dataclass
class InversionResult:
    """Result of inversion check"""

    should_invert: bool
    reason: str
    btc_trend: Optional[str] = None
    btc_price: Optional[float] = None
    btc_ma: Optional[float] = None
    deviation: Optional[float] = None


class SignalInverter:
    """
    Dynamic signal inversion based on market conditions.

    Features:
    - BTC trend-based inversion (EMA/SMA analysis)
    - Time-based schedule inversion
    - Configurable direction filtering (LONG only, SHORT only, or both)
    - Symbol exclusions
    - Confidence threshold filtering
    - Result caching to reduce API calls

    Usage:
        inverter = SignalInverter(config, data_provider)
        result = await inverter.should_invert("SOLUSDT", "LONG", 0.75)
        if result.should_invert:
            direction = "SHORT"  # Inverted
    """

    def __init__(self, config: Dict[str, Any], data_provider=None):
        """
        Initialize SignalInverter.

        Args:
            config: Strategy configuration containing 'signal_inversion' section
            data_provider: Data provider for fetching BTC candles (optional)
        """
        self.full_config = config
        self.config = config.get("signal_inversion", {})
        self.enabled = self.config.get("enabled", False)
        self.data_provider = data_provider

        # Cache for BTC trend to reduce API calls
        self._btc_trend_cache: Optional[BTCTrend] = None
        self._btc_price_cache: Optional[float] = None
        self._btc_ma_cache: Optional[float] = None
        self._btc_deviation_cache: Optional[float] = None
        self._cache_time: float = 0
        self._cache_ttl: int = self.config.get("cache_ttl_seconds", 300)  # 5 min default

        # Statistics
        self._inversions_count: int = 0
        self._checks_count: int = 0

        if self.enabled:
            mode = self.config.get("mode", "manual")
            logger.info(f"SignalInverter initialized: enabled={self.enabled}, mode={mode}")

    def set_data_provider(self, data_provider) -> None:
        """Set data provider after initialization"""
        self.data_provider = data_provider

    async def should_invert(
        self, symbol: str, direction: str, confidence: float = 0.5
    ) -> InversionResult:
        """
        Check if signal should be inverted.

        Args:
            symbol: Trading symbol (e.g., "SOLUSDT")
            direction: Original signal direction ("LONG" or "SHORT")
            confidence: Signal confidence (0-1)

        Returns:
            InversionResult with decision and reason
        """
        self._checks_count += 1

        # Return early if disabled
        if not self.enabled:
            return InversionResult(should_invert=False, reason="disabled")

        # Check exclusions
        exclude_symbols = self.config.get("exclude_symbols", ["BTCUSDT", "BTCPERP"])
        if symbol in exclude_symbols:
            return InversionResult(should_invert=False, reason="excluded_symbol")

        # Check minimum confidence
        min_confidence = self.config.get("min_confidence", 0.0)
        if confidence < min_confidence:
            return InversionResult(
                should_invert=False, reason=f"low_confidence ({confidence:.2f} < {min_confidence})"
            )

        # Check direction filters
        directions_config = self.config.get("directions", {})
        if direction == "LONG" and not directions_config.get("invert_long", True):
            return InversionResult(should_invert=False, reason="long_inversion_disabled")
        if direction == "SHORT" and not directions_config.get("invert_short", True):
            return InversionResult(should_invert=False, reason="short_inversion_disabled")

        # Get mode and check
        mode = self.config.get("mode", "manual")

        if mode == "manual":
            return InversionResult(should_invert=False, reason="manual_mode")

        if mode == "always":
            self._inversions_count += 1
            return InversionResult(should_invert=True, reason="always_invert")

        if mode == "btc_trend":
            return await self._check_btc_trend_inversion(direction)

        if mode == "schedule":
            return self._check_schedule_inversion(direction)

        if mode == "combined":
            # Check both BTC trend and schedule
            btc_result = await self._check_btc_trend_inversion(direction)
            schedule_result = self._check_schedule_inversion(direction)

            if btc_result.should_invert and schedule_result.should_invert:
                self._inversions_count += 1
                return InversionResult(
                    should_invert=True,
                    reason=f"combined: {btc_result.reason} + {schedule_result.reason}",
                    btc_trend=btc_result.btc_trend,
                    btc_price=btc_result.btc_price,
                    btc_ma=btc_result.btc_ma,
                    deviation=btc_result.deviation,
                )
            return InversionResult(should_invert=False, reason="combined_conditions_not_met")

        return InversionResult(should_invert=False, reason=f"unknown_mode: {mode}")

    async def _check_btc_trend_inversion(self, direction: str) -> InversionResult:
        """
        Check if signal should be inverted based on BTC trend.

        Args:
            direction: Original signal direction ("LONG" or "SHORT")

        Returns:
            InversionResult with BTC trend details
        """
        btc_config = self.config.get("btc_trend", {})
        if not btc_config.get("enabled", True):
            return InversionResult(should_invert=False, reason="btc_trend_disabled")

        # Get BTC trend (uses caching)
        trend, price, ma, deviation = await self._get_btc_trend()

        if trend == BTCTrend.UNKNOWN:
            return InversionResult(
                should_invert=False, reason="btc_trend_unknown", btc_trend=trend.value
            )

        # Check inversion rules
        invert_long_in_downtrend = btc_config.get("invert_long_in_downtrend", True)
        invert_short_in_uptrend = btc_config.get("invert_short_in_uptrend", False)
        invert_long_in_uptrend = btc_config.get("invert_long_in_uptrend", False)
        invert_long_in_ranging = btc_config.get("invert_long_in_ranging", False)
        invert_short_in_downtrend = btc_config.get("invert_short_in_downtrend", False)
        invert_short_in_ranging = btc_config.get("invert_short_in_ranging", False)

        should_invert = False
        reason = ""

        # DOWNTREND rules
        if trend == BTCTrend.DOWNTREND and direction == "LONG" and invert_long_in_downtrend:
            should_invert = True
            reason = f"BTC_DOWNTREND (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"
        elif trend == BTCTrend.DOWNTREND and direction == "SHORT" and invert_short_in_downtrend:
            should_invert = True
            reason = f"BTC_DOWNTREND (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"

        # UPTREND rules
        elif trend == BTCTrend.UPTREND and direction == "SHORT" and invert_short_in_uptrend:
            should_invert = True
            reason = f"BTC_UPTREND (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"
        elif trend == BTCTrend.UPTREND and direction == "LONG" and invert_long_in_uptrend:
            should_invert = True
            reason = f"BTC_UPTREND (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"

        # RANGING rules
        elif trend == BTCTrend.RANGING and direction == "LONG" and invert_long_in_ranging:
            should_invert = True
            reason = f"BTC_RANGING (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"
        elif trend == BTCTrend.RANGING and direction == "SHORT" and invert_short_in_ranging:
            should_invert = True
            reason = f"BTC_RANGING (price={price:.2f}, MA={ma:.2f}, dev={deviation*100:.2f}%)"

        if should_invert:
            self._inversions_count += 1

        return InversionResult(
            should_invert=should_invert,
            reason=reason if should_invert else f"no_inversion_needed (BTC={trend.value})",
            btc_trend=trend.value,
            btc_price=price,
            btc_ma=ma,
            deviation=deviation,
        )

    async def _get_btc_trend(
        self,
    ) -> Tuple[BTCTrend, Optional[float], Optional[float], Optional[float]]:
        """
        Get current BTC trend with caching.

        Returns:
            Tuple of (trend, current_price, ma_value, deviation)
        """
        now = time.time()

        # Return cached value if still valid
        if self._btc_trend_cache and (now - self._cache_time) < self._cache_ttl:
            return (
                self._btc_trend_cache,
                self._btc_price_cache,
                self._btc_ma_cache,
                self._btc_deviation_cache,
            )

        # Fetch fresh data
        btc_config = self.config.get("btc_trend", {})
        timeframe = btc_config.get("timeframe", "4h")
        method = btc_config.get("method", "ema")
        period = btc_config.get("period", 20)
        threshold = btc_config.get("threshold", 0.015)  # 1.5% default

        if not self.data_provider:
            logger.warning("SignalInverter: No data provider available for BTC trend")
            return BTCTrend.UNKNOWN, None, None, None

        try:
            # Fetch BTC candles
            candles = await self.data_provider.get_candles(
                symbol="BTCUSDT", timeframe=timeframe, limit=period + 10
            )

            if not candles or len(candles) < period:
                logger.warning(
                    f"SignalInverter: Insufficient BTC candles ({len(candles) if candles else 0})"
                )
                return BTCTrend.UNKNOWN, None, None, None

            # Calculate MA
            closes = [c.close for c in candles]
            current_price = closes[-1]

            if method == "ema":
                ma = self._calculate_ema(closes, period)
            elif method == "sma":
                ma = sum(closes[-period:]) / period
            else:
                # Price change method - compare current to N periods ago
                ma = closes[-period]

            if ma <= 0:
                return BTCTrend.UNKNOWN, current_price, ma, None

            # Calculate deviation from MA
            deviation = (current_price - ma) / ma

            # Determine trend
            if deviation > threshold:
                trend = BTCTrend.UPTREND
            elif deviation < -threshold:
                trend = BTCTrend.DOWNTREND
            else:
                trend = BTCTrend.RANGING

            # Update cache
            self._btc_trend_cache = trend
            self._btc_price_cache = current_price
            self._btc_ma_cache = ma
            self._btc_deviation_cache = deviation
            self._cache_time = now

            logger.debug(
                f"BTC trend updated: {trend.value}, price={current_price:.2f}, "
                f"MA({period})={ma:.2f}, deviation={deviation*100:.2f}%"
            )

            return trend, current_price, ma, deviation

        except Exception as e:
            logger.error(f"SignalInverter: Error getting BTC trend: {e}")
            return BTCTrend.UNKNOWN, None, None, None

    def _calculate_ema(self, values: List[float], period: int) -> float:
        """
        Calculate Exponential Moving Average.

        Args:
            values: List of price values
            period: EMA period

        Returns:
            EMA value
        """
        if len(values) < period:
            return sum(values) / len(values) if values else 0

        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period  # SMA for first period

        for price in values[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        return ema

    def _check_schedule_inversion(self, direction: str) -> InversionResult:
        """
        Check if signal should be inverted based on time schedule.

        Args:
            direction: Original signal direction ("LONG" or "SHORT")

        Returns:
            InversionResult
        """
        schedule_config = self.config.get("schedule", {})
        if not schedule_config.get("enabled", False):
            return InversionResult(should_invert=False, reason="schedule_disabled")

        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_time_str = now.strftime("%H:%M")

        # Check active hours
        active_hours = schedule_config.get("active_hours", [])
        if active_hours and current_hour in active_hours:
            self._inversions_count += 1
            return InversionResult(
                should_invert=True, reason=f"schedule_active_hour ({current_hour}:00 UTC)"
            )

        # Check periods
        periods = schedule_config.get("periods", [])
        for period in periods:
            start = period.get("start", "00:00")
            end = period.get("end", "00:00")
            invert_long = period.get("invert_long", True)
            invert_short = period.get("invert_short", True)

            if self._is_time_in_range(current_time_str, start, end):
                # Check if this direction should be inverted in this period
                if direction == "LONG" and invert_long:
                    self._inversions_count += 1
                    return InversionResult(
                        should_invert=True,
                        reason=f"schedule_period ({start}-{end} UTC, invert_long)",
                    )
                if direction == "SHORT" and invert_short:
                    self._inversions_count += 1
                    return InversionResult(
                        should_invert=True,
                        reason=f"schedule_period ({start}-{end} UTC, invert_short)",
                    )

        return InversionResult(
            should_invert=False, reason=f"schedule_not_active (current: {current_time_str} UTC)"
        )

    def _is_time_in_range(self, current: str, start: str, end: str) -> bool:
        """
        Check if current time is within range.

        Handles overnight ranges (e.g., 22:00 - 06:00).

        Args:
            current: Current time "HH:MM"
            start: Start time "HH:MM"
            end: End time "HH:MM"

        Returns:
            True if current is in range
        """
        try:
            current_minutes = self._time_to_minutes(current)
            start_minutes = self._time_to_minutes(start)
            end_minutes = self._time_to_minutes(end)

            if start_minutes <= end_minutes:
                # Normal range (e.g., 09:00 - 17:00)
                return start_minutes <= current_minutes <= end_minutes
            else:
                # Overnight range (e.g., 22:00 - 06:00)
                return current_minutes >= start_minutes or current_minutes <= end_minutes

        except Exception:
            return False

    def _time_to_minutes(self, time_str: str) -> int:
        """Convert HH:MM to minutes since midnight"""
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    async def get_btc_trend_info(self) -> Dict[str, Any]:
        """
        Get current BTC trend information for display/logging.

        Returns:
            Dict with trend details
        """
        trend, price, ma, deviation = await self._get_btc_trend()

        return {
            "trend": trend.value,
            "price": price,
            "ma": ma,
            "deviation_percent": round(deviation * 100, 2) if deviation else None,
            "cache_age_seconds": int(time.time() - self._cache_time) if self._cache_time else None,
            "config": {
                "timeframe": self.config.get("btc_trend", {}).get("timeframe", "4h"),
                "method": self.config.get("btc_trend", {}).get("method", "ema"),
                "period": self.config.get("btc_trend", {}).get("period", 20),
                "threshold": self.config.get("btc_trend", {}).get("threshold", 0.015),
            },
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get inverter statistics"""
        return {
            "enabled": self.enabled,
            "mode": self.config.get("mode", "manual"),
            "checks_count": self._checks_count,
            "inversions_count": self._inversions_count,
            "inversion_rate": (
                round(self._inversions_count / self._checks_count * 100, 2)
                if self._checks_count > 0
                else 0
            ),
            "btc_trend_cache": {
                "trend": self._btc_trend_cache.value if self._btc_trend_cache else None,
                "price": self._btc_price_cache,
                "ma": self._btc_ma_cache,
                "age_seconds": int(time.time() - self._cache_time) if self._cache_time else None,
            },
        }

    def clear_cache(self) -> None:
        """Clear BTC trend cache"""
        self._btc_trend_cache = None
        self._btc_price_cache = None
        self._btc_ma_cache = None
        self._btc_deviation_cache = None
        self._cache_time = 0
        logger.debug("SignalInverter: Cache cleared")

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """
        Update inverter configuration dynamically.

        Args:
            new_config: New signal_inversion config section
        """
        self.config = new_config
        self.enabled = new_config.get("enabled", False)
        self._cache_ttl = new_config.get("cache_ttl_seconds", 300)
        self.clear_cache()
        logger.info(f"SignalInverter config updated: enabled={self.enabled}")
