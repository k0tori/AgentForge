"""Indexer: orchestrates chunking → embedding → storage for RAG.

Handles incremental indexing by clearing old records before re-inserting.
Indexes Python code into code_embeddings and CONVENTIONS.md into best_practice_embeddings.
"""
from __future__ import annotations

import logging
import os

from src.config import settings
from src.retrieval.chunking import chunk_directory
from src.retrieval.embeddings import embedding_generator
from src.storage.database import async_session_factory
from src.storage.vector import delete_best_practice_embeddings, delete_by_project_path, insert_embedding

logger = logging.getLogger(__name__)


async def index_directory(directory: str | None = None, project_path: str | None = None) -> int:
    """Index all Python files in a directory into the vector store.

    Args:
        directory: Path to scan for .py files. Defaults to settings.TOY_REPO_PATH.
        project_path: Identifier stored in DB for filtering. Defaults to directory.

    Returns:
        Number of chunks indexed.
    """
    directory = os.path.abspath(directory or settings.TOY_REPO_PATH)
    project_path = project_path or directory

    logger.info("Indexing directory: %s", directory)

    # 1. Chunk all Python files
    chunks = chunk_directory(directory)
    if not chunks:
        logger.warning("No chunks found in %s", directory)
        return 0

    logger.info("Extracted %d chunks from %s", len(chunks), directory)

    # 2. Generate embeddings in batch
    texts = [c.content for c in chunks]
    embeddings = embedding_generator.generate_embeddings(texts)
    logger.info("Generated %d embeddings", len(embeddings))

    # 3. Clear old records and insert new ones
    async with async_session_factory() as session:
        deleted = await delete_by_project_path(session, project_path)
        if deleted:
            logger.info("Cleared %d old embeddings for %s", deleted, project_path)

        for chunk, embedding in zip(chunks, embeddings):
            await insert_embedding(
                session,
                table="code_embeddings",
                content=chunk.content,
                embedding=embedding,
                metadata=chunk.metadata,
                project_path=project_path,
                file_path=chunk.file_path,
                chunk_type=chunk.chunk_type,
                chunk_name=chunk.chunk_name,
            )

        await session.commit()

    logger.info("Indexed %d chunks into code_embeddings", len(chunks))
    return len(chunks)


async def index_conventions(directory: str | None = None) -> int:
    """Index CONVENTIONS.md into best_practice_embeddings.

    Args:
        directory: Directory containing CONVENTIONS.md. Defaults to settings.TOY_REPO_PATH.

    Returns:
        Number of entries indexed (0 or 1).
    """
    directory = os.path.abspath(directory or settings.TOY_REPO_PATH)
    conventions_path = os.path.join(directory, "CONVENTIONS.md")

    if not os.path.isfile(conventions_path):
        logger.warning("CONVENTIONS.md not found at %s", conventions_path)
        return 0

    try:
        with open(conventions_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        logger.exception("Failed to read CONVENTIONS.md")
        return 0

    embedding = embedding_generator.generate_embedding(content)

    async with async_session_factory() as session:
        await delete_best_practice_embeddings(session)
        await insert_embedding(
            session,
            table="best_practice_embeddings",
            content=content,
            embedding=embedding,
            category="architecture",
            title="Project Coding Conventions",
            source="CONVENTIONS.md",
        )
        await session.commit()

    logger.info("Indexed CONVENTIONS.md into best_practice_embeddings")
    return 1


async def index_all(directory: str | None = None) -> dict[str, int]:
    """Run full indexing: code chunks + conventions.

    Args:
        directory: Root directory to index. Defaults to settings.TOY_REPO_PATH.

    Returns:
        Dict with counts: {"code_chunks": N, "conventions": M}.
    """
    code_count = await index_directory(directory)
    conv_count = await index_conventions(directory)
    return {"code_chunks": code_count, "conventions": conv_count}
