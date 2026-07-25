"""Strip nested full audit_judge prompts from category notes.

Usage (from repo root):

    $env:PYTHONPATH='backend'; uv run python scripts/unify_audit_judge_notes.py
    $env:PYTHONPATH='backend'; uv run python scripts/unify_audit_judge_notes.py --dry-run
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
from app.audit_judge_notes import looks_like_full_audit_judge_prompt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = config.DB_PATH
    if not Path(db_path).exists():
        print(f"database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT v.id, a.name AS assistant_name, v.node_prompts
           FROM assistant_versions v
           JOIN audit_assistants a ON a.id=v.assistant_id"""
    ).fetchall()

    changed = 0
    for row in rows:
        prompts = json.loads(row["node_prompts"] or "{}")
        if not isinstance(prompts, dict):
            continue
        judge = prompts.get("audit_judge")
        if not isinstance(judge, dict):
            continue
        content = str(judge.get("content") or "")
        if not looks_like_full_audit_judge_prompt(content):
            continue
        print(f"{row['id']} ({row['assistant_name']}): {len(content)} chars -> 0")
        if args.dry_run:
            changed += 1
            continue
        prompts["audit_judge"] = {**judge, "content": ""}
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
