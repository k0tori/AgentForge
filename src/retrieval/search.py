"""Retrieval search: embed query → pgvector search → merge results.

Searches both code_embeddings and best_practice_embeddings tables,
merges results sorted by cosine similarity.
"""
from __future__ import annotations

import logging

from src.retrieval.embeddings import embedding_generator
from src.storage.database import async_session_factory
from src.storage.vector import search_similar

logger = logging.getLogger(__name__)


async def search(query: str, top_k: int = 5) -> list[dict]:
    """Search code and best practices for relevant context.

    Args:
        query: Natural language search query.
        top_k: Maximum number of results to return.

    Returns:
        List of result dicts sorted by similarity (descending).
        Each dict contains: id, content, similarity, chunk_type, chunk_name,
        file_path, source_table.
    """
    # 1. Generate query embedding
    query_embedding = embedding_generator.generate_embedding(query)

    # 2. Search both tables
    async with async_session_factory() as session:
        code_results = await search_similar(
            session, "code_embeddings", query_embedding, top_k=top_k
        )
        practice_results = await search_similar(
            session, "best_practice_embeddings", query_embedding, top_k=top_k
        )

    # 3. Normalize and tag results with source table
    normalized: list[dict] = []
    for r in code_results:
        normalized.append({
            "id": r["id"],
            "content": r["content"],
            "similarity": r["similarity"],
            "chunk_type": r.get("chunk_type"),
            "chunk_name": r.get("chunk_name"),
            "file_path": r.get("file_path"),
            "source_table": "code_embeddings",
        })
    for r in practice_results:
        normalized.append({
            "id": r["id"],
            "content": r["content"],
            "similarity": r["similarity"],
            "chunk_type": "best_practice",
            "chunk_name": r.get("title"),
            "file_path": r.get("source"),
            "source_table": "best_practice_embeddings",
        })

    # 4. Sort by similarity descending, return top_k
    normalized.sort(key=lambda x: x["similarity"], reverse=True)
    results = normalized[:top_k]

    logger.info("Search '%s' returned %d results (code=%d, practice=%d)",
                query[:50], len(results), len(code_results), len(practice_results))
    return results
