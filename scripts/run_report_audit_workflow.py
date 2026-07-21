"""Run a read-only end-to-end report standard-value audit trial."""
from __future__ import annotations

import argparse
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
PARAMETER_FIELDS = {
    "model", "rated_capacity", "rated_voltage", "phase_count",
    "connection_group", "cooling_method", "insulation_level",
}
PEER_CONTEXT_RULES = [
    (
        ("总损耗", "P总"),
        ("空载损耗", "负载损耗", "总损耗", "P0", "Pk", "P总"),
    ),
    (
        ("频率", "Hz"),
        ("频率", "Hz", "持续时间", "试验时间", "感应耐压"),
    ),
]


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
    for key in ("model_config", "node_prompts", "rules", "retrieval_config"):
        payload[key] = json.loads(payload[key] or "{}")
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


def _prompt_content(profile: dict[str, Any], key: str) -> str:
    prompt = profile["node_prompts"].get(key) or {}
    content = str(prompt.get("content") or "")
    if not content:
        raise ValueError(f"assistant version omitted prompt: {key}")
    return content


def _extract_parameters(markdown: str, *, prompt: str, model: str) -> dict[str, str]:
    result = _call_model(
        prompt,
        {"report_markdown": markdown},
        model=model,
    )
    if set(result) != PARAMETER_FIELDS:
        raise ValueError(f"parameter extraction fields mismatch: {sorted(result)}")
    return {key: str(result[key] or "").strip() for key in sorted(PARAMETER_FIELDS)}


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
    runtime_case: dict[str, Any],
) -> dict[str, Any]:
    requirement_text = str(runtime_case["reported_requirement"].get("text") or "")
    project_name = str(runtime_case["test_item"].get("project_name") or "")
    source = f"{project_name} {requirement_text}"
    selected = []
    for rule in rules_payload.get("rules", []):
        rule_id = str(rule.get("rule_id") or "")
        if rule_id == "transformer_total_loss_sum_v1" and (
            "总损耗" in source or "P总" in source
        ):
            selected.append(rule)
    return {**rules_payload, "rules": selected}


def _find_requirement(extracted: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    for item in extracted["items"]:
        if item["item_no"] == case["test_item"]["item_no"] and item["phase"] == case["test_item"]["phase"]:
            expected = case["reported_requirement"]["text"].replace(" ", "")
            for requirement in item["requirements"]:
                if requirement["requirement_text"].replace(" ", "") == expected:
                    return {"test_item": item, "requirement": requirement}
    raise ValueError(f"fresh extraction did not reproduce {case['case_id']}")


def _peer_context_terms(requirement_text: str, project_name: str) -> tuple[str, ...]:
    source = f"{project_name} {requirement_text}"
    terms: list[str] = []
    for triggers, related in PEER_CONTEXT_RULES:
        if any(trigger in source for trigger in triggers):
            terms.extend(related)
    return tuple(dict.fromkeys(terms))


def _build_peer_report_context(
    extracted: dict[str, Any],
    current_item: dict[str, Any],
    current_requirement: dict[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    terms = _peer_context_terms(
        str(current_requirement.get("requirement_text") or ""),
        str(current_item.get("project_name") or ""),
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


def _retrieval_runtime_config(profile: dict[str, Any]) -> dict[str, int | float]:
    raw = profile["retrieval_config"]
    values: dict[str, int | float] = {
        "top_k": int(raw.get("top_k", 10)),
        "route_top_k": int(raw.get("route_top_k", ROUTE_TOP_K)),
        "candidate_count_per_type": int(
            raw.get("candidate_count_per_type", FINAL_PER_TYPE)
        ),
        "rrf_k": int(raw.get("rrf_k", RRF_K)),
        "similarity_threshold": float(raw.get("similarity_threshold", 0.2)),
    }
    bounds = {
        "top_k": (1, 50),
        "route_top_k": (1, 100),
        "candidate_count_per_type": (1, 100),
        "rrf_k": (1, 200),
        "similarity_threshold": (-1, 1),
    }
    for key, value in values.items():
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
    file_ids: list[str],
    top_k: int,
    route_top_k: int,
    candidates_per_type: int,
    rrf_k: int,
    similarity_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run production hybrid_search and map hits into workflow candidate shape."""
    from app import retrieval

    result = retrieval.hybrid_search(
        query,
        top_k=top_k,
        route_top_k=route_top_k,
        candidates_per_type=candidates_per_type,
        rrf_k=rrf_k,
        similarity_threshold=similarity_threshold,
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
    top_k = int(retrieval_config["top_k"])
    route_top_k = int(retrieval_config["route_top_k"])
    final_per_type = int(retrieval_config["candidate_count_per_type"])
    rrf_k = int(retrieval_config["rrf_k"])
    similarity_threshold = float(retrieval_config["similarity_threshold"])
    parameter_prompt = _prompt_content(profile, "report_parameters")
    item_prompt = _prompt_content(profile, "test_items")
    naming_prompt = _prompt_content(profile, "model_decode")
    query_prompt = _prompt_content(profile, "query_planner")
    judge_prompt = _prompt_content(profile, "audit_judge")
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

    cases = [
        case for case in json.loads(CASE_POOL.read_text(encoding="utf-8"))["cases"]
        if case["report_id"] == args.report_id
    ]
    gold_by_id = {
        case["case_id"]: case
        for case in json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    }
    manual_knowledge_rules = _load_manual_knowledge_rules(
        db.resolve_assistant_manual_rules(
            args.assistant_id,
            fallback=profile["rules"],
        )
    )
    few_shot_rules = db.resolve_assistant_few_shot_rules(args.assistant_id)
    results = list(checkpoint.get("cases") or [])
    completed_ids = {item["case_id"] for item in results}
    for index, case in enumerate(cases, start=1):
        if case["case_id"] in completed_ids:
            continue
        fresh = _find_requirement(extracted, case)
        runtime_case = {
            "sample_context": parameters,
            "test_item": {"item_no": fresh["test_item"]["item_no"], "project_name": fresh["test_item"]["project_name"], "phase": fresh["test_item"]["phase"]},
            "reported_requirement": {"text": fresh["requirement"]["requirement_text"], "unit": fresh["requirement"]["unit"]},
        }
        peer_report_context = _build_peer_report_context(
            extracted,
            fresh["test_item"],
            fresh["requirement"],
        )
        selected_manual_knowledge_rules = _select_manual_knowledge_rules(
            manual_knowledge_rules,
            runtime_case,
        )
        planner_input = {**runtime_case, "decoded_model": decoded}
        planned = _call_model(query_prompt, planner_input, model=judge_model)
        queries = {"production": _production_query(runtime_case)}
        for route in ("semantic", "keyword", "table_target", "section_target"):
            value = str(planned.get(route) or "").strip()
            if value:
                queries[route] = value
        # Hybrid search does its own constrained rewrite from the production query,
        # matching knowledge-base 试检索 / production retrieval.
        candidates, retrieval_debug = _retrieve_hybrid_candidates(
            queries["production"],
            file_ids=evidence_file_ids,
            top_k=top_k,
            route_top_k=route_top_k,
            candidates_per_type=final_per_type,
            rrf_k=rrf_k,
            similarity_threshold=similarity_threshold,
        )
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        judge_input = {
            **runtime_case,
            "decoded_model": decoded,
            "peer_report_context": peer_report_context,
            "manual_knowledge_rules": selected_manual_knowledge_rules,
            "candidates": [_compact_candidate(candidate) for candidate in candidates],
        }
        judgment = _call_model(judge_prompt, judge_input, model=judge_model)
        selected = set(judgment.get("evidence_candidate_keys") or [])
        judgment["evidence"] = [
            _compact_candidate(candidate) for candidate in candidates
            if candidate["candidate_key"] in selected
        ]
        gold_hashes = {
            evidence["locator"]["text_sha256"]
            for evidence in gold_by_id[case["case_id"]]["selected_evidence"]
            if evidence["label"] == "direct_candidate"
        }
        hit_hashes = {chunk_text_sha256(candidate["text"]) for candidate in candidates}
        compact_candidates = [_compact_candidate(candidate) for candidate in candidates]
        results.append({
            "case_id": case["case_id"],
            **runtime_case,
            "peer_report_context": peer_report_context,
            "manual_knowledge_rules": selected_manual_knowledge_rules,
            "queries": queries,
            "candidate_counts": {kind: sum(c["content_type"] == kind for c in candidates) for kind in ("table", "section")},
            "direct_gold_available": bool(gold_hashes),
            "direct_gold_recalled": bool(gold_hashes & hit_hashes),
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
                        "candidates_per_type": final_per_type,
                        "rrf_k": rrf_k,
                        "similarity_threshold": similarity_threshold,
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
                "gold_comparison": {
                    "input": {
                        "direct_gold_text_sha256": sorted(gold_hashes),
                        "retrieved_text_sha256": sorted(hit_hashes),
                    },
                    "output": {
                        "direct_gold_available": bool(gold_hashes),
                        "direct_gold_recalled": bool(gold_hashes & hit_hashes),
                    },
                },
            },
        })
        checkpoint["cases"] = results
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"audited {index}/{len(cases)} {case['case_id']}: {judgment.get('status')}", flush=True)

    output = {
        "version": 1,
        "scope": "read_only_hbjc_end_to_end_trial",
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
            "provider_config": llm.public_config(model=judge_model),
            "retrieval_config": {
                "backend": "hybrid_search",
                "embedding_model": embeddings.DEFAULT_MODEL,
                "embedding_dimension": embeddings.DEFAULT_DIMENSION,
                "top_k": top_k,
                "route_top_k": route_top_k,
                "candidates_per_type": final_per_type,
                "rrf_k": rrf_k,
                "similarity_threshold": similarity_threshold,
                "content_types": ["table", "section"],
                "scoped_file_count": len(evidence_file_ids),
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
            "cases": len(results),
            "direct_gold_cases": sum(item["direct_gold_available"] for item in results),
            "direct_gold_recalled": sum(item["direct_gold_recalled"] for item in results),
            "judgments": {status: sum(item["judgment"].get("status") == status for item in results) for status in ("correct", "incorrect", "insufficient_context", "evidence_not_found")},
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
