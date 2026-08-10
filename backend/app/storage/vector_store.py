"""Vector search abstraction.

The retrieval pipeline depends on this small contract instead of knowing
whether vectors are SQLite blobs or PostgreSQL/pgvector rows. SQLite remains a
fully working local adapter; pgvector is selected only by explicit config and
fails with an actionable dependency/DSN error rather than silently falling
back to an unsafe full scan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .. import config, embeddings


class VectorStore(Protocol):
    def search_by_vector(
        self,
        query: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        content_type: str | None = None,
        file_ids: list[str] | None = None,
        model: str = embeddings.DEFAULT_MODEL,
        dimension: int = embeddings.DEFAULT_DIMENSION,
        workspace_id: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SQLiteVectorStore:
    """Compatibility adapter around the current local SQLite implementation."""

    def search_by_vector(
        self,
        query: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        content_type: str | None = None,
        file_ids: list[str] | None = None,
        model: str = embeddings.DEFAULT_MODEL,
        dimension: int = embeddings.DEFAULT_DIMENSION,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        return embeddings.vector_search_by_vector(
            query,
            query_vector,
            top_k=top_k,
            content_type=content_type,
            file_ids=file_ids,
            model=model,
            dimension=dimension,
            workspace_id=workspace_id,
        )


def _vector_literal(vector: list[float], dimension: int) -> str:
    if len(vector) != dimension:
        raise ValueError("unexpected query vector dimension")
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class PgVectorStore:
    """PostgreSQL/pgvector adapter for the normalized vector index table."""

    dsn: str
    table_name: str = "chunk_vector_index"

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pgvector backend requires the optional psycopg package; "
                "install the postgres dependency before enabling it"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def search_by_vector(
        self,
        query: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        content_type: str | None = None,
        file_ids: list[str] | None = None,
        model: str = embeddings.DEFAULT_MODEL,
        dimension: int = embeddings.DEFAULT_DIMENSION,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be blank")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if file_ids is not None and not file_ids:
            return {
                "query": query,
                "model": model,
                "dimension": dimension,
                "content_type": content_type,
                "total_candidates": 0,
                "hits": [],
            }

        vector = _vector_literal(query_vector, dimension)
        where = ["status = 'approved'", "model = %s", "dimension = %s"]
        filter_params: list[Any] = [model, dimension]
        if workspace_id:
            where.append("workspace_id = %s")
            filter_params.append(workspace_id)
        if content_type is not None:
            where.append("business_metadata->>'content_type' = %s")
            filter_params.append(content_type)
        if file_ids is not None:
            where.append("file_id = ANY(%s)")
            filter_params.append(file_ids)
        where_sql = " AND ".join(where)
        count_sql = f"SELECT count(*) AS total FROM {self.table_name} WHERE {where_sql}"
        search_sql = f"""
            SELECT chunk_id, file_id, file_name, page, crop_path, text,
                   business_metadata, source_trace,
                   1 - (embedding <=> %s::vector) AS score
            FROM {self.table_name}
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(count_sql, filter_params)
                total = int(cursor.fetchone()["total"])
                cursor.execute(search_sql, [vector, *filter_params, vector, top_k])
                rows = cursor.fetchall()
        hits = []
        for row in rows:
            crop_path = row.get("crop_path")
            hits.append({
                "chunk_id": row["chunk_id"],
                "score": float(row["score"]),
                "file_id": row["file_id"],
                "file_name": row["file_name"],
                "page": row["page"],
                "crop_url": (
                    f"/crops/{str(crop_path).split('/')[-1]}" if crop_path else None
                ),
                "text": row.get("text") or "",
                "business_metadata": _json_object(row.get("business_metadata")),
                "source_trace": _json_object(row.get("source_trace")),
            })
        return {
            "query": query,
            "model": model,
            "dimension": dimension,
            "content_type": content_type,
            "total_candidates": total,
            "hits": hits,
        }


def pgvector_schema_sql(*, dimension: int = embeddings.DEFAULT_DIMENSION) -> list[str]:
    """Return idempotent DDL for the first PostgreSQL vector migration."""
    if dimension < 1:
        raise ValueError("dimension must be positive")
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS chunk_vector_index (
            workspace_id TEXT NOT NULL DEFAULT 'local-workspace',
            chunk_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL,
            embedding vector({dimension}) NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            page INTEGER,
            crop_path TEXT,
            text TEXT NOT NULL DEFAULT '',
            business_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status TEXT NOT NULL DEFAULT 'approved',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, chunk_id, model, dimension)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_chunk_vector_index_embedding_hnsw "
        "ON chunk_vector_index USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS ix_chunk_vector_index_scope "
        "ON chunk_vector_index (workspace_id, model, dimension, status)",
    ]


def get_vector_store() -> VectorStore:
    """Build the configured adapter without connecting during app import."""
    backend = config.VECTOR_BACKEND
    if backend in {"pgvector", "postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is required when CHUNK_STUDIO_VECTOR_BACKEND=pgvector"
            )
        return PgVectorStore(config.DATABASE_URL)
    if backend != "sqlite":
        raise RuntimeError(f"unsupported vector backend: {backend}")
    return SQLiteVectorStore()
