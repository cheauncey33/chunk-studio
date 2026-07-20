"""Chunk metadata layering helpers.

Legacy builds stored business metadata, parser trace, and chunking bookkeeping
in one flat JSON object. New writes use four explicit layers:

- business_metadata: stable business/search/display fields
- source_trace: parser/PDF provenance and page locations
- chunk_logic: how the chunk was created
- relations: references and parent/child links
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


BUSINESS_METADATA_KEYS = {
    "standard_no",
    "doc_type",
    "knowledge_categories",
    "applicable_date",
    "publish_date",
    "effective_date",
    "content_type",
    "table_kind",
    "table_no",
    "table_title",
    "table_columns",
    "figure_no",
    "figure_title",
    "section",
    "section_title",
    "section_level",
    "section_path",
    "tags",
    "keywords",
    "questions",
}

SOURCE_TRACE_KEYS = {
    "parser",
    "parser_version",
    "parse_id",
    "page_start",
    "page_end",
    "source_blocks",
    "bbox_union",
    "snapshot_path",
    "source_file_hash",
}

CHUNK_LOGIC_KEYS = {
    "creation_mode",
    "generator",
    "chunk_type",
    "strategy",
    "strategy_version",
    "split",
}

RELATION_KEYS = {
    "references",
    "parents",
    "children",
    "related",
    "belongs_to",
}

_LEGACY_TRACE_RENAMES = {
    "mineru_parse_id": "parse_id",
    "mineru_model": "parser_version",
}

_LEGACY_TITLE_RENAMES = {
    "table_header": "table_title",
    "figure_header": "figure_title",
}

_LEGACY_BBOX_SOURCE_TYPES = {
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


def migrate_chunk_metadata_v1(
    metadata: dict[str, Any] | str | None,
) -> dict[str, dict[str, Any]]:
    """Split legacy flat metadata into semantic layers."""
    legacy = parse_json_object(metadata)
    business_metadata: dict[str, Any] = {}
    source_trace: dict[str, Any] = {}
    chunk_logic: dict[str, Any] = {}
    relations: dict[str, Any] = {}

    for raw_key, value in legacy.items():
        key = _LEGACY_TITLE_RENAMES.get(raw_key, raw_key)
        if key == "notes" or key == "auto_chunk_types":
            continue
        if raw_key in _LEGACY_TRACE_RENAMES:
            source_trace[_LEGACY_TRACE_RENAMES[raw_key]] = deepcopy(value)
        elif raw_key in {"mineru_page_idx", "mineru_block_index", *list(_LEGACY_BBOX_SOURCE_TYPES)}:
            source_trace[raw_key] = deepcopy(value)
        elif key in SOURCE_TRACE_KEYS:
            source_trace[key] = deepcopy(value)
        elif raw_key == "auto_source":
            _merge_auto_source(chunk_logic, str(value))
        elif raw_key in {"split_from", "split_reason", "chunk_part", "chunk_parts"}:
            _merge_legacy_split(chunk_logic, raw_key, value)
        elif raw_key in {"table_ref", "figure_ref"}:
            _merge_reference_relation(relations, raw_key, value)
        elif raw_key == "child_sections":
            _merge_legacy_relation(relations, raw_key, value)
        elif raw_key in {"parent_section", "parent_title"}:
            belongs_to = relations.setdefault("belongs_to", {})
            if isinstance(belongs_to, dict):
                belongs_to[raw_key.replace("parent_", "")] = deepcopy(value)
        elif raw_key in {"parent_chunk_id", "child_chunk_ids", "related_chunk_ids", "relations"}:
            _merge_legacy_relation(relations, raw_key, value)
        elif key in CHUNK_LOGIC_KEYS:
            chunk_logic[key] = deepcopy(value)
        elif key in RELATION_KEYS:
            relations[key] = deepcopy(value)
        else:
            business_metadata[key] = deepcopy(value)

    _normalize_business_metadata(business_metadata)
    _normalize_source_trace(source_trace)
    _normalize_chunk_logic(business_metadata, chunk_logic)
    return {
        "business_metadata": business_metadata,
        "source_trace": source_trace,
        "chunk_logic": chunk_logic,
        "relations": relations,
    }


def ensure_layered_chunk(
    *,
    metadata: dict[str, Any] | str | None = None,
    business_metadata: dict[str, Any] | str | None = None,
    source_trace: dict[str, Any] | str | None = None,
    chunk_logic: dict[str, Any] | str | None = None,
    relations: dict[str, Any] | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return effective layers using legacy fields as fallback."""
    migrated = migrate_chunk_metadata_v1(metadata)
    out = {
        "business_metadata": parse_json_object(business_metadata),
        "source_trace": parse_json_object(source_trace),
        "chunk_logic": parse_json_object(chunk_logic),
        "relations": parse_json_object(relations),
    }
    for key in out:
        merge_missing(out[key], migrated[key])
    if _is_default_manual_logic(out["chunk_logic"]) and migrated["chunk_logic"].get("creation_mode") == "auto":
        out["chunk_logic"] = deepcopy(migrated["chunk_logic"])
    _normalize_business_metadata(out["business_metadata"])
    _normalize_source_trace(out["source_trace"])
    _normalize_chunk_logic(out["business_metadata"], out["chunk_logic"])
    return out


def flatten_for_legacy(
    business_metadata: dict[str, Any] | str | None,
    source_trace: dict[str, Any] | str | None,
    chunk_logic: dict[str, Any] | str | None,
    relations: dict[str, Any] | str | None,
    legacy_metadata: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Compose the deprecated flat `metadata` response shape."""
    legacy = parse_json_object(legacy_metadata)
    out: dict[str, Any] = {}
    out.update(parse_json_object(business_metadata))
    out.update(parse_json_object(source_trace))
    out.update(parse_json_object(chunk_logic))
    out.update(parse_json_object(relations))
    merge_missing(out, legacy)
    return out


def split_flat_metadata_for_write(
    metadata: dict[str, Any] | str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    layers = migrate_chunk_metadata_v1(metadata)
    return (
        layers["business_metadata"],
        layers["source_trace"],
        layers["chunk_logic"],
        layers["relations"],
    )


def source_trace_for_region(page: int, bbox: dict[str, Any]) -> dict[str, Any]:
    xyxy = _xywh_to_xyxy(bbox)
    return {
        "page_start": page,
        "page_end": page,
        "source_blocks": [
            {
                "page": page,
                "block_index": None,
                "type": "manual_region",
                "bbox": xyxy,
            }
        ],
        "bbox_union": xyxy,
    }


def chunk_logic_for_manual() -> dict[str, Any]:
    return {
        "creation_mode": "manual",
        "generator": "user",
        "chunk_type": "manual_region",
        "strategy": "manual_region",
        "strategy_version": "1",
    }


def chunk_logic_for_auto(chunk_type: str, strategy: str, *, split: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "creation_mode": "auto",
        "generator": "mineru",
        "chunk_type": chunk_type,
        "strategy": strategy,
        "strategy_version": "1",
    }
    if split:
        out["split"] = split
    return out


def relations_from_refs(table_refs: list[str] | None = None, figure_refs: list[str] | None = None) -> dict[str, Any]:
    references = []
    for value in table_refs or []:
        references.append({"kind": "table", "no": str(value), "type": "references_table"})
    for value in figure_refs or []:
        references.append({"kind": "figure", "no": str(value), "type": "references_figure"})
    return {"references": references} if references else {}


def _normalize_business_metadata(business_metadata: dict[str, Any]) -> None:
    for old, new in _LEGACY_TITLE_RENAMES.items():
        if old in business_metadata and new not in business_metadata:
            business_metadata[new] = business_metadata.pop(old)
        else:
            business_metadata.pop(old, None)
    business_metadata.pop("notes", None)
    business_metadata.pop("auto_chunk_types", None)
    business_metadata.pop("child_sections", None)
    business_metadata.pop("table_ref", None)
    business_metadata.pop("figure_ref", None)


def _normalize_source_trace(source_trace: dict[str, Any]) -> None:
    for old, new in _LEGACY_TRACE_RENAMES.items():
        if old in source_trace and new not in source_trace:
            source_trace[new] = source_trace.pop(old)
        else:
            source_trace.pop(old, None)
    source_trace.setdefault("parser", "mineru")

    page_idx = source_trace.pop("mineru_page_idx", None)
    block_index = source_trace.pop("mineru_block_index", None)
    if isinstance(page_idx, int):
        source_trace.setdefault("page_start", page_idx + 1)
        source_trace.setdefault("page_end", page_idx + 1)

    blocks = source_trace.get("source_blocks")
    if not isinstance(blocks, list):
        blocks = []
    else:
        blocks = [_normalize_source_block(block) for block in blocks if isinstance(block, dict)]

    for key, block_type in _LEGACY_BBOX_SOURCE_TYPES.items():
        bbox = source_trace.pop(key, None)
        if not bbox:
            continue
        candidate = {
            "page": page_idx + 1 if isinstance(page_idx, int) else source_trace.get("page_start"),
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
    if boxes:
        source_trace.setdefault("bbox_union", _union_xyxy(boxes))


def _normalize_source_block(block: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(block)
    if "page" not in out and isinstance(out.get("page_idx"), int):
        out["page"] = out["page_idx"] + 1
    out.pop("page_idx", None)
    return out


def _normalize_chunk_logic(business_metadata: dict[str, Any], chunk_logic: dict[str, Any]) -> None:
    if not chunk_logic and not business_metadata.get("content_type"):
        return
    legacy_auto_source = chunk_logic.pop("auto_source", None)
    if legacy_auto_source and "creation_mode" not in chunk_logic:
        _merge_auto_source(chunk_logic, str(legacy_auto_source))
    if "chunk_type" not in chunk_logic and business_metadata.get("content_type"):
        chunk_logic["chunk_type"] = str(business_metadata["content_type"])
    if "creation_mode" not in chunk_logic:
        chunk_logic["creation_mode"] = "auto" if chunk_logic.get("generator") else "manual"
    if "generator" not in chunk_logic:
        chunk_logic["generator"] = "mineru" if chunk_logic["creation_mode"] == "auto" else "user"
    chunk_type = str(chunk_logic.get("chunk_type") or "")
    if chunk_type.startswith("auto_"):
        chunk_logic["chunk_type"] = chunk_type.removeprefix("auto_")


def _is_default_manual_logic(chunk_logic: dict[str, Any]) -> bool:
    return (
        chunk_logic.get("creation_mode") == "manual"
        and chunk_logic.get("generator") == "user"
        and set(chunk_logic.keys()).issubset({"creation_mode", "generator", "chunk_type"})
    )


def _merge_auto_source(chunk_logic: dict[str, Any], value: str) -> None:
    chunk_logic.setdefault("creation_mode", "auto")
    if value.startswith("mineru"):
        chunk_logic.setdefault("generator", "mineru")
    parts = value.split("_", 1)
    if len(parts) == 2:
        chunk_logic.setdefault("chunk_type", parts[1])


def _merge_legacy_split(chunk_logic: dict[str, Any], key: str, value: Any) -> None:
    split = chunk_logic.setdefault("split", {})
    if not isinstance(split, dict):
        split = {}
        chunk_logic["split"] = split
    mapping = {
        "split_from": "from",
        "split_reason": "reason",
        "chunk_part": "part",
        "chunk_parts": "parts",
    }
    split[mapping[key]] = deepcopy(value)


def _merge_reference_relation(relations: dict[str, Any], key: str, value: Any) -> None:
    kind = "table" if key == "table_ref" else "figure"
    values = value if isinstance(value, list) else [value]
    refs = relations.setdefault("references", [])
    if not isinstance(refs, list):
        refs = []
        relations["references"] = refs
    for item in values:
        if is_empty(item):
            continue
        candidate = {"kind": kind, "no": str(item), "type": f"references_{kind}"}
        if candidate not in refs:
            refs.append(candidate)


def _merge_legacy_relation(relations: dict[str, Any], key: str, value: Any) -> None:
    if key == "parent_chunk_id":
        relations.setdefault("parents", [{"id": value, "type": "parent_chunk"}])
    elif key == "child_chunk_ids":
        relations.setdefault(
            "children",
            [{"id": item, "type": "child_chunk"} for item in (value if isinstance(value, list) else [value])],
        )
    elif key == "related_chunk_ids":
        relations.setdefault(
            "related",
            [{"id": item, "type": "related_chunk"} for item in (value if isinstance(value, list) else [value])],
        )
    elif key == "child_sections":
        relations.setdefault(
            "children",
            [{"section": item, "type": "child_section"} for item in (value if isinstance(value, list) else [value])],
        )
    elif key == "relations" and isinstance(value, dict):
        merge_missing(relations, value)
    elif key == "relations" and isinstance(value, list):
        relations.setdefault("related", value)


def _contains_block(blocks: list[Any], candidate: dict[str, Any]) -> bool:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if (
            block.get("page") == candidate.get("page")
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
