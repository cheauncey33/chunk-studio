"""Locate report requirement text on a MinerU-parsed PDF page.

Audit cards refer to values shown in the report's result-summary tables
(检测结果汇总), so matching prefers those pages over 报告正文 detail pages.
"""
from __future__ import annotations

import json
import re
import zipfile
from typing import Any

from . import config, db

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[:：,，。.%％()（）\[\]【】<>≤≥±\-_]")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}|\d+(?:\.\d+)?%?|≤|≥|±|2[Uu]r")


def _latest_parse(file_id: str) -> dict[str, Any] | None:
    row = db.get_conn().execute(
        """SELECT * FROM document_parses
           WHERE file_id=? AND status='done'
           ORDER BY created_at DESC LIMIT 1""",
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_model_pages(zip_rel: str | None) -> list[list[dict[str, Any]]] | None:
    if not zip_rel:
        return None
    zip_path = config.from_rel(zip_rel)
    if not zip_path.is_file():
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            model_name = next((n for n in zf.namelist() if n.endswith("_model.json")), None)
            if not model_name:
                return None
            raw = json.loads(zf.read(model_name).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, list):
        return None
    pages: list[list[dict[str, Any]]] = []
    for page in raw:
        if not isinstance(page, list):
            pages.append([])
            continue
        pages.append([block for block in page if isinstance(block, dict)])
    return pages


def normalize_locate_text(value: str) -> str:
    text = _TAG_RE.sub(" ", value or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
    )
    return _SPACE_RE.sub("", text).lower()


def compact_locate_text(value: str) -> str:
    return _PUNCT_RE.sub("", normalize_locate_text(value))


def _tokens(normalized: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(normalized) if tok]


def _contains(hay_norm: str, hay_compact: str, needle: str) -> bool:
    needle_norm = normalize_locate_text(needle)
    if not needle_norm:
        return False
    if needle_norm in hay_norm:
        return True
    needle_compact = compact_locate_text(needle)
    return bool(needle_compact) and needle_compact in hay_compact


def _token_hit_ratio(hay_norm: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    hits = sum(1 for tok in tokens if tok.lower() in hay_norm)
    return hits / len(tokens)


def _page_kind(page_norm: str) -> str:
    """Classify page role for audit-card jump targets."""
    if "检测结果汇总" in page_norm:
        return "summary"
    if (
        "检测项目" in page_norm
        and "标准要求" in page_norm
        and ("结论" in page_norm or "检测结果" in page_norm)
    ):
        return "summary"
    if "检测结论" in page_norm and "检测依据" in page_norm:
        return "toc"
    if "报告正文" in page_norm:
        return "body"
    return "other"


def _requirement_match(
    page_norm: str,
    page_compact: str,
    requirement: str,
) -> str | None:
    if not requirement.strip():
        return None
    if _contains(page_norm, page_compact, requirement):
        return "full"
    if ":" in requirement or "：" in requirement:
        left, value = re.split(r"[:：]", requirement, maxsplit=1)
        value = value.strip()
        if value and _contains(page_norm, page_compact, value):
            left_tokens = _tokens(normalize_locate_text(left))
            if _token_hit_ratio(page_norm, left_tokens) >= 0.4 or "试验电压" in page_norm:
                return "value"
            value_tokens = _tokens(normalize_locate_text(value))
            if value_tokens and _token_hit_ratio(page_norm, value_tokens) >= 1.0:
                return "value"
    return None


def _split_query(query: str, project: str, requirement: str) -> tuple[str, str]:
    project = (project or "").strip()
    requirement = (requirement or "").strip()
    if project or requirement:
        return project, requirement
    text = (query or "").strip()
    if not text:
        return "", ""
    parts = text.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _score_page(
    blocks: list[dict[str, Any]],
    *,
    project: str,
    requirement: str,
) -> float:
    if not blocks:
        return 0.0
    page_raw = "\n".join(str(block.get("content") or "") for block in blocks)
    page_norm = normalize_locate_text(page_raw)
    page_compact = compact_locate_text(page_raw)
    if not page_norm:
        return 0.0

    project_tokens = _tokens(normalize_locate_text(project))
    req_tokens = _tokens(normalize_locate_text(requirement))
    has_project = _contains(page_norm, page_compact, project) if project else False
    req_match = _requirement_match(page_norm, page_compact, requirement) if requirement else None
    project_ratio = _token_hit_ratio(page_norm, project_tokens) if project_tokens else 0.0
    req_ratio = _token_hit_ratio(page_norm, req_tokens) if req_tokens else 0.0
    kind = _page_kind(page_norm)

    score = 0.0
    if has_project:
        score += 50.0
    elif project_ratio >= 0.7:
        score += 25.0
    if req_match == "full":
        score += 60.0
    elif req_match == "value":
        score += 40.0
    elif req_ratio >= 0.7:
        score += 25.0
    if has_project and req_match:
        score += 35.0

    if kind == "summary":
        # Audit cards are about reported summary-table requirements.
        score += 80.0
    elif kind == "body":
        score -= 50.0
    elif kind == "toc":
        score -= 100.0

    return score


def locate_text_page(
    file_id: str,
    query: str = "",
    *,
    project: str = "",
    requirement: str = "",
) -> dict[str, Any]:
    """Return 1-based page for the best MinerU page match, or null page."""
    file_row = db.get_conn().execute(
        "SELECT id, page_count FROM files WHERE id=?",
        (file_id,),
    ).fetchone()
    if not file_row:
        raise KeyError("file not found")
    page_count = int(file_row["page_count"] or 0)
    project, requirement = _split_query(query, project, requirement)
    empty = {
        "file_id": file_id,
        "page": None,
        "page_count": page_count,
        "score": 0.0,
        "matched": False,
        "reason": "empty_query" if not (project or requirement) else "no_parse_layout",
    }
    if not (project or requirement):
        return empty

    parse = _latest_parse(file_id)
    if not parse:
        empty["reason"] = "no_parse"
        return empty
    pages = _load_model_pages(parse.get("raw_zip_path"))
    if not pages:
        empty["reason"] = "no_parse_layout"
        return empty

    best_page: int | None = None
    best_score = 0.0
    for page_idx, blocks in enumerate(pages):
        score = _score_page(blocks, project=project, requirement=requirement)
        if score > best_score:
            best_score = score
            best_page = page_idx + 1

    if best_page is None or best_score < 40.0:
        return {
            "file_id": file_id,
            "page": None,
            "page_count": page_count,
            "score": best_score,
            "matched": False,
            "reason": "no_confident_match",
        }
    return {
        "file_id": file_id,
        "page": best_page,
        "page_count": page_count,
        "score": best_score,
        "matched": True,
        "reason": "ok",
    }
