"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : cache
    Project     : Project-103
    Description : Redis cache manager for data storage.

    SPDX-License-Identifier: AGPL-3.0

"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages Redis cache for market data."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        prefix: str = "trading_bot",
    ):
        """
        Initialize cache manager.

        Args:
            host: Redis host
            port: Redis port
            db: Database number
            password: Redis password
            prefix: Key prefix
        """
        self.prefix = prefix

        try:
            self.redis = redis.Redis(
                host=host, port=port, db=db, password=password, decode_responses=True
            )
            self.redis.ping()
            logger.info(f"Connected to Redis at {host}:{port}")
        except RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis = None

    def _make_key(self, key: str) -> str:
        """Create prefixed key."""
        return f"{self.prefix}:{key}"

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds

        Returns:
            Success status
        """
        if not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            json_value = json.dumps(value)

            if ttl:
                return self.redis.setex(full_key, ttl, json_value)
            else:
                return self.redis.set(full_key, json_value)

        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None
        """
        if not self.redis:
            return None

        try:
            full_key = self._make_key(key)
            value = self.redis.get(full_key)

            if value:
                return json.loads(value)
            return None

        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            return self.redis.delete(full_key) > 0
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            return self.redis.exists(full_key) > 0
        except Exception as e:
            logger.error(f"Cache exists error: {e}")
            return False

    def push_list(self, key: str, value: Any, max_length: Optional[int] = None) -> bool:
        """Push value to list."""
        if not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            json_value = json.dumps(value)

            # Push to list
            self.redis.lpush(full_key, json_value)

            # Trim if needed
            if max_length:
                self.redis.ltrim(full_key, 0, max_length - 1)

            return True

        except Exception as e:
            logger.error(f"Cache push error: {e}")
            return False

    def get_list(self, key: str, start: int = 0, end: int = -1) -> List[Any]:
        """Get list from cache."""
        if not self.redis:
            return []

        try:
            full_key = self._make_key(key)
            values = self.redis.lrange(full_key, start, end)

            return [json.loads(v) for v in values]

        except Exception as e:
            logger.error(f"Cache get list error: {e}")
            return []

    def flush(self) -> bool:
        """Flush all cache data."""
        if not self.redis:
            return False

        try:
            pattern = f"{self.prefix}:*"
            keys = self.redis.keys(pattern)

            if keys:
                self.redis.delete(*keys)

            logger.info(f"Flushed {len(keys)} cache keys")
            return True

        except Exception as e:
            logger.error(f"Cache flush error: {e}")
            return False
