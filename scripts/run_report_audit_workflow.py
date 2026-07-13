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

from app import db  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from build_retrieval_evidence_reviews import (  # noqa: E402
    _call_model,
    _merge_hits,
    _production_query,
)
from extract_report_test_items import extract_report  # noqa: E402


PARAMETER_PROMPT = ROOT / "evaluation" / "prompts" / "report_parameter_extraction_v1.md"
ITEM_PROMPT = ROOT / "evaluation" / "prompts" / "report_test_item_extraction_v1.md"
NAMING_PROMPT = ROOT / "evaluation" / "prompts" / "model_naming_decode_v1.md"
QUERY_PROMPT = ROOT / "evaluation" / "prompts" / "retrieval_query_planner_v1.md"
JUDGE_PROMPT = ROOT / "evaluation" / "prompts" / "standard_value_audit_judge_v1.md"
CASE_POOL = ROOT / "evaluation" / "retrieval_case_pool_v1.json"
GOLD = ROOT / "evaluation" / "retrieval_gold_candidates_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "hbjc_end_to_end_audit_v1.json"
PARAMETER_FIELDS = {
    "model", "rated_capacity", "rated_voltage", "phase_count",
    "connection_group", "cooling_method", "insulation_level",
}


def _extract_parameters(markdown: str) -> dict[str, str]:
    result = _call_model(PARAMETER_PROMPT.read_text(encoding="utf-8"), {"report_markdown": markdown})
    if set(result) != PARAMETER_FIELDS:
        raise ValueError(f"parameter extraction fields mismatch: {sorted(result)}")
    return {key: str(result[key] or "").strip() for key in sorted(PARAMETER_FIELDS)}


def _decode_model(parameters: dict[str, str], naming_markdown: str) -> dict[str, Any]:
    result = _call_model(NAMING_PROMPT.read_text(encoding="utf-8"), {
        "raw_model": parameters["model"],
        "report_parameters": parameters,
        "naming_rule_markdown": naming_markdown,
    })
    if result.get("raw_model") != parameters["model"]:
        raise ValueError("naming decoder changed the raw model")
    for feature in result.get("decoded_features") or []:
        quote = str(feature.get("evidence_quote") or "")
        feature["quote_verified"] = bool(quote and quote in naming_markdown)
    return result


def _find_requirement(extracted: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    for item in extracted["items"]:
        if item["item_no"] == case["test_item"]["item_no"] and item["phase"] == case["test_item"]["phase"]:
            expected = case["reported_requirement"]["text"].replace(" ", "")
            for requirement in item["requirements"]:
                if requirement["requirement_text"].replace(" ", "") == expected:
                    return {"test_item": item, "requirement": requirement}
    raise ValueError(f"fresh extraction did not reproduce {case['case_id']}")


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--naming-rule", type=Path, required=True)
    parser.add_argument("--report-id", default="HBJC")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    db.init_db()
    markdown = args.report.read_text(encoding="utf-8")
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"version": 1, "cases": []}
    )
    parameters = checkpoint.get("parameters") or _extract_parameters(markdown)
    checkpoint["parameters"] = parameters
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    extracted = checkpoint.get("extracted_report") or extract_report(
        args.report, prompt=ITEM_PROMPT.read_text(encoding="utf-8"), model="qwen3.6-27b"
    )
    checkpoint["extracted_report"] = extracted
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    decoded = checkpoint.get("model_decode") or _decode_model(
        parameters, args.naming_rule.read_text(encoding="utf-8")
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
    query_prompt = QUERY_PROMPT.read_text(encoding="utf-8")
    judge_prompt = JUDGE_PROMPT.read_text(encoding="utf-8")
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
        planned = _call_model(query_prompt, {**runtime_case, "decoded_model": decoded})
        queries = {"production": _production_query(runtime_case)}
        for route in ("semantic", "keyword", "table_target", "section_target"):
            value = str(planned.get(route) or "").strip()
            if value:
                queries[route] = value
        candidates = _merge_hits(queries, "table") + _merge_hits(queries, "section")
        candidates.sort(key=lambda item: item["rrf_score"], reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        judgment = _call_model(judge_prompt, {
            **runtime_case,
            "decoded_model": decoded,
            "candidates": [_compact_candidate(candidate) for candidate in candidates],
        })
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
        results.append({
            "case_id": case["case_id"],
            **runtime_case,
            "queries": queries,
            "candidate_counts": {kind: sum(c["content_type"] == kind for c in candidates) for kind in ("table", "section")},
            "direct_gold_available": bool(gold_hashes),
            "direct_gold_recalled": bool(gold_hashes & hit_hashes),
            "judgment": judgment,
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
