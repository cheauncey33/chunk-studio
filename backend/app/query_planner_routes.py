"""Fixed query-planner rewrite routes stored on assistant retrieval_config."""
from __future__ import annotations

from typing import Any

QUERY_PLANNER_ROUTE_IDS: tuple[str, ...] = (
    "semantic",
    "keyword",
    "table_target",
    "section_target",
)

_DEFAULT_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "id": "semantic",
        "enabled": True,
        "label": "语义改写",
        "instruction": "用于寻找能判断该报告要求是否有标准依据的规则或参数证据",
    },
    {
        "id": "keyword",
        "enabled": True,
        "label": "关键词",
        "instruction": "保留标准术语、参数符号、产品条件、试验简称与型号关键片段",
    },
    {
        "id": "table_target",
        "enabled": True,
        "label": "表格定向",
        "instruction": "如果目标证据可能是参数表、限值表或试验电压表，描述希望寻找的表格主题，否则为空字符串",
    },
    {
        "id": "section_target",
        "enabled": True,
        "label": "章节定向",
        "instruction": "如果目标证据可能是规则、公式、方法或适用条件，描述希望寻找的章节主题，否则为空字符串",
    },
)


def default_query_planner_routes() -> list[dict[str, Any]]:
    return [dict(item) for item in _DEFAULT_ROUTES]


def resolve_query_planner_routes(payload: Any) -> list[dict[str, Any]]:
    """Normalize stored routes; always return the fixed 4 ids in canonical order."""
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            route_id = str(item.get("id") or "").strip()
            if route_id not in QUERY_PLANNER_ROUTE_IDS:
                continue
            by_id[route_id] = item

    out: list[dict[str, Any]] = []
    for default in _DEFAULT_ROUTES:
        route_id = str(default["id"])
        raw = by_id.get(route_id) or {}
        label = str(raw.get("label") or default["label"]).strip() or str(default["label"])
        instruction = str(raw.get("instruction") or default["instruction"]).strip()
        if not instruction:
            instruction = str(default["instruction"])
        enabled = bool(raw.get("enabled", default["enabled"]))
        out.append(
            {
                "id": route_id,
                "enabled": enabled,
                "label": label,
                "instruction": instruction,
            }
        )
    # At least one route must stay enabled.
    if not any(item["enabled"] for item in out):
        out[0]["enabled"] = True
    return out


def enabled_query_planner_route_ids(payload: Any) -> list[str]:
    return [item["id"] for item in resolve_query_planner_routes(payload) if item["enabled"]]


def looks_like_full_query_planner_prompt(content: str) -> bool:
    """Detect legacy full Planner prompts wrongly stored as category notes."""
    text = (content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if "query planner" not in lowered and "你是" not in text:
        return False
    has_routes = (
        "semantic" in lowered
        and "keyword" in lowered
        and "table_target" in lowered
        and "section_target" in lowered
    )
    has_role = "query planner" in lowered or "检索表达" in text
    return has_routes and has_role
