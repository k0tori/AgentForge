from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.database import async_session_factory

logger = logging.getLogger(__name__)

# Allowlist of tables that accept embeddings — prevents SQL injection via table name
ALLOWED_EMBEDDING_TABLES = frozenset({"code_embeddings", "best_practice_embeddings"})


async def init_vector_tables() -> None:
    """Create pgvector tables and indexes."""
    async with async_session_factory() as session:
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS code_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                project_path TEXT NOT NULL,
                file_path TEXT NOT NULL,
                chunk_type VARCHAR(20) NOT NULL,
                chunk_name TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding VECTOR(1536),
                metadata JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS best_practice_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                category VARCHAR(50) NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding VECTOR(1536),
                source TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        # Create indexes (ignore if already exists)
        try:
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_code_embeddings_ivfflat "
                "ON code_embeddings USING ivfflat (embedding vector_cosine_ops)"
            ))
        except Exception as e:
            logger.debug("Skipping ivfflat index for code_embeddings (may need data first): %s", e)
        try:
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_best_practice_ivfflat "
                "ON best_practice_embeddings USING ivfflat (embedding vector_cosine_ops)"
            ))
        except Exception as e:
            logger.debug("Skipping ivfflat index for best_practice_embeddings (may need data first): %s", e)
        await session.commit()


def _validate_table_name(table: str) -> None:
    """Validate table name against allowlist to prevent SQL injection."""
    if table not in ALLOWED_EMBEDDING_TABLES:
        raise ValueError(
            f"Invalid table name: '{table}'. "
            f"Allowed tables: {ALLOWED_EMBEDDING_TABLES}"
        )


async def insert_embedding(
    session: AsyncSession,
    table: str,
    content: str,
    embedding: list[float],
    metadata: dict | None = None,
    **kwargs,
) -> None:
    """Insert an embedding into the specified table."""
    _validate_table_name(table)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    sql = (
        f"INSERT INTO {table} (content, embedding, metadata) "
        "VALUES (:content, :embedding::vector, :metadata::jsonb)"
    )
    await session.execute(
        text(sql),
        {"content": content, "embedding": embedding_str, "metadata": "{}"},
    )


async def search_similar(
    session: AsyncSession,
    table: str,
    query_embedding: list[float],
    top_k: int = 5,
    filter_metadata: dict | None = None,
) -> list[dict]:
    """Search for similar embeddings using cosine distance."""
    _validate_table_name(table)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    result = await session.execute(
        text(f"""
            SELECT id, content, metadata, 1 - (embedding <=> :query::vector) as similarity
            FROM {table}
            ORDER BY embedding <=> :query::vector
            LIMIT :limit
        """),
        {"query": embedding_str, "limit": top_k},
    )
    rows = result.fetchall()
    return [
        {"id": str(row[0]), "content": row[1], "metadata": row[2], "similarity": row[3]}
        for row in rows
    ]
