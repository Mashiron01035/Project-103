"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : health_check
    Project     : Project-103
    Description : Health check utilities for Docker container monitoring.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def check_health() -> bool:
    """
    Check if the trading bot is healthy.

    Used by Docker HEALTHCHECK to determine container health status.

    Returns:
        True if healthy, False otherwise
    """
    checks = {
        "process_running": _check_process_running(),
        "memory_ok": _check_memory(),
    }

    # Optionally check external services
    if os.environ.get("DATABASE_URL"):
        checks["database"] = _check_database()

    if os.environ.get("REDIS_URL"):
        checks["redis"] = _check_redis()

    # Log health status
    all_healthy = all(checks.values())
    if not all_healthy:
        failed = [k for k, v in checks.items() if not v]
        logger.warning(f"Health check failed: {failed}")

    return all_healthy


def _check_process_running() -> bool:
    """Check if the main process is running."""
    # Simple check - if we can execute this, process is running
    return True


def _check_memory() -> bool:
    """Check if memory usage is acceptable."""
    try:
        import psutil

        process = psutil.Process()
        memory_percent = process.memory_percent()
        # Fail if using more than 90% of available memory
        return memory_percent < 90
    except ImportError:
        # psutil not available, skip check
        return True
    except Exception as e:
        logger.debug(f"Memory check failed: {e}")
        return True


def _check_database() -> bool:
    """Check database connectivity."""
    try:
        import asyncio

        import asyncpg

        async def check():
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return True

            try:
                conn = await asyncio.wait_for(asyncpg.connect(db_url), timeout=5.0)
                await conn.execute("SELECT 1")
                await conn.close()
                return True
            except Exception as e:
                logger.debug(f"Database connection check failed: {e}")
                return False

        return asyncio.run(check())
    except Exception as e:
        # If we can't check, assume it's OK
        logger.debug(f"Database check setup failed: {e}")
        return True


def _check_redis() -> bool:
    """Check Redis connectivity."""
    try:
        import redis

        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            return True

        client = redis.from_url(redis_url, socket_timeout=5)
        client.ping()
        return True
    except Exception as e:
        logger.debug(f"Redis check failed: {e}")
        return False


def get_health_status() -> Dict[str, Any]:
    """
    Get detailed health status.

    Returns:
        Dictionary with health check results
    """
    status = {
        "healthy": check_health(),
        "checks": {
            "process": _check_process_running(),
            "memory": _check_memory(),
        },
    }

    if os.environ.get("DATABASE_URL"):
        status["checks"]["database"] = _check_database()

    if os.environ.get("REDIS_URL"):
        status["checks"]["redis"] = _check_redis()

    return status


if __name__ == "__main__":
    # When called directly, print health status and exit with appropriate code
    import sys

    healthy = check_health()
    print(f"Health: {'OK' if healthy else 'FAILED'}")
    sys.exit(0 if healthy else 1)
