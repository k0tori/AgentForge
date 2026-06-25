from __future__ import annotations

import json
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
                embedding VECTOR(384),
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
                embedding VECTOR(384),
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
    """Insert an embedding into the specified table.

    Extra columns (project_path, file_path, chunk_type, chunk_name for code_embeddings;
    category, title, source for best_practice_embeddings) can be passed via kwargs.
    """
    _validate_table_name(table)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    # Build column/value lists dynamically from kwargs
    columns = ["content", "embedding", "metadata"]
    values = [":content", ":embedding::vector", ":metadata::jsonb"]
    params: dict = {
        "content": content,
        "embedding": embedding_str,
        "metadata": "{}" if metadata is None else json.dumps(metadata),
    }
    for key, val in kwargs.items():
        columns.append(key)
        values.append(f":{key}")
        params[key] = val

    col_str = ", ".join(columns)
    val_str = ", ".join(values)
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({val_str})"
    await session.execute(text(sql), params)


async def delete_by_project_path(
    session: AsyncSession,
    project_path: str,
) -> int:
    """Delete all code_embeddings for a given project path. Returns deleted count."""
    result = await session.execute(
        text("DELETE FROM code_embeddings WHERE project_path = :path"),
        {"path": project_path},
    )
    return result.rowcount


async def delete_best_practice_embeddings(
    session: AsyncSession,
) -> int:
    """Delete all best_practice_embeddings. Returns deleted count."""
    result = await session.execute(text("DELETE FROM best_practice_embeddings"))
    return result.rowcount


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
            SELECT id, content, metadata,
                   file_path, chunk_type, chunk_name, project_path, category, title, source,
                   1 - (embedding <=> :query::vector) as similarity
            FROM {table}
            ORDER BY embedding <=> :query::vector
            LIMIT :limit
        """),
        {"query": embedding_str, "limit": top_k},
    )
    rows = result.fetchall()
    return [
        {
            "id": str(row[0]),
            "content": row[1],
            "metadata": row[2],
            "file_path": row[3],
            "chunk_type": row[4],
            "chunk_name": row[5],
            "project_path": row[6],
            "category": row[7],
            "title": row[8],
            "source": row[9],
            "similarity": row[10],
        }
        for row in rows
    ]
