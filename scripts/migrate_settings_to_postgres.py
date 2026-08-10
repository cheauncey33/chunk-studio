"""Migrate the shared key/value settings table to PostgreSQL.

The command is dry-run by default. Settings are deployment-wide rather than
workspace content, so the migration keeps the existing keys and values exactly
as they are. Environment variables still take precedence for secrets at
runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import config, db


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _read_settings(path: Path) -> dict[str, str]:
    config.DB_PATH = path
    db._conn = None
    db.init_db()
    return db.get_all_settings()


def _apply(dsn: str, settings: dict[str, str]) -> None:
    if not dsn:
        raise SystemExit("--dsn or CHUNK_STUDIO_DATABASE_URL is required")
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc

    with psycopg.connect(dsn) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                 key TEXT PRIMARY KEY,
                 value TEXT NOT NULL
            )"""
        )
        for key, value in settings.items():
            conn.execute(
                """INSERT INTO settings(key, value) VALUES (%s, %s)
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""",
                (key, value),
            )
        conn.commit()


def main() -> None:
    args = _args()
    settings = _read_settings(args.sqlite_path)
    if args.apply:
        _apply(args.dsn, settings)
    print(json.dumps({"apply": args.apply, "settings": len(settings)}, indent=2))


if __name__ == "__main__":
    main()
