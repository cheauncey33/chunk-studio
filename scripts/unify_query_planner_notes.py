"""Strip nested full Query Planner prompts from category notes.

Runtime synthesizes query_planner_brief from routes + schema + KB.
node_prompts.query_planner.content should be short category notes only.

Usage (from repo root):

    $env:PYTHONPATH='backend'; uv run python scripts/unify_query_planner_notes.py
    $env:PYTHONPATH='backend'; uv run python scripts/unify_query_planner_notes.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.db import _oil_query_planner_category_notes  # noqa: E402
from app.query_planner_routes import looks_like_full_query_planner_prompt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-empty", action="store_true", help="Clear all planner notes")
    args = parser.parse_args()

    db_path = config.DB_PATH
    if not Path(db_path).exists():
        print(f"database not found: {db_path}")
        return 1

    oil_path, oil_notes = _oil_query_planner_category_notes()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT v.id, v.assistant_id, v.node_prompts, a.name AS assistant_name
           FROM assistant_versions v
           JOIN audit_assistants a ON a.id=v.assistant_id"""
    ).fetchall()

    changed = 0
    for row in rows:
        prompts = json.loads(row["node_prompts"] or "{}")
        if not isinstance(prompts, dict):
            continue
        planner = prompts.get("query_planner")
        if not isinstance(planner, dict):
            continue
        content = str(planner.get("content") or "")
        if not args.force_empty and not looks_like_full_query_planner_prompt(content):
            continue
        assistant_id = str(row["assistant_id"] or "")
        assistant_name = str(row["assistant_name"] or "")
        is_oil = (
            assistant_id == "assistant_oil_transformer_audit"
            or "油浸" in assistant_name
            or "变压器" in assistant_name
        )
        next_content = "" if args.force_empty else (oil_notes if is_oil else "")
        next_path = oil_path if (is_oil and not args.force_empty) else str(planner.get("path") or "")
        print(
            f"{row['id']} ({assistant_name}): "
            f"{len(content)} chars -> {len(next_content)} chars"
        )
        if args.dry_run:
            changed += 1
            continue
        prompts["query_planner"] = {**planner, "path": next_path, "content": next_content}
        conn.execute(
            "UPDATE assistant_versions SET node_prompts=? WHERE id=?",
            (json.dumps(prompts, ensure_ascii=False), row["id"]),
        )
        changed += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"{'would update' if args.dry_run else 'updated'} {changed} version(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
