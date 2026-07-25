"""Run a read-only end-to-end report standard-value audit.

Default mode audits every extracted test-item requirement in the report.
``--case-pool`` restores the legacy evaluation mode that only audits cases
from the frozen retrieval case pool and computes gold-recall diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, embeddings, llm  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from app.parameter_schema import (  # noqa: E402
    normalize_extracted_parameters,
    resolve_parameter_schema,
)
from app.prompt_vars import build_prompt_var_context, compose_runtime_prompt  # noqa: E402
from app.query_planner_routes import (  # noqa: E402
    enabled_query_planner_route_ids,
    resolve_query_planner_routes,
)
from build_retrieval_evidence_reviews import (  # noqa: E402
    FINAL_PER_TYPE,
    RRF_K,
    ROUTE_TOP_K,
    _call_model,
    _production_query,
)
from extract_report_test_items import extract_report  # noqa: E402


CASE_POOL = ROOT / "evaluation" / "retrieval_case_pool_v1.json"
GOLD = ROOT / "evaluation" / "retrieval_gold_candidates_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "hbjc_end_to_end_audit_v1.json"
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
    row = db.get_conn().execute(
        """SELECT v.*
           FROM audit_assistants a
           JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.id=? AND a.status='active'""",
        (assistant_id,),
    ).fetchone()
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
    bound = db.assistant_bound_knowledge_bases(assistant_id)
    # Bound list is priority ASC; last row is highest priority (same as merge_manual_rules).
    primary = bound[-1] if bound else {}
    manual_rules = db.resolve_assistant_manual_rules(
        assistant_id,
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


def _decode_model(
    parameters: dict[str, str],
    naming_markdown: str,
    *,
    prompt: str,
    model: str,
) -> dict[str, Any]:
    result = _call_model(prompt, {
        "raw_model": parameters["model"],
        "report_parameters": parameters,
        "naming_rule_markdown": naming_markdown,
    }, model=model)
    if result.get("raw_model") != parameters["model"]:
        raise ValueError("naming decoder changed the raw model")
    for feature in result.get("decoded_features") or []:
        quote = str(feature.get("evidence_quote") or "")
        feature["quote_verified"] = bool(quote and quote in naming_markdown)
    return result


def _load_manual_knowledge_rules(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is None:
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


def _build_full_audit_units(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    """One audit unit per extracted item x requirement; ids stable across reruns."""
    units: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for item in extracted["items"]:
        for requirement in item.get("requirements") or []:
            if not str(requirement.get("requirement_text") or "").strip():
                continue
            case_id = _full_audit_case_id(item, requirement)
            # Duplicate item/phase/requirement rows get a deterministic ordinal
            # suffix so checkpoint resume still matches them one-to-one.
            count = seen.get(case_id, 0)
            seen[case_id] = count + 1
            if count:
                case_id = f"{case_id}_{count + 1}"
            units.append({
                "case_id": case_id,
                "test_item": item,
                "requirement": requirement,
                "gold_case": None,
            })
    return units


def _build_case_pool_units(
    extracted: dict[str, Any],
    *,
    report_id: str,
) -> list[dict[str, Any]]:
    cases = [
        case for case in json.loads(CASE_POOL.read_text(encoding="utf-8"))["cases"]
        if case["report_id"] == report_id
    ]
    gold_by_id = {
        case["case_id"]: case
        for case in json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    }
    return [
        {
            "case_id": case["case_id"],
            **_find_requirement(extracted, case),
            "gold_case": gold_by_id[case["case_id"]],
        }
        for case in cases
    ]


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


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_key": candidate["candidate_key"],
        "content_type": candidate["content_type"],
        "business_metadata": candidate["business_metadata"],
        "text": candidate["text"],
    }


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


def _retrieval_runtime_config(profile: dict[str, Any]) -> dict[str, int | float | bool]:
    raw = profile["retrieval_config"]
    values: dict[str, int | float] = {
        "top_k": int(raw.get("top_k", 10)),
        "route_top_k": int(raw.get("route_top_k", ROUTE_TOP_K)),
        "candidate_count_per_type": int(
            raw.get("candidate_count_per_type", FINAL_PER_TYPE)
        ),
        "final_per_type": int(raw.get("final_per_type", 15)),
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
    final_per_type: int,
    special_route_reserve: int,
    rrf_k: int,
    similarity_threshold: float,
    aggregate_continuation_tables: bool,
    expand_references: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run hybrid_search with planner routes when provided."""
    from app import retrieval

    result = retrieval.hybrid_search(
        query,
        top_k=max(top_k, final_per_type * 2),
        route_top_k=route_top_k,
        candidates_per_type=candidates_per_type,
        rrf_k=rrf_k,
        similarity_threshold=similarity_threshold,
        query_routes=query_routes,
        special_route_reserve=special_route_reserve,
        final_per_type=final_per_type,
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
        "final_per_type": result.get("final_per_type"),
        "candidate_count": result.get("candidate_count"),
        "degraded": result.get("degraded") or [],
    }
    return candidates, debug


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--naming-rule", type=Path, required=True)
    parser.add_argument("--report-id", default="HBJC")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assistant-id", default="assistant_oil_transformer_audit")
    parser.add_argument("--report-file-id")
    parser.add_argument("--naming-rule-file-id")
    parser.add_argument("--judge-model")
    parser.add_argument(
        "--case-pool",
        action="store_true",
        help="legacy evaluation mode: audit only frozen case-pool cases and compute gold recall",
    )
    args = parser.parse_args()

    db.init_db()
    profile = _load_assistant_version(args.assistant_id)
    excluded_file_ids = {
        value for value in (args.report_file_id, args.naming_rule_file_id) if value
    }
    evidence_file_ids = _assistant_evidence_file_ids(
        args.assistant_id,
        excluded_file_ids=excluded_file_ids,
    )
    configured_model = str(profile["model_config"].get("model") or "deepseek-v4-flash")
    judge_model = args.judge_model or configured_model
    if not judge_model.lower().startswith("deepseek"):
        raise ValueError("judge model must be a DeepSeek model")
    retrieval_config = _retrieval_runtime_config(profile)
    peer_context_rules = _resolve_peer_context_rules(profile["retrieval_config"])
    top_k = int(retrieval_config["top_k"])
    route_top_k = int(retrieval_config["route_top_k"])
    candidates_per_type = int(retrieval_config["candidate_count_per_type"])
    final_per_type = int(retrieval_config["final_per_type"])
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
    markdown = args.report.read_text(encoding="utf-8")
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"version": 1, "cases": []}
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
    extracted = checkpoint.get("extracted_report") or extract_report(
        args.report, prompt=item_prompt, model=judge_model
    )
    checkpoint["extracted_report"] = extracted
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    decoded = checkpoint.get("model_decode") or _decode_model(
        parameters,
        args.naming_rule.read_text(encoding="utf-8"),
        prompt=naming_prompt,
        model=judge_model,
    )
    checkpoint["model_decode"] = decoded
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.case_pool:
        units = _build_case_pool_units(extracted, report_id=args.report_id)
    else:
        units = _build_full_audit_units(extracted)
    manual_knowledge_rules = _load_manual_knowledge_rules(
        db.resolve_assistant_manual_rules(
            args.assistant_id,
            fallback=profile["rules"],
        )
    )
    few_shot_rules = db.resolve_assistant_few_shot_rules(args.assistant_id)
    results = list(checkpoint.get("cases") or [])
    completed_ids = {item["case_id"] for item in results}
    for index, unit in enumerate(units, start=1):
        if unit["case_id"] in completed_ids:
            continue
        fresh = {"test_item": unit["test_item"], "requirement": unit["requirement"]}
        gold_case = unit.get("gold_case")
        runtime_case = {
            "sample_context": parameters,
            "test_item": {"item_no": fresh["test_item"]["item_no"], "project_name": fresh["test_item"]["project_name"], "phase": fresh["test_item"]["phase"]},
            "reported_requirement": {"text": fresh["requirement"]["requirement_text"], "unit": fresh["requirement"]["unit"]},
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
        planner_input = {**runtime_case, "decoded_model": decoded}
        planned = _call_model(query_prompt, planner_input, model=judge_model)
        queries = _collect_enabled_planner_queries(
            planned if isinstance(planned, dict) else {},
            query_planner_routes=(profile.get("retrieval_config") or {}).get(
                "query_planner_routes"
            ),
            production_query=_production_query(runtime_case),
        )
        # Pass full planner routes into hybrid_search (no second rewrite).
        candidates, retrieval_debug = _retrieve_hybrid_candidates(
            queries["production"],
            query_routes=queries,
            file_ids=evidence_file_ids,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=candidates_per_type,
            final_per_type=final_per_type,
            special_route_reserve=special_route_reserve,
            rrf_k=rrf_k,
            similarity_threshold=similarity_threshold,
            aggregate_continuation_tables=aggregate_continuation_tables,
            expand_references=expand_references,
        )
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        judge_input = {
            **runtime_case,
            "decoded_model": decoded,
            "peer_report_context": peer_report_context,
            "manual_knowledge_rules": selected_manual_knowledge_rules,
            # Human-curated examples align output style/caliber only; the judge
            # prompt forbids using them as evidence.
            **(
                {"few_shot_examples": few_shot_rules.get("items")}
                if few_shot_rules.get("items")
                else {}
            ),
            "candidates": [_compact_candidate(candidate) for candidate in candidates],
        }
        judgment = _validate_judgment(
            _call_model(judge_prompt, judge_input, model=judge_model),
            candidates,
        )
        gold_hashes = {
            evidence["locator"]["text_sha256"]
            for evidence in gold_case["selected_evidence"]
            if evidence["label"] == "direct_candidate"
        } if gold_case is not None else set()
        hit_hashes = {chunk_text_sha256(candidate["text"]) for candidate in candidates}
        compact_candidates = [_compact_candidate(candidate) for candidate in candidates]
        entry: dict[str, Any] = {
            "case_id": unit["case_id"],
            **runtime_case,
            "peer_report_context": peer_report_context,
            "manual_knowledge_rules": selected_manual_knowledge_rules,
            "queries": queries,
            "candidate_counts": {kind: sum(c["content_type"] == kind for c in candidates) for kind in ("table", "section")},
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
                        "final_per_type": final_per_type,
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
                "audit_judge": {
                    "input": judge_input,
                    "output": judgment,
                },
            },
        }
        if gold_case is not None:
            entry["direct_gold_available"] = bool(gold_hashes)
            entry["direct_gold_recalled"] = bool(gold_hashes & hit_hashes)
            entry["workflow_trace"]["gold_comparison"] = {
                "input": {
                    "direct_gold_text_sha256": sorted(gold_hashes),
                    "retrieved_text_sha256": sorted(hit_hashes),
                },
                "output": {
                    "direct_gold_available": bool(gold_hashes),
                    "direct_gold_recalled": bool(gold_hashes & hit_hashes),
                },
            }
        results.append(entry)
        checkpoint["cases"] = results
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"audited {index}/{len(units)} {unit['case_id']}: {judgment.get('status')}", flush=True)

    output = {
        "version": 1,
        "scope": "read_only_case_pool_trial" if args.case_pool else "full_report_audit",
        "audit_mode": "case_pool" if args.case_pool else "full_report",
        "database_writes": False,
        "report": str(args.report),
        "naming_rule": str(args.naming_rule),
        "assistant_id": args.assistant_id,
        "assistant_version_id": profile["id"],
        "assistant_version": profile["version"],
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
        "workflow_definition": {
            "version": 1,
            "assistant_id": args.assistant_id,
            "assistant_version_id": profile["id"],
            "category_profile": profile["category_profile"],
            "initialization_provenance": profile["initialization_provenance"],
            "provider_config": llm.public_config(model=judge_model),
            "retrieval_config": {
                "backend": "hybrid_search",
                "embedding_model": embeddings.DEFAULT_MODEL,
                "embedding_dimension": embeddings.DEFAULT_DIMENSION,
                "top_k": top_k,
                "route_top_k": route_top_k,
                "candidates_per_type": candidates_per_type,
                "final_per_type": final_per_type,
                "special_route_reserve": special_route_reserve,
                "rrf_k": rrf_k,
                "similarity_threshold": similarity_threshold,
                "aggregate_continuation_tables": aggregate_continuation_tables,
                "expand_references": expand_references,
                "content_types": ["table", "section"],
                "scoped_file_count": len(evidence_file_ids),
                "planner_routes_enabled": True,
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
                },
            },
        },
        "parameters": parameters,
        "model_decode": decoded,
        "extraction_summary": {"items": len(extracted["items"]), "requirements": sum(len(item["requirements"]) for item in extracted["items"])},
        "summary": {
            "mode": "case_pool" if args.case_pool else "full_report",
            "cases": len(results),
            **(
                {
                    "direct_gold_cases": sum(bool(item.get("direct_gold_available")) for item in results),
                    "direct_gold_recalled": sum(bool(item.get("direct_gold_recalled")) for item in results),
                }
                if args.case_pool
                else {}
            ),
            "judgments": {status: sum(item["judgment"].get("status") == status for item in results) for status in JUDGE_STATUSES},
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
