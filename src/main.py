from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routes.health import router as health_router
from src.api.routes.tasks import router as tasks_router
from src.storage.cache import close_redis
from src.storage.database import init_db
from src.storage.vector import init_vector_tables

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize storage on startup, close on shutdown."""
    await init_db()
    await init_vector_tables()

    # Index toy-repo for RAG (non-blocking, logs results)
    try:
        from src.retrieval.indexer import index_all
        result = await index_all()
        logger.info("RAG indexing complete: %s", result)
    except Exception:
        logger.exception("RAG indexing failed (non-fatal)")

    yield
    await close_redis()


app = FastAPI(
    title="AgentForge",
    description="Planner-Generator-Evaluator code assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(tasks_router, prefix="/api/v1")
