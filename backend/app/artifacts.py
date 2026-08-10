"""Shared access helpers for local and object-backed derived artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import config
from .storage.object_store import get_object_store


def parse_artifact_key(workspace_id: str, file_id: str, parse_id: str, kind: str) -> str:
    suffix = {"markdown": "markdown.md", "layout_zip": "layout.zip"}.get(kind)
    if not suffix:
        raise ValueError(f"unsupported parse artifact kind: {kind}")
    return f"workspaces/{workspace_id}/files/{file_id}/parses/{parse_id}/{suffix}"


def crop_artifact_key(workspace_id: str, file_id: str, chunk_id: str) -> str:
    return f"workspaces/{workspace_id}/files/{file_id}/chunks/{chunk_id}/crop.png"


def read_artifact(local_rel: str | None, object_key: str | None) -> bytes | None:
    """Read an object key first, falling back to the legacy local path."""
    key = str(object_key or "").strip()
    if key:
        try:
            return get_object_store().get_bytes(key)
        except Exception:
            # Local paths are retained as an explicit rollback path during the
            # staged migration.  If both are unavailable, return None so the
            # caller can produce a domain-specific 404/error.
            pass
    rel = str(local_rel or "").strip()
    if rel:
        path = config.from_rel(rel)
        if path.is_file():
            return path.read_bytes()
    return None


def materialize_artifact(
    local_rel: str | None,
    object_key: str | None,
    *,
    cache_name: str,
    suffix: str,
) -> Path | None:
    """Return a local path for subprocess-only consumers such as the audit CLI."""
    rel = str(local_rel or "").strip()
    if rel:
        path = config.from_rel(rel)
        if path.is_file():
            return path
    data = read_artifact(local_rel, object_key)
    if data is None:
        return None
    digest = hashlib.sha256(str(object_key or cache_name).encode()).hexdigest()[:24]
    cache_dir = config.PARSES_DIR / "object-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{digest}-{cache_name}{suffix}"
    if not target.is_file():
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)
    return target


def object_metadata(store: Any, key: str, data: bytes, *, content_type: str | None = None) -> dict[str, Any]:
    info = store.put_bytes(key, data, content_type=content_type)
    return {"key": info.key, "sha256": info.sha256, "size": info.size}
