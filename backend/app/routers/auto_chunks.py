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

from .. import chunk_schema, config, db, extractors, pdf
from ..adapters.ocr import _repair_mojibake
from ..models import AutoImageChunkRequest, AutoSectionChunkRequest, AutoTableChunkRequest
from .chunks import _row_to_out

router = APIRouter(prefix="/auto-chunks", tags=["auto-chunks"])

_TABLE_TITLE_RE = re.compile(r"^表\s*0*((?:[A-Za-z]\s*\.\s*)?\d+)\s*(.+)?")
_TABLE_REF_RE = re.compile(r"表\s*0*(\d+)(?:\s*[～~\-—至]\s*表?\s*0*(\d+))?")
_FIGURE_TITLE_RE = re.compile(r"^图\s*0*(\d+)\s*(.+)?")
_FIGURE_REF_RE = re.compile(r"图\s*0*(\d+)(?:\s*[～~\-—至]\s*图?\s*0*(\d+))?")
_TABLE_TITLE_PARSE_RE = re.compile(r"^表\s*0*(?P<number>(?:[A-Za-z]\s*\.\s*)?\d+)(?P<title>.*)$")
_BARE_TABLE_TITLE_PARSE_RE = re.compile(r"^0*(?P<number>(?:[A-Za-z]\s*\.\s*)?\d+)\s+(?P<title>.+)$")
_BARE_CONTINUED_TABLE_PARSE_RE = re.compile(r"^0*(?P<number>[A-Za-z]\s*\.\s*\d+)\s*(?:[（(]\s*续\s*[）)]|续)\s*$")
_FIGURE_TITLE_PARSE_RE = re.compile(r"^图\s*0*(?P<digits>\d+)(?P<title>.*)$")
_VOLTAGE_TITLE_LEAD_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:kV|V|MVA|kVA)\b", re.IGNORECASE)
_SECTION_TITLE_RE = re.compile(r"^(?P<section>\d+(?:\.\d+)*)(?:\s+(?P<title>.+))?$")
_APPENDIX_ROOT_RE = re.compile(
    r"^附录\s*(?P<section>[A-Za-z])(?:\s*[（(][^）)]*附录[^）)]*[）)])?\s*$",
    re.IGNORECASE,
)
_APPENDIX_SECTION_RE = re.compile(
    r"^(?P<section>[A-Za-z](?:\.\d+)+)(?:\s+(?P<title>.+))?$",
    re.IGNORECASE,
)
_CONTINUATION_SUFFIX_RE = re.compile(r"(?:（\s*续\s*）|\(\s*续\s*\)|续)\s*$")
_CAPTION_TYPES = {"image_caption", "table_caption", "text", "paragraph_title", "title"}
_SECTION_TEXT_TYPES = {"text", "ocr_text", "paragraph_title", "title", "doc_title", "equation", "inline_formula"}
_SKIP_SECTION_TYPES = {"header", "page_number", "index", "table", "image", "image_caption", "image_footnote"}
_DEDUP_SIMILARITY_THRESHOLD = 0.95
_SYMBOL_TABLE_TERMS = ("符号", "缩略语", "含义", "单位")
_REPORT_FORM_TERMS = ("报告编号", "声压法", "声强法", "额定电压", "额定电流", "分接位置", "运行风扇", "运行油泵")
_CALCULATION_TABLE_TERMS = (
    "声功率级", "声压级", "声强级", "测量级", "表面测量级", "表面面积", "规定轮廓线",
    "频率", "倍频程", "dB(A)", "LWA", "LpA", "总损耗", "铁损", "I^2R",
)
_FORMULA_TABLE_TERMS = ("公式", "声波频率", "电流频率", "试验电流", "贡献频率", "I_1", "f_1")


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
class MarkdownTableCaption:
    index: int
    caption: str
    table_html: str
    normalized_table: str


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
    markdown_tables = _load_markdown_table_captions(parse)
    markdown_cursor = 0
    candidates: list[Candidate] = []
    seen_table_numbers: list[str] = []
    seen_table_titles: dict[str, str] = {}
    seen_table_pages: dict[str, int] = {}
    previous_candidate: Candidate | None = None
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
            continued_from_markdown = False
            if not caption:
                previous_markdown_cursor = markdown_cursor
                markdown_match, proposed_cursor = _match_markdown_table_caption(
                    table_html, markdown_tables, markdown_cursor
                )
                if markdown_match and _markdown_caption_allowed(
                    markdown_match.caption,
                    page_idx + 1,
                    seen_table_pages,
                ):
                    caption = markdown_match.caption
                    continued_from_markdown = markdown_match.index < previous_markdown_cursor
                    markdown_cursor = proposed_cursor
            merged_bbox = _bbox_to_xywh(_union_bbox(table_bbox, caption_bbox or table_bbox))
            text = _chunk_text(caption, table_html)
            metadata = _metadata_for_candidate(
                f, text, parse, page_idx, block_index, caption, table_bbox, caption_bbox,
                seen_table_numbers, seen_table_titles, continued_from_markdown,
            )
            _apply_adjacent_table_continuation(
                metadata,
                page=page_idx + 1,
                previous=previous_candidate,
            )
            _remember_number(seen_table_numbers, metadata.get("table_no"))
            _remember_table_title(seen_table_titles, metadata)
            table_no = _normalize_number_token(metadata.get("table_no"))
            if table_no:
                seen_table_pages[table_no] = page_idx + 1
            candidate = Candidate(
                page=page_idx + 1,
                bbox=merged_bbox,
                table_bbox=table_bbox,
                caption_bbox=caption_bbox,
                caption=caption,
                table_html=table_html,
                block_index=block_index,
                metadata=metadata,
            )
            candidates.append(candidate)
            previous_candidate = candidate
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
    seen_figure_numbers: list[str] = []
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
                f, parse, page_idx, block_index, caption, image_bbox, caption_bbox, section, section_path,
                seen_figure_numbers,
            )
            _remember_number(seen_figure_numbers, metadata.get("figure_no"))
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
    top_units = _select_section_roots(units, body.target_level)
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


def _select_section_roots(units: list[SectionUnit], target_level: int) -> list[SectionUnit]:
    """Prefer the requested heading depth, retaining shallower leaf sections.

    A document may have a top-level section such as ``10 外施耐压试验``
    without any ``10.1`` child. Selecting only level-two headings would drop
    that entire section, so the leaf section becomes its own chunk root.
    """
    roots: list[SectionUnit] = []
    for unit in units:
        if unit.level == target_level:
            roots.append(unit)
            continue
        if unit.level > target_level:
            continue

        subtree = _section_subtree(units, unit)
        has_eligible_descendant = any(
            child.level > unit.level and child.level <= target_level
            for child in subtree[1:]
        )
        if not has_eligible_descendant:
            roots.append(unit)
    return roots


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
    text = block.text.strip()
    appendix_root = _APPENDIX_ROOT_RE.match(text)
    appendix_section = _APPENDIX_SECTION_RE.match(text)
    is_title_block = block.type in {"paragraph_title", "title", "doc_title"}
    if appendix_root:
        m = appendix_root
        section = m.group("section").upper()
        title = ""
    elif appendix_section and is_title_block:
        m = appendix_section
        section = m.group("section").upper()
        title = (m.group("title") or "").strip()
    elif is_title_block:
        m = _SECTION_TITLE_RE.match(text)
        if not m:
            return None
        section = m.group("section")
        title = (m.group("title") or "").strip()
    else:
        return None
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
        candidate.metadata.setdefault("split", {})["part"] = idx
        candidate.metadata.setdefault("split", {})["parts"] = len(candidates)
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
        candidate.metadata.setdefault("split", {})["part"] = idx
        candidate.metadata.setdefault("split", {})["parts"] = len(candidates)
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
    prefix = f"附录 {section}" if len(section) == 1 and section.isalpha() else section
    return f"{prefix} {title}".strip()


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
        "creation_mode": "auto",
        "generator": "mineru",
        "chunk_type": "section",
        "strategy": "section_by_heading",
        "strategy_version": "1",
        "parse_id": parse["id"],
        "parser": "mineru",
        "parser_version": "vlm",
        "section": unit.section,
        "section_title": unit.title,
        "section_level": unit.level,
        "section_path": unit.section_path,
        "source_blocks": source_blocks,
        "page_start": min((b["page_idx"] for b in source_blocks), default=0) + 1,
        "page_end": max((b["page_idx"] for b in source_blocks), default=0) + 1,
    })
    if parent:
        meta["belongs_to"] = {"section": parent["section"], "title": parent["title"]}
    if child_sections:
        meta["children"] = [{"section": section, "type": "child_section"} for section in child_sections]
    refs = _extract_figure_refs(text)
    references = []
    if refs:
        references.extend({"kind": "figure", "no": ref, "type": "references_figure"} for ref in refs)
    table_refs = _extract_numbered_refs(text, _TABLE_REF_RE)
    if table_refs:
        references.extend({"kind": "table", "no": ref, "type": "references_table"} for ref in table_refs)
    if references:
        meta["references"] = references
    if split_from:
        meta.setdefault("split", {})["from"] = split_from
    if split_reason:
        meta.setdefault("split", {})["reason"] = split_reason
    if chunk_part is not None:
        meta.setdefault("split", {})["part"] = chunk_part
    if chunk_parts is not None:
        meta.setdefault("split", {})["parts"] = chunk_parts
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
    business_metadata, source_trace, chunk_logic, relations = chunk_schema.split_flat_metadata_for_write(candidate.metadata)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, business_metadata, metadata_llm, source_trace, chunk_logic, relations,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                candidate.text,
                "digital",
                "{}",
                json.dumps(business_metadata, ensure_ascii=False),
                "{}",
                json.dumps(source_trace, ensure_ascii=False),
                json.dumps(chunk_logic, ensure_ascii=False),
                json.dumps(relations, ensure_ascii=False),
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
    seen_figure_numbers: list[str] | None = None,
) -> dict[str, Any]:
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    extractors.merge_auto_metadata(meta, caption, f["name"])
    figure_no, figure_title = _parse_numbered_title(caption, "figure", seen_figure_numbers)
    figure_title = figure_title or caption
    meta.update({
        "content_type": "image",
        "creation_mode": "auto",
        "generator": "mineru",
        "chunk_type": "image",
        "strategy": "image_with_caption" if caption_bbox else "image_only",
        "strategy_version": "1",
        "parse_id": parse["id"],
        "parser": "mineru",
        "parser_version": "vlm",
        "page_start": page_idx + 1,
        "page_end": page_idx + 1,
        "source_blocks": [
            {"page": page_idx + 1, "block_index": block_index, "type": "image", "bbox": image_bbox}
        ],
        "figure_no": figure_no,
        "figure_title": figure_title,
        "section": section,
        "section_path": section_path,
    })
    if caption_bbox:
        meta["source_blocks"].append(
            {"page": page_idx + 1, "block_index": block_index, "type": "caption", "bbox": caption_bbox}
        )
    return meta


async def _create_image_chunk(f: dict[str, Any], candidate: ImageCandidate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cid = uuid.uuid4().hex
    text = candidate.caption or f"图像 第 {candidate.page} 页"
    crop = await asyncio.to_thread(
        pdf.crop_region, f["path"], candidate.page - 1, candidate.bbox
    )
    business_metadata, source_trace, chunk_logic, relations = chunk_schema.split_flat_metadata_for_write(candidate.metadata)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, business_metadata, metadata_llm, source_trace, chunk_logic, relations,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                text,
                "digital",
                "{}",
                json.dumps(business_metadata, ensure_ascii=False),
                "{}",
                json.dumps(source_trace, ensure_ascii=False),
                json.dumps(chunk_logic, ensure_ascii=False),
                json.dumps(relations, ensure_ascii=False),
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
        "layers": _layers_for_preview(candidate.metadata),
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
        "layers": _layers_for_preview(candidate.metadata),
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


def _load_markdown_table_captions(parse: dict[str, Any]) -> list[MarkdownTableCaption]:
    markdown = ""
    markdown_rel = parse.get("markdown_path")
    if markdown_rel:
        markdown_path = config.from_rel(markdown_rel)
        if markdown_path.exists():
            markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")
    if not markdown:
        zip_rel = parse.get("raw_zip_path")
        if not zip_rel:
            return []
        zip_path = config.from_rel(zip_rel)
        if not zip_path.exists():
            return []
        try:
            with zipfile.ZipFile(zip_path) as zf:
                full_md = next((n for n in zf.namelist() if n.endswith("full.md")), None)
                if full_md:
                    markdown = zf.read(full_md).decode("utf-8", errors="ignore")
        except zipfile.BadZipFile:
            return []
    return _extract_markdown_table_captions(markdown)


def _extract_markdown_table_captions(markdown: str) -> list[MarkdownTableCaption]:
    entries: list[MarkdownTableCaption] = []
    for match in re.finditer(r"<table\b.*?</table>", markdown or "", flags=re.IGNORECASE | re.DOTALL):
        caption = _find_markdown_caption_before(markdown[:match.start()])
        if not caption:
            continue
        table_html = _clean_text(match.group(0))
        normalized = _normalize_for_dedupe(table_html)
        if not normalized:
            continue
        entries.append(MarkdownTableCaption(len(entries), caption, table_html, normalized))
    return entries


def _find_markdown_caption_before(prefix: str) -> str:
    nonempty = [_clean_text(line) for line in prefix.splitlines() if _clean_text(line)]
    for line in reversed(nonempty[-6:]):
        if _looks_like_table_caption(line):
            return line
    return ""


def _match_markdown_table_caption(
    table_html: str,
    markdown_tables: list[MarkdownTableCaption],
    cursor: int,
) -> tuple[MarkdownTableCaption | None, int]:
    if not markdown_tables:
        return None, cursor
    normalized = _normalize_for_dedupe(table_html)
    if not normalized:
        return None, cursor

    start = max(0, cursor - 1)
    end = min(len(markdown_tables), cursor + 6)
    best: tuple[float, MarkdownTableCaption] | None = None
    for entry in markdown_tables[start:end]:
        score = _table_html_match_score(normalized, entry.normalized_table)
        if best is None or score > best[0]:
            best = (score, entry)
    if best is None or best[0] < 0.30:
        return None, cursor

    entry = best[1]
    next_cursor = max(cursor, entry.index + 1)
    return entry, next_cursor


def _table_html_match_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    containment = len(short) / max(1, len(long)) if short in long else 0.0
    return max(containment, SequenceMatcher(None, a, b).ratio())


def _markdown_caption_allowed(
    caption: str, page: int, seen_table_pages: dict[str, int]
) -> bool:
    """Prevent a content-similar table from reusing a distant old caption."""
    table_no, _ = _parse_numbered_title(caption, "table", allow_bare_table=False)
    if not table_no:
        return False
    previous_page = seen_table_pages.get(table_no)
    return previous_page is None or page <= previous_page + 1


def _find_caption_block(
    blocks: list[Any], table_index: int, table_bbox: list[float], body: AutoTableChunkRequest
) -> dict[str, Any] | None:
    if not body.include_caption:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for idx, block in enumerate(blocks):
        if idx == table_index:
            continue
        score = _table_caption_score(block, idx, table_index, table_bbox, body)
        if score is None:
            continue
        if best is None or score < best[0]:
            best = (score, block)
    if not best:
        return None
    return _merge_adjacent_caption_blocks(blocks, best[1], table_bbox)


def _merge_adjacent_caption_blocks(
    blocks: list[Any], primary: dict[str, Any], table_bbox: list[float]
) -> dict[str, Any]:
    """Join stacked caption lines so a trailing ``（续）`` is not lost."""
    primary_bbox = _normalize_bbox(primary.get("bbox"))
    if not primary_bbox:
        return primary
    selected = [primary]
    for block in blocks:
        if block is primary or not isinstance(block, dict):
            continue
        if block.get("type") not in {"table_caption", "image_caption"}:
            continue
        bbox = _normalize_bbox(block.get("bbox"))
        text = _clean_text(block.get("content"))
        if not bbox or not text or bbox[3] > table_bbox[1] + 0.005:
            continue
        horizontal_overlap = max(0.0, min(primary_bbox[2], bbox[2]) - max(primary_bbox[0], bbox[0]))
        min_width = max(0.001, min(primary_bbox[2] - primary_bbox[0], bbox[2] - bbox[0]))
        vertical_gap = max(0.0, max(primary_bbox[1], bbox[1]) - min(primary_bbox[3], bbox[3]))
        if horizontal_overlap / min_width >= 0.45 and vertical_gap <= 0.04:
            selected.append(block)
    if len(selected) == 1:
        return primary
    selected.sort(key=lambda item: (_normalize_bbox(item.get("bbox")) or [0, 0, 0, 0])[1])
    boxes = [_normalize_bbox(item.get("bbox")) for item in selected]
    valid_boxes = [box for box in boxes if box]
    return {
        **primary,
        "content": "\n".join(_clean_text(item.get("content")) for item in selected),
        "bbox": _union_many_bbox(valid_boxes),
    }


def _table_caption_score(
    block: Any,
    idx: int,
    table_index: int,
    table_bbox: list[float],
    body: AutoTableChunkRequest,
) -> float | None:
    if not isinstance(block, dict) or block.get("type") not in _CAPTION_TYPES:
        return None
    bbox = _normalize_bbox(block.get("bbox"))
    if not bbox:
        return None
    text = _clean_text(block.get("content"))
    allow_bare = block.get("type") in {"table_caption", "image_caption"}
    if not _looks_like_table_caption(text, allow_bare=allow_bare):
        return None

    table_w = max(0.001, table_bbox[2] - table_bbox[0])
    table_h = max(0.001, table_bbox[3] - table_bbox[1])
    caption_w = max(0.001, bbox[2] - bbox[0])
    caption_h = max(0.001, bbox[3] - bbox[1])
    horizontal_overlap = max(0.0, min(table_bbox[2], bbox[2]) - max(table_bbox[0], bbox[0]))
    vertical_overlap = max(0.0, min(table_bbox[3], bbox[3]) - max(table_bbox[1], bbox[1]))

    scores: list[float] = []

    gap_above = table_bbox[1] - bbox[3]
    if -0.005 <= gap_above <= body.max_caption_gap * 1.5 and horizontal_overlap / min(table_w, caption_w) >= 0.30:
        scores.append(gap_above + abs(idx - table_index) * 0.002)

    gap_left = table_bbox[0] - bbox[2]
    if -0.01 <= gap_left <= 0.08 and vertical_overlap / min(table_h, caption_h) >= 0.45:
        scores.append(gap_left + 0.01 + abs(idx - table_index) * 0.002)

    gap_right = bbox[0] - table_bbox[2]
    if -0.01 <= gap_right <= 0.08 and vertical_overlap / min(table_h, caption_h) >= 0.45:
        scores.append(gap_right + 0.015 + abs(idx - table_index) * 0.002)

    if not scores:
        return None
    score = min(scores)
    if block.get("type") in {"table_caption", "image_caption"}:
        score -= 0.01
    if idx < table_index:
        score -= 0.002
    return score


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


def _union_many_bbox(boxes: list[list[float]]) -> list[float]:
    merged = boxes[0]
    for box in boxes[1:]:
        merged = _union_bbox(merged, box)
    return merged


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
    text = extractors.normalize_latex_text(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _looks_like_table_caption(text: str, *, allow_bare: bool = False) -> bool:
    # Bare appendix numbering such as ``B.2 声压法`` is a section heading far
    # more often than a table caption. Explicit ``表 B.2`` remains supported;
    # callers that already know they have a caption can still opt into the
    # permissive parser via ``allow_bare_table=True``.
    return _parse_numbered_title(text, "table", allow_bare_table=allow_bare)[0] is not None


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
    seen_table_numbers: list[str] | None = None,
    seen_table_titles: dict[str, str] | None = None,
    continued_from_markdown: bool = False,
) -> dict[str, Any]:
    try:
        meta = json.loads(f.get("metadata") or "{}")
    except Exception:
        meta = {}
    extractors.merge_auto_metadata(meta, text, f["name"])
    table_no, table_title = _parse_numbered_title(
        caption, "table", seen_table_numbers, allow_bare_table=True
    )
    if table_no:
        meta["table_no"] = table_no
    if table_title:
        meta["table_title"] = table_title
    _apply_table_continuation_metadata(meta, seen_table_titles or {})
    if continued_from_markdown and table_no:
        meta.setdefault("split", {})["from"] = f"table:{table_no}"
        meta.setdefault("split", {})["reason"] = "continued_table"
        meta.setdefault("belongs_to", {"table_no": table_no, "type": "continued_table"})
    meta["table_kind"] = _classify_table_kind(text, meta)
    meta.update({
        "content_type": "table",
        "creation_mode": "auto",
        "generator": "mineru",
        "chunk_type": "table",
        "strategy": "table_with_caption" if caption_bbox else ("table_with_markdown_caption" if caption else "table_only"),
        "strategy_version": "1",
        "parse_id": parse["id"],
        "parser": "mineru",
        "parser_version": "vlm",
        "page_start": page_idx + 1,
        "page_end": page_idx + 1,
        "source_blocks": [
            {"page": page_idx + 1, "block_index": block_index, "type": "table", "bbox": table_bbox}
        ],
    })
    if caption_bbox:
        meta["source_blocks"].append(
            {"page": page_idx + 1, "block_index": block_index, "type": "caption", "bbox": caption_bbox}
        )
    return meta


def _classify_table_kind(text: str, metadata: dict[str, Any]) -> str:
    split = metadata.get("split")
    if isinstance(split, dict) and split.get("reason") == "continued_table":
        return "continued_table"
    if metadata.get("table_no") and metadata.get("table_title"):
        return "numbered_table"

    plain = _plain_table_text(text)
    columns = metadata.get("table_columns") if isinstance(metadata.get("table_columns"), list) else []
    column_text = " ".join(str(value) for value in columns)
    combined = f"{column_text} {plain}"

    if _has_terms(column_text, _SYMBOL_TABLE_TERMS, minimum=2) or _has_terms(plain[:400], _SYMBOL_TABLE_TERMS, minimum=3):
        return "symbol_table"
    if "报告编号" in combined or _has_terms(combined, _REPORT_FORM_TERMS, minimum=3):
        return "report_form"
    if _has_terms(combined, _FORMULA_TABLE_TERMS, minimum=2) or _formula_density(plain) >= 0.045:
        return "formula_table"
    if _has_terms(combined, _CALCULATION_TABLE_TERMS, minimum=2):
        return "calculation_table"
    return "unnumbered_table"


def _plain_table_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_terms(text: str, terms: tuple[str, ...], *, minimum: int) -> bool:
    compact = re.sub(r"\s+", "", text or "").lower()
    hits = 0
    for term in terms:
        if re.sub(r"\s+", "", term).lower() in compact:
            hits += 1
    return hits >= minimum


def _formula_density(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0.0
    formula_chars = len(re.findall(r"[_^=∑√≤≥Δθλμ×]|\\(?:overline|frac|sum|mathrm|tag)", compact))
    return formula_chars / max(1, len(compact))


async def _create_chunk_from_candidate(
    f: dict[str, Any], parse: dict[str, Any], candidate: Candidate
):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cid = uuid.uuid4().hex
    text = _chunk_text(candidate.caption, candidate.table_html)
    crop = await asyncio.to_thread(
        pdf.crop_region, f["path"], candidate.page - 1, candidate.bbox
    )
    business_metadata, source_trace, chunk_logic, relations = chunk_schema.split_flat_metadata_for_write(candidate.metadata)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chunks
               (id, file_id, page, bbox, rotation, crop_path, text, text_source,
                metadata, business_metadata, metadata_llm, source_trace, chunk_logic, relations,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                f["id"],
                candidate.page,
                json.dumps(candidate.bbox),
                0,
                crop.crop_rel,
                text,
                "digital",
                "{}",
                json.dumps(business_metadata, ensure_ascii=False),
                "{}",
                json.dumps(source_trace, ensure_ascii=False),
                json.dumps(chunk_logic, ensure_ascii=False),
                json.dumps(relations, ensure_ascii=False),
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


def _layers_for_preview(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return chunk_schema.migrate_chunk_metadata_v1(metadata)


def _remember_number(seen: list[str], value: Any) -> None:
    number = _normalize_number_token(value)
    if not number:
        return
    if number not in seen:
        seen.append(number)


def _remember_table_title(seen_titles: dict[str, str], metadata: dict[str, Any]) -> None:
    table_no = _normalize_number_token(metadata.get("table_no"))
    table_title = str(metadata.get("table_title") or "").strip()
    if table_no and table_title and not _is_continuation_title(table_title):
        seen_titles[table_no] = table_title


def _apply_table_continuation_metadata(metadata: dict[str, Any], seen_titles: dict[str, str]) -> None:
    table_no = _normalize_number_token(metadata.get("table_no"))
    table_title = str(metadata.get("table_title") or "").strip()
    if not table_no or not _is_continuation_title(table_title):
        return
    metadata.setdefault("split", {})["from"] = f"table:{table_no}"
    metadata.setdefault("split", {})["reason"] = "continued_table"
    metadata.setdefault("belongs_to", {"table_no": table_no, "type": "continued_table"})
    if seen_titles.get(table_no):
        metadata["table_title"] = seen_titles[table_no]


def _is_continuation_title(value: str | None) -> bool:
    return _CONTINUATION_SUFFIX_RE.search(str(value or "").strip()) is not None


def _apply_adjacent_table_continuation(
    metadata: dict[str, Any], *, page: int, previous: Candidate | None
) -> None:
    """Mark an adjacent-page table fragment when MinerU dropped ``（续）``."""
    if previous is None or page != previous.page + 1:
        return
    table_no = _normalize_number_token(metadata.get("table_no"))
    previous_no = _normalize_number_token(previous.metadata.get("table_no"))
    columns = metadata.get("table_columns")
    previous_columns = previous.metadata.get("table_columns")
    if not table_no or table_no != previous_no:
        return
    if not _table_columns_match(columns, previous_columns):
        return
    metadata.setdefault("split", {})["from"] = f"table:{table_no}"
    metadata.setdefault("split", {})["reason"] = "continued_table"
    metadata.setdefault("belongs_to", {"table_no": table_no, "type": "continued_table"})
    previous_title = str(previous.metadata.get("table_title") or "").strip()
    if previous_title:
        metadata["table_title"] = previous_title
    metadata["table_kind"] = "continued_table"


def _table_columns_match(current: Any, previous: Any) -> bool:
    if not isinstance(current, list) or not isinstance(previous, list) or not current:
        return False
    return [_normalize_for_dedupe(str(item)) for item in current] == [
        _normalize_for_dedupe(str(item)) for item in previous
    ]


def _parse_numbered_title(
    text: str,
    kind: str,
    seen_numbers: list[str] | None = None,
    *,
    allow_bare_table: bool = False,
) -> tuple[str | None, str | None]:
    pattern = _TABLE_TITLE_PARSE_RE if kind == "table" else _FIGURE_TITLE_PARSE_RE
    match = pattern.search(text.strip())
    bare_match = False
    if not match and kind == "table" and allow_bare_table:
        match = _BARE_TABLE_TITLE_PARSE_RE.search(text.strip())
        bare_match = match is not None
        if not match:
            continued = _BARE_CONTINUED_TABLE_PARSE_RE.search(text.strip())
            if continued:
                return _normalize_number_token(continued.group("number")), "(续)"
    if not match:
        return None, None
    raw_digits = match.group("digits" if kind != "table" else "number")
    raw_digits = _normalize_number_token(raw_digits) or "0"
    raw_title = (match.group("title") or "").strip()
    if bare_match:
        number, title = raw_digits, raw_title
    else:
        number, title = _disambiguate_caption_number(raw_digits, raw_title, seen_numbers or [])
    return number, extractors.normalize_latex_text(title)


def _disambiguate_caption_number(
    raw_digits: str,
    raw_title: str,
    seen_numbers: list[str],
) -> tuple[str, str]:
    if not raw_digits.isdigit():
        return raw_digits, raw_title
    candidates: list[tuple[str, str]] = [(raw_digits, raw_title)]
    for split_at in range(1, len(raw_digits)):
        prefix = raw_digits[:split_at]
        title = (raw_digits[split_at:] + raw_title).strip()
        if _looks_like_voltage_title_start(title):
            candidates.append((prefix, title))

    if len(candidates) == 1:
        return candidates[0]

    numeric_seen = [int(value) for value in seen_numbers if str(value).isdigit()]
    expected = max(numeric_seen) + 1 if numeric_seen else None
    if expected is not None:
        for number, title in candidates:
            if int(number) == expected:
                return number, title

    raw_number = int(raw_digits)
    if numeric_seen and raw_number > max(numeric_seen) + 5:
        return candidates[1]
    if not seen_numbers and _looks_like_voltage_title_start(candidates[1][1]):
        return candidates[1]
    return candidates[0]


def _looks_like_voltage_title_start(text: str) -> bool:
    return bool(_VOLTAGE_TITLE_LEAD_RE.match(text))


def _normalize_number_token(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"\s+", "", raw).upper()
    raw = raw.lstrip("0") or "0" if raw.isdigit() else raw
    return raw


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
        "layers": _layers_for_preview(candidate.metadata),
    }
