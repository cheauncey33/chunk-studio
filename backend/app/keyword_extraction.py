"""Versioned LLM keyword and question suggestions for chunks."""
from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from decimal import Decimal
from http import HTTPStatus
from typing import Any

from . import chunk_schema, db
from .evidence_locator import chunk_text_sha256


DEFAULT_MODEL = "qwen-flash"
DEFAULT_BATCH_SIZE = 8
MAX_BATCH_SIZE = 12
MAX_TEXT_CHARS = 12_000
PROMPT_VERSION = "power_standard_keywords_questions_v1"
SUGGESTION_FIELDS = ("keywords", "questions")
SAMPLED_CONTENT_TYPES = ("table", "section")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_STANDARD_RE = re.compile(
    r"(?:GB(?:/T)?|JB/T|DL/T|Q/GDW|IEC|ISO)\s*[0-9][0-9A-Z.\-/]*",
    re.IGNORECASE,
)

SuggestionMap = dict[str, dict[str, list[str]]]
SuggestionExtractor = Callable[..., SuggestionMap]


def pending_chunks(
    *,
    force: bool = False,
    chunk_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    clauses = ["status='approved'"]
    args: list[Any] = []
    if chunk_ids:
        placeholders = ",".join("?" for _ in chunk_ids)
        clauses.append(f"id IN ({placeholders})")
        args.extend(chunk_ids)
    rows = db.get_conn().execute(
        f"""SELECT id, file_id, page, text, business_metadata, metadata_llm
            FROM chunks WHERE {' AND '.join(clauses)}
            ORDER BY file_id, page, created_at""",
        args,
    ).fetchall()
    result = []
    for row in rows:
        metadata_llm = chunk_schema.parse_json_object(row["metadata_llm"])
        if force or not _suggestions_are_current(row, metadata_llm):
            result.append(dict(row))
    return result


def extract_with_qwen(
    items: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    _retry_missing: bool = True,
) -> SuggestionMap:
    if not 1 <= len(items) <= MAX_BATCH_SIZE:
        raise ValueError(f"batch size must be between 1 and {MAX_BATCH_SIZE}")
    api_key = os.environ.get("DASHSCOPE_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    from dashscope import Generation

    payload = json.dumps(items, ensure_ascii=False)
    response = Generation.call(
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是电力标准知识库的检索元数据生成器。对每个chunk生成3到8个关键词，以及"
                    "2到4个可由该chunk直接回答的问题。只能使用输入text和metadata中明确出现的"
                    "事实，不得补充标准号、型号含义、产品结构、参数值、适用关系、计算结果或答案。"
                    "保留原文中的标准术语、试验名称、设备类型、型号、容量、电压、参数名和单位；"
                    "不要改写精确数字。表格问题应尽量包含用于定位行列的原文维度，条款问题应聚焦"
                    "要求、方法、适用条件或计算规则。问题之间不得同义重复，不要生成“本文讲了什么”"
                    "之类泛化问题。严格返回JSON对象："
                    '{"items":[{"id":"原id","keywords":["词1"],'
                    '"questions":["问题1？"]}]}。不得输出解释或其他字段。'
                ),
            },
            {"role": "user", "content": payload},
        ],
        result_format="message",
        response_format={"type": "json_object"},
        temperature=0,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"Qwen metadata extraction failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )
    content = response.output["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
    items_by_id = {item["id"]: item for item in items}
    output: SuggestionMap = {}
    for item in parsed.get("items", []):
        if not isinstance(item, dict) or item.get("id") not in items_by_id:
            continue
        chunk_id = str(item["id"])
        keywords = _normalize_keywords(item.get("keywords"))
        questions = _normalize_questions(item.get("questions"))
        if not keywords or not questions:
            continue
        try:
            _validate_grounded(items_by_id[chunk_id], keywords + questions)
        except ValueError:
            continue
        output[chunk_id] = {"keywords": keywords, "questions": questions}

    missing = set(items_by_id) - output.keys()
    if missing:
        if not _retry_missing:
            raise RuntimeError(f"Qwen response omitted or invalidated {len(missing)} chunk ids")
        for chunk_id in missing:
            output.update(
                extract_with_qwen(
                    [items_by_id[chunk_id]],
                    model=model,
                    _retry_missing=False,
                )
            )
    return output


def extract_suggestions(
    *,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    limit: int | None = None,
    sample_per_type: int | None = None,
    chunk_ids: list[str] | None = None,
    extractor: SuggestionExtractor = extract_with_qwen,
    on_batch: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if sample_per_type is not None and sample_per_type < 1:
        raise ValueError("sample_per_type must be positive")
    if limit is not None and sample_per_type is not None:
        raise ValueError("limit and sample_per_type cannot be used together")
    if chunk_ids and sample_per_type is not None:
        raise ValueError("chunk_ids and sample_per_type cannot be used together")

    rows = pending_chunks(force=force, chunk_ids=chunk_ids)
    if sample_per_type is not None:
        rows = _sample_by_content_type(rows, sample_per_type)
    elif limit is not None:
        rows = rows[:limit]

    completed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        items = [_prompt_item(row) for row in batch]
        suggestions = extractor(items, model=model)
        _store_suggestions(suggestions, model=model)
        completed += len(batch)
        if on_batch:
            on_batch(completed, len(rows))
    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "eligible": len(rows),
        "extracted": completed,
        "by_content_type": _count_content_types(rows),
    }


def extract_keywords(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible entry point for callers using the old function name."""
    return extract_suggestions(**kwargs)


def _prompt_item(row: Any) -> dict[str, str]:
    business = chunk_schema.parse_json_object(row["business_metadata"])
    context = {
        key: business.get(key)
        for key in (
            "standard_no",
            "content_type",
            "section",
            "section_title",
            "table_no",
            "table_title",
            "table_columns",
        )
        if business.get(key) is not None
    }
    return {
        "id": str(row["id"]),
        "metadata": json.dumps(context, ensure_ascii=False),
        "text": str(row["text"] or "")[:MAX_TEXT_CHARS],
    }


def _store_suggestions(suggestions: SuggestionMap, *, model: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with db.transaction() as conn:
        for chunk_id, values in suggestions.items():
            keywords = _normalize_keywords(values.get("keywords"))
            questions = _normalize_questions(values.get("questions"))
            if not keywords or not questions:
                continue
            row = conn.execute(
                "SELECT text, metadata_llm FROM chunks WHERE id=?", (chunk_id,)
            ).fetchone()
            if not row:
                continue
            metadata_llm = chunk_schema.parse_json_object(row["metadata_llm"])
            provenance = {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "source_text_sha256": chunk_text_sha256(row["text"]),
                "generated_at": now,
                "status": "suggested",
            }
            metadata_llm["keywords"] = {"value": keywords, **provenance}
            metadata_llm["questions"] = {"value": questions, **provenance}
            conn.execute(
                "UPDATE chunks SET metadata_llm=?, updated_at=? WHERE id=?",
                (json.dumps(metadata_llm, ensure_ascii=False), now, chunk_id),
            )


def _suggestions_are_current(row: Any, metadata_llm: dict[str, Any]) -> bool:
    source_hash = chunk_text_sha256(row["text"])
    for field in SUGGESTION_FIELDS:
        value = metadata_llm.get(field)
        if not isinstance(value, dict) or not value.get("value"):
            return False
        if value.get("prompt_version") != PROMPT_VERSION:
            return False
        if value.get("source_text_sha256") != source_hash:
            return False
    return True


def _sample_by_content_type(rows: list[dict[str, Any]], per_type: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for content_type in SAMPLED_CONTENT_TYPES:
        matching = [row for row in rows if _content_type(row) == content_type]
        selected.extend(_round_robin_files(matching, per_type))
    return selected


def _round_robin_files(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_file: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        by_file[str(row["file_id"])].append(row)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_file.values()):
        for file_id in sorted(by_file):
            if by_file[file_id]:
                selected.append(by_file[file_id].popleft())
                if len(selected) == limit:
                    break
    return selected


def _count_content_types(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        content_type = _content_type(row)
        counts[content_type] = counts.get(content_type, 0) + 1
    return counts


def _content_type(row: Any) -> str:
    metadata = chunk_schema.parse_json_object(row["business_metadata"])
    return str(metadata.get("content_type") or "unknown")


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, str):
        value = re.split(r"[,，、;；\n]", value)
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        keyword = re.sub(r"\s+", " ", str(item)).strip(" ,，、;；")
        if keyword and keyword not in result:
            result.append(keyword[:80])
    return result[:8]


def _normalize_questions(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        question = re.sub(r"\s+", " ", str(item)).strip()
        question = re.sub(r"^\s*\d+[.、)]\s*", "", question)
        if question and question not in result:
            result.append(question[:240])
    return result[:4]


def _validate_grounded(prompt_item: dict[str, str], generated: list[str]) -> None:
    source = f"{prompt_item.get('metadata', '')}\n{prompt_item.get('text', '')}"
    output = "\n".join(generated)
    source_standards = {_normalize_reference(item) for item in _STANDARD_RE.findall(source)}
    output_standards = {_normalize_reference(item) for item in _STANDARD_RE.findall(output)}
    if output_standards - source_standards:
        raise ValueError("suggestion introduced a standard reference")

    source_numbers = {Decimal(item) for item in _NUMBER_RE.findall(source)}
    output_numbers = {Decimal(item) for item in _NUMBER_RE.findall(output)}
    if output_numbers - source_numbers:
        raise ValueError("suggestion introduced numeric facts")


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _parse_json_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise RuntimeError("Qwen returned non-text content")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Qwen returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Qwen returned a non-object JSON value")
    return parsed
