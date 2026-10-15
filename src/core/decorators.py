"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : decorators
    Project     : Project-103
    Description : Decorator functions for the trading bot system. This module provides useful decorators for retry logic, timing, validation, caching, etc.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import functools
import logging
import threading
import time
from typing import Any, Awaitable, Callable, Dict, Optional, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# Type variables for generic typing
P = ParamSpec("P")
R = TypeVar("R")


# ==================== Retry Decorators ====================


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator for retrying function calls with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between attempts in seconds
        backoff: Backoff multiplier for delay
        exceptions: Tuple of exceptions to catch and retry

    Example:
        @retry(max_attempts=3, delay=1.0)
        def fetch_data():
            return api.get_data()
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            current_delay = delay
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay:.2f} seconds..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {e}")

            if last_exception:
                raise last_exception
            raise RuntimeError(f"Failed after {max_attempts} attempts")

        return wrapper

    return decorator


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Async decorator for retrying function calls with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between attempts in seconds
        backoff: Backoff multiplier for delay
        exceptions: Tuple of exceptions to catch and retry

    Example:
        @async_retry(max_attempts=3, delay=1.0)
        async def fetch_data():
            return await api.get_data()
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            current_delay = delay
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay:.2f} seconds..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {e}")

            if last_exception:
                raise last_exception
            raise RuntimeError(f"Failed after {max_attempts} attempts")

        return wrapper

    return decorator


# ==================== Timing Decorators ====================


def measure_time(
    logger: Optional[logging.Logger] = None, log_level: int = logging.INFO
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to measure and log function execution time.

    Args:
        logger: Logger to use (defaults to module logger)
        log_level: Log level for timing messages

    Example:
        @measure_time(logger=logger)
        def process_data(data):
            # Process data
            return result
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        log = logger or logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed_time = time.perf_counter() - start_time
                log.log(log_level, f"{func.__name__} executed in {elapsed_time:.4f} seconds")
                return result
            except Exception as e:
                elapsed_time = time.perf_counter() - start_time
                log.error(f"{func.__name__} failed after {elapsed_time:.4f} seconds: {e}")
                raise

        return wrapper

    return decorator


def async_measure_time(
    logger: Optional[logging.Logger] = None, log_level: int = logging.INFO
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Async decorator to measure and log function execution time.

    Args:
        logger: Logger to use (defaults to module logger)
        log_level: Log level for timing messages

    Example:
        @async_measure_time(logger=logger)
        async def process_data(data):
            # Process data
            return result
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        log = logger or logging.getLogger(func.__module__)

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed_time = time.perf_counter() - start_time
                log.log(log_level, f"{func.__name__} executed in {elapsed_time:.4f} seconds")
                return result
            except Exception as e:
                elapsed_time = time.perf_counter() - start_time
                log.error(f"{func.__name__} failed after {elapsed_time:.4f} seconds: {e}")
                raise

        return wrapper

    return decorator


# ==================== Validation Decorators ====================


def validate_input(
    **validators: Callable[[Any], bool]
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to validate function input parameters.

    Args:
        **validators: Keyword arguments mapping parameter names to validation functions

    Example:
        @validate_input(
            price=lambda x: x > 0,
            quantity=lambda x: x > 0
        )
        def place_order(price: float, quantity: float):
            # Place order
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Get function signature
            import inspect

            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()

            # Validate each parameter
            for param_name, validator in validators.items():
                if param_name in bound.arguments:
                    value = bound.arguments[param_name]
                    if not validator(value):
                        raise ValueError(f"Invalid {param_name}: {value}")

            return func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Caching Decorators ====================


def cache_result(ttl: int = 60) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator for caching function results with TTL.

    Args:
        ttl: Time to live in seconds

    Example:
        @cache_result(ttl=300)
        def expensive_calculation(x):
            return x ** 2
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        cache: Dict[str, tuple[R, float]] = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Create cache key
            key = str(args) + str(sorted(kwargs.items()))

            with lock:
                # Check cache
                if key in cache:
                    result, timestamp = cache[key]
                    if time.time() - timestamp < ttl:
                        return result
                    else:
                        del cache[key]

                # Calculate and cache result
                result = func(*args, **kwargs)
                cache[key] = (result, time.time())

                # Clean old entries
                current_time = time.time()
                expired_keys = [k for k, (_, t) in cache.items() if current_time - t >= ttl]
                for k in expired_keys:
                    del cache[k]

                return result

        # Add cache control methods
        wrapper.cache_clear = lambda: cache.clear()
        wrapper.cache_info = lambda: {"size": len(cache), "keys": list(cache.keys())}

        return wrapper

    return decorator


# ==================== Singleton Decorator ====================


def singleton(cls):
    """
    Decorator to make a class a singleton.

    Example:
        @singleton
        class ConfigManager:
            pass
    """
    instances = {}
    lock = threading.Lock()

    @functools.wraps(cls)
    def wrapper(*args, **kwargs):
        if cls not in instances:
            with lock:
                if cls not in instances:
                    instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    # Add instance control methods
    wrapper.get_instance = lambda: instances.get(cls)
    wrapper.clear_instance = lambda: instances.pop(cls, None)

    return wrapper


# ==================== Synchronization Decorator ====================


def synchronized(
    lock: Optional[threading.Lock] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator for thread-safe method execution.

    Args:
        lock: Lock object (creates new if not provided)

    Example:
        @synchronized()
        def critical_section():
            # Thread-safe code
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        func_lock = lock or threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with func_lock:
                return func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Rate Limiting Decorator ====================


def rate_limit(calls: int = 10, period: float = 1.0) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to limit function call rate.

    Args:
        calls: Maximum number of calls
        period: Time period in seconds

    Example:
        @rate_limit(calls=10, period=1.0)
        def api_call():
            # Rate-limited API call
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        timestamps: list[float] = []
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with lock:
                now = time.time()

                # Remove old timestamps
                timestamps[:] = [t for t in timestamps if now - t < period]

                # Check rate limit
                if len(timestamps) >= calls:
                    sleep_time = period - (now - timestamps[0])
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                # Add current timestamp and execute
                timestamps.append(time.time())

            return func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Exception Handling Decorator ====================


def log_exceptions(
    logger: Optional[logging.Logger] = None,
    message: str = "Exception occurred",
    reraise: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to log exceptions with optional re-raise.

    Args:
        logger: Logger to use
        message: Log message prefix
        reraise: Whether to re-raise the exception

    Example:
        @log_exceptions(logger=logger, reraise=False)
        def risky_operation():
            # Code that might fail
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        log = logger or logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Optional[R]:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log.error(f"{message} in {func.__name__}: {e}", exc_info=True)
                if reraise:
                    raise
                return None

        return wrapper

    return decorator


# ==================== Deprecation Decorator ====================


def deprecated(reason: str = "") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to mark functions as deprecated.

    Args:
        reason: Deprecation reason or migration guide

    Example:
        @deprecated("Use new_function() instead")
        def old_function():
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            import warnings

            message = f"{func.__name__} is deprecated"
            if reason:
                message += f": {reason}"

            warnings.warn(message, category=DeprecationWarning, stacklevel=2)

            return func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Async Caching Decorator ====================


def async_cache_result(
    ttl: int = 60,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Async decorator for caching function results with TTL.

    Args:
        ttl: Time to live in seconds

    Example:
        @async_cache_result(ttl=300)
        async def expensive_calculation(x):
            return x ** 2
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        cache: Dict[str, tuple[R, float]] = {}
        lock = asyncio.Lock()

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Create cache key
            key = str(args) + str(sorted(kwargs.items()))

            async with lock:
                # Check cache
                if key in cache:
                    result, timestamp = cache[key]
                    if time.time() - timestamp < ttl:
                        return result
                    else:
                        del cache[key]

                # Calculate and cache result
                result = await func(*args, **kwargs)
                cache[key] = (result, time.time())

                # Clean old entries
                current_time = time.time()
                expired_keys = [k for k, (_, t) in cache.items() if current_time - t >= ttl]
                for k in expired_keys:
                    del cache[k]

                return result

        # Add cache control methods
        wrapper.cache_clear = lambda: cache.clear()
        wrapper.cache_info = lambda: {"size": len(cache), "keys": list(cache.keys())}

        return wrapper

    return decorator


# ==================== Throttle Decorator ====================


def throttle(min_interval: float = 1.0) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to throttle function calls to a minimum interval.

    Args:
        min_interval: Minimum time between calls in seconds

    Example:
        @throttle(min_interval=2.0)
        def update_display():
            # Update display at most every 2 seconds
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        last_call_time = [0.0]
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Optional[R]:
            with lock:
                now = time.time()
                time_since_last_call = now - last_call_time[0]

                if time_since_last_call < min_interval:
                    return None

                last_call_time[0] = now

            return func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Timeout Decorator ====================


def timeout(seconds: float) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to add timeout to function execution.

    Args:
        seconds: Timeout in seconds

    Example:
        @timeout(seconds=5.0)
        def slow_operation():
            # Operation that should complete within 5 seconds
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            import signal

            def timeout_handler(signum, frame):
                raise TimeoutError(f"{func.__name__} timed out after {seconds} seconds")

            # Set the timeout handler
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(int(seconds))

            try:
                result = func(*args, **kwargs)
            finally:
                # Cancel the alarm and restore the old handler
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            return result

        return wrapper

    return decorator


# ==================== Async Timeout Decorator ====================


def async_timeout(
    seconds: float,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Async decorator to add timeout to function execution.

    Args:
        seconds: Timeout in seconds

    Example:
        @async_timeout(seconds=5.0)
        async def slow_operation():
            # Async operation that should complete within 5 seconds
            pass
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                raise TimeoutError(f"{func.__name__} timed out after {seconds} seconds")

        return wrapper

    return decorator


# ==================== Performance Profiling Decorator ====================


def profile(output_file: Optional[str] = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator to profile function execution.

    Args:
        output_file: Optional file to save profiling results

    Example:
        @profile(output_file="profile_results.txt")
        def complex_operation():
            # Code to profile
            pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            import cProfile
            import io
            import pstats

            profiler = cProfile.Profile()
            profiler.enable()

            try:
                result = func(*args, **kwargs)
            finally:
                profiler.disable()

                # Generate profiling report
                stream = io.StringIO()
                stats = pstats.Stats(profiler, stream=stream)
                stats.sort_stats(pstats.SortKey.CUMULATIVE)
                stats.print_stats()

                profile_output = stream.getvalue()

                # Log or save the results
                if output_file:
                    with open(output_file, "w") as f:
                        f.write(profile_output)
                else:
                    logger.info(f"Profile for {func.__name__}:\n{profile_output}")

            return result

        return wrapper

    return decorator
