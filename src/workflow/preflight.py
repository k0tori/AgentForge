"""Required checks before a PGE run, and optional retrieval that must not block it."""
from __future__ import annotations

import asyncio

from src.config import settings
from src.harness.sandbox import runtime as sandbox_runtime
from src.llm.errors import MESSAGES
from src.retrieval.indexer import index_all, storage_available
from src.workflow.failures import TaskFailureError


def require_pge_ready() -> None:
    """Raise when the default PGE path cannot run safely.

    Host mode is the explicit opt-in that skips the Docker checks.
    Retrieval is not checked here.
    """
    if not settings.DEEPSEEK_API_KEY.strip():
        raise TaskFailureError("missing_model_config")
    mode = settings.SANDBOX_MODE.strip().lower()
    if mode == "host":
        return
    if mode != "docker":
        raise TaskFailureError("sandbox_unavailable", MESSAGES["sandbox_unavailable"])
    sandbox_runtime.last_sandbox_block_reason = ""
    if not sandbox_runtime.docker_ready():
        raise TaskFailureError(
            "sandbox_unavailable",
            sandbox_runtime.last_sandbox_block_reason or None,
        )
    if not sandbox_runtime.image_present(settings.SANDBOX_IMAGE):
        raise TaskFailureError("sandbox_image_missing")


async def assess_retrieval(codebase_path: str) -> list[dict[str, str]]:
    """Report retrieval degradation. Never raises, and does not include the database URL."""
    note = {"code": "retrieval_unavailable", "message": MESSAGES["retrieval_unavailable"]}
    try:
        available = await asyncio.wait_for(storage_available(), timeout=3)
    except TimeoutError:
        return [note]
    if not available:
        return [note]
    try:
        await asyncio.wait_for(index_all(codebase_path), timeout=settings.RETRIEVAL_TIMEOUT_SECONDS)
    except Exception:
        return [note]
    return []
