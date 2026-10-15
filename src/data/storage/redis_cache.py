"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : redis_cache
    Project     : Project-103
    Description : Async Redis cache for market data and bot state.

    SPDX-License-Identifier: AGPL-3.0

"""

import json
import logging
from typing import Any, Dict, List, Optional

try:
    import redis.asyncio as aioredis

    AIOREDIS_AVAILABLE = True
except ImportError:
    AIOREDIS_AVAILABLE = False
    aioredis = None

logger = logging.getLogger(__name__)


class RedisCache:
    """Async Redis cache manager"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        prefix: str = "trading_bot",
    ):
        """
        Initialize Redis cache

        Args:
            host: Redis host
            port: Redis port
            db: Database number
            password: Redis password
            prefix: Key prefix
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.prefix = prefix
        self.redis = None
        self.connected = False

        if not AIOREDIS_AVAILABLE:
            logger.warning("aioredis not installed. Install with: pip install aioredis")

    async def connect(self) -> bool:
        """Connect to Redis"""
        if not AIOREDIS_AVAILABLE:
            logger.error("aioredis not available")
            return False

        try:
            # Create redis URL
            redis_url = f"redis://"
            if self.password:
                redis_url += f":{self.password}@"
            redis_url += f"{self.host}:{self.port}/{self.db}"

            self.redis = await aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)

            # Test connection
            await self.redis.ping()
            self.connected = True
            logger.info(f"Connected to Redis at {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.connected = False
            return False

    async def close(self) -> None:
        """Close Redis connection"""
        if self.redis:
            # close() is deprecated since redis 5.0.1 in favour of aclose().
            # getattr — in case the library is rolled back to a version without it.
            closer = getattr(self.redis, "aclose", None) or self.redis.close
            await closer()
            self.connected = False
            logger.info("Redis connection closed")

    def _make_key(self, key: str) -> str:
        """Create prefixed key"""
        return f"{self.prefix}:{key}"

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds

        Returns:
            bool: Success status
        """
        if not self.connected or not self.redis:
            return False

        try:
            full_key = self._make_key(key)

            # Serialize value
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            elif not isinstance(value, str):
                value = str(value)

            # Set with or without TTL
            if ttl:
                await self.redis.setex(full_key, ttl, value)
            else:
                await self.redis.set(full_key, value)

            return True

        except Exception as e:
            logger.error(f"Failed to set cache key {key}: {e}")
            return False

    async def get(self, key: str, default: Any = None) -> Optional[Any]:
        """
        Get value from cache

        Args:
            key: Cache key
            default: Default value if not found

        Returns:
            Optional[Any]: Cached value or default
        """
        if not self.connected or not self.redis:
            return default

        try:
            full_key = self._make_key(key)
            value = await self.redis.get(full_key)

            if value is None:
                return default

            # Try to deserialize JSON
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value

        except Exception as e:
            logger.error(f"Failed to get cache key {key}: {e}")
            return default

    async def delete(self, key: str) -> bool:
        """
        Delete key from cache

        Args:
            key: Cache key

        Returns:
            bool: Success status
        """
        if not self.connected or not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            await self.redis.delete(full_key)
            return True

        except Exception as e:
            logger.error(f"Failed to delete cache key {key}: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """
        Check if key exists

        Args:
            key: Cache key

        Returns:
            bool: True if exists
        """
        if not self.connected or not self.redis:
            return False

        try:
            full_key = self._make_key(key)
            return await self.redis.exists(full_key) > 0

        except Exception as e:
            logger.error(f"Failed to check key {key}: {e}")
            return False

    async def ping(self) -> bool:
        """
        Ping Redis server

        Returns:
            bool: True if responsive
        """
        if not self.connected or not self.redis:
            return False

        try:
            await self.redis.ping()
            return True
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    async def flush(self) -> bool:
        """
        Flush all keys with prefix

        Returns:
            bool: Success status
        """
        if not self.connected or not self.redis:
            return False

        try:
            pattern = self._make_key("*")
            keys = await self.redis.keys(pattern)

            if keys:
                await self.redis.delete(*keys)

            logger.info(f"Flushed {len(keys)} keys from cache")
            return True

        except Exception as e:
            logger.error(f"Failed to flush cache: {e}")
            return False

    async def get_ttl(self, key: str) -> Optional[int]:
        """
        Get TTL for key

        Args:
            key: Cache key

        Returns:
            Optional[int]: TTL in seconds or None
        """
        if not self.connected or not self.redis:
            return None

        try:
            full_key = self._make_key(key)
            ttl = await self.redis.ttl(full_key)
            return ttl if ttl > 0 else None

        except Exception as e:
            logger.error(f"Failed to get TTL for key {key}: {e}")
            return None

    async def set_many(self, mapping: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Set multiple key-value pairs

        Args:
            mapping: Dictionary of key-value pairs
            ttl: Time to live in seconds

        Returns:
            bool: Success status
        """
        if not self.connected or not self.redis:
            return False

        try:
            for key, value in mapping.items():
                await self.set(key, value, ttl)
            return True

        except Exception as e:
            logger.error(f"Failed to set multiple keys: {e}")
            return False

    async def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """
        Get multiple values

        Args:
            keys: List of keys

        Returns:
            Dict[str, Any]: Dictionary of key-value pairs
        """
        result = {}

        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value

        return result

    async def incr(self, key: str, amount: int = 1) -> Optional[int]:
        """
        Increment counter

        Args:
            key: Cache key
            amount: Increment amount

        Returns:
            Optional[int]: New value
        """
        if not self.connected or not self.redis:
            return None

        try:
            full_key = self._make_key(key)
            return await self.redis.incrby(full_key, amount)

        except Exception as e:
            logger.error(f"Failed to increment key {key}: {e}")
            return None

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics

        Returns:
            Dict[str, Any]: Cache statistics
        """
        if not self.connected or not self.redis:
            return {}

        try:
            info = await self.redis.info()

            return {
                "connected": self.connected,
                "host": self.host,
                "port": self.port,
                "db": self.db,
                "used_memory": info.get("used_memory_human", "N/A"),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": await self.redis.dbsize(),
            }

        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {"connected": False}

    # ==================== List Operations ====================

    async def rpush(self, key: str, value: Any) -> Optional[int]:
        """
        Push value to the right of a list.

        Args:
            key: List key
            value: Value to push

        Returns:
            Optional[int]: New list length
        """
        if not self.connected or not self.redis:
            return None

        try:
            full_key = self._make_key(key)

            # Serialize value
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            elif not isinstance(value, str):
                value = str(value)

            return await self.redis.rpush(full_key, value)

        except Exception as e:
            logger.error(f"Failed to rpush to {key}: {e}")
            return None

    async def lpop(self, key: str) -> Optional[Any]:
        """
        Pop value from the left of a list.

        Args:
            key: List key

        Returns:
            Optional[Any]: Popped value or None
        """
        if not self.connected or not self.redis:
            return None

        try:
            full_key = self._make_key(key)
            value = await self.redis.lpop(full_key)

            if value is None:
                return None

            # Try to deserialize JSON
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value

        except Exception as e:
            logger.error(f"Failed to lpop from {key}: {e}")
            return None

    async def llen(self, key: str) -> int:
        """
        Get length of a list.

        Args:
            key: List key

        Returns:
            int: List length
        """
        if not self.connected or not self.redis:
            return 0

        try:
            full_key = self._make_key(key)
            return await self.redis.llen(full_key)

        except Exception as e:
            logger.error(f"Failed to get length of {key}: {e}")
            return 0

    async def lrange(self, key: str, start: int = 0, end: int = -1) -> List[Any]:
        """
        Get range of values from a list.

        Args:
            key: List key
            start: Start index
            end: End index (-1 for all)

        Returns:
            List[Any]: List of values
        """
        if not self.connected or not self.redis:
            return []

        try:
            full_key = self._make_key(key)
            values = await self.redis.lrange(full_key, start, end)

            result = []
            for value in values:
                try:
                    result.append(json.loads(value))
                except (json.JSONDecodeError, TypeError):
                    result.append(value)

            return result

        except Exception as e:
            logger.error(f"Failed to lrange from {key}: {e}")
            return []
