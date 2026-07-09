"""Chunk metadata v2 layering helpers.

The old schema stored user-facing metadata, parser trace, and auto-chunk
bookkeeping in one flat JSON object. These helpers keep the compatibility rules
centralized while new writes use separate JSON layers.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


METADATA_V2_KEYS = {
    "standard_no",
    "doc_type",
    "knowledge_categories",
    "applicable_date",
    "publish_date",
    "effective_date",
    "notes",
    "content_type",
    "table_no",
    "table_header",
    "table_columns",
    "table_ref",
    "figure_ref",
    "figure_no",
    "figure_header",
    "section",
    "section_title",
    "section_level",
    "section_path",
    "tags",
    "summary",
    "keywords",
    "scope",
}

SOURCE_TRACE_KEYS = {
    "mineru_parse_id",
    "mineru_model",
    "mineru_page_idx",
    "mineru_block_index",
    "mineru_table_bbox",
    "mineru_image_bbox",
    "mineru_caption_bbox",
    "source_blocks",
    "page_start",
    "page_end",
    "bbox_union",
    "snapshot_path",
    "source_file_id",
    "source_file_hash",
    "parser",
}

CHUNK_LOGIC_KEYS = {
    "auto_source",
    "chunk_type",
    "strategy",
    "strategy_version",
    "split_from",
    "split_reason",
    "chunk_part",
    "chunk_parts",
    "child_sections",
    "parent_section",
    "parent_title",
    "parent_chunk_id",
    "child_chunk_ids",
    "related_chunk_ids",
    "relations",
}

_BBOX_SOURCE_TYPES = {
    "mineru_table_bbox": "table",
    "mineru_image_bbox": "image",
    "mineru_caption_bbox": "caption",
}


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return deepcopy(parsed) if isinstance(parsed, dict) else {}


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def merge_missing(target: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Fill empty fields in target from fallback without overwriting edits."""
    for key, value in fallback.items():
        if key not in target or is_empty(target.get(key)):
            target[key] = deepcopy(value)
    return target


def migrate_chunk_metadata_v1_to_v2(
    metadata: dict[str, Any] | str | None,
) -> dict[str, dict[str, Any]]:
    """Split legacy flat metadata into v2 semantic layers.

    Unknown keys stay in `metadata_v2` because legacy user fields are more likely
    business metadata than parser internals unless explicitly recognized.
    """
    legacy = parse_json_object(metadata)
    metadata_v2: dict[str, Any] = {}
    source_trace: dict[str, Any] = {}
    chunk_logic: dict[str, Any] = {}

    for key, value in legacy.items():
        if key in SOURCE_TRACE_KEYS:
            source_trace[key] = deepcopy(value)
        elif key in CHUNK_LOGIC_KEYS:
            chunk_logic[key] = deepcopy(value)
        else:
            metadata_v2[key] = deepcopy(value)

    _normalize_source_blocks(source_trace)
    _infer_chunk_logic(metadata_v2, chunk_logic)
    return {
        "metadata_v2": metadata_v2,
        "source_trace": source_trace,
        "chunk_logic": chunk_logic,
    }


def ensure_layered_chunk(
    *,
    metadata: dict[str, Any] | str | None = None,
    metadata_v2: dict[str, Any] | str | None = None,
    source_trace: dict[str, Any] | str | None = None,
    chunk_logic: dict[str, Any] | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return effective v2 layers, using legacy metadata as fallback."""
    migrated = migrate_chunk_metadata_v1_to_v2(metadata)
    out = {
        "metadata_v2": parse_json_object(metadata_v2),
        "source_trace": parse_json_object(source_trace),
        "chunk_logic": parse_json_object(chunk_logic),
    }
    for key in out:
        merge_missing(out[key], migrated[key])
    return out


def flatten_for_legacy(
    metadata_v2: dict[str, Any] | str | None,
    source_trace: dict[str, Any] | str | None,
    chunk_logic: dict[str, Any] | str | None,
    legacy_metadata: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Compose the legacy `metadata` response shape from layered fields."""
    legacy = parse_json_object(legacy_metadata)
    out: dict[str, Any] = {}
    out.update(parse_json_object(metadata_v2))
    out.update(parse_json_object(source_trace))
    out.update(parse_json_object(chunk_logic))
    merge_missing(out, legacy)
    return out


def split_flat_metadata_for_write(
    metadata: dict[str, Any] | str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    layers = migrate_chunk_metadata_v1_to_v2(metadata)
    return layers["metadata_v2"], layers["source_trace"], layers["chunk_logic"]


def source_trace_for_region(page: int, bbox: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_start": page,
        "page_end": page,
        "source_blocks": [
            {
                "page_idx": page - 1,
                "block_index": None,
                "type": "manual_region",
                "bbox": _xywh_to_xyxy(bbox),
            }
        ],
    }


def chunk_logic_for_manual() -> dict[str, Any]:
    return {
        "chunk_type": "manual_region",
        "auto_source": None,
    }


def _normalize_source_blocks(source_trace: dict[str, Any]) -> None:
    blocks = source_trace.get("source_blocks")
    if not isinstance(blocks, list):
        blocks = []
    else:
        blocks = deepcopy(blocks)

    page_idx = source_trace.get("mineru_page_idx")
    block_index = source_trace.get("mineru_block_index")
    if isinstance(page_idx, int):
        source_trace.setdefault("page_start", page_idx + 1)
        source_trace.setdefault("page_end", page_idx + 1)
    for key, block_type in _BBOX_SOURCE_TYPES.items():
        bbox = source_trace.get(key)
        if not bbox:
            continue
        candidate = {
            "page_idx": page_idx,
            "block_index": block_index,
            "type": block_type,
            "bbox": deepcopy(bbox),
        }
        if not _contains_block(blocks, candidate):
            blocks.append(candidate)
    if blocks:
        source_trace["source_blocks"] = blocks

    boxes = [
        block.get("bbox")
        for block in blocks
        if isinstance(block, dict) and _valid_xyxy(block.get("bbox"))
    ]
    if boxes and "bbox_union" not in source_trace:
        source_trace["bbox_union"] = _union_xyxy(boxes)


def _infer_chunk_logic(metadata_v2: dict[str, Any], chunk_logic: dict[str, Any]) -> None:
    auto_source = chunk_logic.get("auto_source")
    content_type = metadata_v2.get("content_type")
    if "chunk_type" not in chunk_logic:
        if auto_source:
            chunk_logic["chunk_type"] = f"auto_{content_type or 'chunk'}"
        elif content_type:
            chunk_logic["chunk_type"] = str(content_type)


def _contains_block(blocks: list[Any], candidate: dict[str, Any]) -> bool:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if (
            block.get("page_idx") == candidate.get("page_idx")
            and block.get("block_index") == candidate.get("block_index")
            and block.get("type") == candidate.get("type")
            and block.get("bbox") == candidate.get("bbox")
        ):
            return True
    return False


def _valid_xyxy(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(item, int | float) for item in value)
    )


def _union_xyxy(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _xywh_to_xyxy(bbox: dict[str, Any]) -> list[float] | None:
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        w = float(bbox["w"])
        h = float(bbox["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return [x, y, x + w, y + h]
