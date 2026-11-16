from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.storage.database import async_session_factory

logger = logging.getLogger(__name__)

# Allowlist of tables that accept embeddings — prevents SQL injection via table name
ALLOWED_EMBEDDING_TABLES = frozenset({"code_embeddings", "best_practice_embeddings"})

# Columns that actually exist. Search and insert must stay inside these sets.
CODE_TABLE_COLUMNS = frozenset({
    "id", "project_path", "file_path", "chunk_type", "chunk_name",
    "content", "embedding", "metadata", "created_at", "updated_at",
})
PRACTICE_TABLE_COLUMNS = frozenset({
    "id", "category", "title", "content", "embedding", "source", "created_at",
})
CODE_INSERT_FIELDS = frozenset({"project_path", "file_path", "chunk_type", "chunk_name"})
PRACTICE_INSERT_FIELDS = frozenset({"category", "title", "source"})


def embedding_type_ddl() -> str:
    """pgvector column type from the configured dimension."""
    dim = int(settings.EMBEDDING_DIM)
    if not 1 <= dim <= 4096:
        raise ValueError(f"EMBEDDING_DIM out of range: {dim}")
    return f"VECTOR({dim})"


def insert_statement(table: str, extra: dict) -> tuple[str, list[str]]:
    """Build a parameterized INSERT. Extra keys must be real columns of `table`.

    best_practice_embeddings has no metadata column; code_embeddings does.
    """
    _validate_table_name(table)
    allowed = CODE_INSERT_FIELDS if table == "code_embeddings" else PRACTICE_INSERT_FIELDS
    unknown = set(extra) - allowed
    if unknown:
        raise ValueError(f"Unexpected columns for {table}: {sorted(unknown)}")

    if table == "code_embeddings":
        columns = ["content", "embedding", "metadata"]
        values = [":content", ":embedding::vector", ":metadata::jsonb"]
    else:
        columns = ["content", "embedding"]
        values = [":content", ":embedding::vector"]

    for key in extra:
        columns.append(key)
        values.append(f":{key}")

    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(values)})"
    return sql, columns


def build_search_sql(table: str, *, filter_project: bool) -> str:
    """SELECT only columns that exist on `table`, filling the other shape with NULL."""
    _validate_table_name(table)
    if table == "code_embeddings":
        where = "WHERE project_path = :project_path" if filter_project else ""
        return f"""
            SELECT id, content, metadata, file_path, chunk_type, chunk_name, project_path,
                   NULL::varchar AS category, NULL::text AS title, NULL::text AS source,
                   1 - (embedding <=> :query::vector) AS similarity
            FROM code_embeddings
            {where}
            ORDER BY embedding <=> :query::vector
            LIMIT :limit
        """
    return """
        SELECT id, content, NULL::jsonb AS metadata, NULL::text AS file_path,
               NULL::varchar AS chunk_type, NULL::text AS chunk_name, NULL::text AS project_path,
               category, title, source,
               1 - (embedding <=> :query::vector) AS similarity
        FROM best_practice_embeddings
        ORDER BY embedding <=> :query::vector
        LIMIT :limit
    """


async def _try_execute(session: AsyncSession, sql: str, label: str) -> None:
    """Run optional DDL in a savepoint so a failure does not abort table creation."""
    await session.execute(text("SAVEPOINT vec_idx"))
    try:
        await session.execute(text(sql))
    except Exception as exc:
        await session.execute(text("ROLLBACK TO SAVEPOINT vec_idx"))
        logger.debug("Skipping %s: %s", label, exc)
    else:
        await session.execute(text("RELEASE SAVEPOINT vec_idx"))


async def init_vector_tables() -> None:
    """Create pgvector tables and indexes."""
    vector_type = embedding_type_ddl()
    async with async_session_factory() as session:
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await session.execute(text(f"""
            CREATE TABLE IF NOT EXISTS code_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                project_path TEXT NOT NULL,
                file_path TEXT NOT NULL,
                chunk_type VARCHAR(20) NOT NULL,
                chunk_name TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding {vector_type},
                metadata JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await session.execute(text(f"""
            CREATE TABLE IF NOT EXISTS best_practice_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                category VARCHAR(50) NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding {vector_type},
                source TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await _try_execute(
            session,
            "CREATE INDEX IF NOT EXISTS idx_code_embeddings_ivfflat "
            "ON code_embeddings USING ivfflat (embedding vector_cosine_ops)",
            "ivfflat index for code_embeddings",
        )
        await _try_execute(
            session,
            "CREATE INDEX IF NOT EXISTS idx_best_practice_ivfflat "
            "ON best_practice_embeddings USING ivfflat (embedding vector_cosine_ops)",
            "ivfflat index for best_practice_embeddings",
        )
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
    expected_dim = int(settings.EMBEDDING_DIM)
    if len(embedding) != expected_dim:
        raise ValueError(f"Embedding dim {len(embedding)} does not match EMBEDDING_DIM {expected_dim}")
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    sql, columns = insert_statement(table, kwargs)
    params: dict = {"content": content, "embedding": embedding_str}
    if "metadata" in columns:
        params["metadata"] = "{}" if metadata is None else json.dumps(metadata)
    params.update(kwargs)
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
    source: str | None = None,
) -> int:
    """Delete best_practice_embeddings, optionally limited to one source document."""
    if source is None:
        result = await session.execute(text("DELETE FROM best_practice_embeddings"))
    else:
        result = await session.execute(
            text("DELETE FROM best_practice_embeddings WHERE source = :source"),
            {"source": source},
        )
    return result.rowcount


async def search_similar(
    session: AsyncSession,
    table: str,
    query_embedding: list[float],
    top_k: int = 5,
    project_path: str | None = None,
) -> list[dict]:
    """Search for similar embeddings using cosine distance."""
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    filter_project = bool(project_path) and table == "code_embeddings"
    params: dict = {"query": embedding_str, "limit": top_k}
    if filter_project:
        params["project_path"] = project_path
    result = await session.execute(
        text(build_search_sql(table, filter_project=filter_project)),
        params,
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
