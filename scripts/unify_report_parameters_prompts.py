"""Collapse report_parameters prompts to schema-driven extraction_brief.

Static node_prompts.report_parameters.content becomes optional notes only
(usually empty). Runtime builds one brief from parameter_schema + KB.

Usage (from repo root):

    $env:PYTHONPATH='backend'; uv run python scripts/unify_report_parameters_prompts.py
    $env:PYTHONPATH='backend'; uv run python scripts/unify_report_parameters_prompts.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.parameter_schema import resolve_parameter_schema  # noqa: E402

CABLE_TRANSFORMER_HINTS: dict[str, str] = {
    "model": "报告首页「样品型号」，如 S20-、S13-；须尽量完整",
    "rated_capacity": "通常从型号或铭牌提取，如 200kVA、400kVA",
    "rated_voltage": "高压/低压组合，如 10/0.4kV、10000/400V",
    "phase_count": "三相或单相，常见写法：三相、3相",
    "connection_group": "如 Dyn11、Yyn0；电压比结果中 D/yn11 与 Dyn11 等价",
    "cooling_method": "如 ONAN、ONAF；仅当报告明确记载时提取，勿从型号臆测",
    "insulation_level": "如 LI75 AC35；勿与耐压试验电压混淆",
    "rated_frequency": "通常 50Hz 或 60Hz",
}

OIL_TRANSFORMER_HINTS: dict[str, str] = {
    "model": "首页「样品型号」或「型号规格」，完整保留勿截断",
    "rated_capacity": "型号或参数表，如 400 kVA；型号中的数字需与参数表核对",
    "rated_voltage": "高压/低压组合，如 10/0.4 kV；注意分接范围写法",
    "phase_count": "通常为三相",
    "connection_group": "电压比结果中的联结组标号，如 Dyn11、D/yn11，保留原始写法",
    "cooling_method": "油浸常见 ONAN，干式常见 AN/AF；仅当报告明确记载时提取",
    "insulation_level": "绝缘试验依据或标准中，如 LI 75 AC 35；勿与耐压试验电压混淆",
    "tapping_type": "无励磁调压 / 有载调压等",
    "insulation_class": "干式常见 B/F/H 级；油浸式常不标注",
    "standard": "检测依据中的主要标准编号，如 GB/T 6451-2023",
}


def _enrich_schema_hints(
    schema: dict[str, Any],
    hints: dict[str, str],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    resolved = resolve_parameter_schema(schema)
    fields: list[dict[str, Any]] = []
    for field in resolved.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        current = str(field.get("hint") or "").strip()
        mapped = hints.get(key, "")
        next_hint = mapped if (overwrite and mapped) else (current or mapped)
        fields.append({**field, "hint": next_hint})
    return {
        "version": int(resolved.get("version") or 1),
        "allow_extra": bool(resolved.get("allow_extra")),
        "fields": fields,
    }


CABLE_TRANSFORMER_NOTES = """\
依据 GB/T 1094.1 与配电变压器报告习惯：型号多在首页「样品型号」；联结组别常见于电压比测量结果。
- 联结组别（connection_group）与「联结组标号」是同一概念，注意格式（如 Dyn11）；D/yn11 与 Dyn11 视为同类信息，保留原文。
- 绝缘水平是报告级参数；外施耐压试验电压是检测结果，不得提取。
- 冷却方式若报告未明确记载，不得从型号推测（如 S20 不自动推断为 ONAN）。
- 额定容量若已包含在型号中（如 S20-200/10）可从型号提取；型号不完整时再查其它位置。"""

OIL_TRANSFORMER_NOTES = """\
本品类为电力变压器（油浸式/干式）。型号、容量、电压等多见于报告首页样品信息与参数表；联结组标号常见于电压比测量结果；执行标准多见于检测依据。
- 联结组标号写法可能含斜杠（D/yn11），与 Dyn11 视为同类信息，保留原文。
- 绝缘水平是报告级参数；外施耐压试验电压/时长是检测结果，不得提取。
- 冷却方式仅当报告明确记载时提取，勿仅凭型号字母臆测。
- 另禁止提取：空载/负载损耗、短路阻抗、空载电流、温升、绝缘电阻、电压比偏差、声级测定结果、任何检测项目的符合/不符合结论。"""


def _profile(assistant_name: str, schema: dict[str, Any], content: str, version_name: str) -> tuple[dict[str, str], str]:
    blob = f"{assistant_name} {version_name} {content}"
    keys = {str(f.get("key") or "") for f in (schema.get("fields") or []) if isinstance(f, dict)}
    if "tapping_type" in keys or "油浸" in assistant_name or "绝缘耐热" in content:
        return OIL_TRANSFORMER_HINTS, OIL_TRANSFORMER_NOTES
    if "电力变压器" in blob or "rated_frequency" in keys:
        return CABLE_TRANSFORMER_HINTS, CABLE_TRANSFORMER_NOTES
    if "connection_group" in keys and "油浸" in assistant_name:
        return OIL_TRANSFORMER_HINTS, OIL_TRANSFORMER_NOTES
    if "connection_group" in keys:
        return CABLE_TRANSFORMER_HINTS, CABLE_TRANSFORMER_NOTES
    return {}, ""


def _needs_unify(content: str, *, force: bool) -> bool:
    if force:
        return True
    text = (content or "").strip()
    if "本品类需提取" in text or "| `model` |" in text:
        return True
    if '"model":' in text and '"parameters"' not in text:
        return True
    if "你是检测报告参数提取器" in text and "品类约束与易混淆" not in text:
        return True
    if "字段清单以系统注入" in text:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT a.id AS assistant_id, a.name AS assistant_name,
               v.id AS version_id, v.version, v.name AS version_name,
               v.node_prompts, v.parameter_schema
        FROM audit_assistants a
        JOIN assistant_versions v ON v.assistant_id = a.id
        ORDER BY a.name, v.version
        """
    ).fetchall()

    updated = 0
    for row in rows:
        prompts = json.loads(row["node_prompts"] or "{}")
        rp = prompts.get("report_parameters") if isinstance(prompts, dict) else None
        if not isinstance(rp, dict):
            continue
        content = str(rp.get("content") or "")
        if not _needs_unify(content, force=args.force):
            continue
        schema = json.loads(row["parameter_schema"] or "{}")
        if not isinstance(schema, dict):
            schema = {}
        hints, notes = _profile(
            str(row["assistant_name"] or ""),
            schema,
            content,
            str(row["version_name"] or ""),
        )
        new_schema = (
            _enrich_schema_hints(schema, hints, overwrite=True)
            if hints
            else resolve_parameter_schema(schema)
        )
        prompts["report_parameters"] = {
            **rp,
            "content": notes,
            "path": "unified/extraction_brief_v1",
        }
        print(
            f"update {row['assistant_name']} v{row['version']} "
            f"{row['version_name'] or ''} notes={len(notes)} hints={len(hints)}"
        )
        if not args.dry_run:
            conn.execute(
                """
                UPDATE assistant_versions
                SET node_prompts = ?, parameter_schema = ?
                WHERE id = ?
                """,
                (
                    json.dumps(prompts, ensure_ascii=False),
                    json.dumps(new_schema, ensure_ascii=False),
                    row["version_id"],
                ),
            )
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(("dry-run " if args.dry_run else "") + f"updated {updated} version(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
