"""Copy local file objects to the configured object store.

The command is dry-run by default. ``--apply`` writes objects and records only
the portable ``files.object_key``; the legacy local ``files.path`` remains as a
rollback/read-compatibility path until all consumers have switched to object
storage.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app import config, db
from app.storage.object_store import get_object_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db.init_db()
    rows = db.get_conn().execute(
        "SELECT id, workspace_id, name, path, object_key FROM files ORDER BY created_at"
    ).fetchall()
    pending: list[dict[str, Any]] = []
    for row in rows:
        local_path = config.from_rel(row["path"])
        if row["object_key"]:
            continue
        pending.append({
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "name": row["name"],
            "path": str(local_path),
            "exists": local_path.is_file(),
        })
    print(json.dumps({"backend": config.OBJECT_STORAGE_BACKEND, "pending": len(pending), "apply": args.apply}, ensure_ascii=False))
    if not args.apply:
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return 0
    store = get_object_store()
    migrated = 0
    for item in pending:
        if not item["exists"]:
            print(json.dumps({"file_id": item["id"], "error": "local file missing"}, ensure_ascii=False))
            continue
        key = f"workspaces/{item['workspace_id']}/files/{item['id']}/content.pdf"
        source_row = next(row for row in rows if row["id"] == item["id"])
        info = store.put_bytes(
            key,
            config.from_rel(source_row["path"]).read_bytes(),
            content_type="application/pdf",
        )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE files SET object_key=? WHERE id=? AND workspace_id=?",
                (info.key, item["id"], item["workspace_id"]),
            )
        migrated += 1
    print(json.dumps({"migrated": migrated}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
