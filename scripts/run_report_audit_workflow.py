"""Run a read-only end-to-end report standard-value audit.

Audits every extracted test-item requirement in the report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, embeddings, llm  # noqa: E402
from app.storage.repositories import get_content_repository, get_content_write_repository  # noqa: E402
from app.agent_runtime import AgentPolicy  # noqa: E402
from app.audit_policy import (  # noqa: E402
    PRODUCTION_EVIDENCE_COMPRESSION_MODE,
    PRODUCTION_RECOVERY_MODE,
    RECOVERY_MAX_SEARCH_CALLS,
    RECOVERY_MAX_TOOL_CALLS,
    RECOVERY_MAX_TURNS,
    RECOVERY_TIMEOUT_SECONDS,
)
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from app.evidence_compression import compress_judge_input  # noqa: E402
from app.audit_semantics import (  # noqa: E402
    annotate_candidates,
    build_deterministic_comparisons,
    evaluate_candidate_applicability,
    resolve_applicability,
)
from app.parameter_schema import (  # noqa: E402
    normalize_extracted_parameters,
    present_parameter_labels,
    resolve_parameter_schema,
)
from app.prompt_vars import build_prompt_var_context, compose_runtime_prompt  # noqa: E402
from app.query_planner_routes import (  # noqa: E402
    enabled_query_planner_route_ids,
    resolve_query_planner_routes,
)
from app.recovery.gate import decide_recovery  # noqa: E402
from app.recovery.runner import run_recovery_agent  # noqa: E402
from app.recovery.tools import RecoveryToolEnvironment, _default_exact_search  # noqa: E402
from extract_report_test_items import extract_report  # noqa: E402


ROUTE_TOP_K = 20
FINAL_PER_TYPE = 20
RRF_K = 60
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "hbjc_end_to_end_audit_v1.json"
DEFAULT_JUDGE_CONCURRENCY = 8
MAX_JUDGE_CONCURRENCY = 500


def _content_repository():
    return get_content_repository() or get_content_write_repository()


def _call_model(system_prompt: str, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
    return llm.chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        model=model,
        temperature=0,
    )


def _production_query(case: dict[str, Any]) -> str:
    context = case.get("sample_context") or {}
    parts = [
        str(context.get(key) or "").strip()
        for key in ("model", "rated_capacity", "rated_voltage", "sample_name")
    ]
    parts.extend([
        case["test_item"]["project_name"],
        case["reported_requirement"]["text"],
    ])
    return " ".join(dict.fromkeys(part for part in parts if part))
_DETECTION_BASIS_CELL_RE = re.compile(
    r"<td\b[^>]*>\s*(?:检测|检验)依据\s*</td>\s*"
    r"<td\b[^>]*>(?P<content>.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_DETECTION_BASIS_SECTION_RE = re.compile(
    r"(?:^|\n)#{1,6}\s*(?:检测|检验)依据\s*\n"
    r"(?P<content>.*?)(?=\n#{1,6}\s|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_STANDARD_NO_RE = re.compile(
    r"(?<![A-Z0-9])"
    r"(?P<prefix>GB\s*(?:[/∕／]\s*T)?|JB\s*[/∕／]\s*T|"
    r"DL\s*[/∕／]\s*T|Q\s*[/∕／]\s*GDW|IEC|ISO)"
    r"\s*(?P<number>\d+(?:\.\d+)*\s*-\s*\d{4})",
    re.IGNORECASE,
)

STAGE_LABELS = {
    "report_parameters": "提取样品参数",
    "test_items": "提取检测项目",
    "model_decode": "型号解码",
    "audit_cases": "逐项判定",
    "writing_report": "写入结果",
}


def _normalize_standard_no(value: str) -> str:
    text = str(value or "").upper().replace("∕", "/").replace("／", "/")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"^GBT(?=\s*\d)", "GB/T", text)
    return text


def _extract_detection_basis_standard_nos(markdown: str) -> list[str]:
    """Extract declared standard numbers only from the report detection-basis block."""
    blocks = [
        match.group("content")
        for match in _DETECTION_BASIS_CELL_RE.finditer(markdown)
    ]
    if not blocks:
        blocks = [
            match.group("content")
            for match in _DETECTION_BASIS_SECTION_RE.finditer(markdown)
        ]
    standards: list[str] = []
    for block in blocks:
        text = unescape(re.sub(r"<[^>]+>", " ", block))
        for match in _STANDARD_NO_RE.finditer(text):
            standard_no = _normalize_standard_no(
                f"{match.group('prefix')} {match.group('number')}"
            )
            if standard_no not in standards:
                standards.append(standard_no)
    if not standards:
        raise ValueError("报告检测依据中没有可识别的标准号")
    return standards


def _filter_evidence_file_ids_by_detection_basis(
    file_ids: list[str],
    declared_standard_nos: list[str],
) -> list[str]:
    """Keep bound-KB evidence files whose chunk standard number is declared."""
    if not file_ids:
        raise ValueError("助手知识库没有可用标准文件")
    repository = _content_repository()
    if repository is not None:
        rows = [
            {
                "file_id": file_id,
                "standard_no": (chunk.get("business_metadata") or {}).get("standard_no"),
            }
            for file_id in file_ids
            for chunk in repository.list_chunks(file_id=file_id, limit=1000)
        ]
    else:
        placeholders = ",".join("?" for _ in file_ids)
        rows = db.get_conn().execute(
            f"""SELECT DISTINCT file_id,
                       json_extract(business_metadata, '$.standard_no') AS standard_no
                FROM chunks
                WHERE file_id IN ({placeholders})""",
            file_ids,
        ).fetchall()
    declared = {_normalize_standard_no(item) for item in declared_standard_nos}
    standards_by_file: dict[str, set[str]] = {}
    matched_standards: set[str] = set()
    for row in rows:
        standard_no = _normalize_standard_no(row["standard_no"])
        if not standard_no:
            continue
        standards_by_file.setdefault(str(row["file_id"]), set()).add(standard_no)
        if standard_no in declared:
            matched_standards.add(standard_no)
    missing = sorted(declared - matched_standards)
    if missing:
        raise ValueError(
            "报告检测依据中的标准未在当前知识库找到：" + "、".join(missing)
        )
    scoped = [
        file_id
        for file_id in file_ids
        if standards_by_file.get(file_id, set()) & declared
    ]
    if not scoped:
        raise ValueError("报告检测依据没有匹配到可检索的知识库文件")
    return scoped


def _report_job_progress(job_id: str, **fields: Any) -> None:
    """Best-effort live progress into jobs.result.progress (never fails the audit)."""
    jid = str(job_id or "").strip()
    if not jid:
        return
    stage = str(fields.get("stage") or "").strip()
    progress = {
        "stage": stage,
        "stage_label": str(fields.get("stage_label") or STAGE_LABELS.get(stage) or stage),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for key in ("case_done", "case_total", "case_label", "percent", "message", "project_name"):
        if key in fields and fields[key] is not None:
            progress[key] = fields[key]
    try:
        from app import jobs

        jobs.merge_job_result(jid, {"progress": progress})
    except Exception:
        pass
# Fallback for assistant versions created before peer_context_rules moved into
# retrieval_config (see db._default_retrieval_config).
DEFAULT_PEER_CONTEXT_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (
        ("总损耗", "P总"),
        ("空载损耗", "负载损耗", "总损耗", "P0", "Pk", "P总"),
    ),
    (
        ("频率", "Hz"),
        ("频率", "Hz", "持续时间", "试验时间", "感应耐压"),
    ),
]


def _resolve_peer_context_rules(
    retrieval_config: dict[str, Any],
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Read peer-context trigger rules from assistant config, else fallback.

    An explicitly configured empty list disables peer context; only a missing
    key falls back to the built-in defaults.
    """
    raw = retrieval_config.get("peer_context_rules")
    if not isinstance(raw, list):
        return list(DEFAULT_PEER_CONTEXT_RULES)
    rules: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        triggers = tuple(
            str(value).strip() for value in item.get("triggers") or [] if str(value).strip()
        )
        related = tuple(
            str(value).strip() for value in item.get("related") or [] if str(value).strip()
        )
        if triggers and related:
            rules.append((triggers, related))
    return rules


def _load_assistant_version(assistant_id: str) -> dict[str, Any]:
    repository = _content_repository()
    row = (
        repository.get_active_assistant_version(assistant_id)
        if repository is not None
        else db.get_conn().execute(
            """SELECT v.*
               FROM audit_assistants a
               JOIN assistant_versions v ON v.id=a.active_version_id
               WHERE a.id=? AND a.status='active'""",
            (assistant_id,),
        ).fetchone()
    )
    if not row:
        raise ValueError(f"active assistant version not found: {assistant_id}")
    payload = dict(row)
    for key in (
        "model_config",
        "node_prompts",
        "rules",
        "retrieval_config",
        "parameter_schema",
        "category_profile",
        "initialization_provenance",
    ):
        raw = payload.get(key)
        if raw is None:
            payload[key] = {}
        elif isinstance(raw, (dict, list)):
            payload[key] = raw
        else:
            payload[key] = json.loads(raw or "{}")
    payload["parameter_schema"] = resolve_parameter_schema(payload.get("parameter_schema"))
    if payload["model_config"].get("provider") != "deepseek":
        raise ValueError("only DeepSeek assistant versions can run this workflow")
    return payload


def _assistant_evidence_file_ids(
    assistant_id: str,
    *,
    excluded_file_ids: set[str] | None = None,
) -> list[str]:
    repository = _content_repository()
    if repository is not None:
        return repository.assistant_evidence_file_ids(
            assistant_id,
            excluded_file_ids=excluded_file_ids,
        )
    return db.assistant_evidence_file_ids(
        assistant_id,
        excluded_file_ids=excluded_file_ids,
    )


def _prompt_content(
    profile: dict[str, Any],
    key: str,
    *,
    var_context: dict[str, str] | None = None,
) -> str:
    prompt = profile["node_prompts"].get(key) or {}
    content = str(prompt.get("content") or "")
    if var_context is None:
        if not content.strip():
            raise ValueError(f"assistant version omitted prompt: {key}")
        return content
    composed = compose_runtime_prompt(content, step_id=key, context=var_context)
    if not composed.strip():
        raise ValueError(f"assistant version omitted prompt: {key}")
    return composed


def _assistant_prompt_var_context(
    assistant_id: str,
    profile: dict[str, Any],
) -> dict[str, str]:
    repository = _content_repository()
    bound = (
        repository.assistant_bound_knowledge_bases(assistant_id)
        if repository is not None
        else db.assistant_bound_knowledge_bases(assistant_id)
    )
    # Bound list is priority ASC; last row is highest priority (same as merge_manual_rules).
    primary = bound[-1] if bound else {}
    manual_rules = db.merge_manual_rules(
        bound,
        fallback=profile.get("rules"),
    )
    retrieval_config = profile.get("retrieval_config") or {}
    if not isinstance(retrieval_config, dict):
        retrieval_config = {}
    return build_prompt_var_context(
        parameter_schema=profile.get("parameter_schema"),
        manual_rules=manual_rules,
        kb_name=str(primary.get("name") or ""),
        kb_description=str(primary.get("description") or ""),
        query_planner_routes=resolve_query_planner_routes(
            retrieval_config.get("query_planner_routes"),
        ),
        assistant_rules=profile.get("rules"),
    )


def _extract_parameters(
    markdown: str,
    *,
    prompt: str,
    model: str,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, str]:
    schema = resolve_parameter_schema(parameter_schema)
    # Schema lives in the system prompt (extraction_brief); do not send it again.
    result = _call_model(
        prompt,
        {"report_markdown": markdown},
        model=model,
    )
    return normalize_extracted_parameters(result, schema)


def _empty_schema_fields(
    parameters: dict[str, Any] | None,
    parameter_schema: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Schema fields that are still blank after report-parameter extraction."""
    schema = resolve_parameter_schema(parameter_schema)
    params = parameters if isinstance(parameters, dict) else {}
    empty: list[dict[str, str]] = []
    for field in schema.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            continue
        if value is not None and not isinstance(value, str) and value != "":
            continue
        empty.append(
            {
                "key": key,
                "label": str(field.get("label") or key).strip() or key,
            }
        )
    return empty


def _decode_model(
    parameters: dict[str, str],
    naming_markdown: str,
    *,
    prompt: str,
    model: str,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    empty_fields = _empty_schema_fields(parameters, parameter_schema)
    result = _call_model(
        prompt,
        {
            "raw_model": parameters["model"],
            "report_parameters": parameters,
            "empty_schema_fields": empty_fields,
            "naming_rule_markdown": naming_markdown,
        },
        model=model,
    )
    if result.get("raw_model") != parameters["model"]:
        raise ValueError("naming decoder changed the raw model")
    for feature in result.get("decoded_features") or []:
        quote = str(feature.get("evidence_quote") or "")
        feature["quote_verified"] = bool(quote and quote in naming_markdown)
    # Keep only schema keys that were actually empty; drop unknown / non-empty targets.
    allowed = {item["key"] for item in empty_fields}
    raw_fills = result.get("schema_fills")
    fills: dict[str, str] = {}
    if isinstance(raw_fills, dict):
        for key, value in raw_fills.items():
            name = str(key or "").strip()
            text = str(value or "").strip()
            if name in allowed and text:
                fills[name] = text
    result["schema_fills"] = fills
    return result


def _load_manual_knowledge_rules(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is None:
        if _content_repository() is None:
            db.init_db()
        payload = _load_assistant_version(
            "assistant_oil_transformer_audit"
        )["rules"]
    if payload.get("scope") != "knowledge_base_manual_rules":
        raise ValueError("manual knowledge rule scope mismatch")
    if not isinstance(payload.get("rules"), list):
        raise ValueError("manual knowledge rules must contain a rules list")
    return payload


def _select_manual_knowledge_rules(
    rules_payload: dict[str, Any],
    runtime_case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pass KB manual rules straight into audit_judge.

    Selection used to hard-code a single total-loss rule_id. Rules are few and
    the judge prompt already constrains allowed_use, so the full set is injected.
    ``runtime_case`` is kept for call-site compatibility.
    """
    del runtime_case  # unused; kept so callers need not change
    rules = [
        rule
        for rule in (rules_payload.get("rules") or [])
        if isinstance(rule, dict) and str(rule.get("rule_text") or "").strip()
    ]
    return {**rules_payload, "rules": rules}


def _collect_enabled_planner_queries(
    planned: dict[str, Any] | None,
    *,
    query_planner_routes: Any,
    production_query: str,
) -> dict[str, str]:
    """Keep production fallback; only copy non-empty enabled planner routes."""
    queries: dict[str, str] = {"production": production_query}
    planned_map = planned if isinstance(planned, dict) else {}
    for route in enabled_query_planner_route_ids(query_planner_routes):
        value = str(planned_map.get(route) or "").strip()
        if value:
            queries[route] = value
    return queries


def _find_requirement(extracted: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    for item in extracted["items"]:
        if item["item_no"] == case["test_item"]["item_no"] and item["phase"] == case["test_item"]["phase"]:
            expected = case["reported_requirement"]["text"].replace(" ", "")
            for requirement in item["requirements"]:
                if requirement["requirement_text"].replace(" ", "") == expected:
                    return {"test_item": item, "requirement": requirement}
    raise ValueError(f"fresh extraction did not reproduce {case['case_id']}")


def _full_audit_case_id(item: dict[str, Any], requirement: dict[str, Any]) -> str:
    basis = "|".join((
        str(item.get("item_no") or ""),
        str(item.get("phase") or ""),
        str(requirement.get("requirement_text") or "").replace(" ", ""),
    ))
    return f"item_{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:12]}"


def _normalize_dedupe_number(match: re.Match[str]) -> str:
    """Collapse trailing zeros so 1.0 / 1.00 match 1 (keep meaningful decimals)."""
    raw = match.group(0)
    if "." not in raw:
        return raw
    trimmed = raw.rstrip("0").rstrip(".")
    return trimmed or "0"


def _normalize_requirement_dedupe_text(text: str, unit: str = "") -> str:
    """Normalize requirement wording for program dedupe (not semantic LLM dedupe).

    Handles whitespace, common unit decorations, inequality glyphs, and numeric
    trailing zeros so ``≤1`` / ``≤1.0`` / ``(%) :≤1.0`` collapse when equivalent.
    """
    value = str(text or "")
    value = (
        value.replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
        .replace("％", "%")
        .replace("≤", "<=")
        .replace("≧", ">=")
        .replace("≥", ">=")
        .replace("＜=", "<=")
        .replace("＞=", ">=")
    )
    value = "".join(value.split())
    unit_text = str(unit or "").strip()
    if unit_text and unit_text not in {"/", "-"}:
        for token in (
            f"({unit_text})",
            unit_text,
        ):
            value = value.replace(token, "")
    # Inline unit markers often duplicated in requirement_text even when unit="%".
    for token in ("(%)", "%", "(％)", "％"):
        value = value.replace(token, "")
    value = re.sub(r"\d+\.\d+", _normalize_dedupe_number, value)
    return value.lower()


def _requirement_dedupe_key(item: dict[str, Any], requirement: dict[str, Any]) -> str:
    """Normalize project + requirement text for cross-phase dedupe."""
    project = str(item.get("project_name") or "").strip()
    text = _normalize_requirement_dedupe_text(
        str(requirement.get("requirement_text") or ""),
        str(requirement.get("unit") or ""),
    )
    return f"{project}\n{text}"


def _phase_dedupe_rank(phase: Any) -> int:
    """Lower rank wins when the same requirement appears in multiple phases."""
    value = str(phase or "").strip()
    if value == "initial":
        return 0
    if value == "repeat_routine":
        return 1
    return 2


def _build_full_audit_units(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    """One audit unit per unique project+requirement; prefer initial over repeat.

    Duplicates across ``initial`` / ``repeat_routine`` (and within the same phase)
    are audited once after lightweight text/number normalization.
    """
    preferred: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in extracted.get("items") or []:
        if not isinstance(item, dict):
            continue
        for requirement in item.get("requirements") or []:
            if not isinstance(requirement, dict):
                continue
            if not str(requirement.get("requirement_text") or "").strip():
                continue
            key = _requirement_dedupe_key(item, requirement)
            if not key.split("\n", 1)[-1]:
                continue
            candidate = {
                "test_item": item,
                "requirement": requirement,
                "phase": str(item.get("phase") or ""),
            }
            existing = preferred.get(key)
            if existing is None:
                preferred[key] = candidate
                order.append(key)
                continue
            if _phase_dedupe_rank(candidate["phase"]) < _phase_dedupe_rank(
                existing["phase"]
            ):
                preferred[key] = candidate

    units: list[dict[str, Any]] = []
    seen_case_ids: dict[str, int] = {}
    for key in order:
        chosen = preferred[key]
        item = chosen["test_item"]
        requirement = chosen["requirement"]
        case_id = _full_audit_case_id(item, requirement)
        count = seen_case_ids.get(case_id, 0)
        seen_case_ids[case_id] = count + 1
        if count:
            case_id = f"{case_id}_{count + 1}"
        units.append(
            {
                "case_id": case_id,
                "test_item": item,
                "requirement": requirement,
            }
        )
    return units


def _peer_context_terms(
    requirement_text: str,
    project_name: str,
    peer_context_rules: list[tuple[tuple[str, ...], tuple[str, ...]]],
) -> tuple[str, ...]:
    source = f"{project_name} {requirement_text}"
    terms: list[str] = []
    for triggers, related in peer_context_rules:
        if any(trigger in source for trigger in triggers):
            terms.extend(related)
    return tuple(dict.fromkeys(terms))


def _build_peer_report_context(
    extracted: dict[str, Any],
    current_item: dict[str, Any],
    current_requirement: dict[str, Any],
    *,
    peer_context_rules: list[tuple[tuple[str, ...], tuple[str, ...]]],
    limit: int = 12,
) -> list[dict[str, str]]:
    terms = _peer_context_terms(
        str(current_requirement.get("requirement_text") or ""),
        str(current_item.get("project_name") or ""),
        peer_context_rules,
    )
    if not terms:
        return []

    current_key = (
        str(current_item.get("item_no") or ""),
        str(current_item.get("phase") or ""),
        str(current_requirement.get("requirement_text") or "").replace(" ", ""),
    )
    peers: list[tuple[int, dict[str, str]]] = []
    for item in extracted["items"]:
        item_no = str(item.get("item_no") or "")
        phase = str(item.get("phase") or "")
        project_name = str(item.get("project_name") or "")
        for requirement in item.get("requirements") or []:
            text = str(requirement.get("requirement_text") or "")
            peer_key = (item_no, phase, text.replace(" ", ""))
            if peer_key == current_key:
                continue
            searchable = f"{project_name} {text}"
            score = sum(3 for term in terms if term and term in searchable)
            if item_no == str(current_item.get("item_no") or ""):
                score += 2
            if phase == str(current_item.get("phase") or ""):
                score += 1
            if score <= 0:
                continue
            peers.append((
                score,
                {
                    "item_no": item_no,
                    "project_name": project_name,
                    "phase": phase,
                    "requirement_text": text,
                    "unit": str(requirement.get("unit") or ""),
                },
            ))

    peers.sort(key=lambda item: item[0], reverse=True)
    return [peer for _, peer in peers[:limit]]


_TECH_SPEC_MARKERS = ("技术规范", "招标技术规范", "采购规范", "产品技术要求")
_INDUSTRY_STANDARD_PREFIX_RE = re.compile(
    r"^(JB|DL|NB|HB|SH|SY|MT|YD|TB|CJ|JGJ|NY|HJ|HJ/T|NB/T|JB/T|DL/T)(/|$)",
    re.IGNORECASE,
)


def _standard_priority(
    standard_no: str,
    *,
    title_blob: str = "",
) -> dict[str, Any]:
    """Classify evidence source priority for judge selection.

    Rank (lower is higher priority):
    1 技术规范书 → 2 企/行标 → 3 国标 → 4 其他/未知
    """
    sn = _normalize_standard_no(standard_no)
    blob = f"{sn} {title_blob}"
    if any(marker in blob for marker in _TECH_SPEC_MARKERS):
        return {"rank": 1, "tier": "technical_spec", "label": "技术规范书"}
    if sn.startswith("Q/") or re.match(r"^Q\s*\d", sn):
        return {"rank": 2, "tier": "enterprise_industry", "label": "企/行标"}
    if _INDUSTRY_STANDARD_PREFIX_RE.match(sn):
        return {"rank": 2, "tier": "enterprise_industry", "label": "企/行标"}
    if sn.startswith("GB"):
        return {"rank": 3, "tier": "national", "label": "国标"}
    return {"rank": 4, "tier": "other", "label": "其他"}


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("business_metadata") or {}
    title_blob = " ".join(
        str(metadata.get(key) or "")
        for key in ("table_title", "section_title", "section")
    )
    compact = {
        "candidate_key": candidate["candidate_key"],
        "content_type": candidate["content_type"],
        "business_metadata": metadata,
        "standard_priority": _standard_priority(
            str(metadata.get("standard_no") or ""),
            title_blob=title_blob,
        ),
        "text": candidate["text"],
    }
    roles = [str(role) for role in candidate.get("evidence_roles") or [] if str(role)]
    if roles:
        compact["evidence_roles"] = roles
    if isinstance(candidate.get("table_row_binding"), dict):
        compact["table_row_binding"] = candidate["table_row_binding"]
    return compact


def _candidate_locator(candidate: dict[str, Any]) -> tuple[str, str, str]:
    metadata = candidate["business_metadata"]
    return metadata.get("standard_no", ""), candidate["content_type"], chunk_text_sha256(candidate["text"])


def _evidence_locator(candidate: dict[str, Any]) -> dict[str, Any]:
    """Stable locator (no chunk UUIDs) so adopted evidence survives rebuilds."""
    metadata = candidate.get("business_metadata") or {}
    trace = candidate.get("source_trace") or {}
    locator: dict[str, Any] = {
        "standard_no": str(metadata.get("standard_no") or ""),
        "content_type": str(
            candidate.get("content_type") or metadata.get("content_type") or "section"
        ),
        "text_sha256": chunk_text_sha256(str(candidate.get("text") or "")),
    }
    page_start = trace.get("page_start") or candidate.get("page")
    page_end = trace.get("page_end") or page_start
    if page_start:
        locator["page_start"] = page_start
        locator["page_end"] = page_end
    for key in ("section", "section_title", "table_no", "table_title"):
        value = metadata.get(key)
        if value:
            locator[key] = value
    return locator


JUDGE_STATUSES = ("supported", "mismatch", "insufficient_context", "not_audited")
# Older assistant versions carry prompt snapshots that still emit these values.
LEGACY_JUDGE_STATUS_MAP = {
    "correct": "supported",
    "incorrect": "mismatch",
    "evidence_not_found": "not_audited",
}

_STATUS_CLAIM_RE = re.compile(
    r"(?:应判定为|故判定为|判定为|应输出)\s*"
    r"(supported|mismatch|insufficient_context|not_audited)",
    re.IGNORECASE,
)
_META_CORRECTION_RE = re.compile(r"之前误判|现更正|误判为|改判为|自我修正")
_SUPPORTED_SIGNAL_RE = re.compile(
    r"与标准一致|与报告要求一致|被标准支持|符合标准|报告要求被支持|"
    r"应判定为\s*supported|判定为\s*supported|应输出\s*supported",
    re.IGNORECASE,
)
_MISMATCH_SIGNAL_RE = re.compile(
    r"两者冲突|与标准冲突|与标准不符|直接冲突|报告要求与标准不一致|"
    r"应判定为\s*mismatch|判定为\s*mismatch|应输出\s*mismatch",
    re.IGNORECASE,
)
_MISSING_FIELD_SUFFIXES = ("ur", "um", "u", "值", "参数", "信息")


_COMPARISON_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_COMPARISON_KINDS = {
    "exact",
    "upper_bound",
    "lower_bound",
    "tolerance",
    "range",
    "scope_count",
    "text",
}
_COMPARISON_OPERATORS = {"eq", "le", "lt", "ge", "gt", "range", "tolerance", "unknown", ""}


def _comparison_number(value: Any) -> float | None:
    match = _COMPARISON_NUMBER_RE.search(str(value or "").replace(",", "."))
    return float(match.group(0)) if match else None


def _numeric_comparison_value(value: Any) -> bool:
    """True for quantities/formulas, false for alphanumeric category labels."""
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.match(r"^[\s≤≥<>±+\-]?(?:\d|\.|\(|sqrt)", text, re.IGNORECASE))


def _operator_direction(value: Any) -> str | None:
    operator = str(value or "").strip().lower()
    if operator in {"le", "lt"}:
        return "upper"
    if operator in {"ge", "gt"}:
        return "lower"
    if operator == "eq":
        return "exact"
    return None


def _standard_value_grounded_in_selected_evidence(
    comparison: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> bool:
    """Verify a simple nominal value is actually present in cited evidence."""
    raw_value = str(comparison.get("standard_value") or "").strip()
    unit = str(comparison.get("standard_unit") or "").strip()
    number = _comparison_number(raw_value)
    if number is None:
        return False
    number_token = re.escape(f"{number:g}")
    unit_token = re.escape(unit) if unit else ""
    for item in evidence:
        text = str(item.get("text") or "")
        if not re.search(rf"(?<![\d.]){number_token}(?![\d.])", text):
            continue
        if not unit_token:
            return True
        close = re.search(
            rf"(?:{number_token}\s*.{{0,16}}?{unit_token}|{unit_token}.{{0,16}}?{number_token})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        metadata = item.get("business_metadata") or {}
        is_table = str(metadata.get("content_type") or item.get("content_type") or "") == "table"
        if close or (is_table and re.search(unit_token, text, re.IGNORECASE)):
            return True
    return False


def _comparison_consistency_issues(judgment: dict[str, Any]) -> list[str]:
    """Cross-check the Judge's structured comparison without inventing evidence."""
    comparison = judgment.get("comparison")
    if comparison is None:
        return []
    if not isinstance(comparison, dict):
        return ["comparison must be an object"]
    kind = str(comparison.get("kind") or "").strip()
    if kind not in _COMPARISON_KINDS:
        return [f"comparison has invalid kind '{kind}'"]
    if kind == "text":
        if (
            _numeric_comparison_value(comparison.get("report_value"))
            or _numeric_comparison_value(comparison.get("standard_value"))
        ):
            return ["numeric comparison cannot use kind 'text'"]
        return []

    issues: list[str] = []
    status = str(judgment.get("status") or "").strip()
    relation = str(comparison.get("relation") or "unknown").strip()
    conclusion = str(comparison.get("conclusion") or "unknown").strip()
    report_operator = str(comparison.get("report_operator") or "").strip().lower()
    standard_operator = str(comparison.get("standard_operator") or "").strip().lower()
    if report_operator not in _COMPARISON_OPERATORS:
        issues.append(f"comparison has invalid report_operator '{report_operator}'")
    if standard_operator not in _COMPARISON_OPERATORS:
        issues.append(f"comparison has invalid standard_operator '{standard_operator}'")
    if status == "supported" and conclusion == "conflicts":
        issues.append("comparison conclusion conflicts with supported status")
    if status == "mismatch" and conclusion == "supports":
        issues.append("comparison conclusion supports the report under mismatch status")

    report_value = _comparison_number(comparison.get("report_value"))
    standard_value = _comparison_number(comparison.get("standard_value"))
    report_unit = str(comparison.get("report_unit") or "").strip().lower()
    standard_unit = str(comparison.get("standard_unit") or "").strip().lower()
    comparable_units = not report_unit or not standard_unit or report_unit == standard_unit
    computed: str | None = None
    if report_value is not None and standard_value is not None and comparable_units:
        equal = abs(report_value - standard_value) <= 1e-9
        report_direction = _operator_direction(report_operator)
        standard_direction = _operator_direction(standard_operator)
        if (
            report_direction in {"upper", "lower"}
            and standard_direction in {"upper", "lower"}
            and report_direction != standard_direction
        ):
            computed = "different"
        elif kind in {"exact", "scope_count"}:
            same_scope = True
            if kind == "scope_count":
                report_scope = str(comparison.get("report_scope") or "").strip().lower()
                standard_scope = str(comparison.get("standard_scope") or "").strip().lower()
                same_scope = bool(
                    report_scope and standard_scope and report_scope == standard_scope
                )
            computed = "equal" if equal and same_scope else "different"
        elif kind == "upper_bound":
            computed = (
                "equal"
                if equal
                else ("stricter" if report_value < standard_value else "looser")
            )
        elif kind == "lower_bound":
            computed = (
                "equal"
                if equal
                else ("stricter" if report_value > standard_value else "looser")
            )

    if kind == "tolerance":
        report_tolerance = _comparison_number(comparison.get("report_tolerance"))
        standard_tolerance = _comparison_number(comparison.get("standard_tolerance"))
        if status == "supported" and (report_value is None or standard_value is None):
            issues.append("tolerance comparison requires separate nominal report and standard values")
        if status == "supported" and (report_tolerance is None or standard_tolerance is None):
            issues.append("tolerance comparison requires separate report and standard tolerances")
        if (
            status == "supported"
            and not _standard_value_grounded_in_selected_evidence(
                comparison,
                [item for item in judgment.get("evidence") or [] if isinstance(item, dict)],
            )
        ):
            issues.append(
                "supported tolerance comparison nominal standard value is not grounded in selected evidence"
            )
        if (
            report_value is not None
            and standard_value is not None
            and report_tolerance is not None
            and standard_tolerance is not None
            and comparable_units
        ):
            same_base = abs(report_value - standard_value) <= 1e-9
            if not same_base:
                computed = "different"
            elif abs(report_tolerance - standard_tolerance) <= 1e-9:
                computed = "equal"
            elif report_tolerance < standard_tolerance:
                computed = "stricter"
            else:
                computed = "looser"

    compatible_conflicts = (
        status == "mismatch"
        and conclusion == "conflicts"
        and computed in {"different", "looser"}
        and relation in {"different", "looser"}
    )
    if computed and relation not in {computed, "unknown"} and not compatible_conflicts:
        issues.append(
            f"comparison relation '{relation}' disagrees with deterministic relation '{computed}'"
        )
    if computed in {"different", "looser"} and conclusion == "supports":
        issues.append(f"comparison marks deterministic relation '{computed}' as supports")
    if computed in {"equal", "stricter"} and conclusion == "conflicts":
        issues.append(f"comparison marks deterministic relation '{computed}' as conflicts")
    if status == "supported" and computed in {"different", "looser"}:
        issues.append(
            f"deterministic comparison relation '{computed}' conflicts with supported status"
        )
    return issues


def _evidence_role_issues(judgment: dict[str, Any]) -> list[str]:
    """Require value evidence instead of accepting a method clause as a value."""
    status = str(judgment.get("status") or "").strip()
    comparison = judgment.get("comparison")
    if status not in {"supported", "mismatch"} or not isinstance(comparison, dict):
        return []
    kind = str(comparison.get("kind") or "").strip()
    numeric_claim = kind in _COMPARISON_KINDS - {"text"}
    if not numeric_claim:
        return []
    selected = [item for item in judgment.get("evidence") or [] if isinstance(item, dict)]
    roles = {
        str(role)
        for item in selected
        for role in (item.get("evidence_roles") or [])
        if str(role)
    }
    issues: list[str] = []
    if "nominal_rule" not in roles:
        if roles and roles <= {"method_rule", "tolerance_rule", "applicability_rule"}:
            issues.append(
                "numeric nominal conclusion is supported only by method/tolerance/applicability evidence"
            )
        else:
            issues.append("numeric nominal conclusion requires selected nominal_rule evidence")
    if kind == "tolerance" and "tolerance_rule" not in roles:
        issues.append("tolerance conclusion requires selected tolerance_rule evidence")
    return issues


def _normalize_profile_token(value: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    return text.replace("铁心", "铁芯")


def _profile_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _sample_profile_coverage(
    sample_profile: dict[str, Any] | None,
) -> tuple[set[str], str]:
    """Return (alias tokens, joined searchable text) from sample_profile."""
    profile = sample_profile if isinstance(sample_profile, dict) else {}
    from_report = (
        profile.get("from_report") if isinstance(profile.get("from_report"), dict) else {}
    )
    decode = (
        profile.get("from_model_decode")
        if isinstance(profile.get("from_model_decode"), dict)
        else {}
    )
    aliases: set[str] = set()
    blobs: list[str] = []

    for key, label in present_parameter_labels(from_report).items():
        aliases.add(_normalize_profile_token(key))
        aliases.add(_normalize_profile_token(label))
        value = from_report.get(key)
        if _profile_has_value(value):
            blobs.append(str(value))

    raw_model = str(decode.get("raw_model") or "").strip()
    if raw_model:
        aliases.add(_normalize_profile_token(raw_model))
        blobs.append(raw_model)

    for item in decode.get("features") or []:
        if not isinstance(item, dict):
            continue
        segment = str(item.get("segment") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        if segment:
            aliases.add(_normalize_profile_token(segment))
            blobs.append(segment)
        if meaning:
            aliases.add(_normalize_profile_token(meaning))
            blobs.append(meaning)

    for segment, meaning in (decode.get("feature_meanings") or {}).items():
        seg = str(segment or "").strip()
        mean = str(meaning or "").strip()
        if seg:
            aliases.add(_normalize_profile_token(seg))
            blobs.append(seg)
        if mean:
            aliases.add(_normalize_profile_token(mean))
            blobs.append(mean)

    for term in decode.get("retrieval_terms") or []:
        text = str(term or "").strip()
        if text:
            aliases.add(_normalize_profile_token(text))
            blobs.append(text)

    aliases.discard("")
    joined = _normalize_profile_token("\n".join(blobs))
    return aliases, joined


def _missing_field_variants(field: str) -> list[str]:
    base = _normalize_profile_token(field)
    if not base:
        return []
    variants = [base]
    for suffix in _MISSING_FIELD_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix) + 1:
            variants.append(base[: -len(suffix)])
    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _missing_field_covered_by_profile(field: str, aliases: set[str], corpus: str) -> bool:
    for variant in _missing_field_variants(field):
        if variant in aliases:
            return True
        if len(variant) >= 2 and variant in corpus:
            return True
        for alias in aliases:
            if len(alias) < 2 or len(variant) < 2:
                continue
            # Avoid tiny ASCII tokens like "20" matching everywhere.
            if variant.isascii() and len(variant) < 3:
                continue
            if variant in alias or alias in variant:
                return True
    return False


def _spurious_missing_context_fields(
    judgment: dict[str, Any],
    sample_profile: dict[str, Any] | None,
) -> list[str]:
    """missing_context_fields that are already answered by sample_profile."""
    raw_fields = judgment.get("missing_context_fields") or []
    if not isinstance(raw_fields, list) or not raw_fields:
        return []
    aliases, corpus = _sample_profile_coverage(sample_profile)
    if not aliases and not corpus:
        return []
    covered: list[str] = []
    for item in raw_fields:
        field = str(item or "").strip()
        if field and _missing_field_covered_by_profile(field, aliases, corpus):
            covered.append(field)
    return covered


def _reason_status_conflict_issues(judgment: dict[str, Any]) -> list[str]:
    """Detect reason text that fights the declared status (generic heuristics)."""
    status = str(judgment.get("status") or "").strip()
    reason = str(judgment.get("reason") or "").strip()
    if not reason or status not in JUDGE_STATUSES:
        return []

    issues: list[str] = []
    if _META_CORRECTION_RE.search(reason):
        issues.append("reason uses self-correction phrasing (误判/更正/改判)")

    claimed = {match.group(1).lower() for match in _STATUS_CLAIM_RE.finditer(reason)}
    conflicting_claims = sorted(claimed - {status})
    if conflicting_claims:
        issues.append(
            "reason claims status "
            f"{'/'.join(conflicting_claims)} but status is '{status}'"
        )

    def has_unnegated_signal(pattern: re.Pattern[str]) -> bool:
        negative = re.compile(r"(?:无法|不能|未|难以|缺少|不足以|是否)\S{0,8}$")
        return any(
            not negative.search(reason[max(0, match.start() - 20):match.start()])
            for match in pattern.finditer(reason)
        )

    has_supported = has_unnegated_signal(_SUPPORTED_SIGNAL_RE)
    has_mismatch = has_unnegated_signal(_MISMATCH_SIGNAL_RE)
    if status == "mismatch" and has_supported and not has_mismatch:
        issues.append("reason supports agreement but status is mismatch")
    if status == "supported" and has_mismatch and not has_supported:
        issues.append("reason describes a conflict but status is supported")
    if status == "insufficient_context" and has_supported:
        issues.append(
            "reason asserts requirement/standard agreement but status is insufficient_context"
        )
    return issues


def _collect_judgment_consistency_issues(
    judgment: dict[str, Any],
    sample_profile: dict[str, Any] | None,
) -> list[str]:
    """Program checks that warrant rejecting the judgment and re-running judge."""
    issues = _reason_status_conflict_issues(judgment)
    issues.extend(_comparison_consistency_issues(judgment))
    issues.extend(_evidence_role_issues(judgment))
    covered = _spurious_missing_context_fields(judgment, sample_profile)
    if covered:
        issues.append(
            "missing_context_fields already present in sample_profile: "
            + ", ".join(covered)
        )
    return issues


def _validate_judgment(
    judgment: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enforce the audit-result contract on raw model output.

    Model output is never trusted as a hard filter: invalid statuses and
    evidence keys are normalized/dropped, and definitive verdicts without
    surviving evidence are downgraded to insufficient_context. Every applied
    fix is recorded under judgment.validation_issues.
    """
    by_key = {candidate["candidate_key"]: candidate for candidate in candidates}
    issues: list[str] = []

    raw_status = str(judgment.get("status") or "")
    status = LEGACY_JUDGE_STATUS_MAP.get(raw_status, raw_status)
    if status != raw_status:
        issues.append(f"legacy status '{raw_status}' mapped to '{status}'")
    if status not in JUDGE_STATUSES:
        issues.append(f"invalid status '{raw_status}' downgraded to insufficient_context")
        status = "insufficient_context"

    raw_keys = [str(key) for key in judgment.get("evidence_candidate_keys") or []]
    valid_keys = [key for key in raw_keys if key in by_key]
    dropped = [key for key in raw_keys if key not in by_key]
    if dropped:
        issues.append(f"dropped unknown evidence keys: {', '.join(sorted(set(dropped)))}")
    if status in ("supported", "mismatch") and not valid_keys:
        issues.append(
            f"definitive status '{status}' without valid evidence downgraded to insufficient_context"
        )
        status = "insufficient_context"

    judgment["status"] = status
    judgment["evidence_candidate_keys"] = valid_keys
    judgment["evidence"] = [
        {**_compact_candidate(by_key[key]), "locator": _evidence_locator(by_key[key])}
        for key in valid_keys
    ]
    if issues:
        judgment["validation_issues"] = issues
    return judgment


def _apply_deterministic_conflict(
    judgment: dict[str, Any],
    judge_input: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply only high-confidence conflicts produced from retrieved evidence."""
    comparisons = [
        item
        for item in judge_input.get("deterministic_comparisons") or []
        if isinstance(item, dict)
        and item.get("source") == "generic_bound_table_claim"
        and isinstance(item.get("trace"), dict)
        and item.get("relation") == "different"
        and item.get("conclusion") == "conflicts"
    ]
    by_key = {str(item.get("candidate_key") or ""): item for item in candidates}
    binding = next(
        (
            item
            for item in comparisons
            if str(item.get("candidate_key") or "") in by_key
        ),
        None,
    )
    if binding is None:
        return judgment
    key = str(binding["candidate_key"])
    source = str(binding.get("source") or "deterministic_binding")
    previous = {
        "status": judgment.get("status"),
        "reason": judgment.get("reason"),
        "evidence_candidate_keys": list(judgment.get("evidence_candidate_keys") or []),
    }
    deterministic = {
        **judgment,
        "status": "mismatch",
        "reason": (
            "确定性证据绑定发现报告名义值与适用标准值冲突："
            f"{binding.get('report_value')} != {binding.get('standard_value')}"
            f"（{source}）。"
        ),
        "evidence_candidate_keys": [key],
        "missing_context_fields": [],
        "comparison": {
            "kind": str(binding.get("kind") or "exact"),
            "report_value": str(binding.get("report_value") or ""),
            "report_unit": "",
            "report_operator": "eq",
            "standard_value": str(binding.get("standard_value") or ""),
            "standard_unit": "",
            "standard_operator": "eq",
            "report_tolerance": "",
            "standard_tolerance": "",
            "report_scope": "",
            "standard_scope": "",
            "relation": "different",
            "conclusion": "conflicts",
        },
        "deterministic_judge": {
            "applied": True,
            "binding": binding,
            "previous_judgment": previous,
        },
    }
    return _validate_judgment(deterministic, candidates)


def _run_audit_judge_with_consistency(
    *,
    judge_prompt: str,
    judge_input: dict[str, Any],
    judge_model: str,
    candidates: list[dict[str, Any]],
    sample_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call judge, validate contract, and rejudge once on consistency failures."""
    judgment = _validate_judgment(
        _call_model(judge_prompt, judge_input, model=judge_model),
        candidates,
    )
    judgment = _apply_deterministic_conflict(judgment, judge_input, candidates)
    trace: dict[str, Any] = {
        "input": judge_input,
        "output": judgment,
    }
    consistency_issues = _collect_judgment_consistency_issues(judgment, sample_profile)
    if not consistency_issues:
        return judgment, trace

    previous = {
        "status": judgment.get("status"),
        "reason": judgment.get("reason"),
        "missing_context_fields": list(judgment.get("missing_context_fields") or []),
        "evidence_candidate_keys": list(judgment.get("evidence_candidate_keys") or []),
        "validation_issues": list(judgment.get("validation_issues") or []),
        "comparison": judgment.get("comparison"),
    }
    feedback_input = {
        **judge_input,
        "validation_feedback": {
            "previous_judgment": previous,
            "issues": consistency_issues,
            "repair_instructions": [
                "For numeric/count cases, repair comparison first; status must agree with its deterministic relation.",
                "先前输出未通过程序一致性校验，请重新输出完整 JSON。",
                "reason 必须与 status 一致；禁止误判/更正/改判等自我修正话术。",
                "missing_context_fields 不得列入 sample_profile 已给出的信息"
                "（含 from_report 与 from_model_decode）。",
                "若结论为 supported 或 mismatch，必须填写有效的 evidence_candidate_keys。",
            ],
        },
    }
    judgment = _validate_judgment(
        _call_model(judge_prompt, feedback_input, model=judge_model),
        candidates,
    )
    remaining = _collect_judgment_consistency_issues(judgment, sample_profile)
    issues = list(judgment.get("validation_issues") or [])
    issues.append("rejudge triggered: " + "; ".join(consistency_issues))
    if remaining:
        issues.append("rejudge still inconsistent: " + "; ".join(remaining))
        judgment["status"] = "insufficient_context"
        judgment["reason"] = "二次判定仍未通过一致性校验，本条暂按依据不足处理。"
        judgment["evidence_candidate_keys"] = []
        judgment["evidence"] = []
        judgment["missing_context_fields"] = []
    judgment["validation_issues"] = issues
    judgment["rejudge"] = {
        "triggered": True,
        "issues": consistency_issues,
        "previous_judgment": previous,
        "remaining_issues": remaining,
    }
    trace["rejudge_input"] = feedback_input
    trace["output"] = judgment
    return judgment, trace


def _resolve_final_delivery_quotas(raw: dict[str, Any]) -> tuple[int, int]:
    """Judge delivery after rerank: prefer final_table / final_section (default 8+6)."""
    has_table = "final_table" in raw
    has_section = "final_section" in raw
    if has_table or has_section:
        return (
            int(raw.get("final_table", 8)),
            int(raw.get("final_section", 6)),
        )
    if "final_per_type" in raw:
        equal = int(raw["final_per_type"])
        return equal, equal
    return 8, 6


def _retrieval_runtime_config(profile: dict[str, Any]) -> dict[str, int | float | bool]:
    raw = profile["retrieval_config"]
    final_table, final_section = _resolve_final_delivery_quotas(raw)
    values: dict[str, int | float] = {
        "top_k": int(raw.get("top_k", 10)),
        "route_top_k": int(raw.get("route_top_k", ROUTE_TOP_K)),
        "candidate_count_per_type": int(
            raw.get("candidate_count_per_type", FINAL_PER_TYPE)
        ),
        "final_table": final_table,
        "final_section": final_section,
        # Legacy mirror: max of typed quotas (tests / older debug fields).
        "final_per_type": max(final_table, final_section),
        "special_route_reserve": int(raw.get("special_route_reserve", 3)),
        "rrf_k": int(raw.get("rrf_k", RRF_K)),
        "similarity_threshold": float(raw.get("similarity_threshold", 0.2)),
        "aggregate_continuation_tables": bool(
            raw.get("aggregate_continuation_tables", False)
        ),
        "expand_references": bool(raw.get("expand_references", False)),
    }
    bounds = {
        "top_k": (1, 50),
        "route_top_k": (1, 100),
        "candidate_count_per_type": (1, 100),
        "final_table": (1, 50),
        "final_section": (1, 50),
        "final_per_type": (1, 50),
        "special_route_reserve": (0, 20),
        "rrf_k": (1, 200),
        "similarity_threshold": (-1, 1),
    }
    for key, value in values.items():
        if isinstance(value, bool):
            continue
        lower, upper = bounds[key]
        if not lower <= value <= upper:
            raise ValueError(f"assistant retrieval setting {key} must be between {lower} and {upper}")
    return values


def _select_retrieval_candidates(
    candidates: list[dict[str, Any]],
    *,
    top_k: int,
    similarity_threshold: float,
) -> list[dict[str, Any]]:
    eligible = [
        candidate
        for candidate in candidates
        if max(candidate["route_scores"].values(), default=-1)
        >= similarity_threshold
    ]
    eligible.sort(key=lambda item: item["rrf_score"], reverse=True)
    return eligible[:top_k]


def _retrieve_hybrid_candidates(
    query: str,
    *,
    query_routes: dict[str, str] | None = None,
    file_ids: list[str],
    top_k: int,
    route_top_k: int,
    candidates_per_type: int,
    final_table: int,
    final_section: int,
    special_route_reserve: int,
    rrf_k: int,
    similarity_threshold: float,
    aggregate_continuation_tables: bool,
    expand_references: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run hybrid_search with planner routes when provided."""
    from app import retrieval

    delivery_n = final_table + final_section
    result = retrieval.hybrid_search(
        query,
        top_k=max(top_k, delivery_n),
        route_top_k=route_top_k,
        candidates_per_type=candidates_per_type,
        rrf_k=rrf_k,
        similarity_threshold=similarity_threshold,
        query_routes=query_routes,
        special_route_reserve=special_route_reserve,
        final_table=final_table,
        final_section=final_section,
        aggregate_continuation_tables=aggregate_continuation_tables,
        expand_references=expand_references,
        file_ids=file_ids,
    )
    candidates: list[dict[str, Any]] = []
    for hit in result.get("hits") or []:
        meta = hit.get("business_metadata") or {}
        content_type = str(meta.get("content_type") or "section")
        score = float(
            hit["rerank_score"]
            if hit.get("rerank_score") is not None
            else hit.get("score") or 0
        )
        candidates.append({
            **hit,
            "content_type": content_type,
            "route_scores": {"hybrid": score},
            "rrf_score": float(hit.get("rrf_score") or score),
        })
    debug = {
        "retrieval_mode": result.get("retrieval_mode"),
        "query_routes": result.get("query_routes"),
        "routes_injected": result.get("routes_injected"),
        "special_route_reserve": result.get("special_route_reserve"),
        "final_table": result.get("final_table"),
        "final_section": result.get("final_section"),
        "final_per_type": result.get("final_per_type"),
        "candidate_count": result.get("candidate_count"),
        "degraded": result.get("degraded") or [],
    }
    return candidates, debug


def _resolve_report_file_name(file_id: str | None) -> str | None:
    fid = str(file_id or "").strip()
    if not fid:
        return None
    repository = _content_repository()
    row = (
        repository.get_file(fid)
        if repository is not None
        else db.get_conn().execute(
            "SELECT name FROM files WHERE id=?",
            (fid,),
        ).fetchone()
    )
    return str(row["name"]) if row and row["name"] else None


def _resolve_assistant_name(assistant_id: str) -> str:
    repository = _content_repository()
    if repository is not None:
        return repository.get_assistant_name(assistant_id) or assistant_id
    row = db.get_conn().execute(
        "SELECT name FROM audit_assistants WHERE id=?",
        (assistant_id,),
    ).fetchone()
    return str(row["name"]) if row and row["name"] else assistant_id


def _resolve_bound_knowledge_base(assistant_id: str) -> dict[str, Any]:
    repository = _content_repository()
    bound = (
        repository.assistant_bound_knowledge_bases(assistant_id)
        if repository is not None
        else db.assistant_bound_knowledge_bases(assistant_id)
    )
    if not bound:
        return {"knowledge_base_id": None, "knowledge_base_name": None}
    primary = bound[-1]
    return {
        "knowledge_base_id": str(primary.get("id") or "") or None,
        "knowledge_base_name": str(primary.get("name") or "") or None,
    }


def _build_sample_profile(
    parameters: dict[str, Any] | None,
    decoded: dict[str, Any] | None,
    *,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge report parameters with model-decode output for downstream nodes.

    Deterministic post-step of model_decode (not an LLM call): empty
    ``from_report`` keys may be filled from decode ``schema_fills`` (LLM-mapped
    Schema keys only). Downstream planner/judge should treat ``sample_profile``
    as the single sample archive.
    """
    schema = resolve_parameter_schema(parameter_schema)
    allowed_keys = {
        str(field.get("key") or "").strip()
        for field in (schema.get("fields") or [])
        if isinstance(field, dict) and str(field.get("key") or "").strip()
    }

    from_report: dict[str, Any] = {}
    for key, value in (parameters or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        from_report[name] = value

    decoded_payload = decoded if isinstance(decoded, dict) else {}
    features: list[dict[str, str]] = []
    feature_meanings: dict[str, str] = {}
    for item in decoded_payload.get("decoded_features") or []:
        if not isinstance(item, dict):
            continue
        segment = str(item.get("segment") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        if not segment and not meaning:
            continue
        features.append({"segment": segment, "meaning": meaning})
        if segment and meaning and segment not in feature_meanings:
            feature_meanings[segment] = meaning

    schema_fills: dict[str, str] = {}
    raw_fills = decoded_payload.get("schema_fills")
    if isinstance(raw_fills, dict):
        for key, value in raw_fills.items():
            name = str(key or "").strip()
            text = str(value or "").strip()
            if not name or not text:
                continue
            if allowed_keys and name not in allowed_keys:
                continue
            schema_fills[name] = text

    filled_from_decode: list[str] = []
    for key, text in schema_fills.items():
        current = from_report.get(key)
        if isinstance(current, str) and current.strip():
            continue
        if current is not None and not isinstance(current, str) and current != "":
            continue
        from_report[key] = text
        filled_from_decode.append(key)

    # Ensure schema keys exist even when still blank after merge.
    for key in allowed_keys:
        if key not in from_report:
            from_report[key] = ""

    raw_model = str(decoded_payload.get("raw_model") or from_report.get("model") or "").strip()
    retrieval_terms = [
        str(term).strip()
        for term in (decoded_payload.get("retrieval_terms") or [])
        if str(term).strip()
    ]
    unresolved = [
        str(segment).strip()
        for segment in (decoded_payload.get("unresolved_segments") or [])
        if str(segment).strip()
    ]
    return {
        "from_report": from_report,
        "from_model_decode": {
            "raw_model": raw_model,
            "features": features,
            "feature_meanings": feature_meanings,
            "retrieval_terms": retrieval_terms,
            "unresolved_segments": unresolved,
            "schema_fills": schema_fills,
            "filled_keys": filled_from_decode,
        },
    }


def _resolve_judge_concurrency(
    cli_value: int | None,
    model_config: dict[str, Any] | None = None,
) -> int:
    """Resolve parallel case-audit workers.

    Priority: CLI ``--judge-concurrency`` > env ``AUDIT_JUDGE_CONCURRENCY`` >
    ``model_config.judge_concurrency`` > default 8. Values are clamped to
    ``[1, MAX_JUDGE_CONCURRENCY]`` (DeepSeek practical ceiling).
    """
    raw: Any
    if cli_value is not None:
        raw = cli_value
    else:
        env_raw = str(os.environ.get("AUDIT_JUDGE_CONCURRENCY") or "").strip()
        if env_raw:
            raw = env_raw
        else:
            cfg = model_config if isinstance(model_config, dict) else {}
            raw = cfg.get("judge_concurrency", DEFAULT_JUDGE_CONCURRENCY)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"judge_concurrency must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError("judge_concurrency must be >= 1 (use 1 to disable concurrency)")
    return min(value, MAX_JUDGE_CONCURRENCY)


def _sample_profile_with_recovery(
    sample_profile: dict[str, Any],
    recovered_parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add only sourced, previously missing report fields for the final Judge."""
    updated = dict(sample_profile)
    from_report = dict(
        sample_profile.get("from_report")
        if isinstance(sample_profile.get("from_report"), dict)
        else {}
    )
    provenance = []
    for item in recovered_parameters:
        if not isinstance(item, dict) or str(item.get("status") or "") != "found":
            continue
        field = str(item.get("field") or item.get("key") or "").strip()
        value = item.get("value")
        if not field or value in (None, "") or from_report.get(field) not in (None, ""):
            continue
        from_report[field] = value
        provenance.append(dict(item))
    updated["from_report"] = from_report
    if provenance:
        updated["from_recovery"] = provenance
    return updated


def _recovery_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """Keep recovery state useful without pinning full retrieved chunks."""
    compact = _compact_candidate(candidate)
    compact["text"] = str(compact.get("text") or "")[:500]
    return compact


def _audit_one_case(
    unit: dict[str, Any],
    *,
    sample_profile: dict[str, Any],
    extracted: dict[str, Any],
    peer_context_rules: list[tuple[tuple[str, ...], tuple[str, ...]]],
    manual_knowledge_rules: dict[str, Any],
    few_shot_rules: dict[str, Any],
    query_prompt: str,
    judge_prompt: str,
    judge_model: str,
    evidence_compression_mode: str,
    profile: dict[str, Any],
    evidence_file_ids: list[str],
    top_k: int,
    route_top_k: int,
    candidates_per_type: int,
    final_table: int,
    final_section: int,
    special_route_reserve: int,
    rrf_k: int,
    similarity_threshold: float,
    aggregate_continuation_tables: bool,
    expand_references: bool,
    retrieval_lock: threading.Lock,
    report_markdown: str = "",
    recovery_mode: str = "off",
    recovery_semaphore: threading.Semaphore | None = None,
) -> dict[str, Any]:
    """Run planner → retrieval → judge for one audit unit."""
    fresh = {"test_item": unit["test_item"], "requirement": unit["requirement"]}
    sample_profile = dict(sample_profile)
    deterministic_applicability = resolve_applicability(
        sample_profile,
        project_name=str(fresh["test_item"].get("project_name") or ""),
        requirement_text=str(fresh["requirement"].get("requirement_text") or ""),
    )
    sample_profile["deterministic_applicability"] = deterministic_applicability
    from_report = (
        sample_profile.get("from_report")
        if isinstance(sample_profile.get("from_report"), dict)
        else {}
    )
    # sample_context kept as report-extracted alias for production_query / UI.
    runtime_case = {
        "sample_profile": sample_profile,
        "sample_context": from_report,
        "test_item": {
            "item_no": fresh["test_item"]["item_no"],
            "project_name": fresh["test_item"]["project_name"],
            "phase": fresh["test_item"]["phase"],
        },
        "reported_requirement": {
            "text": fresh["requirement"]["requirement_text"],
            "unit": fresh["requirement"]["unit"],
        },
    }
    peer_report_context = _build_peer_report_context(
        extracted,
        fresh["test_item"],
        fresh["requirement"],
        peer_context_rules=peer_context_rules,
    )
    selected_manual_knowledge_rules = _select_manual_knowledge_rules(
        manual_knowledge_rules,
        runtime_case,
    )
    planner_input = {
        "sample_profile": sample_profile,
        "test_item": runtime_case["test_item"],
        "reported_requirement": runtime_case["reported_requirement"],
    }
    planned = _call_model(query_prompt, planner_input, model=judge_model)
    queries = _collect_enabled_planner_queries(
        planned if isinstance(planned, dict) else {},
        query_planner_routes=(profile.get("retrieval_config") or {}).get(
            "query_planner_routes"
        ),
        production_query=_production_query(runtime_case),
    )
    with retrieval_lock:
        candidates, retrieval_debug = _retrieve_hybrid_candidates(
            queries["production"],
            query_routes=queries,
            file_ids=evidence_file_ids,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=candidates_per_type,
            final_table=final_table,
            final_section=final_section,
            special_route_reserve=special_route_reserve,
            rrf_k=rrf_k,
            similarity_threshold=similarity_threshold,
            aggregate_continuation_tables=aggregate_continuation_tables,
            expand_references=expand_references,
        )
    retrieval_debug["applicability_exact"] = {
        "searched": False,
        "added": 0,
        "reason": "case-shaped fixed clause lookup removed",
    }
    annotate_candidates(candidates, deterministic_applicability)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["candidate_key"] = f"c{rank:02d}"
    deterministic_applicability["candidate_evaluations"] = (
        evaluate_candidate_applicability(candidates, deterministic_applicability)
    )
    deterministic_table_bindings = [
        {
            "candidate_key": candidate["candidate_key"],
            "standard_no": str(
                (candidate.get("business_metadata") or {}).get("standard_no") or ""
            ),
            "table_no": str(
                (candidate.get("business_metadata") or {}).get("table_no") or ""
            ),
            "table_title": str(
                (candidate.get("business_metadata") or {}).get("table_title") or ""
            ),
            "binding": candidate["table_row_binding"],
        }
        for candidate in candidates
        if isinstance(candidate.get("table_row_binding"), dict)
    ]
    deterministic_comparisons = build_deterministic_comparisons(
        runtime_case["reported_requirement"]["text"],
        runtime_case["test_item"]["project_name"],
        candidates,
    )
    raw_judge_input = {
        "sample_profile": sample_profile,
        "deterministic_applicability": deterministic_applicability,
        "deterministic_table_bindings": deterministic_table_bindings,
        "deterministic_comparisons": deterministic_comparisons,
        "test_item": runtime_case["test_item"],
        "reported_requirement": runtime_case["reported_requirement"],
        "peer_report_context": peer_report_context,
        "manual_knowledge_rules": selected_manual_knowledge_rules,
        **(
            {"few_shot_examples": few_shot_rules.get("items")}
            if few_shot_rules.get("items")
            else {}
        ),
        "candidates": [_compact_candidate(candidate) for candidate in candidates],
    }
    judge_input = raw_judge_input
    compression_trace: dict[str, Any] = {
        "mode": evidence_compression_mode,
        "applied": False,
        "fallback": False,
        "additional_model_calls": 0,
    }
    if evidence_compression_mode == "active":
        judge_input, compression_trace = compress_judge_input(
            judge_input,
            candidates,
            call_model=lambda prompt, payload: _call_model(
                prompt,
                payload,
                model=judge_model,
            ),
        )
    judgment, judge_trace = _run_audit_judge_with_consistency(
        judge_prompt=judge_prompt,
        judge_input=judge_input,
        judge_model=judge_model,
        candidates=candidates,
        sample_profile=sample_profile,
    )
    judge_trace["evidence_compression"] = compression_trace
    provisional_judgment = dict(judgment)
    recovery_decision = decide_recovery(
        judgment=provisional_judgment,
        retrieval_trace={
            "output": {
                **retrieval_debug,
                "candidate_counts": {
                    kind: sum(c["content_type"] == kind for c in candidates)
                    for kind in ("table", "section")
                },
            }
        },
        sample_profile=sample_profile,
    )
    recovery_trace: dict[str, Any] | None = None
    if (
        recovery_mode in {"shadow", "active"}
        and recovery_decision.get("action") == "agent_recovery"
    ):
        immutable_recovery_state = {
            "case_id": unit["case_id"],
            "assistant_id": profile.get("assistant_id"),
            "assistant_version_id": profile.get("id"),
            "allowed_file_ids": evidence_file_ids,
            "test_item": runtime_case["test_item"],
            "reported_requirement": runtime_case["reported_requirement"],
            "sample_profile": sample_profile,
            "initial_queries": queries,
            "initial_candidates": [_recovery_candidate_summary(item) for item in candidates],
            "provisional_judgment": provisional_judgment,
            "recovery_decision": recovery_decision,
        }
        recovery_config = {
            "top_k": top_k,
            "route_top_k": route_top_k,
            "candidate_count_per_type": candidates_per_type,
            "final_table": final_table,
            "final_section": final_section,
            "special_route_reserve": special_route_reserve,
            "rrf_k": rrf_k,
            "similarity_threshold": similarity_threshold,
            "aggregate_continuation_tables": aggregate_continuation_tables,
            "expand_references": expand_references,
        }
        recovery_environment = RecoveryToolEnvironment(
            report_markdown=report_markdown,
            allowed_file_ids=evidence_file_ids,
            requirement_text=runtime_case["reported_requirement"]["text"],
            original_query=(
                queries.get("semantic")
                or queries.get("keyword")
                or runtime_case["test_item"]["project_name"]
            ),
            model=judge_model,
            retrieval_config=recovery_config,
        )
        initial_pool = {
            "query": queries["production"],
            "hits": candidates,
            "candidate_count": len(candidates),
            "degraded": retrieval_debug.get("degraded") or [],
            "retrieval_mode": "initial_delivered_pool",
        }

        def _run_recovery() -> dict[str, Any]:
            return run_recovery_agent(
                immutable_state=immutable_recovery_state,
                environment=recovery_environment,
                initial_pool=initial_pool,
                policy=AgentPolicy(
                    max_turns=RECOVERY_MAX_TURNS,
                    max_tool_calls=RECOVERY_MAX_TOOL_CALLS,
                    max_search_calls=RECOVERY_MAX_SEARCH_CALLS,
                    timeout_seconds=RECOVERY_TIMEOUT_SECONDS,
                ),
            )

        if recovery_semaphore is None:
            recovery_trace = _run_recovery()
        else:
            with recovery_semaphore:
                recovery_trace = _run_recovery()

        merged_retrieval = recovery_trace.get("merged_retrieval")
        merged_hits = (
            list(merged_retrieval.get("hits") or [])
            if isinstance(merged_retrieval, dict)
            else []
        )
        if merged_hits:
            recovered_candidates = []
            for rank, hit in enumerate(merged_hits, start=1):
                metadata = hit.get("business_metadata") or {}
                recovered_candidates.append({
                    **hit,
                    "candidate_key": f"r{rank:02d}",
                    "content_type": str(
                        metadata.get("content_type")
                        or hit.get("content_type")
                        or "section"
                    ),
                })
            recovered_profile = _sample_profile_with_recovery(
                sample_profile,
                list(
                    (recovery_trace.get("mutable_state") or {}).get(
                        "recovered_parameters"
                    )
                    or []
                ),
            )
            recovered_applicability = resolve_applicability(
                recovered_profile,
                project_name=str(runtime_case["test_item"].get("project_name") or ""),
                requirement_text=str(runtime_case["reported_requirement"].get("text") or ""),
            )
            recovered_profile["deterministic_applicability"] = recovered_applicability
            annotate_candidates(recovered_candidates, recovered_applicability)
            recovered_applicability["candidate_evaluations"] = (
                evaluate_candidate_applicability(
                    recovered_candidates,
                    recovered_applicability,
                )
            )
            recovered_table_bindings = [
                {
                    "candidate_key": candidate["candidate_key"],
                    "standard_no": str(
                        (candidate.get("business_metadata") or {}).get("standard_no") or ""
                    ),
                    "table_no": str(
                        (candidate.get("business_metadata") or {}).get("table_no") or ""
                    ),
                    "table_title": str(
                        (candidate.get("business_metadata") or {}).get("table_title") or ""
                    ),
                    "binding": candidate["table_row_binding"],
                }
                for candidate in recovered_candidates
                if isinstance(candidate.get("table_row_binding"), dict)
            ]
            recovered_deterministic_comparisons = build_deterministic_comparisons(
                runtime_case["reported_requirement"]["text"],
                runtime_case["test_item"]["project_name"],
                recovered_candidates,
            )
            recovery_judge_input = {
                **raw_judge_input,
                "sample_profile": recovered_profile,
                "deterministic_applicability": recovered_applicability,
                "deterministic_table_bindings": recovered_table_bindings,
                "deterministic_comparisons": recovered_deterministic_comparisons,
                "candidates": [
                    _compact_candidate(candidate)
                    for candidate in recovered_candidates
                ],
                "recovery_context": {
                    "decision": recovery_decision,
                    "result": recovery_trace.get("result") or {},
                },
            }
            recovery_compression_trace: dict[str, Any] = {
                "mode": evidence_compression_mode,
                "applied": False,
                "fallback": False,
                "additional_model_calls": 0,
            }
            if evidence_compression_mode == "active":
                recovery_judge_input, recovery_compression_trace = compress_judge_input(
                    recovery_judge_input,
                    recovered_candidates,
                    call_model=lambda prompt, payload: _call_model(
                        prompt,
                        payload,
                        model=judge_model,
                    ),
                )
            recovered_judgment, recovered_judge_trace = (
                _run_audit_judge_with_consistency(
                    judge_prompt=judge_prompt,
                    judge_input=recovery_judge_input,
                    judge_model=judge_model,
                    candidates=recovered_candidates,
                    sample_profile=recovered_profile,
                )
            )
            recovered_judge_trace["evidence_compression"] = (
                recovery_compression_trace
            )
            recovery_trace["shadow_judgment"] = recovered_judgment
            recovery_trace["final_judge"] = recovered_judge_trace
            recovery_trace["recovered_sample_profile"] = recovered_profile
            if recovery_mode == "active":
                judgment = recovered_judgment
    compact_candidates = [_compact_candidate(candidate) for candidate in candidates]
    entry: dict[str, Any] = {
        "case_id": unit["case_id"],
        **runtime_case,
        "peer_report_context": peer_report_context,
        "manual_knowledge_rules": selected_manual_knowledge_rules,
        "queries": queries,
        "candidate_counts": {
            kind: sum(c["content_type"] == kind for c in candidates)
            for kind in ("table", "section")
        },
        "provisional_judgment": provisional_judgment,
        "recovery_decision": recovery_decision,
        "agent_recovery": recovery_trace,
        "judgment": judgment,
        "workflow_trace": {
            "query_planner": {
                "input": planner_input,
                "output": planned,
            },
            "retrieval": {
                "input": {
                    "query": queries["production"],
                    "queries": queries,
                    "file_ids": evidence_file_ids,
                    "retrieval_backend": "hybrid_search",
                    "top_k": top_k,
                    "route_top_k": route_top_k,
                    "candidates_per_type": candidates_per_type,
                    "final_table": final_table,
                    "final_section": final_section,
                    "special_route_reserve": special_route_reserve,
                    "rrf_k": rrf_k,
                    "similarity_threshold": similarity_threshold,
                    "aggregate_continuation_tables": aggregate_continuation_tables,
                    "expand_references": expand_references,
                },
                "output": {
                    "candidate_counts": {
                        kind: sum(c["content_type"] == kind for c in candidates)
                        for kind in ("table", "section")
                    },
                    "candidates": compact_candidates,
                    **retrieval_debug,
                },
            },
            "audit_judge": judge_trace,
        },
    }
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--naming-rule", type=Path, required=True)
    parser.add_argument("--report-id", default="HBJC")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assistant-id", default="assistant_oil_transformer_audit")
    parser.add_argument("--report-file-id")
    parser.add_argument("--naming-rule-file-id")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--judge-model")
    parser.add_argument(
        "--evidence-compression",
        choices=("off", "active"),
        default=str(
            os.environ.get("AUDIT_EVIDENCE_COMPRESSION")
            or PRODUCTION_EVIDENCE_COMPRESSION_MODE
        ).strip().lower(),
        help="validated LLM Evidence Cards before the fixed Judge",
    )
    parser.add_argument(
        "--recovery-mode",
        choices=("off", "shadow", "active"),
        default=str(
            os.environ.get("AUDIT_RECOVERY_MODE") or PRODUCTION_RECOVERY_MODE
        ).strip().lower(),
        help="conditional retrieval recovery: off, shadow comparison, or active final judgment",
    )
    parser.add_argument(
        "--judge-concurrency",
        type=int,
        default=None,
        help=(
            "max parallel case audits (query planner + judge). "
            "1 disables concurrency. Default: AUDIT_JUDGE_CONCURRENCY env, "
            f"else model_config.judge_concurrency, else {DEFAULT_JUDGE_CONCURRENCY}. "
            f"Capped at {MAX_JUDGE_CONCURRENCY}."
        ),
    )
    args = parser.parse_args()

    if _content_repository() is None:
        db.init_db()
    started_at = str(args.started_at or "").strip() or time.strftime("%Y-%m-%dT%H:%M:%S")
    profile = _load_assistant_version(args.assistant_id)
    excluded_file_ids = {
        value for value in (args.report_file_id, args.naming_rule_file_id) if value
    }
    bound_evidence_file_ids = _assistant_evidence_file_ids(
        args.assistant_id,
        excluded_file_ids=excluded_file_ids,
    )
    markdown = args.report.read_text(encoding="utf-8")
    declared_standard_nos = _extract_detection_basis_standard_nos(markdown)
    evidence_file_ids = _filter_evidence_file_ids_by_detection_basis(
        bound_evidence_file_ids,
        declared_standard_nos,
    )
    configured_model = str(profile["model_config"].get("model") or "deepseek-v4-flash")
    judge_model = args.judge_model or configured_model
    if not judge_model.lower().startswith("deepseek"):
        raise ValueError("judge model must be a DeepSeek model")
    judge_concurrency = _resolve_judge_concurrency(
        args.judge_concurrency,
        profile.get("model_config") if isinstance(profile.get("model_config"), dict) else {},
    )
    retrieval_config = _retrieval_runtime_config(profile)
    peer_context_rules = _resolve_peer_context_rules(profile["retrieval_config"])
    top_k = int(retrieval_config["top_k"])
    route_top_k = int(retrieval_config["route_top_k"])
    candidates_per_type = int(retrieval_config["candidate_count_per_type"])
    final_table = int(retrieval_config["final_table"])
    final_section = int(retrieval_config["final_section"])
    special_route_reserve = int(retrieval_config["special_route_reserve"])
    rrf_k = int(retrieval_config["rrf_k"])
    similarity_threshold = float(retrieval_config["similarity_threshold"])
    aggregate_continuation_tables = bool(
        retrieval_config["aggregate_continuation_tables"]
    )
    expand_references = bool(retrieval_config["expand_references"])
    prompt_vars = _assistant_prompt_var_context(args.assistant_id, profile)
    parameter_prompt = _prompt_content(profile, "report_parameters", var_context=prompt_vars)
    item_prompt = _prompt_content(profile, "test_items", var_context=prompt_vars)
    naming_prompt = _prompt_content(profile, "model_decode", var_context=prompt_vars)
    query_prompt = _prompt_content(profile, "query_planner", var_context=prompt_vars)
    judge_prompt = _prompt_content(profile, "audit_judge", var_context=prompt_vars)
    job_id = str(args.job_id or "").strip()
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"version": 1, "cases": []}
    )
    _report_job_progress(
        job_id,
        stage="report_parameters",
        percent=5,
        message="正在提取样品参数…",
    )
    parameters = checkpoint.get("parameters") or _extract_parameters(
        markdown,
        prompt=parameter_prompt,
        model=judge_model,
        parameter_schema=profile.get("parameter_schema"),
    )
    checkpoint["parameters"] = parameters
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    _report_job_progress(
        job_id,
        stage="test_items",
        percent=12,
        message="正在提取检测项目…",
    )
    extracted = checkpoint.get("extracted_report") or extract_report(
        args.report, prompt=item_prompt, model=judge_model
    )
    checkpoint["extracted_report"] = extracted
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    _report_job_progress(
        job_id,
        stage="model_decode",
        percent=18,
        message="正在解码型号命名…",
    )
    decoded = checkpoint.get("model_decode") or _decode_model(
        parameters,
        args.naming_rule.read_text(encoding="utf-8"),
        prompt=naming_prompt,
        model=judge_model,
        parameter_schema=profile.get("parameter_schema"),
    )
    checkpoint["model_decode"] = decoded
    # Always rebuild from current parameters + decode (deterministic merge).
    sample_profile = _build_sample_profile(
        parameters,
        decoded,
        parameter_schema=profile.get("parameter_schema"),
    )
    checkpoint["sample_profile"] = sample_profile
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    units = _build_full_audit_units(extracted)
    repository = _content_repository()
    bound_knowledge_bases = (
        repository.assistant_bound_knowledge_bases(args.assistant_id)
        if repository is not None
        else db.assistant_bound_knowledge_bases(args.assistant_id)
    )
    manual_knowledge_rules = _load_manual_knowledge_rules(
        db.merge_manual_rules(
            bound_knowledge_bases,
            fallback=profile["rules"],
        )
    )
    few_shot_rules = db.merge_few_shot_rules(bound_knowledge_bases)
    results = list(checkpoint.get("cases") or [])
    completed_ids = {item["case_id"] for item in results}
    total_units = len(units)
    unit_order = {unit["case_id"]: index for index, unit in enumerate(units)}
    pending = [
        (index, unit)
        for index, unit in enumerate(units, start=1)
        if unit["case_id"] not in completed_ids
    ]
    state_lock = threading.Lock()
    retrieval_lock = threading.Lock()
    recovery_semaphore = threading.Semaphore(2)
    _report_job_progress(
        job_id,
        stage="audit_cases",
        percent=20,
        case_done=len(completed_ids),
        case_total=total_units,
        message=(
            f"开始逐项判定（共 {total_units} 项，并发 {judge_concurrency}）…"
        ),
    )
    print(
        f"audit_cases pending={len(pending)} concurrency={judge_concurrency}",
        flush=True,
    )

    def _run_pending_case(index: int, unit: dict[str, Any]) -> None:
        project_name = str(unit["test_item"].get("project_name") or "").strip()
        req_text = str(unit["requirement"].get("requirement_text") or "").strip()
        case_label = " · ".join(part for part in (project_name, req_text) if part)
        with state_lock:
            done_before = len(results)
            percent = 20 + int(75 * done_before / total_units) if total_units else 95
            _report_job_progress(
                job_id,
                stage="audit_cases",
                percent=min(percent, 94),
                case_done=done_before,
                case_total=total_units,
                project_name=project_name,
                case_label=case_label[:120],
                message=f"判定中 {done_before + 1}/{total_units}（并发 {judge_concurrency}）",
            )
        entry = _audit_one_case(
            unit,
            sample_profile=sample_profile,
            extracted=extracted,
            peer_context_rules=peer_context_rules,
            manual_knowledge_rules=manual_knowledge_rules,
            few_shot_rules=few_shot_rules,
            query_prompt=query_prompt,
            judge_prompt=judge_prompt,
            judge_model=judge_model,
            evidence_compression_mode=args.evidence_compression,
            profile=profile,
            evidence_file_ids=evidence_file_ids,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=candidates_per_type,
            final_table=final_table,
            final_section=final_section,
            special_route_reserve=special_route_reserve,
            rrf_k=rrf_k,
            similarity_threshold=similarity_threshold,
            aggregate_continuation_tables=aggregate_continuation_tables,
            expand_references=expand_references,
            retrieval_lock=retrieval_lock,
            report_markdown=markdown,
            recovery_mode=args.recovery_mode,
            recovery_semaphore=recovery_semaphore,
        )
        with state_lock:
            results.append(entry)
            results.sort(key=lambda item: unit_order.get(item["case_id"], 10**9))
            checkpoint["cases"] = results
            checkpoint_path.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            done_after = len(results)
            percent = 20 + int(75 * done_after / total_units) if total_units else 95
            _report_job_progress(
                job_id,
                stage="audit_cases",
                percent=min(percent, 95),
                case_done=done_after,
                case_total=total_units,
                project_name=project_name,
                case_label=case_label[:120],
                message=f"已完成 {done_after}/{total_units}",
            )
            status = (entry.get("judgment") or {}).get("status")
            print(
                f"audited {index}/{total_units} {unit['case_id']}: {status}",
                flush=True,
            )

    if pending:
        with ThreadPoolExecutor(max_workers=judge_concurrency) as pool:
            futures = [
                pool.submit(_run_pending_case, index, unit)
                for index, unit in pending
            ]
            try:
                for future in as_completed(futures):
                    future.result()
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    _report_job_progress(
        job_id,
        stage="writing_report",
        percent=97,
        case_done=len(results),
        case_total=total_units,
        message="正在写入审查结果…",
    )
    recovery_rows = [
        item for item in results if isinstance(item.get("agent_recovery"), dict)
    ]
    recovery_usage = [item["agent_recovery"].get("usage") or {} for item in recovery_rows]
    failure_classes: dict[str, int] = {}
    resolution_states: dict[str, int] = {}
    for item in results:
        decision = item.get("recovery_decision") or {}
        failure_class = str(decision.get("failure_class") or "unknown")
        resolution_state = str(decision.get("resolution_state") or "unknown")
        failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
        resolution_states[resolution_state] = resolution_states.get(resolution_state, 0) + 1
    recovery_summary = {
        "mode": args.recovery_mode,
        "failure_classes": dict(sorted(failure_classes.items())),
        "resolution_states": dict(sorted(resolution_states.items())),
        "eligible_cases": sum(
            (item.get("recovery_decision") or {}).get("action") == "agent_recovery"
            for item in results
        ),
        "activated_cases": len(recovery_rows),
        "shadow_judged_cases": sum(
            isinstance(item["agent_recovery"].get("shadow_judgment"), dict)
            for item in recovery_rows
        ),
        "shadow_status_changes": sum(
            (item.get("provisional_judgment") or {}).get("status")
            != (item["agent_recovery"].get("shadow_judgment") or {}).get("status")
            for item in recovery_rows
            if isinstance(item["agent_recovery"].get("shadow_judgment"), dict)
        ),
        "turns": sum(int(usage.get("turns") or 0) for usage in recovery_usage),
        "tool_calls": sum(int(usage.get("tool_calls") or 0) for usage in recovery_usage),
        "search_calls": sum(int(usage.get("search_calls") or 0) for usage in recovery_usage),
        "elapsed_seconds": round(
            sum(float(usage.get("elapsed_seconds") or 0.0) for usage in recovery_usage),
            3,
        ),
    }
    output = {
        "version": 1,
        "scope": "full_report_audit",
        "audit_mode": "full_report",
        "database_writes": False,
        "report": str(args.report),
        "naming_rule": str(args.naming_rule),
        "report_file_id": str(args.report_file_id or "").strip() or None,
        "report_file_name": _resolve_report_file_name(args.report_file_id),
        "naming_rule_file_id": str(args.naming_rule_file_id or "").strip() or None,
        "declared_standard_nos": declared_standard_nos,
        "job_id": str(args.job_id or "").strip() or None,
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assistant_id": args.assistant_id,
        "assistant_name": _resolve_assistant_name(args.assistant_id),
        "assistant_version_id": profile["id"],
        "assistant_version": profile["version"],
        **_resolve_bound_knowledge_base(args.assistant_id),
        "manual_knowledge_rules": "knowledge_bases.manual_rules|assistant_version.rules_fallback",
        "manual_knowledge_rule_summary": {
            "version": manual_knowledge_rules.get("version"),
            "status": manual_knowledge_rules.get("status"),
            "rule_ids": [rule.get("rule_id") for rule in manual_knowledge_rules.get("rules", [])],
        },
        "few_shot_rule_summary": {
            "version": few_shot_rules.get("version"),
            "item_ids": [item.get("id") for item in few_shot_rules.get("items", [])],
        },
        "judge_provider": "deepseek",
        "judge_model": judge_model,
        "judge_concurrency": judge_concurrency,
        "workflow_definition": {
            "version": 1,
            "assistant_id": args.assistant_id,
            "assistant_version_id": profile["id"],
            "category_profile": profile["category_profile"],
            "initialization_provenance": profile["initialization_provenance"],
            "provider_config": {
                **llm.public_config(model=judge_model),
                "judge_concurrency": judge_concurrency,
            },
            "retrieval_config": {
                "backend": "hybrid_search",
                "embedding_model": embeddings.DEFAULT_MODEL,
                "embedding_dimension": embeddings.DEFAULT_DIMENSION,
                "top_k": top_k,
                "route_top_k": route_top_k,
                "candidates_per_type": candidates_per_type,
                "final_table": final_table,
                "final_section": final_section,
                "special_route_reserve": special_route_reserve,
                "rrf_k": rrf_k,
                "similarity_threshold": similarity_threshold,
                "aggregate_continuation_tables": aggregate_continuation_tables,
                "expand_references": expand_references,
                "content_types": ["table", "section"],
                "scope_source": "report_detection_basis",
                "declared_standard_nos": declared_standard_nos,
                "bound_file_count": len(bound_evidence_file_ids),
                "scoped_file_count": len(evidence_file_ids),
                "planner_routes_enabled": True,
            },
            "recovery_config": {
                "mode": args.recovery_mode,
                "provider": "deepseek",
                "model": judge_model,
                "thinking": "disabled",
                "max_turns": RECOVERY_MAX_TURNS,
                "max_tool_calls": RECOVERY_MAX_TOOL_CALLS,
                "max_search_calls": RECOVERY_MAX_SEARCH_CALLS,
                "timeout_seconds": RECOVERY_TIMEOUT_SECONDS,
                "concurrency": 2,
            },
            "evidence_compression_config": {
                "mode": args.evidence_compression,
                "provider": "deepseek",
                "model": judge_model,
                "thinking": "disabled",
                "max_cards": 5,
                "fallback": "full_candidates",
            },
            "prompts": {
                key: dict(value)
                for key, value in profile["node_prompts"].items()
                if key in {
                    "report_parameters",
                    "test_items",
                    "model_decode",
                    "query_planner",
                    "audit_judge",
                }
            },
            "global_trace": {
                "report_parameters": {
                    "input": {"report_markdown": markdown},
                    "output": parameters,
                },
                "test_items": {
                    "input": {
                        "report_id": args.report_id,
                        "report_markdown": markdown,
                    },
                    "output": extracted,
                },
                "model_decode": {
                    "input": {
                        "raw_model": parameters["model"],
                        "report_parameters": parameters,
                        "naming_rule_markdown": args.naming_rule.read_text(encoding="utf-8"),
                    },
                    "output": decoded,
                    "sample_profile": sample_profile,
                },
            },
        },
        "parameters": parameters,
        "model_decode": decoded,
        "sample_profile": sample_profile,
        "extraction_summary": {"items": len(extracted["items"]), "requirements": sum(len(item["requirements"]) for item in extracted["items"])},
        "summary": {
            "mode": "full_report",
            "cases": len(results),
            "judgments": {status: sum(item["judgment"].get("status") == status for item in results) for status in JUDGE_STATUSES},
            "recovery": recovery_summary,
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
