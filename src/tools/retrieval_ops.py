from __future__ import annotations

from src.tools.registry import registry


def retrieval_search(query: str, top_k: int = 5) -> list[dict]:
    """RAG-as-Tool: search code and conventions for relevant context.

    STUB: Full implementation deferred to Phase 4.
    Returns empty list for now.
    """
    return []


registry.register(
    name="retrieval_search",
    func=retrieval_search,
    description="Search code repository and conventions for relevant context using RAG.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
