"""Adversarially downgrade or reject first-pass retrieval evidence labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_retrieval_evidence_reviews import (
    DEFAULT_CANDIDATES,
    DEFAULT_CASES,
    DEFAULT_LABELS,
    DEFAULT_OUTPUT,
    MODEL,
    _call_model,
    _read_json,
    _write_json,
    build_review,
    db,
    validate_locators,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "evaluation" / "prompts" / "retrieval_evidence_adversarial_audit_v1.md"
DEFAULT_AUDIT = ROOT / "backend" / "data" / "reports" / "retrieval_label_audit_40_v1.json"
ACTIONS = {"keep", "downgrade_supporting", "downgrade_uncertain", "reject"}
MAIN_REVIEW_OVERRIDES = [
    ("hbjc-15-5-r1", "JB/T 501-2021", "table_no", "7", "downgrade_supporting",
     "该表只规定负载损耗允许偏差，不能单独证明报告中的绝对限值3.615 kW。"),
    ("whc-13-r2", "Q/GDW 12126.4-2024", "table_no", "19", "reject",
     "表19针对叠铁心产品，样品型号RL表示立体卷铁芯，产品结构不匹配。"),
    ("whc-14-1-r4", "GB/T 1094.5-2008", "section", "4.2", "downgrade_uncertain",
     "2%限值依赖圆形同心式绕组，报告上下文未提供绕组结构证据。"),
    ("xyc-5-r3", "GB/T 25446-2010", "table_no", "2", "reject",
     "该标准针对非晶合金铁心配电变压器，与S20-M.RL电工钢立体卷铁芯产品不匹配。"),
]


def _normalize_missing(value: str) -> str:
    text = str(value or "").strip()
    return "" if text in {"无", "无。", "没有", "没有。", "不缺少", "不缺少。"} else text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    candidates = _read_json(args.candidates, {})
    labels = _read_json(args.labels, {})
    prompt = args.prompt.read_text(encoding="utf-8")
    audit = _read_json(args.audit, {"version": 1, "model": MODEL, "cases": {}})
    db.init_db()

    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in audit["cases"]:
            continue
        by_key = {item["candidate_key"]: item for item in candidates["cases"][case_id]["candidates"]}
        selected = []
        for item in labels["cases"][case_id]["selected"]:
            candidate = by_key[item["candidate_key"]]
            metadata = candidate["business_metadata"]
            selected.append({
                **item,
                "content_type": candidate["content_type"],
                "standard_no": metadata.get("standard_no"),
                "section": metadata.get("section"),
                "section_title": metadata.get("section_title"),
                "table_no": metadata.get("table_no"),
                "table_title": metadata.get("table_title"),
                "text": candidate["text"][:12000],
            })
        response = _call_model(prompt, {
            "case": {
                "sample_context": case.get("sample_context") or {},
                "test_item": case["test_item"],
                "reported_requirement": case["reported_requirement"],
            },
            "selected_candidates": selected,
        })
        decisions = {}
        valid_keys = {item["candidate_key"] for item in selected}
        for decision in response.get("decisions") or []:
            key = str(decision.get("candidate_key") or "")
            action = str(decision.get("action") or "")
            if key in valid_keys and action in ACTIONS:
                decisions[key] = {"action": action, "reason": str(decision.get("reason") or "").strip()}
        if decisions.keys() != valid_keys:
            missing = sorted(valid_keys - decisions.keys())
            raise ValueError(f"{case_id}: audit omitted decisions for {missing}")
        audit["cases"][case_id] = {
            "decisions": decisions,
            "review_note": str(response.get("review_note") or "").strip(),
        }
        _write_json(args.audit, audit)
        print(f"audited {index}/{len(cases)} {case_id}", flush=True)

    final_labels = {"version": 2, "model": MODEL, "cases": {}}
    applied_overrides = []
    for case_id, standard_no, field, value, action, reason in MAIN_REVIEW_OVERRIDES:
        by_key = {
            item["candidate_key"]: item
            for item in candidates["cases"][case_id]["candidates"]
        }
        matching_keys = [
            item["candidate_key"]
            for item in labels["cases"][case_id]["selected"]
            if by_key[item["candidate_key"]]["business_metadata"].get("standard_no") == standard_no
            and by_key[item["candidate_key"]]["business_metadata"].get(field) == value
        ]
        if len(matching_keys) != 1:
            raise ValueError(f"main review override for {case_id} resolved to {len(matching_keys)} candidates")
        key = matching_keys[0]
        audit["cases"][case_id]["decisions"][key] = {"action": action, "reason": reason}
        applied_overrides.append({
            "case_id": case_id,
            "standard_no": standard_no,
            field: value,
            "action": action,
            "reason": reason,
        })
    for case in cases:
        case_id = case["case_id"]
        decisions = audit["cases"][case_id]["decisions"]
        kept = []
        for item in labels["cases"][case_id]["selected"]:
            decision = decisions[item["candidate_key"]]
            action = decision["action"]
            if action == "reject":
                continue
            final_item = dict(item)
            if action == "downgrade_supporting":
                final_item["label"] = "supporting_candidate"
            elif action == "downgrade_uncertain":
                final_item["label"] = "uncertain"
            final_item["reason"] = decision["reason"]
            kept.append(final_item)
        final_labels["cases"][case_id] = {
            "selected": kept,
            "missing_evidence": _normalize_missing(labels["cases"][case_id]["missing_evidence"]),
            "review_note": audit["cases"][case_id]["review_note"],
        }

    review = build_review(cases, candidates, final_labels)
    review["status"] = "candidate_two_pass_model_reviewed_pending_domain_review"
    review["adversarial_audit_model"] = MODEL
    review["main_review_overrides"] = applied_overrides
    review["validation"] = validate_locators(review)
    args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "cases": len(cases),
        "selected_evidence": sum(len(case["selected_evidence"]) for case in review["cases"]),
        "direct": sum(
            item["label"] == "direct_candidate"
            for case in review["cases"] for item in case["selected_evidence"]
        ),
        "supporting": sum(
            item["label"] == "supporting_candidate"
            for case in review["cases"] for item in case["selected_evidence"]
        ),
        "uncertain": sum(
            item["label"] == "uncertain"
            for case in review["cases"] for item in case["selected_evidence"]
        ),
        "cases_with_direct": sum(
            any(item["label"] == "direct_candidate" for item in case["selected_evidence"])
            for case in review["cases"]
        ),
        "checked_locators": review["validation"]["checked_locators"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
