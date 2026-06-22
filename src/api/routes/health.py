from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from src.storage.cache import get_redis
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

    # Check Redis
    try:
        r = await get_redis()
        await r.ping()
        result["redis"] = True
    except Exception:
        result["status"] = "degraded"

    return result
