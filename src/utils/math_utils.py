"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : math_utils
    Project     : Project-103
    Description : Mathematical utility functions. Provides technical analysis calculations and statistical operations.

    SPDX-License-Identifier: AGPL-3.0

"""

import math
from typing import List


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Percentage change between two values"""
    if old_value == 0:
        return 0.0
    return ((new_value - old_value) / old_value) * 100


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """RSI (Relative Strength Index)"""
    if len(prices) < period + 1:
        raise ValueError(f"Need at least {period + 1} prices for RSI calculation")

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def calculate_ema(prices: List[float], period: int) -> float:
    """EMA (Exponential Moving Average)"""
    if len(prices) < period:
        raise ValueError(f"Need at least {period} prices for EMA calculation")

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 8)


def calculate_sma(prices: List[float], period: int) -> float:
    """SMA (Simple Moving Average)"""
    if len(prices) < period:
        raise ValueError(f"Need at least {period} prices for SMA calculation")

    return round(sum(prices[-period:]) / period, 8)


def round_to_precision(value: float, precision: int) -> float:
    """Round a value to the given precision"""
    return round(value, precision)


def round_to_tick_size(value: float, tick_size: float) -> float:
    """Round a value to the tick size"""
    if tick_size == 0:
        return value

    # Calculate precision from tick_size (handles scientific notation like 1e-06)
    import math

    if tick_size >= 1:
        precision = 0
    else:
        precision = int(-math.log10(tick_size) + 0.5)  # +0.5 for rounding

    rounded = round(value / tick_size) * tick_size

    return round(rounded, precision)


def normalize_value(
    value: float, min_value: float, max_value: float, new_min: float = 0.0, new_max: float = 1.0
) -> float:
    """Normalize a value into a new range"""
    if max_value == min_value:
        return new_min

    normalized = (value - min_value) / (max_value - min_value)
    scaled = normalized * (new_max - new_min) + new_min

    return round(scaled, 8)


def calculate_standard_deviation(values: List[float]) -> float:
    """Standard deviation"""
    if not values:
        return 0.0

    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)

    return round(math.sqrt(variance), 8)


def calculate_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """ATR (Average True Range)"""
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("All lists must have the same length")

    if len(highs) < period + 1:
        raise ValueError(f"Need at least {period + 1} candles for ATR calculation")

    true_ranges = []
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i - 1])
        low_close = abs(lows[i] - closes[i - 1])

        true_range = max(high_low, high_close, low_close)
        true_ranges.append(true_range)

    atr = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return round(atr, 8)


def clamp(value: float, min_value: float, max_value: float) -> float:
    """Clamp a value to the given range"""
    return max(min_value, min(value, max_value))


def safe_log(value: float, base: float = math.e) -> float:
    """Safe logarithm"""
    if value <= 0:
        return 0.0

    return math.log(value, base)
