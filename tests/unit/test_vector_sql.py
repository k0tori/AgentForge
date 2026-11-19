"""SQL shape tests for the embedding tables. No database required."""
from __future__ import annotations

import pytest

from src.storage.vector import (
    CODE_TABLE_COLUMNS,
    PRACTICE_TABLE_COLUMNS,
    build_search_sql,
    embedding_type_ddl,
    insert_statement,
)


def test_code_search_does_not_read_practice_columns():
    sql = build_search_sql("code_embeddings", filter_project=True)
    assert "FROM code_embeddings" in sql
    assert "project_path = :project_path" in sql
    assert "NULL::varchar AS category" in sql
    assert "NULL::text AS title" in sql
    assert "NULL::text AS source" in sql
    for column in ("category", "title", "source"):
        assert column not in CODE_TABLE_COLUMNS


def test_practice_search_does_not_read_code_columns():
    sql = build_search_sql("best_practice_embeddings", filter_project=False)
    assert "FROM best_practice_embeddings" in sql
    assert "NULL::jsonb AS metadata" in sql
    assert "NULL::text AS file_path" in sql
    assert "NULL::varchar AS chunk_type" in sql
    for column in ("metadata", "file_path", "chunk_type", "chunk_name", "project_path"):
        assert column not in PRACTICE_TABLE_COLUMNS


def test_practice_insert_omits_metadata_column():
    sql, columns = insert_statement(
        "best_practice_embeddings",
        {"category": "architecture", "title": "Rules", "source": "CONVENTIONS.md"},
    )
    assert "metadata" not in columns
    assert "metadata" not in sql
    assert "category" in columns


def test_code_insert_keeps_metadata_and_rejects_unknown_columns():
    _sql, columns = insert_statement(
        "code_embeddings",
        {
            "project_path": "/tmp/repo",
            "file_path": "a.py",
            "chunk_type": "function",
            "chunk_name": "f",
        },
    )
    assert columns[:3] == ["content", "embedding", "metadata"]
    with pytest.raises(ValueError):
        insert_statement("code_embeddings", {"category": "nope"})


def test_embedding_ddl_uses_configured_dimension():
    assert embedding_type_ddl() == "VECTOR(384)"
