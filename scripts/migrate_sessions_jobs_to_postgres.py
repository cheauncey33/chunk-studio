"""Migrate workspace identities, conversations, events and jobs to PostgreSQL.

The command is dry-run by default. It is intentionally limited to the tables
served by the PostgreSQL Repository slice; knowledge-base content remains in
the existing database until its own migration is approved. Jobs use
``ON CONFLICT DO NOTHING`` so a worker that already started in PostgreSQL is
never overwritten by a repeated migration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config, db
from app.storage.repositories import postgres_schema_sql
from app.storage.vector_store import pgvector_schema_sql


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _configure_sqlite(path: Path) -> None:
    config.DB_PATH = path
    db._conn = None
    db.init_db()


def _jsonb(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False)


def _rows(workspace_id: str) -> dict[str, list[dict[str, Any]]]:
    conn = db.get_conn()
    scope = workspace_id.strip()
    params = (scope,) if scope else ()
    where = " WHERE workspace_id=?" if scope else ""
    identity_rows = {
        "users": [dict(row) for row in conn.execute(
            """SELECT DISTINCT u.* FROM users u
               JOIN workspace_members wm ON wm.user_id=u.id"""
            + (" WHERE wm.workspace_id=?" if scope else ""),
            params,
        ).fetchall()],
        "workspaces": [dict(row) for row in conn.execute(
            "SELECT * FROM workspaces" + (" WHERE id=?" if scope else ""),
            params,
        ).fetchall()],
        "workspace_members": [dict(row) for row in conn.execute(
            "SELECT * FROM workspace_members" + where,
            params,
        ).fetchall()],
    }
    resource_rows = {
        "chat_conversations": [dict(row) for row in conn.execute(
            "SELECT * FROM chat_conversations" + where,
            params,
        ).fetchall()],
        "jobs": [dict(row) for row in conn.execute(
            "SELECT * FROM jobs" + where,
            params,
        ).fetchall()],
    }
    conversation_ids = [row["id"] for row in resource_rows["chat_conversations"]]
    if conversation_ids:
        placeholders = ",".join("?" for _ in conversation_ids)
        event_rows = conn.execute(
            "SELECT * FROM chat_events WHERE conversation_id IN (" + placeholders + ")",
            conversation_ids,
        ).fetchall()
    else:
        event_rows = []
    resource_rows["chat_events"] = [dict(row) for row in event_rows]
    return {**identity_rows, **resource_rows}


def _summary(rows: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(items) for name, items in rows.items()}


def _apply(dsn: str, rows: dict[str, list[dict[str, Any]]], dimension: int) -> None:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cursor:
            for statement in postgres_schema_sql() + pgvector_schema_sql(dimension=dimension):
                cursor.execute(statement)

            for row in rows["users"]:
                cursor.execute(
                    """INSERT INTO users
                       (id, external_subject, display_name, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         external_subject=EXCLUDED.external_subject,
                         display_name=EXCLUDED.display_name,
                         status=EXCLUDED.status,
                         updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["external_subject"], row["display_name"], row["status"],
                     row["created_at"], row["updated_at"]),
                )
            for row in rows["workspaces"]:
                cursor.execute(
                    """INSERT INTO workspaces
                       (id, name, slug, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         name=EXCLUDED.name, slug=EXCLUDED.slug,
                         status=EXCLUDED.status, updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["name"], row["slug"], row["status"],
                     row["created_at"], row["updated_at"]),
                )
            for row in rows["workspace_members"]:
                cursor.execute(
                    """INSERT INTO workspace_members
                       (workspace_id, user_id, role, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (workspace_id, user_id) DO UPDATE SET
                         role=EXCLUDED.role, status=EXCLUDED.status,
                         updated_at=EXCLUDED.updated_at""",
                    (row["workspace_id"], row["user_id"], row["role"], row["status"],
                     row["created_at"], row["updated_at"]),
                )
            for row in rows["chat_conversations"]:
                cursor.execute(
                    """INSERT INTO chat_conversations
                       (id, assistant_id, workspace_id, user_id, title, summary,
                        summary_version, summary_sequence, config_snapshot, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         title=EXCLUDED.title, summary=EXCLUDED.summary,
                         summary_version=EXCLUDED.summary_version,
                         summary_sequence=EXCLUDED.summary_sequence,
                         config_snapshot=EXCLUDED.config_snapshot,
                         updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["assistant_id"], row["workspace_id"], row["user_id"],
                     row["title"], row["summary"], row["summary_version"],
                     row["summary_sequence"], _jsonb(row["config_snapshot"]),
                     row["created_at"], row["updated_at"]),
                )
            for row in rows["chat_events"]:
                cursor.execute(
                    """INSERT INTO chat_events
                       (id, conversation_id, sequence, event_type, payload, created_at)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                       ON CONFLICT (conversation_id, sequence) DO NOTHING""",
                    (row["id"], row["conversation_id"], row["sequence"],
                     row["event_type"], _jsonb(row["payload"]), row["created_at"]),
                )
            for row in rows["jobs"]:
                cursor.execute(
                    """INSERT INTO jobs
                       (id, workspace_id, type, target_type, target_id, status, priority,
                        attempts, max_attempts, error, result, created_at, started_at,
                        finished_at, available_at, locked_by, locked_until, dead_letter)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                               %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (row["id"], row["workspace_id"], row["type"], row["target_type"],
                     row["target_id"], row["status"], row["priority"], row["attempts"],
                     row["max_attempts"], row["error"], _jsonb(row["result"]),
                     row["created_at"], row["started_at"], row["finished_at"],
                     row["available_at"], row["locked_by"], row["locked_until"],
                     bool(row["dead_letter"])),
                )
        conn.commit()


def main() -> int:
    args = _args()
    _configure_sqlite(args.sqlite_path)
    rows = _rows(args.workspace_id)
    report = {
        "sqlite_path": str(args.sqlite_path),
        "workspace_id": args.workspace_id or None,
        "apply": bool(args.apply),
        "counts": _summary(rows),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run: no PostgreSQL writes performed")
        return 0
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required with --apply")
    _apply(args.dsn, rows, args.dimension)
    print(json.dumps({"applied": report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
