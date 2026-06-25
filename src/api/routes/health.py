from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from src.config import settings
from src.storage.database import async_session_factory

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
async def health_check() -> dict:
    """Health check: verify PostgreSQL and Redis connectivity."""
    result: dict = {"status": "healthy", "postgres": False, "redis": False}

    # Check PostgreSQL
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        result["postgres"] = True
    except Exception:
        result["status"] = "degraded"

    # Check Redis (async)
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        await r.close()
        result["redis"] = True
    except Exception:
        result["status"] = "degraded"

    return result
