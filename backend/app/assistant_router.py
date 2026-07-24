"""LLM router: pick the 1:1 knowledge-base assistant for a report."""
from __future__ import annotations

import json
from typing import Any

from . import audit_run, config, db, llm

GENERIC_TEMPLATE_ID = "assistant_audit_template"
DEFAULT_FALLBACK_ID = "assistant_oil_transformer_audit"
ROUTER_PROMPT_PATH = (
    config.PROJECT_ROOT / "evaluation" / "prompts" / "generic" / "assistant_router_v1.md"
)
MAX_REPORT_CHARS = 8000


def _fallback_assistant_id() -> str:
    value = str(db.get_setting("audit.default_assistant_id") or "").strip()
    return value or DEFAULT_FALLBACK_ID


def _router_prompt() -> str:
    if ROUTER_PROMPT_PATH.is_file():
        return ROUTER_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "从 candidates 中选择最匹配的 assistant_id，输出 JSON："
        '{"assistant_id":"","knowledge_base_id":"","confidence":0,"reason":""}'
    )


def list_routable_candidates() -> list[dict[str, Any]]:
    """Assistants with exactly one bound active KB (exclude template)."""
    rows = db.get_conn().execute(
        """SELECT a.id AS assistant_id, a.name AS assistant_name,
                  a.description AS assistant_description,
                  a.active_version_id,
                  v.category_profile,
                  kb.id AS knowledge_base_id, kb.name AS knowledge_base_name,
                  kb.description AS knowledge_base_description
           FROM audit_assistants a
           JOIN assistant_knowledge_bases akb
             ON akb.assistant_id=a.id AND akb.enabled=1
           JOIN assistant_versions v ON v.id=a.active_version_id
           JOIN knowledge_bases kb
             ON kb.id=akb.knowledge_base_id AND kb.status='active'
           WHERE a.status='active'
             AND a.id!=?
             AND a.active_version_id IS NOT NULL
           ORDER BY kb.name ASC, a.name ASC""",
        (GENERIC_TEMPLATE_ID,),
    ).fetchall()
    # Keep one row per assistant (1:1 enforcement may leave temporary duplicates).
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        assistant_id = str(row["assistant_id"])
        if assistant_id in seen:
            continue
        seen.add(assistant_id)
        try:
            category_profile = json.loads(row["category_profile"] or "{}")
        except (json.JSONDecodeError, TypeError):
            category_profile = {}
        if not isinstance(category_profile, dict):
            category_profile = {}
        out.append(
            {
                "assistant_id": assistant_id,
                "assistant_name": row["assistant_name"],
                "assistant_description": row["assistant_description"] or "",
                "category_profile": category_profile,
                "knowledge_base_id": row["knowledge_base_id"],
                "knowledge_base_name": row["knowledge_base_name"],
                "knowledge_base_description": row["knowledge_base_description"] or "",
            }
        )
    return out


def route_report_to_assistant(
    *,
    report_file_id: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Choose an assistant for the report; fall back when needed."""
    candidates = list_routable_candidates()
    fallback_id = _fallback_assistant_id()
    markdown_path = audit_run.resolve_markdown_path(report_file_id)
    excerpt = markdown_path.read_text(encoding="utf-8")[:MAX_REPORT_CHARS]

    if not candidates:
        raise ValueError("系统中没有可路由的审查配置（需要知识库已绑定助手）")

    if len(candidates) == 1:
        only = candidates[0]
        return {
            "assistant_id": only["assistant_id"],
            "knowledge_base_id": only["knowledge_base_id"],
            "confidence": 1.0,
            "reason": "系统仅有一个可路由审查配置",
            "routed_by": "singleton",
            "candidates": candidates,
            "fallback_used": False,
        }

    try:
        result = llm.chat_json(
            [
                {"role": "system", "content": _router_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "report_excerpt": excerpt,
                            "candidates": candidates,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            model=model,
        )
    except Exception as exc:
        chosen = next(
            (item for item in candidates if item["assistant_id"] == fallback_id),
            candidates[0],
        )
        return {
            "assistant_id": chosen["assistant_id"],
            "knowledge_base_id": chosen["knowledge_base_id"],
            "confidence": 0.0,
            "reason": f"路由模型调用失败，使用兜底：{exc}",
            "routed_by": "fallback_error",
            "candidates": candidates,
            "fallback_used": True,
        }

    assistant_id = str(result.get("assistant_id") or "").strip()
    by_id = {item["assistant_id"]: item for item in candidates}
    if assistant_id not in by_id:
        chosen = next(
            (item for item in candidates if item["assistant_id"] == fallback_id),
            candidates[0],
        )
        return {
            "assistant_id": chosen["assistant_id"],
            "knowledge_base_id": chosen["knowledge_base_id"],
            "confidence": 0.0,
            "reason": f"模型返回了无效助手 id，使用兜底。原始理由：{result.get('reason') or ''}",
            "routed_by": "fallback_invalid",
            "candidates": candidates,
            "fallback_used": True,
            "raw": result,
        }

    chosen = by_id[assistant_id]
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "assistant_id": chosen["assistant_id"],
        "knowledge_base_id": chosen["knowledge_base_id"],
        "confidence": confidence,
        "reason": str(result.get("reason") or "").strip() or "模型已选择",
        "routed_by": "llm",
        "candidates": candidates,
        "fallback_used": False,
        "raw": result,
    }
