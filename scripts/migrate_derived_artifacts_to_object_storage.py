"""Migrate parse outputs and crop images to the configured object store.

The command is dry-run by default.  ``--apply`` uploads Markdown, layout ZIPs
and crop images under workspace-scoped deterministic keys, then records key,
SHA-256 and size in SQLite.  Local paths are deliberately retained for
rollback and are only removed in a later, separately approved cleanup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app import config, db
from app.artifacts import crop_artifact_key, parse_artifact_key
from app.storage.object_store import get_object_store


def _entry(
    *,
    kind: str,
    row: Any,
    local_rel: str | None,
    key: str,
    key_column: str,
    sha_column: str,
    size_column: str,
    content_type: str,
) -> dict[str, Any] | None:
    local_path = config.from_rel(local_rel) if local_rel else None
    if not key and (local_path is None or not local_path.is_file()):
        return None
    return {
        "kind": kind,
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "file_id": str(row["file_id"]),
        "local_path": str(local_path) if local_path else "",
        "key": key,
        "key_column": key_column,
        "sha_column": sha_column,
        "size_column": size_column,
        "stored_sha256": str(row[sha_column] or ""),
        "stored_size": int(row[size_column] or 0),
        "content_type": content_type,
    }


def _collect() -> list[dict[str, Any]]:
    conn = db.get_conn()
    entries: list[dict[str, Any]] = []
    for row in conn.execute(
        """SELECT id, workspace_id, file_id, markdown_path, markdown_object_key,
                         markdown_sha256, markdown_size, raw_zip_path, raw_zip_object_key,
                         raw_zip_sha256, raw_zip_size
           FROM document_parses ORDER BY created_at, id"""
    ).fetchall():
        markdown = _entry(
            kind="markdown",
            row=row,
            local_rel=row["markdown_path"],
            key=str(row["markdown_object_key"] or ""),
            key_column="markdown_object_key",
            sha_column="markdown_sha256",
            size_column="markdown_size",
            content_type="text/markdown; charset=utf-8",
        )
        if markdown:
            markdown["key"] = markdown["key"] or parse_artifact_key(
                row["workspace_id"], row["file_id"], row["id"], "markdown"
            )
            entries.append(markdown)
        layout = _entry(
            kind="layout_zip",
            row=row,
            local_rel=row["raw_zip_path"],
            key=str(row["raw_zip_object_key"] or ""),
            key_column="raw_zip_object_key",
            sha_column="raw_zip_sha256",
            size_column="raw_zip_size",
            content_type="application/zip",
        )
        if layout:
            layout["key"] = layout["key"] or parse_artifact_key(
                row["workspace_id"], row["file_id"], row["id"], "layout_zip"
            )
            entries.append(layout)

    for row in conn.execute(
        """SELECT c.id, c.workspace_id, c.file_id, c.crop_path, c.crop_object_key,
                         c.crop_sha256, c.crop_size
           FROM chunks c ORDER BY c.created_at, c.id"""
    ).fetchall():
        crop = _entry(
            kind="crop",
            row=row,
            local_rel=row["crop_path"],
            key=str(row["crop_object_key"] or ""),
            key_column="crop_object_key",
            sha_column="crop_sha256",
            size_column="crop_size",
            content_type="image/png",
        )
        if crop:
            crop["key"] = crop["key"] or crop_artifact_key(
                row["workspace_id"], row["file_id"], row["id"]
            )
            entries.append(crop)
    return entries


def _inspect(entries: list[dict[str, Any]], store: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pending: list[dict[str, Any]] = []
    inconsistent: list[dict[str, Any]] = []
    metadata_updates: list[dict[str, Any]] = []
    for item in entries:
        local_path = Path(item["local_path"]) if item["local_path"] else None
        local_exists = bool(local_path and local_path.is_file())
        object_exists = False
        object_data: bytes | None = None
        try:
            object_exists = store.exists(item["key"])
            if object_exists:
                object_data = store.get_bytes(item["key"])
        except Exception as exc:
            inconsistent.append({**item, "error": str(exc)})
            continue
        if object_exists and object_data is not None:
            sha256 = hashlib.sha256(object_data).hexdigest()
            if item["stored_sha256"] != sha256 or item["stored_size"] != len(object_data):
                metadata_updates.append({**item, "sha256": sha256, "size": len(object_data)})
            continue
        if local_exists:
            pending.append(item)
        else:
            inconsistent.append({**item, "error": "local source is missing"})
    return pending, inconsistent, metadata_updates


def _apply_item(conn: Any, store: Any, item: dict[str, Any]) -> dict[str, Any]:
    local_path = Path(item["local_path"])
    info = store.put_bytes(item["key"], local_path.read_bytes(), content_type=item["content_type"])
    conn.execute(
        f"UPDATE { 'document_parses' if item['kind'] in {'markdown', 'layout_zip'} else 'chunks' } "
        f"SET {item['key_column']}=?, {item['sha_column']}=?, {item['size_column']}=? "
        "WHERE id=? AND workspace_id=?",
        (info.key, info.sha256, info.size, item["id"], item["workspace_id"]),
    )
    return {"kind": item["kind"], "id": item["id"], "key": info.key, "size": info.size}


def _update_metadata(conn: Any, item: dict[str, Any]) -> None:
    table = "document_parses" if item["kind"] in {"markdown", "layout_zip"} else "chunks"
    conn.execute(
        f"UPDATE {table} SET {item['sha_column']}=?, {item['size_column']}=? "
        "WHERE id=? AND workspace_id=?",
        (item["sha256"], item["size"], item["id"], item["workspace_id"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db.init_db()
    store = get_object_store()
    entries = _collect()
    pending, inconsistent, metadata_updates = _inspect(entries, store)
    summary = {
        "backend": config.OBJECT_STORAGE_BACKEND,
        "artifacts": len(entries),
        "pending": len(pending),
        "inconsistent": len(inconsistent),
        "metadata_updates": len(metadata_updates),
        "apply": bool(args.apply),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not args.apply:
        print(json.dumps({"pending": pending, "inconsistent": inconsistent}, ensure_ascii=False, indent=2))
        return 0

    migrated: list[dict[str, Any]] = []
    failed = list(inconsistent)
    with db.transaction() as conn:
        for item in metadata_updates:
            _update_metadata(conn, item)
        for item in pending:
            try:
                migrated.append(_apply_item(conn, store, item))
            except Exception as exc:
                failed.append({"kind": item["kind"], "id": item["id"], "error": str(exc)})
    by_kind: dict[str, int] = {}
    for item in migrated:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    print(json.dumps({
        "migrated": len(migrated),
        "migrated_by_kind": by_kind,
        "metadata_updated": len(metadata_updates),
        "failed": failed,
    }, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
