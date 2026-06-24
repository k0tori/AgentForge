from __future__ import annotations

from src.retrieval.search import search
from src.tools.registry import registry


async def retrieval_search(query: str, top_k: int = 5) -> list[dict]:
    """RAG-as-Tool: search code and conventions for relevant context.

    Searches indexed code chunks and best practices using vector similarity.
    """
    return await search(query, top_k=top_k)


registry.register(
    name="retrieval_search",
    func=retrieval_search,
    description="Search code repository and conventions for relevant context using RAG.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
