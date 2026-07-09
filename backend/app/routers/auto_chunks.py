"""Auto chunk generation from MinerU full-document parse results."""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import config, db, extractors, pdf
from ..adapters.ocr import _repair_mojibake
from ..models import AutoImageChunkRequest, AutoSectionChunkRequest, AutoTableChunkRequest
from .chunks import _row_to_out

router = APIRouter(prefix="/auto-chunks", tags=["auto-chunks"])

_TABLE_TITLE_RE = re.compile(r"^表\s*0*(\d+)\s*(.+)?")
_TABLE_REF_RE = re.compile(r"表\s*0*(\d+)(?:\s*[～~\-—至]\s*表?\s*0*(\d+))?")
_FIGURE_TITLE_RE = re.compile(r"^图\s*0*(\d+)\s*(.+)?")
_FIGURE_REF_RE = re.compile(r"图\s*0*(\d+)(?:\s*[～~\-—至]\s*图?\s*0*(\d+))?")
_SECTION_TITLE_RE = re.compile(r"^(?P<section>\d+(?:\.\d+)*)(?:\s+(?P<title>.+))?$")
_CAPTION_TYPES = {"image_caption", "table_caption", "text", "paragraph_title", "title"}
_SECTION_TEXT_TYPES = {"text", "ocr_text", "paragraph_title", "title", "doc_title", "equation", "inline_formula"}
_SKIP_SECTION_TYPES = {"header", "page_number", "index", "table", "image", "image_caption", "image_footnote"}
_DEDUP_SIMILARITY_THRESHOLD = 0.95


@dataclass
class Candidate:
    page: int
    bbox: dict[str, float]
    table_bbox: list[float]
    caption_bbox: list[float] | None
    caption: str
    table_html: str
    block_index: int
    metadata: dict[str, Any]


@dataclass
class TextBlock:
    page_idx: int
    block_index: int
    type: str
    text: str
    bbox: list[float] | None


@dataclass
class SectionUnit:
    section: str
    title: str
    level: int
    section_path: list[dict[str, str]]
    blocks: list[TextBlock]


@dataclass
class SectionCandidate:
    page: int
    bbox: dict[str, float]
    text: str
    section: str
    section_title: str
    section_level: int
    section_path: list[dict[str, str]]
    source_blocks: list[dict[str, Any]]
    split_from: str | None
    split_reason: str | None
    chunk_part: int | None
    chunk_parts: int | None
    metadata: dict[str, Any]


@dataclass
class ImageCandidate:
    page: int
    bbox: dict[str, float]
    image_bbox: list[float]
    caption_bbox: list[float] | None
    caption: str
    block_index: int
    section: str | None
    section_path: list[dict[str, str]]
    metadata: dict[str, Any]


@router.post("/tables/{file_id}")
async def auto_table_chunks(file_id: str, body: AutoTableChunkRequest):
    """Preview or create table chunks from a MinerU parse.

    Uses MinerU's normalized `model.json` coordinates. When a table is preceded
    by a nearby caption-like block such as `image_caption`, the caption bbox is
    unioned with the table bbox and its text is prepended to the chunk text.
    """
    f = _get_file(file_id)
    parse = _select_parse(file_id, body.parse_id)
    zip_rel = parse.get("raw_zip_path")
    if not zip_rel:
        raise HTTPException(409, "selected parse has no raw_zip_path")

    candidates = _build_table_candidates(f, parse, body)
    if body.dry_run:
        return {
            "file_id": file_id,
            "parse_id": parse["id"],
            "dry_run": True,
            "candidates": [_candidate_out(c) for c in candidates],
            "created": [],
            "skipped": 0,
        }

    created = []
    skipped = 0
    for candidate in candidates:
        text = _chunk_text(candidate.caption, candidate.table_html)
        if body.skip_existing and _existing_similar_chunk(file_id, candidate.page, text):
            skipped += 1
            continue
        chunk = await _create_chunk_from_candidate(f, parse, candidate)
        created.append(chunk)

    return {
        "file_id": file_id,
        "parse_id": parse["id"],
        "dry_run": False,
        "candidates": [_candidate_out(c) for c in candidates],
        "created": created,
        "skipped": skipped,
    }


@router.post("/images/{file_id}")
async def auto_image_chunks(file_id: str, body: AutoImageChunkRequest):
    """Preview or create image/figure chunks from MinerU image blocks."""
    f = _get_file(file_id)
    parse = _select_parse(file_id, body.parse_id)
    if not parse.get("raw_zip_path"):
        raise HTTPException(409, "selected parse has no raw_zip_path")

    candidates = _build_image_candidates(f, parse, body)
    if body.dry_run:
        return {
            "file_id": file_id,
            "parse_id": parse["id"],
            "dry_run": True,
            "candidates": [_image_candidate_out(c) for c in candidates],
            "created": [],
            "skipped": 0,
        }

    created = []
    skipped = 0
    for candidate in candidates:
        text = candidate.caption or f"图像 第 {candidate.page} 页"
        if body.skip_existing and _existing_similar_chunk(file_id, candidate.page, text):
            skipped += 1
            continue
        chunk = await _create_image_chunk(f, candidate)
        created.append(chunk)

    return {
        "file_id": file_id,
        "parse_id": parse["id"],
        "dry_run": False,
        "candidates": [_image_candidate_out(c) for c in candidates],
        "created": created,
        "skipped": skipped,
    }


@router.post("/sections/{file_id}")
async def auto_section_chunks(file_id: str, body: AutoSectionChunkRequest):
    """Preview or create section chunks from MinerU text/title blocks.

    Default strategy: use second-level headings (e.g. 4.2) as the primary
    boundary. If a second-level subtree exceeds `max_chars`, pack consecutive
    third-level child sections into chunks up to `max_chars`.
    """
    f = _get_file(file_id)
    parse = _select_parse(file_id, body.parse_id)
    if not parse.get("raw_zip_path"):
        raise HTTPException(409, "selected parse has no raw_zip_path")

    candidates = _build_section_candidates(f, parse, body)
    if body.dry_run:
        return {
            "file_id": file_id,
            "parse_id": parse["id"],
            "dry_run": True,
            "candidates": [_section_candidate_out(c) for c in candidates],
            "created": [],
            "skipped": 0,
        }

    created = []
    skipped = 0
    for candidate in candidates:
        if body.skip_existing and _existing_similar_chunk(file_id, candidate.page, candidate.text):
            skipped += 1
            continue
        chunk = await _create_section_chunk(f, candidate)
        created.append(chunk)

    return {
        "file_id": file_id,
        "parse_id": parse["id"],
        "dry_run": False,
        "candidates": [_section_candidate_out(c) for c in candidates],
        "created": created,
        "skipped": skipped,
    }


def _get_file(file_id: str) -> dict[str, Any]:
    row = db.get_conn().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "file not found")
    return dict(row)


def _select_parse(file_id: str, parse_id: str | None) -> dict[str, Any]:
    if parse_id:
        row = db.get_conn().execute(
            "SELECT * FROM document_parses WHERE id=? AND file_id=?",
            (parse_id, file_id),
        ).fetchone()
    else:
        row = db.get_conn().execute(
            """SELECT * FROM document_parses
               WHERE file_id=? AND status='done' AND raw_zip_path IS NOT NULL
               ORDER BY updated_at DESC, created_at DESC
               LIMIT 1""",
            (file_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "MinerU parse result not found for this file")
    return dict(row)


def _build_table_candidates(
    f: dict[str, Any], parse: dict[str, Any], body: AutoTableChunkRequest
) -> list[Candidate]:
    model = _load_model_json(parse["raw_zip_path"])
    candidates: list[Candidate] = []
    for page_idx, blocks in enumerate(model):
        if not isinstance(blocks, list):
            continue
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "table":
                continue
            table_bbox = _normalize_bbox(block.get("bbox"))
            if not table_bbox:
                continue
            caption_block = _find_caption_block(blocks, block_index, table_bbox, body)
            caption_bbox = _normalize_bbox(caption_block.get("bbox")) if caption_block else None
            caption = _clean_text(caption_block.get("content")) if caption_block else ""
            table_html = _clean_text(block.get("content"))
            merged_bbox = _bbox_to_xywh(_union_bbox(table_bbox, caption_bbox or table_bbox))
            text = _chunk_text(caption, table_html)
            metadata = _metadata_for_candidate(f, text, parse, page_idx, block_index, caption, table_bbox, caption_bbox)
            candidates.append(Candidate(
                page=page_idx + 1,
                bbox=merged_bbox,
                table_bbox=table_bbox,
                caption_bbox=caption_bbox,
                caption=caption,
                table_html=table_html,
                block_index=block_index,
                metadata=metadata,
            ))
    return candidates


def _build_image_candidates(
    f: dict[str, Any], parse: dict[str, Any], body: AutoImageChunkRequest
) -> list[ImageCandidate]:
    model = _load_model_json(parse["raw_zip_path"])
    section_at: dict[tuple[int, int], tuple[str, list[dict[str, str]]]] = {}
    current_section: tuple[str, list[dict[str, str]]] | None = None
    section_units = _extract_section_units(model)
    for unit in section_units:
        if unit.blocks:
            section_at[(unit.blocks[0].page_idx, unit.blocks[0].block_index)] = (unit.section, unit.section_path)

    candidates: list[ImageCandidate] = []
    for page_idx, blocks in enumerate(model):
        if not isinstance(blocks, list):
            continue
        for block_index, block in enumerate(blocks):
            if (page_idx, block_index) in section_at:
                current_section = section_at[(page_idx, block_index)]
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            image_bbox = _normalize_bbox(block.get("bbox"))
            if not image_bbox:
                continue
            caption_block = _find_image_caption_block(blocks, block_index, image_bbox, body)
            caption_bbox = _normalize_bbox(caption_block.get("bbox")) if caption_block else None
            caption = _clean_text(caption_block.get("content")) if caption_block else ""
            if body.require_caption and not _FIGURE_TITLE_RE.search(caption.strip()):
                continue
            merged_bbox = _bbox_to_xywh(_union_bbox(image_bbox, caption_bbox or image_bbox))
            section, section_path = current_section if current_section else (None, [])
            metadata = _metadata_for_image_candidate(
                f, parse, page_idx, block_index, caption, image_bbox, caption_bbox, section, section_path
            )
            candidates.append(ImageCandidate(
                page=page_idx + 1,
                bbox=merged_bbox,
                image_bbox=image_bbox,
                caption_bbox=caption_bbox,
                caption=caption,
                block_index=block_index,
                section=section,
                section_path=section_path,
                metadata=metadata,
            ))
    return candidates


def _find_image_caption_block(
    blocks: list[Any], image_index: int, image_bbox: list[float], body: AutoImageChunkRequest
) -> dict[str, Any] | None:
    if not body.include_caption:
        return None
    best: dict[str, Any] | None = None
    best_gap = body.max_caption_gap
    for idx in range(image_index + 1, min(len(blocks), image_index + 5)):
        block = blocks[idx]
        if not isinstance(block, dict) or block.get("type") not in {"image_caption", "text"}:
            continue
        bbox = _normalize_bbox(block.get("bbox"))
        if not bbox:
            continue
        text = _clean_text(block.get("content"))
        gap = bbox[1] - image_bbox[3]
        horizontal_overlap = max(0.0, min(image_bbox[2], bbox[2]) - max(image_bbox[0], bbox[0]))
        min_width = max(0.001, min(image_bbox[2] - image_bbox[0], bbox[2] - bbox[0]))
        if gap < -0.005 or gap > body.max_caption_gap:
            continue
        if horizontal_overlap / min_width < 0.35:
            continue
        if not _FIGURE_TITLE_RE.search(text.strip()):
            continue
        if gap <= best_gap:
            best = block
            best_gap = gap
    return best


def _build_section_candidates(
    f: dict[str, Any], parse: dict[str, Any], body: AutoSectionChunkRequest
) -> list[SectionCandidate]:
    model = _load_model_json(parse["raw_zip_path"])
    units = _extract_section_units(model)
    top_units = [u for u in units if u.level == body.target_level]
    candidates: list[SectionCandidate] = []
    for unit in top_units:
        subtree = _section_subtree(units, unit)
        whole = _section_candidate_from_units(f, parse, subtree, body, split_from=None, split_reason=None)
        if len(whole.text) <= body.max_chars:
            candidates.append(whole)
            continue

        packed = _pack_oversize_section(f, parse, unit, subtree, body)
        candidates.extend(packed)
    return candidates


def _extract_section_units(model: list[Any]) -> list[SectionUnit]:
    blocks = _flatten_section_blocks(model)
    units: list[SectionUnit] = []
    current: SectionUnit | None = None
    stack: list[dict[str, str]] = []
    consumed: set[tuple[int, int]] = set()

    for idx, block in enumerate(blocks):
        key = (block.page_idx, block.block_index)
        if key in consumed:
            continue
        heading = _parse_section_heading(block, blocks[idx + 1] if idx + 1 < len(blocks) else None)
        if heading:
            section, title, level, consume_next, next_remainder = heading
            if consume_next:
                nxt = blocks[idx + 1]
                consumed.add((nxt.page_idx, nxt.block_index))
            while stack and _section_level(stack[-1]["section"]) >= level:
                stack.pop()
            path_item = {"section": section, "title": title}
            section_path = [*stack, path_item]
            stack.append(path_item)
            heading_text = _section_heading_text(section, title)
            heading_block = TextBlock(
                page_idx=block.page_idx,
                block_index=block.block_index,
                type=block.type,
                text=heading_text,
                bbox=block.bbox,
            )
            if consume_next:
                nxt = blocks[idx + 1]
                heading_block = TextBlock(
                    page_idx=block.page_idx,
                    block_index=block.block_index,
                    type=block.type,
                    text=heading_text,
                    bbox=_union_optional_bbox(block.bbox, nxt.bbox),
                )
            current = SectionUnit(
                section=section,
                title=title,
                level=level,
                section_path=section_path,
                blocks=[heading_block],
            )
            if consume_next and next_remainder:
                nxt = blocks[idx + 1]
                current.blocks.append(TextBlock(
                    page_idx=nxt.page_idx,
                    block_index=nxt.block_index,
                    type=nxt.type,
                    text=next_remainder,
                    bbox=nxt.bbox,
                ))
            units.append(current)
        elif current and block.text:
            current.blocks.append(block)
    _fill_empty_section_titles(units)
    return units


def _fill_empty_section_titles(units: list[SectionUnit]) -> None:
    for unit in units:
        if unit.title or len(unit.blocks) < 2:
            continue
        source = unit.blocks[1]
        lines = [line.strip() for line in source.text.splitlines() if line.strip()]
        if not lines:
            continue
        unit.title = lines[0]
        unit.blocks[0].text = _section_heading_text(unit.section, unit.title)
        unit.section_path[-1]["title"] = unit.title


def _flatten_section_blocks(model: list[Any]) -> list[TextBlock]:
    out: list[TextBlock] = []
    for page_idx, page in enumerate(model):
        if not isinstance(page, list):
            continue
        for block_index, block in enumerate(page):
            if not isinstance(block, dict):
                continue
            typ = str(block.get("type") or "")
            if typ in _SKIP_SECTION_TYPES or typ not in _SECTION_TEXT_TYPES:
                continue
            text = _clean_text(block.get("content"))
            if not text:
                continue
            out.append(TextBlock(
                page_idx=page_idx,
                block_index=block_index,
                type=typ,
                text=text,
                bbox=_normalize_bbox(block.get("bbox")),
            ))
    return out


def _parse_section_heading(
    block: TextBlock, next_block: TextBlock | None
) -> tuple[str, str, int, bool, str] | None:
    if block.type not in {"paragraph_title", "title", "doc_title"}:
        return None
    text = block.text.strip()
    m = _SECTION_TITLE_RE.match(text)
    if not m:
        return None
    section = m.group("section")
    title = (m.group("title") or "").strip()
    consume_next = False
    next_remainder = ""
    if not title and next_block and next_block.type in {"text", "ocr_text"}:
        same_page = next_block.page_idx == block.page_idx
        close = False
        if block.bbox and next_block.bbox:
            y_gap = max(0.0, next_block.bbox[1] - block.bbox[3])
            close = y_gap <= 0.06
        if same_page and close and not _parse_section_heading(next_block, None):
            lines = [line.strip() for line in next_block.text.splitlines() if line.strip()]
            title = lines[0] if lines else next_block.text.strip()
            next_remainder = "\n".join(lines[1:]).strip()
            consume_next = True
    if not title and _section_level(section) == 1:
        title = ""
    return section, title, _section_level(section), consume_next, next_remainder


def _section_subtree(units: list[SectionUnit], root: SectionUnit) -> list[SectionUnit]:
    start = units.index(root)
    out = [root]
    for unit in units[start + 1:]:
        if unit.level <= root.level:
            break
        if unit.section.startswith(root.section + "."):
            out.append(unit)
    return out


def _pack_oversize_section(
    f: dict[str, Any],
    parse: dict[str, Any],
    root: SectionUnit,
    subtree: list[SectionUnit],
    body: AutoSectionChunkRequest,
) -> list[SectionCandidate]:
    child_level = root.level + 1
    groups: list[list[SectionUnit]] = []
    current: list[SectionUnit] = [root]
    current_len = _unit_text_len(root)

    direct_children = [u for u in subtree[1:] if u.level == child_level]
    if not direct_children:
        return _split_units_by_paragraphs(f, parse, subtree, body, root.section)

    for child in direct_children:
        child_subtree = _section_subtree(subtree, child)
        child_len = sum(_unit_text_len(u) for u in child_subtree)
        if len(current) > 1 and current_len + child_len > body.max_chars:
            groups.append(current)
            current = [root]
            current_len = _unit_text_len(root)
        current.extend(child_subtree)
        current_len += child_len
    if len(current) > 1:
        groups.append(current)

    candidates = [
        _section_candidate_from_units(
            f, parse, group, body, split_from=root.section, split_reason="over_max_chars_child_pack"
        )
        for group in groups
    ]
    for idx, candidate in enumerate(candidates, start=1):
        candidate.chunk_part = idx
        candidate.chunk_parts = len(candidates)
        candidate.metadata["chunk_part"] = idx
        candidate.metadata["chunk_parts"] = len(candidates)
    return candidates


def _split_units_by_paragraphs(
    f: dict[str, Any],
    parse: dict[str, Any],
    units: list[SectionUnit],
    body: AutoSectionChunkRequest,
    split_from: str,
) -> list[SectionCandidate]:
    root = units[0]
    heading_blocks = root.blocks[:1]
    text_blocks = [b for u in units for b in u.blocks]
    groups: list[list[TextBlock]] = []
    current = list(heading_blocks)
    current_len = sum(len(b.text) + 2 for b in current)
    for block in text_blocks[1:]:
        block_len = len(block.text) + 2
        if len(current) > len(heading_blocks) and current_len + block_len > body.max_chars:
            groups.append(current)
            current = list(heading_blocks)
            current_len = sum(len(b.text) + 2 for b in current)
        current.append(block)
        current_len += block_len
    if len(current) > len(heading_blocks):
        groups.append(current)

    candidates: list[SectionCandidate] = []
    for group in groups:
        unit = SectionUnit(
            section=root.section,
            title=root.title,
            level=root.level,
            section_path=root.section_path,
            blocks=group,
        )
        candidates.append(_section_candidate_from_units(
            f, parse, [unit], body, split_from=split_from, split_reason="paragraph_overflow"
        ))
    for idx, candidate in enumerate(candidates, start=1):
        candidate.chunk_part = idx
        candidate.chunk_parts = len(candidates)
        candidate.metadata["chunk_part"] = idx
        candidate.metadata["chunk_parts"] = len(candidates)
    return candidates


def _section_candidate_from_units(
    f: dict[str, Any],
    parse: dict[str, Any],
    units: list[SectionUnit],
    body: AutoSectionChunkRequest,
    *,
    split_from: str | None,
    split_reason: str | None,
) -> SectionCandidate:
    blocks = _dedupe_blocks([block for unit in units for block in unit.blocks])
    text = "\n\n".join(block.text for block in blocks if block.text).strip()
    first = units[0]
    bbox = _first_page_bbox(blocks)
    source_blocks = [
        {
            "page_idx": b.page_idx,
            "block_index": b.block_index,
            "type": b.type,
            "bbox": b.bbox,
        }
        for b in blocks
    ]
    metadata = _metadata_for_section_candidate(
        f, parse, first, text, source_blocks, split_from, split_reason, None, None
    )
    return SectionCandidate(
        page=blocks[0].page_idx + 1 if blocks else 1,
        bbox=bbox,
        text=text,
        section=first.section,
        section_title=first.title,
        section_level=first.level,
        section_path=first.section_path,
        source_blocks=source_blocks,
        split_from=split_from,
        split_reason=split_reason,
        chunk_part=None,
        chunk_parts=None,
        metadata=metadata,
    )


def _dedupe_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    out: list[TextBlock] = []
    seen: set[tuple[int, int]] = set()
    for block in blocks:
        key = (block.page_idx, block.block_index)
        if key in seen:
            continue
        seen.add(key)
        out.append(block)
    return out


def _unit_text_len(unit: SectionUnit) -> int:
    return sum(len(block.text) + 2 for block in unit.blocks)


def _section_level(section: str) -> int:
    return section.count(".") + 1


def _section_heading_text(section: str, title: str) -> str:
    return f"{section} {title}".strip()


def _union_optional_bbox(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    if a and b:
        return _union_bbox(a, b)
    return a or b


def _first_page_bbox(blocks: list[TextBlock]) -> dict[str, float]:
    if not blocks:
        return {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    first_page = blocks[0].page_idx
    boxes = [b.bbox for b in blocks if b.page_idx == first_page and b.bbox]
    if not boxes:
        return {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.08}
    merged = boxes[0]
    for box in boxes[1:]:
        merged = _union_bbox(merged, box)
    return _bbox_to_xywh(merged)


def _metadata_for_section_candidate(
    f: dict[str, Any],
    parse: dict[str, Any],
    unit: SectionUnit,
    text: str,
    source_blocks: list[dict[str, Any]],
    split_from: str | None,
    split_reason: str | None,
    chunk_part: int | None,
    chunk_parts: int | None,
) -> dict[str, Any]:
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    extractors.merge_auto_metadata(meta, text, f["name"])
    parent = unit.section_path[-2] if len(unit.section_path) >= 2 else None
    child_sections = _extract_child_sections_from_blocks(source_blocks, text, unit.section)
    meta.update({
        "content_type": "section",
        "auto_source": "mineru_section",
        "mineru_parse_id": parse["id"],
        "section": unit.section,
        "section_title": unit.title,
        "section_level": unit.level,
        "section_path": unit.section_path,
        "parent_section": parent["section"] if parent else None,
        "parent_title": parent["title"] if parent else None,
        "source_blocks": source_blocks,
        "child_sections": child_sections,
        "page_start": min((b["page_idx"] for b in source_blocks), default=0) + 1,
        "page_end": max((b["page_idx"] for b in source_blocks), default=0) + 1,
    })
    refs = _extract_figure_refs(text)
    if refs:
        meta["figure_ref"] = refs
    table_refs = _extract_numbered_refs(text, _TABLE_REF_RE)
    if table_refs:
        meta["table_ref"] = table_refs
    if split_from:
        meta["split_from"] = split_from
    if split_reason:
        meta["split_reason"] = split_reason
    if chunk_part is not None:
        meta["chunk_part"] = chunk_part
    if chunk_parts is not None:
        meta["chunk_parts"] = chunk_parts
    return meta


def _extract_child_sections_from_blocks(
    source_blocks: list[dict[str, Any]], text: str, section: str
) -> list[str]:
    child_level = _section_level(section) + 1
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?m)^(\d+(?:\.\d+)*)\b", text):
        value = m.group(1)
        if value.startswith(section + ".") and _section_level(value) == child_level and value not in seen:
            seen.add(value)
            found.append(value)
    return found


async def _create_section_chunk(f: dict[str, Any], candidate: SectionCandidate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cid = uuid.uuid4().hex
    crop = await asyncio.to_thread(
        pdf.crop_region, f["path"], candidate.page - 1, candidate.bbox
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, metadata_llm, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                candidate.text,
                "digital",
                json.dumps(candidate.metadata, ensure_ascii=False),
                "{}",
                "pending",
                now,
                now,
            ),
        )
    row = db.get_conn().execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
    return _row_to_out(row)


def _metadata_for_image_candidate(
    f: dict[str, Any],
    parse: dict[str, Any],
    page_idx: int,
    block_index: int,
    caption: str,
    image_bbox: list[float],
    caption_bbox: list[float] | None,
    section: str | None,
    section_path: list[dict[str, str]],
) -> dict[str, Any]:
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    title = _FIGURE_TITLE_RE.search(caption.strip())
    figure_no = title.group(1) if title else None
    figure_header = title.group(2).strip() if title and title.group(2) else caption
    meta.update({
        "content_type": "image",
        "auto_source": "mineru_image",
        "mineru_parse_id": parse["id"],
        "mineru_page_idx": page_idx,
        "mineru_block_index": block_index,
        "mineru_image_bbox": image_bbox,
        "figure_no": figure_no,
        "figure_header": figure_header,
        "section": section,
        "section_path": section_path,
    })
    if caption_bbox:
        meta["mineru_caption_bbox"] = caption_bbox
    return meta


async def _create_image_chunk(f: dict[str, Any], candidate: ImageCandidate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cid = uuid.uuid4().hex
    text = candidate.caption or f"图像 第 {candidate.page} 页"
    crop = await asyncio.to_thread(
        pdf.crop_region, f["path"], candidate.page - 1, candidate.bbox
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, metadata_llm, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                text,
                "digital",
                json.dumps(candidate.metadata, ensure_ascii=False),
                "{}",
                "pending",
                now,
                now,
            ),
        )
    row = db.get_conn().execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
    return _row_to_out(row)


def _extract_figure_refs(text: str) -> list[str]:
    return _extract_numbered_refs(text, _FIGURE_REF_RE)


def _extract_numbered_refs(text: str, pattern: re.Pattern[str]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for m in pattern.finditer(text):
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if end < start or end - start > 50:
            end = start
        for num in range(start, end + 1):
            value = str(num)
            if value not in seen:
                seen.add(value)
                refs.append(value)
    return refs


def _image_candidate_out(candidate: ImageCandidate) -> dict[str, Any]:
    return {
        "page": candidate.page,
        "bbox": candidate.bbox,
        "image_bbox": candidate.image_bbox,
        "caption_bbox": candidate.caption_bbox,
        "caption": candidate.caption,
        "block_index": candidate.block_index,
        "section": candidate.section,
        "section_path": candidate.section_path,
        "metadata": candidate.metadata,
    }


def _section_candidate_out(candidate: SectionCandidate) -> dict[str, Any]:
    return {
        "page": candidate.page,
        "bbox": candidate.bbox,
        "text": candidate.text,
        "section": candidate.section,
        "section_title": candidate.section_title,
        "section_level": candidate.section_level,
        "section_path": candidate.section_path,
        "source_blocks": candidate.source_blocks,
        "split_from": candidate.split_from,
        "split_reason": candidate.split_reason,
        "chunk_part": candidate.chunk_part,
        "chunk_parts": candidate.chunk_parts,
        "metadata": candidate.metadata,
    }


def _load_model_json(zip_rel: str) -> list[Any]:
    zip_path = config.from_rel(zip_rel)
    if not zip_path.exists():
        raise HTTPException(404, f"parse zip not found: {zip_rel}")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            model_name = next((n for n in names if n.endswith("_model.json")), None)
            if not model_name:
                raise HTTPException(422, "parse zip has no *_model.json")
            return json.loads(zf.read(model_name).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, "parse zip is invalid") from exc


def _find_caption_block(
    blocks: list[Any], table_index: int, table_bbox: list[float], body: AutoTableChunkRequest
) -> dict[str, Any] | None:
    if not body.include_caption:
        return None
    best: dict[str, Any] | None = None
    best_gap = body.max_caption_gap
    # Search a few preceding blocks; standards often put the table title right
    # before the table, but headers/page numbers can appear in between.
    for idx in range(table_index - 1, max(-1, table_index - 6), -1):
        block = blocks[idx]
        if not isinstance(block, dict) or block.get("type") not in _CAPTION_TYPES:
            continue
        bbox = _normalize_bbox(block.get("bbox"))
        if not bbox:
            continue
        text = _clean_text(block.get("content"))
        gap = table_bbox[1] - bbox[3]
        horizontal_overlap = max(0.0, min(table_bbox[2], bbox[2]) - max(table_bbox[0], bbox[0]))
        min_width = max(0.001, min(table_bbox[2] - table_bbox[0], bbox[2] - bbox[0]))
        if gap < -0.005 or gap > body.max_caption_gap:
            continue
        if horizontal_overlap / min_width < 0.35:
            continue
        if not _looks_like_table_caption(text):
            continue
        if gap <= best_gap:
            best = block
            best_gap = gap
    return best


def _normalize_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        nums = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    # MinerU model.json returns 0..1. Other files may carry 0..1000 values; make
    # this tolerant so the auto-chunker can also consume content_list-like data.
    if max(nums) > 1.5:
        nums = [x / 1000.0 for x in nums]
    x0, y0, x1, y1 = nums
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _union_bbox(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _bbox_to_xywh(bbox: list[float]) -> dict[str, float]:
    return {
        "x": round(bbox[0], 6),
        "y": round(bbox[1], 6),
        "w": round(bbox[2] - bbox[0], 6),
        "h": round(bbox[3] - bbox[1], 6),
    }


def _clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    text = _repair_mojibake(str(value or ""))
    return re.sub(r"[ \t]+", " ", text).strip()


def _looks_like_table_caption(text: str) -> bool:
    return bool(_TABLE_TITLE_RE.search(text.strip()))


def _chunk_text(caption: str, table_html: str) -> str:
    if caption and table_html:
        return f"{caption}\n\n{table_html}"
    return table_html or caption


def _metadata_for_candidate(
    f: dict[str, Any],
    text: str,
    parse: dict[str, Any],
    page_idx: int,
    block_index: int,
    caption: str,
    table_bbox: list[float],
    caption_bbox: list[float] | None,
) -> dict[str, Any]:
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    extractors.merge_auto_metadata(meta, text, f["name"])
    title = _TABLE_TITLE_RE.search(caption.strip())
    if title:
        meta.setdefault("table_no", title.group(1))
        if title.group(2):
            meta.setdefault("table_header", title.group(2).strip())
    meta.update({
        "content_type": "table",
        "auto_source": "mineru_table",
        "mineru_parse_id": parse["id"],
        "mineru_page_idx": page_idx,
        "mineru_block_index": block_index,
        "mineru_table_bbox": table_bbox,
    })
    if caption_bbox:
        meta["mineru_caption_bbox"] = caption_bbox
    return meta


async def _create_chunk_from_candidate(
    f: dict[str, Any], parse: dict[str, Any], candidate: Candidate
):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cid = uuid.uuid4().hex
    text = _chunk_text(candidate.caption, candidate.table_html)
    crop = await asyncio.to_thread(
        pdf.crop_region, f["path"], candidate.page - 1, candidate.bbox
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, metadata_llm, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                text,
                "digital",
                json.dumps(candidate.metadata, ensure_ascii=False),
                "{}",
                "pending",
                now,
                now,
            ),
        )
    row = db.get_conn().execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
    return _row_to_out(row)


def _existing_similar_chunk(file_id: str, page: int, text: str) -> bool:
    """Deduplicate auto chunks by file -> page -> normalized content similarity."""
    normalized = _normalize_for_dedupe(text)
    if not normalized:
        return False

    rows = db.get_conn().execute(
        "SELECT text FROM chunks WHERE file_id=? AND page=?",
        (file_id, page),
    ).fetchall()
    for row in rows:
        existing = _normalize_for_dedupe(row["text"] or "")
        if not existing:
            continue
        if SequenceMatcher(None, normalized, existing).ratio() >= _DEDUP_SIMILARITY_THRESHOLD:
            return True
    return False


def _normalize_for_dedupe(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _candidate_out(candidate: Candidate) -> dict[str, Any]:
    return {
        "page": candidate.page,
        "bbox": candidate.bbox,
        "table_bbox": candidate.table_bbox,
        "caption_bbox": candidate.caption_bbox,
        "caption": candidate.caption,
        "text": _chunk_text(candidate.caption, candidate.table_html),
        "block_index": candidate.block_index,
        "metadata": candidate.metadata,
    }
