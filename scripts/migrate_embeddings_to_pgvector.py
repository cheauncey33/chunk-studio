"""Backfill approved SQLite embeddings into the PostgreSQL vector index.

The command is dry-run by default. It only writes after ``--apply`` is passed,
and it never deletes or mutates the SQLite source. Run it against a copied
database first, then compare retrieval results before switching the backend.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from app import config
from app.storage.vector_store import _vector_literal, pgvector_schema_sql


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--workspace-id", default=config.DEFAULT_WORKSPACE_ID)
    parser.add_argument("--model", default="text-embedding-v4")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _rows(
    sqlite_path: Path,
    *,
    workspace_id: str,
    model: str,
    dimension: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT e.chunk_id, e.model, e.dimension, e.text_sha256, e.embedding,
                   e.created_at, e.updated_at,
                   c.file_id, c.page, c.crop_path, c.crop_object_key, c.crop_sha256, c.crop_size, c.text,
                   c.business_metadata, c.source_trace,
                   f.name AS file_name, c.status
            FROM chunk_embeddings e
            JOIN chunks c ON c.id=e.chunk_id
            JOIN files f ON f.id=c.file_id
            WHERE c.status='approved'
              AND c.workspace_id=? AND f.workspace_id=?
              AND e.model=? AND e.dimension=?
            ORDER BY c.file_id, c.page, c.id
            """,
            (workspace_id, workspace_id, model, dimension),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            blob = row["embedding"]
            if not isinstance(blob, (bytes, bytearray)) or len(blob) != dimension * 4:
                continue
            result.append({
                "chunk_id": row["chunk_id"],
                "model": row["model"],
                "dimension": row["dimension"],
                "text_sha256": row["text_sha256"],
                "embedding": _vector_literal(
                    list(struct.unpack(f"<{dimension}f", blob)), dimension
                ),
                "file_id": row["file_id"],
                "file_name": row["file_name"] or "",
                "page": row["page"],
                "crop_path": row["crop_path"],
                "crop_object_key": row["crop_object_key"] or "",
                "crop_sha256": row["crop_sha256"] or "",
                "crop_size": row["crop_size"] or 0,
                "text": row["text"] or "",
                "business_metadata": _json(row["business_metadata"]),
                "source_trace": _json(row["source_trace"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return result
    finally:
        conn.close()


def _json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> int:
    args = _parse_args()
    if not args.workspace_id.strip():
        raise SystemExit("--workspace-id must not be blank")
    rows = _rows(
        args.sqlite_path,
        workspace_id=args.workspace_id,
        model=args.model,
        dimension=args.dimension,
    )
    print(json.dumps({
        "sqlite_path": str(args.sqlite_path),
        "workspace_id": args.workspace_id,
        "model": args.model,
        "dimension": args.dimension,
        "eligible_rows": len(rows),
        "apply": bool(args.apply),
    }, ensure_ascii=False))
    if not args.apply:
        print("dry-run: no PostgreSQL writes performed")
        return 0
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required with --apply")
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "psycopg is required for --apply; install the postgres dependency first"
        ) from exc

    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cursor:
            for statement in pgvector_schema_sql(dimension=args.dimension):
                cursor.execute(statement)
            cursor.executemany(
                """
                INSERT INTO chunk_vector_index
                (workspace_id, chunk_id, model, dimension, text_sha256, embedding,
                 file_id, file_name, page, crop_path, crop_object_key, crop_sha256, crop_size, text, business_metadata,
                 source_trace, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                        'approved',COALESCE(%s, now()),COALESCE(%s, now()))
                ON CONFLICT (workspace_id, chunk_id, model, dimension) DO UPDATE SET
                  text_sha256=excluded.text_sha256,
                  embedding=excluded.embedding,
                  file_id=excluded.file_id,
                  file_name=excluded.file_name,
                  page=excluded.page,
                  crop_path=excluded.crop_path,
                  crop_object_key=excluded.crop_object_key,
                  crop_sha256=excluded.crop_sha256,
                  crop_size=excluded.crop_size,
                  text=excluded.text,
                  business_metadata=excluded.business_metadata,
                  source_trace=excluded.source_trace,
                  updated_at=excluded.updated_at
                """,
                [
                    (
                        args.workspace_id,
                        row["chunk_id"],
                        row["model"],
                        row["dimension"],
                        row["text_sha256"],
                        row["embedding"],
                        row["file_id"],
                        row["file_name"],
                        row["page"],
                        row["crop_path"],
                        row["crop_object_key"],
                        row["crop_sha256"],
                        row["crop_size"],
                        row["text"],
                        json.dumps(row["business_metadata"], ensure_ascii=False),
                        json.dumps(row["source_trace"], ensure_ascii=False),
                        row["created_at"],
                        row["updated_at"],
                    )
                    for row in rows
                ],
            )
        conn.commit()
    print(json.dumps({"migrated_rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
