"""Compare SQLite and pgvector retrieval for the same embedded queries.

Input is JSONL so the script never embeds or mutates data. Each line must be
``{"query": "...", "vector": [0.1, ...]}``. Run it after the pgvector
backfill and before switching ``VECTOR_BACKEND``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config, db
from app.storage.vector_store import PgVectorStore, SQLiteVectorStore


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True, help="query/vector JSONL file")
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--workspace-id", default=config.DEFAULT_WORKSPACE_ID)
    parser.add_argument("--model", default="text-embedding-v4")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-overlap", type=float, default=0.95)
    return parser.parse_args()


def _cases(path: Path, dimension: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(item, dict) or not str(item.get("query") or "").strip():
            raise SystemExit(f"query is required at {path}:{line_number}")
        vector = item.get("vector")
        if not isinstance(vector, list) or len(vector) != dimension:
            raise SystemExit(f"vector dimension mismatch at {path}:{line_number}")
        cases.append({"query": str(item["query"]), "vector": [float(value) for value in vector]})
    if not cases:
        raise SystemExit("queries file contains no cases")
    return cases


def compare(
    cases: list[dict[str, Any]],
    *,
    sqlite_store: SQLiteVectorStore,
    pg_store: PgVectorStore,
    workspace_id: str,
    model: str,
    dimension: int,
    top_k: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        kwargs = {
            "top_k": top_k,
            "model": model,
            "dimension": dimension,
            "workspace_id": workspace_id,
        }
        sqlite_result = sqlite_store.search_by_vector(case["query"], case["vector"], **kwargs)
        pg_result = pg_store.search_by_vector(case["query"], case["vector"], **kwargs)
        sqlite_ids = [str(hit["chunk_id"]) for hit in sqlite_result.get("hits", [])]
        pg_ids = [str(hit["chunk_id"]) for hit in pg_result.get("hits", [])]
        overlap = len(set(sqlite_ids) & set(pg_ids)) / max(1, min(len(sqlite_ids), len(pg_ids)))
        rows.append({
            "query": case["query"],
            "sqlite_total_candidates": sqlite_result.get("total_candidates", 0),
            "pgvector_total_candidates": pg_result.get("total_candidates", 0),
            "sqlite_chunk_ids": sqlite_ids,
            "pgvector_chunk_ids": pg_ids,
            "top_k_overlap": round(overlap, 6),
            "candidate_count_equal": sqlite_result.get("total_candidates") == pg_result.get("total_candidates"),
        })
    return {
        "cases": len(rows),
        "candidate_count_equal_cases": sum(1 for row in rows if row["candidate_count_equal"]),
        "min_top_k_overlap": min(row["top_k_overlap"] for row in rows),
        "results": rows,
    }


def main() -> int:
    args = _args()
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required")
    # Initialize the SQLite adapter against the explicitly supplied database.
    # The comparison must not accidentally read the application's default DB.
    config.DB_PATH = args.sqlite_path
    db._conn = None
    db.init_db()
    cases = _cases(args.queries, args.dimension)
    report = compare(
        cases,
        sqlite_store=SQLiteVectorStore(),
        pg_store=PgVectorStore(args.dsn),
        workspace_id=args.workspace_id,
        model=args.model,
        dimension=args.dimension,
        top_k=args.top_k,
    )
    report.update({
        "workspace_id": args.workspace_id,
        "model": args.model,
        "dimension": args.dimension,
        "top_k": args.top_k,
        "pass": report["min_top_k_overlap"] >= float(args.min_overlap),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
