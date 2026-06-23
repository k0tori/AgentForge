from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from src.config import settings

_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get or create the Redis client singleton."""
    global _redis
    if _redis is None:
        # Parse URL manually for better Windows compatibility
        from urllib.parse import urlparse

        parsed = urlparse(settings.REDIS_URL)
        _redis = redis.Redis(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def cache_get(key: str) -> Any | None:
    """Get a deserialized JSON value from Redis cache.

    Args:
        key: Redis key to retrieve.

    Returns:
        Deserialized value if key exists, None otherwise.
    """
    r = await get_redis()
    val = await r.get(key)
    if val is None:
        return None
    return json.loads(val)


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    """Set a JSON-serialized value in Redis cache with TTL.

    Args:
        key: Redis key to set.
        value: JSON-serializable value to store.
        ttl: Time-to-live in seconds (default 3600).
    """
    r = await get_redis()
    await r.set(key, json.dumps(value), ex=ttl)


async def cache_delete(key: str) -> None:
    """Delete a key from Redis cache."""
    r = await get_redis()
    await r.delete(key)


async def get_task_state(task_id: str) -> dict | None:
    """Get task state from cache (TTL 24h)."""
    return await cache_get(f"task:{task_id}:state")


async def set_task_state(task_id: str, state: dict) -> None:
    """Set task state in cache (TTL 24h)."""
    await cache_set(f"task:{task_id}:state", state, ttl=86400)


async def get_retrieval_cache(query_hash: str) -> list[dict] | None:
    """Get cached retrieval results (TTL 30min)."""
    return await cache_get(f"retrieval:cache:{query_hash}")


async def set_retrieval_cache(query_hash: str, results: list[dict]) -> None:
    """Cache retrieval results (TTL 30min)."""
    await cache_set(f"retrieval:cache:{query_hash}", results, ttl=1800)
