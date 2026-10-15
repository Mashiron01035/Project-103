"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : datetime_utils
    Project     : Project-103
    Description : Date and time utility functions. Handles timestamp conversions, timeframe calculations, and date formatting.

    SPDX-License-Identifier: AGPL-3.0

"""

from datetime import datetime, timezone
from typing import Dict, Union

# Timeframe -> milliseconds mapping
TIMEFRAME_MILLISECONDS: Dict[str, int] = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


def utcnow() -> datetime:
    """Current UTC time without a timezone — an exact replacement for datetime.utcnow().

    `datetime.utcnow()` is deprecated in Python 3.12 and will be removed, but it
    must NOT be replaced with `datetime.now(timezone.utc)`: that returns an
    aware object, while naive time is used everywhere here.

    Why this matters in this project specifically:
      * DB columns are declared as `DateTime` without `timezone=True`, so
        Postgres stores naive timestamps and returns them naive;
      * naive and aware times cannot be compared — Python raises TypeError,
        and it would fail not at startup but at the moment of comparison, i.e. live;
      * the project has already been bitten by naive vs aware time: a naive
        datetime in history_deals_get was interpreted as LOCAL time, and the
        position ticket was never found.

    So the previous behaviour is preserved to the microsecond, and only the
    deprecated call goes away. Moving to aware time is a separate task that
    requires changing the DB schema and every comparison at once.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp_to_datetime(timestamp: int) -> datetime:
    """
    Convert a Unix timestamp (milliseconds) to a datetime object

    Args:
        timestamp: Unix timestamp in milliseconds

    Returns:
        datetime object in UTC

    Example:
        >>> timestamp_to_datetime(1704067200000)
        datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    """
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> int:
    """
    Convert a datetime object to a Unix timestamp (milliseconds)

    Args:
        dt: datetime object

    Returns:
        Unix timestamp in milliseconds

    Example:
        >>> dt = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        >>> datetime_to_timestamp(dt)
        1704067200000
    """
    return int(dt.timestamp() * 1000)


def get_current_timestamp() -> int:
    """
    Get the current Unix timestamp in milliseconds

    Returns:
        Current Unix timestamp in milliseconds

    Example:
        >>> get_current_timestamp()
        1704067200000
    """
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def format_timestamp(timestamp: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format a timestamp as a string

    Args:
        timestamp: Unix timestamp in milliseconds
        fmt: Output format (strftime format)

    Returns:
        Formatted string

    Example:
        >>> format_timestamp(1704067200000)
        '2024-01-01 00:00:00'
    """
    dt = timestamp_to_datetime(timestamp)
    return dt.strftime(fmt)


def get_timeframe_milliseconds(timeframe: str) -> int:
    """
    Get the number of milliseconds in a timeframe

    Args:
        timeframe: Timeframe (e.g. "1m", "5m", "1h")

    Returns:
        Number of milliseconds

    Raises:
        ValueError: If the timeframe is unknown

    Example:
        >>> get_timeframe_milliseconds("1m")
        60000
        >>> get_timeframe_milliseconds("1h")
        3600000
    """
    if timeframe not in TIMEFRAME_MILLISECONDS:
        raise ValueError(
            f"Unknown timeframe: {timeframe}. " f"Available: {list(TIMEFRAME_MILLISECONDS.keys())}"
        )
    return TIMEFRAME_MILLISECONDS[timeframe]


def align_timestamp_to_timeframe(timestamp: int, timeframe: str) -> int:
    """
    Align a timestamp to the given timeframe
    (round down to the start of the period)

    Args:
        timestamp: Unix timestamp in milliseconds
        timeframe: Timeframe (e.g. "1m", "5m", "1h")

    Returns:
        Aligned timestamp

    Example:
        >>> align_timestamp_to_timeframe(1704067230000, "1m")  # 00:00:30
        1704067200000  # 00:00:00
        >>> align_timestamp_to_timeframe(1704067230000, "5m")  # 00:00:30
        1704067200000  # 00:00:00
    """
    tf_ms = get_timeframe_milliseconds(timeframe)
    return (timestamp // tf_ms) * tf_ms


def get_timeframe_start_end(timestamp: int, timeframe: str) -> tuple[int, int]:
    """
    Get the start and end of the period for a given timestamp and timeframe

    Args:
        timestamp: Unix timestamp in milliseconds
        timeframe: Timeframe (e.g. "1m", "5m", "1h")

    Returns:
        Tuple (start_timestamp, end_timestamp)

    Example:
        >>> get_timeframe_start_end(1704067230000, "1m")
        (1704067200000, 1704067260000)
    """
    start = align_timestamp_to_timeframe(timestamp, timeframe)
    tf_ms = get_timeframe_milliseconds(timeframe)
    end = start + tf_ms
    return start, end


def is_timestamp_valid(timestamp: Union[int, None]) -> bool:
    """
    Check whether a timestamp is valid

    Args:
        timestamp: Unix timestamp in milliseconds

    Returns:
        True if the timestamp is valid, else False

    Example:
        >>> is_timestamp_valid(1704067200000)
        True
        >>> is_timestamp_valid(None)
        False
        >>> is_timestamp_valid(-1)
        False
    """
    if timestamp is None:
        return False
    if timestamp < 0:
        return False
    # Sanity check (not before 2000, not after 2100)
    if timestamp < 946684800000 or timestamp > 4102444800000:
        return False
    return True


def timestamp_diff_seconds(timestamp1: int, timestamp2: int) -> float:
    """
    Compute the difference between two timestamps in seconds

    Args:
        timestamp1: First timestamp in milliseconds
        timestamp2: Second timestamp in milliseconds

    Returns:
        Difference in seconds (may be negative)

    Example:
        >>> timestamp_diff_seconds(1704067260000, 1704067200000)
        60.0
    """
    return (timestamp1 - timestamp2) / 1000
