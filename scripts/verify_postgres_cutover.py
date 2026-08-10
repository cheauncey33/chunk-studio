"""Read-only gate for the SQLite -> PostgreSQL content cutover.

The command compares workspace-owned identifiers and selected source-of-truth
fields. It never writes either database. Run it after the content/vector/artifact
migrations and before enabling PostgreSQL as the default runtime backend.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app import config


CONTENT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("files", "id, COALESCE(sha, '')", "id, COALESCE(sha, '')"),
    ("chunks", "id, COALESCE(text, ''), status", "id, COALESCE(text, ''), status"),
    ("document_parses", "id, status, COALESCE(markdown_object_key, ''), COALESCE(raw_zip_object_key, '')", "id, status, COALESCE(markdown_object_key, ''), COALESCE(raw_zip_object_key, '')"),
    ("knowledge_bases", "id, name, status", "id, name, status"),
    ("knowledge_base_files", "knowledge_base_id, file_id, enabled", "knowledge_base_id, file_id, enabled"),
    ("audit_assistants", "id, name, status", "id, name, status"),
    ("assistant_versions", "id, assistant_id, version, status", "id, assistant_id, version, status"),
    ("assistant_knowledge_bases", "assistant_id, knowledge_base_id, enabled", "assistant_id, knowledge_base_id, enabled"),
    ("assistant_init_drafts", "assistant_id, status", "assistant_id, status"),
    ("audit_case_reviews", "report_name, case_id, status, corrected_status", "report_name, case_id, status, corrected_status"),
)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = "" if value is None else str(value)
    if text.casefold() == "true":
        return "1"
    if text.casefold() == "false":
        return "0"
    return text


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--workspace-id", default=config.DEFAULT_WORKSPACE_ID)
    return parser.parse_args()


def _sqlite_rows(path: Path, table: str, columns: str, workspace: str) -> set[tuple[str, ...]]:
    conn = sqlite3.connect(path)
    try:
        if table == "assistant_versions":
            sql = (
                f"SELECT v.{columns.replace(', ', ', v.') } "
                "FROM assistant_versions v "
                "JOIN audit_assistants a ON a.id=v.assistant_id "
                "WHERE a.workspace_id=?"
            )
        else:
            sql = f"SELECT {columns} FROM {table} WHERE workspace_id=?"
        rows = conn.execute(
            sql,
            (workspace,),
        ).fetchall()
        return {tuple(_value(value) for value in row) for row in rows}
    finally:
        conn.close()


def _postgres_rows(dsn: str, table: str, columns: str, workspace: str) -> set[tuple[str, ...]]:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc
    with psycopg.connect(dsn) as conn:
        if table == "assistant_versions":
            sql = (
                f"SELECT v.{columns.replace(', ', ', v.') } "
                "FROM assistant_versions v "
                "JOIN audit_assistants a ON a.id=v.assistant_id "
                "WHERE a.workspace_id=%s"
            )
        else:
            sql = f"SELECT {columns} FROM {table} WHERE workspace_id=%s"
        rows = conn.execute(
            sql,
            (workspace,),
        ).fetchall()
    return {tuple(_value(value) for value in row) for row in rows}


def _sqlite_object_stats(path: Path, workspace: str) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM files WHERE workspace_id=? AND object_key <> '') AS files,
                 (SELECT count(*) FROM chunks WHERE workspace_id=? AND crop_object_key <> '') AS crops,
                 (SELECT count(*) FROM document_parses WHERE workspace_id=? AND markdown_object_key <> '') AS markdown,
                 (SELECT count(*) FROM document_parses WHERE workspace_id=? AND raw_zip_object_key <> '') AS layout_zip""",
            (workspace, workspace, workspace, workspace),
        ).fetchone()
        return {"files": int(row[0]), "crops": int(row[1]), "markdown": int(row[2]), "layout_zip": int(row[3])}
    finally:
        conn.close()


def _object_stats(dsn: str, workspace: str) -> dict[str, int]:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM files WHERE workspace_id=%s AND object_key <> '') AS files,
                 (SELECT count(*) FROM chunks WHERE workspace_id=%s AND crop_object_key <> '') AS crops,
                 (SELECT count(*) FROM document_parses WHERE workspace_id=%s AND markdown_object_key <> '') AS markdown,
                 (SELECT count(*) FROM document_parses WHERE workspace_id=%s AND raw_zip_object_key <> '') AS layout_zip""",
            (workspace, workspace, workspace, workspace),
        ).fetchone()
    return {"files": int(row[0]), "crops": int(row[1]), "markdown": int(row[2]), "layout_zip": int(row[3])}


def _queue_stats(dsn: str, workspace: str) -> dict[str, int]:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT status, count(*) FROM jobs WHERE workspace_id=%s GROUP BY status",
            (workspace,),
        ).fetchall()
    return {str(status): int(count) for status, count in rows}


def verify(*, sqlite_path: Path, dsn: str, workspace: str) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    for table, sqlite_columns, postgres_columns in CONTENT_CHECKS:
        sqlite_values = _sqlite_rows(sqlite_path, table, sqlite_columns, workspace)
        postgres_values = _postgres_rows(dsn, table, postgres_columns, workspace)
        checks[table] = {
            "sqlite_count": len(sqlite_values),
            "postgres_count": len(postgres_values),
            "equal": sqlite_values == postgres_values,
            "missing_in_postgres": len(sqlite_values - postgres_values),
            "extra_in_postgres": len(postgres_values - sqlite_values),
        }

    objects = _object_stats(dsn, workspace)
    sqlite_counts = _sqlite_object_stats(sqlite_path, workspace)
    object_gate = {
        kind: {"rows": sqlite_counts[kind], "objects": objects[kind], "complete": objects[kind] >= sqlite_counts[kind]}
        for kind in sqlite_counts
    }
    return {
        "workspace_id": workspace,
        "checks": checks,
        "objects": object_gate,
        "postgres_queue": _queue_stats(dsn, workspace),
        "pass": all(item["equal"] for item in checks.values())
        and all(item["complete"] for item in object_gate.values()),
    }


def main() -> int:
    args = _args()
    if not args.workspace_id.strip():
        raise SystemExit("--workspace-id must not be blank")
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required")
    report = verify(sqlite_path=args.sqlite_path, dsn=args.dsn, workspace=args.workspace_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
