from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from src.database import init_db
from src.exceptions import register_exception_handlers
from src.routers.note import router as note_router
from src.routers.user import router as user_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(
    title="Toy-Repo",
    description="Minimal FastAPI service for AgentForge demo",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(user_router, prefix="/api/v1")
app.include_router(note_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
