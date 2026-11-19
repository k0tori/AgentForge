from __future__ import annotations

import os

from src.config import settings
from src.retrieval.context import project_path_var
from src.retrieval.search import search
from src.tools.registry import registry


async def retrieval_search(query: str, top_k: int = 5) -> list[dict]:
    """RAG-as-Tool: search code and conventions for relevant context.

    Searches indexed code chunks and best practices using vector similarity.
    Code hits are limited to the task codebase when one is active.
    """
    project_path = project_path_var.get() or os.path.abspath(settings.TOY_REPO_PATH)
    return await search(query, top_k=top_k, project_path=project_path)


registry.register(
    name="retrieval_search",
    func=retrieval_search,
    description="Search code repository and conventions for relevant context using RAG.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
