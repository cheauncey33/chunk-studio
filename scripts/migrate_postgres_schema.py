"""Create PostgreSQL session/job and pgvector tables without switching the app."""
from __future__ import annotations

import argparse
import json

from app import config, embeddings
from app.storage.repositories import postgres_schema_sql
from app.storage.vector_store import pgvector_schema_sql


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--dimension", type=int, default=embeddings.DEFAULT_DIMENSION)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    statements = postgres_schema_sql() + pgvector_schema_sql(dimension=args.dimension)
    print(json.dumps({"statements": len(statements), "dimension": args.dimension, "apply": args.apply}, ensure_ascii=False))
    if not args.apply:
        print("dry-run: no PostgreSQL writes performed")
        return 0
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required with --apply")
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
    print(json.dumps({"applied": len(statements)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
