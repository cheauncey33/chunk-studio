"""Small-model keyword suggestions for chunks."""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from . import chunk_schema, db


DEFAULT_MODEL = "qwen-flash"
DEFAULT_BATCH_SIZE = 8
MAX_BATCH_SIZE = 12
MAX_TEXT_CHARS = 12_000


def pending_chunks(*, force: bool = False, chunk_ids: list[str] | None = None) -> list[dict[str, Any]]:
    clauses = ["status='approved'"]
    args: list[Any] = []
    if chunk_ids:
        placeholders = ",".join("?" for _ in chunk_ids)
        clauses.append(f"id IN ({placeholders})")
        args.extend(chunk_ids)
    rows = db.get_conn().execute(
        f"""SELECT id, text, business_metadata, metadata_llm
            FROM chunks WHERE {' AND '.join(clauses)}
            ORDER BY file_id, page, created_at""",
        args,
    ).fetchall()
    result = []
    for row in rows:
        metadata_llm = chunk_schema.parse_json_object(row["metadata_llm"])
        if force or not _normalize_keywords(metadata_llm.get("keywords")):
            result.append(dict(row))
    return result


def extract_with_qwen(
    items: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    _retry_missing: bool = True,
) -> dict[str, list[str]]:
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
                    "你是电力标准知识库的关键词抽取器。为每个chunk提取3到8个检索关键词。"
                    "优先保留标准术语、试验名称、设备类型、型号、表格主题和有区分度的数值条件；"
                    "不要输出泛词，不要解释。严格返回JSON对象："
                    '{"items":[{"id":"原id","keywords":["词1","词2"]}]}。'
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
            f"Qwen keyword extraction failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )
    content = response.output["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
    expected_ids = {item["id"] for item in items}
    output: dict[str, list[str]] = {}
    for item in parsed.get("items", []):
        if not isinstance(item, dict) or item.get("id") not in expected_ids:
            continue
        keywords = _normalize_keywords(item.get("keywords"))
        if keywords:
            output[str(item["id"])] = keywords
    missing = expected_ids - output.keys()
    if missing:
        if not _retry_missing:
            raise RuntimeError(f"Qwen response omitted {len(missing)} chunk ids")
        by_id = {item["id"]: item for item in items}
        for chunk_id in missing:
            output.update(
                extract_with_qwen(
                    [by_id[chunk_id]],
                    model=model,
                    _retry_missing=False,
                )
            )
    return output


def extract_keywords(
    *,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    limit: int | None = None,
    chunk_ids: list[str] | None = None,
    extractor: Callable[..., dict[str, list[str]]] = extract_with_qwen,
    on_batch: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    rows = pending_chunks(force=force, chunk_ids=chunk_ids)
    if limit is not None:
        rows = rows[:limit]
    completed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        items = [_prompt_item(row) for row in batch]
        suggestions = extractor(items, model=model)
        _store_keywords(suggestions, model=model)
        completed += len(batch)
        if on_batch:
            on_batch(completed, len(rows))
    return {"model": model, "eligible": len(rows), "extracted": completed}


def _prompt_item(row: Any) -> dict[str, str]:
    business = chunk_schema.parse_json_object(row["business_metadata"])
    context = {
        key: business.get(key)
        for key in ("standard_no", "content_type", "section", "section_title", "table_no", "table_title")
        if business.get(key) is not None
    }
    return {
        "id": str(row["id"]),
        "metadata": json.dumps(context, ensure_ascii=False),
        "text": str(row["text"] or "")[:MAX_TEXT_CHARS],
    }


def _store_keywords(suggestions: dict[str, list[str]], *, model: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        for chunk_id, keywords in suggestions.items():
            keywords = _normalize_keywords(keywords)
            if not keywords:
                continue
            row = conn.execute("SELECT metadata_llm FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if not row:
                continue
            metadata_llm = chunk_schema.parse_json_object(row["metadata_llm"])
            metadata_llm["keywords"] = {
                "value": keywords,
                "model": model,
                "generated_at": now,
            }
            conn.execute(
                "UPDATE chunks SET metadata_llm=?, updated_at=? WHERE id=?",
                (json.dumps(metadata_llm, ensure_ascii=False), now, chunk_id),
            )


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
