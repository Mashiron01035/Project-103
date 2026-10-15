"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : helpers
    Project     : Project-103
    Description : General helper functions. Provides decorators, data manipulation utilities, and common operations.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, List, TypeVar

T = TypeVar("T")


def retry_on_exception(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """Decorator that retries a function on exception"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logging.error(f"All {max_retries} attempts failed")

            raise last_exception

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logging.error(f"All {max_retries} attempts failed")

            raise last_exception

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def measure_execution_time(func: Callable) -> Callable:
    """Decorator that measures function execution time"""

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        execution_time = (time.time() - start_time) * 1000
        logging.debug(f"{func.__name__} executed in {execution_time:.2f}ms")
        return result

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = (time.time() - start_time) * 1000
        logging.debug(f"{func.__name__} executed in {execution_time:.2f}ms")
        return result

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that handles division by zero"""
    try:
        if denominator == 0:
            return default
        return numerator / denominator
    except (ZeroDivisionError, TypeError):
        return default


def chunks(lst: List[T], chunk_size: int) -> List[List[T]]:
    """Split a list into chunks of the given size"""
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


def get_nested_value(
    data: Dict[str, Any], path: str, default: Any = None, separator: str = "."
) -> Any:
    """Get a value from a nested dict by path"""
    keys = path.split(separator)
    value = data

    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        return default


def set_nested_value(data: Dict[str, Any], path: str, value: Any, separator: str = ".") -> None:
    """Set a value in a nested dict by path"""
    keys = path.split(separator)
    current = data

    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]

    current[keys[-1]] = value


def flatten_dict(
    data: Dict[str, Any], parent_key: str = "", separator: str = "."
) -> Dict[str, Any]:
    """Flatten a nested dict"""
    items = []
    for k, v in data.items():
        new_key = f"{parent_key}{separator}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, separator).items())
        else:
            items.append((new_key, v))
    return dict(items)


def deep_merge(dict1: Dict, dict2: Dict) -> Dict:
    """Deep-merge two dicts"""
    result = dict1.copy()

    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def is_within_percentage(value: float, target: float, percentage: float) -> bool:
    """Check whether a value is within a percentage of the target"""
    if target == 0:
        return value == 0

    diff_percent = abs((value - target) / target) * 100
    return diff_percent <= percentage


def clamp_dict_values(
    data: Dict[str, float], min_value: float, max_value: float
) -> Dict[str, float]:
    """Clamp all dict values to the given range"""
    return {k: max(min_value, min(v, max_value)) for k, v in data.items()}


def moving_average(values: List[float], window: int) -> List[float]:
    """Moving average"""
    if len(values) < window:
        return []

    result = []
    for i in range(len(values) - window + 1):
        avg = sum(values[i : i + window]) / window
        result.append(avg)

    return result
