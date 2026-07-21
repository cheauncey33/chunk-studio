"""Resolve and run post-parse auto-chunk pipelines from KB/file config."""
from __future__ import annotations

import copy
import json
import logging
from typing import Any

from . import db
from .models import AutoImageChunkRequest, AutoSectionChunkRequest, AutoTableChunkRequest

logger = logging.getLogger(__name__)

DEFAULT_PARSER_CONFIG: dict[str, Any] = {
    "auto_chunk_after_parse": True,
    "sections": {
        "enabled": True,
        "target_level": 2,
        "max_chars": 8192,
    },
    "tables": {
        "enabled": True,
        "include_caption": True,
        "max_caption_gap": 0.04,
    },
    "images": {
        "enabled": True,
        "include_caption": True,
        "require_caption": True,
        "max_caption_gap": 0.04,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def normalize_parser_config(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    return deep_merge(DEFAULT_PARSER_CONFIG, payload)


def file_chunk_override(file_id: str) -> dict[str, Any]:
    row = db.get_conn().execute(
        "SELECT metadata FROM files WHERE id=?",
        (file_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        metadata = json.loads(row["metadata"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(metadata, dict):
        return {}
    override = metadata.get("chunk_config")
    return override if isinstance(override, dict) else {}


def primary_kb_parser_config(file_id: str) -> dict[str, Any]:
    row = db.get_conn().execute(
        """SELECT kb.parser_config
           FROM knowledge_base_files kbf
           JOIN knowledge_bases kb ON kb.id=kbf.knowledge_base_id
           WHERE kbf.file_id=? AND kb.status='active'
           ORDER BY kb.is_default DESC, kbf.created_at ASC
           LIMIT 1""",
        (file_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(row["parser_config"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_file_chunk_config(file_id: str) -> dict[str, Any]:
    """DEFAULT <- knowledge-base parser_config <- file.metadata.chunk_config."""
    return normalize_parser_config(
        deep_merge(primary_kb_parser_config(file_id), file_chunk_override(file_id))
    )


async def run_auto_chunk_pipeline(
    file_id: str,
    parse_id: str,
    *,
    chunk_config: dict[str, Any] | None = None,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Create section/table/image chunks for a completed parse."""
    from .routers.auto_chunks import (
        auto_image_chunks,
        auto_section_chunks,
        auto_table_chunks,
    )

    config = normalize_parser_config(chunk_config or resolve_file_chunk_config(file_id))
    summary: dict[str, Any] = {
        "file_id": file_id,
        "parse_id": parse_id,
        "sections": 0,
        "tables": 0,
        "images": 0,
        "skipped": [],
        "config": config,
    }

    sections_cfg = config.get("sections") or {}
    if sections_cfg.get("enabled", True):
        result = await auto_section_chunks(
            file_id,
            AutoSectionChunkRequest(
                parse_id=parse_id,
                dry_run=False,
                target_level=int(sections_cfg.get("target_level") or 2),
                max_chars=int(sections_cfg.get("max_chars") or 8192),
                skip_existing=skip_existing,
            ),
        )
        summary["sections"] = len(result.get("created") or [])
    else:
        summary["skipped"].append("sections")

    tables_cfg = config.get("tables") or {}
    if tables_cfg.get("enabled", True):
        result = await auto_table_chunks(
            file_id,
            AutoTableChunkRequest(
                parse_id=parse_id,
                dry_run=False,
                include_caption=bool(tables_cfg.get("include_caption", True)),
                max_caption_gap=float(tables_cfg.get("max_caption_gap") or 0.04),
                skip_existing=skip_existing,
            ),
        )
        summary["tables"] = len(result.get("created") or [])
    else:
        summary["skipped"].append("tables")

    images_cfg = config.get("images") or {}
    if images_cfg.get("enabled", True):
        result = await auto_image_chunks(
            file_id,
            AutoImageChunkRequest(
                parse_id=parse_id,
                dry_run=False,
                include_caption=bool(images_cfg.get("include_caption", True)),
                require_caption=bool(images_cfg.get("require_caption", True)),
                max_caption_gap=float(images_cfg.get("max_caption_gap") or 0.04),
                skip_existing=skip_existing,
            ),
        )
        summary["images"] = len(result.get("created") or [])
    else:
        summary["skipped"].append("images")

    summary["total"] = (
        int(summary["sections"]) + int(summary["tables"]) + int(summary["images"])
    )
    logger.info(
        "auto-chunk pipeline file=%s parse=%s total=%s sections=%s tables=%s images=%s",
        file_id,
        parse_id,
        summary["total"],
        summary["sections"],
        summary["tables"],
        summary["images"],
    )
    return summary
