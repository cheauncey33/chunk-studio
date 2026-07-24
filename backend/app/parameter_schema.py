"""Report-parameter schema helpers for audit assistants."""
from __future__ import annotations

from typing import Any


OIL_PARAMETER_FIELDS: tuple[str, ...] = (
    "model",
    "rated_capacity",
    "rated_voltage",
    "phase_count",
    "connection_group",
    "cooling_method",
    "insulation_level",
)

_OIL_FIELD_LABELS: dict[str, str] = {
    "model": "型号",
    "rated_capacity": "额定容量",
    "rated_voltage": "额定电压",
    "phase_count": "相数",
    "connection_group": "联结组别",
    "cooling_method": "冷却方式",
    "insulation_level": "绝缘水平",
}


def oil_parameter_schema() -> dict[str, Any]:
    """Oil-transformer specialized schema (matches legacy fixed fields)."""
    return {
        "version": 1,
        "allow_extra": False,
        "fields": [
            {
                "key": key,
                "label": _OIL_FIELD_LABELS[key],
                "required": key == "model",
                "hint": "",
            }
            for key in OIL_PARAMETER_FIELDS
        ],
    }


def generic_parameter_schema() -> dict[str, Any]:
    """Minimal category-agnostic schema; LLM may add extras."""
    return {
        "version": 1,
        "allow_extra": True,
        "fields": [
            {
                "key": "model",
                "label": "型号",
                "required": True,
                "hint": "报告记载的产品型号",
            }
        ],
    }


def resolve_parameter_schema(payload: Any) -> dict[str, Any]:
    """Normalize stored schema; fall back to oil fields for legacy versions."""
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
        return oil_parameter_schema()
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload["fields"]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        fields.append(
            {
                "key": key,
                "label": str(item.get("label") or key).strip() or key,
                "required": bool(item.get("required")),
                "hint": str(item.get("hint") or "").strip(),
            }
        )
    if not fields:
        return oil_parameter_schema()
    if "model" not in seen:
        fields.insert(
            0,
            {
                "key": "model",
                "label": "型号",
                "required": True,
                "hint": "报告记载的产品型号",
            },
        )
    return {
        "version": int(payload.get("version") or 1),
        "allow_extra": bool(payload.get("allow_extra", True)),
        "fields": fields,
    }


def _join_value_unit(value: Any, unit: Any = "") -> str:
    text = str(value or "").strip()
    unit_text = str(unit or "").strip()
    if text and unit_text and unit_text not in text:
        return f"{text} {unit_text}".strip()
    return text


def _raw_parameter_items(result: Any) -> list[tuple[str, str]]:
    """Accept open list format or legacy flat string map."""
    if not isinstance(result, dict):
        raise ValueError("parameter extraction must return a JSON object")

    items = result.get("parameters")
    if isinstance(items, list):
        parsed: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            parsed.append((key, _join_value_unit(item.get("value"), item.get("unit"))))
        return parsed

    # Legacy flat map: {"model": "...", "rated_capacity": "..."}
    if any(key in result for key in OIL_PARAMETER_FIELDS) or (
        result and all(isinstance(v, (str, int, float, type(None))) for v in result.values())
    ):
        return [
            (str(key).strip(), _join_value_unit(value))
            for key, value in result.items()
            if str(key).strip() and key != "parameters"
        ]

    raise ValueError("parameter extraction missing parameters list")


def normalize_extracted_parameters(
    result: Any,
    schema: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize LLM output into a flat dict for downstream nodes."""
    resolved = resolve_parameter_schema(schema)
    schema_keys = [str(field["key"]) for field in resolved["fields"]]
    allow_extra = bool(resolved.get("allow_extra"))
    raw_items = _raw_parameter_items(result)

    collected: dict[str, str] = {}
    for key, value in raw_items:
        if key in schema_keys or allow_extra:
            # First write wins for duplicates; schema order filled below.
            collected.setdefault(key, value)

    out: dict[str, str] = {}
    for key in schema_keys:
        out[key] = collected.get(key, "")
    if allow_extra:
        for key, value in collected.items():
            if key not in out:
                out[key] = value
    return out
