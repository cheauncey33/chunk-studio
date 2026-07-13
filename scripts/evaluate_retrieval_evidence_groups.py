"""Evaluate typed retrieval against OR-within/AND-across evidence groups."""
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
from build_retrieval_evidence_reviews import _merge_hits  # noqa: E402


DEFAULT_WORKFLOW = BACKEND / "data" / "reports" / "hbjc_end_to_end_audit_v1.json"
DEFAULT_GROUPS = ROOT / "evaluation" / "retrieval_evidence_groups_hbjc_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "hbjc_retrieval_group_eval_v1.json"


def selector_matches(candidate: dict[str, Any], selector: dict[str, str]) -> bool:
    if "manual_rule_id" in selector:
        return False
    metadata = candidate["business_metadata"]
    if candidate["content_type"] != selector["content_type"]:
        return False
    return all(
        str(metadata.get(field) or "") == value
        for field, value in selector.items()
        if field != "content_type"
    )


def evaluate_groups(
    candidates: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    manual_rule_ids: set[str] | None = None,
) -> dict[str, Any]:
    manual_rule_ids = manual_rule_ids or set()
    results = []
    for group in groups:
        matched = []
        matched_manual_rules = []
        for alternative in group["alternatives"]:
            if "manual_rule_id" in alternative:
                rule_id = alternative["manual_rule_id"]
                if rule_id in manual_rule_ids:
                    matched_manual_rules.append(rule_id)
                continue
            hits = [
                candidate for candidate in candidates
                if selector_matches(candidate, alternative)
            ]
            if hits:
                matched.append({
                    "alternative": alternative,
                    "candidate_keys": [candidate["candidate_key"] for candidate in hits],
                })
        results.append({
            "group_id": group["group_id"],
            "recalled": bool(matched or matched_manual_rules),
            "matched_alternatives": matched,
            "matched_manual_rules": matched_manual_rules,
        })
    return {
        "required_group_count": len(results),
        "recalled_group_count": sum(group["recalled"] for group in results),
        "all_required_groups_recalled": all(group["recalled"] for group in results),
        "groups": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    db.init_db()
    workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
    definitions = json.loads(args.groups.read_text(encoding="utf-8"))["cases"]
    results = []
    for index, case in enumerate(workflow["cases"], start=1):
        definition = definitions[case["case_id"]]
        if definition.get("evaluation_status") == "context_required":
            results.append({
                "case_id": case["case_id"],
                "evaluation_status": "context_required",
                "missing_context_fields": definition["missing_context_fields"],
            })
            continue
        candidates = _merge_hits(case["queries"], "table") + _merge_hits(case["queries"], "section")
        candidates.sort(key=lambda item: item["rrf_score"], reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        manual_rule_ids = {
            rule.get("rule_id")
            for rule in case.get("manual_knowledge_rules", {}).get("rules", [])
            if rule.get("rule_id")
        }
        evaluated = evaluate_groups(
            candidates,
            definition["required_groups"],
            manual_rule_ids=manual_rule_ids,
        )
        results.append({"case_id": case["case_id"], "evaluation_status": "evaluated", **evaluated})
        print(f"evaluated {index}/{len(workflow['cases'])} {case['case_id']}: {evaluated['recalled_group_count']}/{evaluated['required_group_count']}", flush=True)

    evaluated_cases = [case for case in results if case["evaluation_status"] == "evaluated"]
    total_groups = sum(case["required_group_count"] for case in evaluated_cases)
    recalled_groups = sum(case["recalled_group_count"] for case in evaluated_cases)
    output = {
        "version": 1,
        "retrieval_policy": "Top 20 per query and content type; RRF; final 20 table + 20 section",
        "summary": {
            "cases": len(results),
            "evaluated_cases": len(evaluated_cases),
            "context_required_cases": len(results) - len(evaluated_cases),
            "complete_case_recall": sum(case["all_required_groups_recalled"] for case in evaluated_cases),
            "complete_case_recall_rate": sum(case["all_required_groups_recalled"] for case in evaluated_cases) / len(evaluated_cases),
            "required_groups": total_groups,
            "recalled_groups": recalled_groups,
            "evidence_group_recall": recalled_groups / total_groups,
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
