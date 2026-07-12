"""Recover direct evidence for cases missed by the frozen retrieval queries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_retrieval_evidence_reviews import (
    DEFAULT_CASES,
    DEFAULT_CANDIDATES,
    DEFAULT_JUDGE_PROMPT,
    DEFAULT_QUERIES,
    MODEL,
    RRF_K,
    _call_model,
    _locator,
    _read_json,
    _write_json,
    db,
    embeddings,
    resolve_evidence_locator,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_REVIEW = ROOT / "evaluation" / "retrieval_evidence_candidates_v1.json"
RECOVERY_PROMPT = ROOT / "evaluation" / "prompts" / "gold_evidence_recovery_query_v1.md"
AUDIT_PROMPT = ROOT / "evaluation" / "prompts" / "retrieval_evidence_adversarial_audit_v1.md"
REPORT_DIR = ROOT / "backend" / "data" / "reports"
RECOVERY_QUERIES = REPORT_DIR / "gold_recovery_queries_v1.json"
RECOVERY_CANDIDATES = REPORT_DIR / "gold_recovery_candidates_v1.json"
RECOVERY_REVIEWS = REPORT_DIR / "gold_recovery_reviews_v1.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "retrieval_gold_candidates_v1.json"
CONTENT_TYPES = ("table", "section")
TOP_K = 20
FINAL_PER_TYPE = 20
LABELS = {"direct_candidate", "supporting_candidate", "uncertain"}
ACTIONS = {"keep", "downgrade_supporting", "downgrade_uncertain", "reject"}
LABEL_PRIORITY = {
    "uncertain": 0,
    "supporting_candidate": 1,
    "direct_candidate": 2,
}

# Recovery is deliberately broad. These overrides prevent a semantically close
# chunk from becoming direct gold when an applicability condition is still
# missing from the report context.
RECOVERY_MAIN_OVERRIDES = {
    ("whc-5-r4", "GB 20052-2024", "table", "1"): (
        "supporting_candidate",
        "表1给出空载损耗和负载损耗分项限值，但不能单独直接证明报告中的总损耗2.400 kW。",
    ),
    ("whc-14-1-r4", "GB/T 1094.5-2008", "section", "4.2"): (
        "uncertain",
        "2%限值依赖圆形同心式绕组；当前报告级上下文未确认该绕组结构。",
    ),
}

# These locators were found by exact corpus inspection after the broad recovery
# missed them. They are resolved by stable business metadata, never by chunk ID.
MANUAL_DIRECT_EVIDENCE = {
    "hbjc-5-r1": [("Q/GDW 12126.4-2024", "table", "table_no", "6")],
    "hbjc-5-r3": [
        ("Q/GDW 12126.4-2024", "table", "table_no", "6"),
        ("GB/T 1094.2-2013", "section", "section", "7.3"),
    ],
    "hbjc-15-5-r1": [("Q/GDW 12126.4-2024", "table", "table_no", "6")],
    "ezc-5-r4": [
        ("Q/GDW 12126.4-2024", "table", "table_no", "6"),
        ("GB/T 1094.2-2013", "section", "section", "7.3"),
    ],
    "whc-5-r4": [
        ("Q/GDW 12126.4-2024", "table", "table_no", "6"),
        ("GB/T 1094.2-2013", "section", "section", "7.3"),
    ],
    "xyc-4-r1": [("Q/GDW 12126.4-2024", "table", "table_no", "6")],
    "xyc-5-r4": [
        ("Q/GDW 12126.4-2024", "table", "table_no", "6"),
        ("GB/T 1094.2-2013", "section", "section", "7.3"),
    ],
}


def _resolve_manual_evidence(spec: tuple[str, str, str, str]) -> dict[str, Any]:
    standard_no, content_type, identity_field, identity_value = spec
    rows = db.get_conn().execute(
        """
        SELECT page, text, business_metadata, source_trace
        FROM chunks
        WHERE json_extract(business_metadata, '$.standard_no') = ?
          AND json_extract(business_metadata, '$.content_type') = ?
          AND json_extract(business_metadata, ?) = ?
        """,
        (standard_no, content_type, f"$.{identity_field}", identity_value),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"manual evidence must resolve uniquely: {spec}, matches={len(rows)}")
    row = rows[0]
    candidate = {
        "page": row["page"],
        "text": row["text"],
        "content_type": content_type,
        "business_metadata": json.loads(row["business_metadata"] or "{}"),
        "source_trace": json.loads(row["source_trace"] or "{}"),
    }
    if standard_no == "Q/GDW 12126.4-2024":
        reason = "表6直接给出S20-NX2在相应额定容量下的空载损耗和负载损耗限值。"
    else:
        reason = "7.3.2明确规定总损耗由已测空载损耗与参考温度下负载损耗之和得到。"
    return {
        "label": "direct_candidate",
        "locator": _locator(candidate),
        "evidence_quote": "",
        "quote_verified": False,
        "reason": reason,
        "retrieval": {"purpose": "manual_corpus_recovery", "method": "stable_metadata_lookup"},
    }


def _has_direct(case: dict[str, Any]) -> bool:
    return any(item["label"] == "direct_candidate" for item in case["selected_evidence"])


def _missing_cases(case_pool: list[dict[str, Any]], review: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {case["case_id"]: case for case in review["cases"]}
    return [case for case in case_pool if not _has_direct(by_id[case["case_id"]])]


def plan_recovery(
    cases: list[dict[str, Any]], base_review: dict[str, Any], original_queries: dict[str, Any],
    prompt: str, path: Path,
) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "purpose": "gold_discovery_only", "cases": {}})
    review_by_id = {case["case_id"]: case for case in base_review["cases"]}
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        planned = _call_model(prompt, {
            "case": {
                "sample_context": case.get("sample_context") or {},
                "test_item": case["test_item"],
                "reported_requirement": case["reported_requirement"],
            },
            "original_queries": original_queries["cases"][case_id],
            "missing_evidence": review_by_id[case_id]["missing_evidence"],
            "review_note": review_by_id[case_id]["review_note"],
        })
        result = {}
        for field in ("table_recovery", "section_recovery"):
            values = planned.get(field) or []
            result[field] = [str(value).strip() for value in values[:2] if str(value).strip()]
        output["cases"][case_id] = result
        _write_json(path, output)
        print(f"recovery planned {index}/{len(cases)} {case_id}", flush=True)
    return output


def _retrieve_type(queries: dict[str, str], content_type: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, query in queries.items():
        result = embeddings.vector_search(query, top_k=TOP_K, content_type=content_type)
        for rank, hit in enumerate(result["hits"], start=1):
            candidate = merged.setdefault(hit["chunk_id"], {
                **hit,
                "content_type": content_type,
                "route_ranks": {},
                "route_scores": {},
                "rrf_score": 0.0,
            })
            candidate["route_ranks"][route] = rank
            candidate["route_scores"][route] = hit["score"]
            candidate["rrf_score"] += 1 / (RRF_K + rank)
    ranked = sorted(
        merged.values(),
        key=lambda item: (item["rrf_score"], max(item["route_scores"].values())),
        reverse=True,
    )[:FINAL_PER_TYPE]
    for rank, candidate in enumerate(ranked, start=1):
        candidate["type_rank"] = rank
    return ranked


def retrieve_recovery(
    cases: list[dict[str, Any]], original: dict[str, Any], recovery: dict[str, Any], path: Path
) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "purpose": "gold_discovery_only", "cases": {}})
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        queries = dict(original["cases"][case_id])
        for content_type in CONTENT_TYPES:
            for query_index, query in enumerate(recovery["cases"][case_id][f"{content_type}_recovery"], start=1):
                queries[f"gold_{content_type}_{query_index}"] = query
        candidates = []
        for content_type in CONTENT_TYPES:
            candidates.extend(_retrieve_type(queries, content_type))
        candidates.sort(key=lambda item: item["rrf_score"], reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        output["cases"][case_id] = {"queries": queries, "candidates": candidates}
        _write_json(path, output)
        print(f"recovery retrieved {index}/{len(cases)} {case_id}", flush=True)
    return output


def _compact(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate["business_metadata"]
    return {
        "candidate_key": candidate["candidate_key"],
        "content_type": candidate["content_type"],
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "page": candidate["page"],
        "text": candidate["text"][:10000],
    }


def review_recovery(
    cases: list[dict[str, Any]], candidate_data: dict[str, Any], judge_prompt: str,
    audit_prompt: str, path: Path,
) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "model": MODEL, "cases": {}})
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        candidates = candidate_data["cases"][case_id]["candidates"]
        compact = [_compact(candidate) for candidate in candidates]
        case_input = {
            "sample_context": case.get("sample_context") or {},
            "test_item": case["test_item"],
            "reported_requirement": case["reported_requirement"],
        }
        first = _call_model(judge_prompt, {"case": case_input, "candidates": compact})
        valid = {item["candidate_key"] for item in candidates}
        first_selected = []
        for item in first.get("selected") or []:
            key = str(item.get("candidate_key") or "")
            label = str(item.get("label") or "")
            if key in valid and label in LABELS and key not in {row["candidate_key"] for row in first_selected}:
                first_selected.append({
                    "candidate_key": key,
                    "label": label,
                    "evidence_quote": str(item.get("evidence_quote") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                })
        by_key = {item["candidate_key"]: item for item in compact}
        audit_input = []
        for item in first_selected:
            audit_input.append({**item, **by_key[item["candidate_key"]]})
        audited = _call_model(audit_prompt, {"case": case_input, "selected_candidates": audit_input})
        decisions = {
            str(item.get("candidate_key") or ""): {
                "action": str(item.get("action") or ""),
                "reason": str(item.get("reason") or "").strip(),
            }
            for item in audited.get("decisions") or []
            if str(item.get("candidate_key") or "") in {row["candidate_key"] for row in first_selected}
            and str(item.get("action") or "") in ACTIONS
        }
        if decisions.keys() != {item["candidate_key"] for item in first_selected}:
            raise ValueError(f"{case_id}: incomplete adversarial decisions")
        final = []
        for item in first_selected:
            decision = decisions[item["candidate_key"]]
            if decision["action"] == "reject":
                continue
            label = item["label"]
            if decision["action"] == "downgrade_supporting":
                label = "supporting_candidate"
            elif decision["action"] == "downgrade_uncertain":
                label = "uncertain"
            final.append({**item, "label": label, "reason": decision["reason"]})
        output["cases"][case_id] = {
            "selected": final,
            "missing_evidence": str(first.get("missing_evidence") or "").strip(),
            "review_note": str(audited.get("review_note") or "").strip(),
        }
        _write_json(path, output)
        print(f"recovery reviewed {index}/{len(cases)} {case_id}: {len(final)} selected", flush=True)
    return output


def merge_review(
    case_pool: list[dict[str, Any]], base: dict[str, Any], candidate_data: dict[str, Any],
    recovered: dict[str, Any], missing_ids: set[str],
) -> dict[str, Any]:
    by_case = {case["case_id"]: case for case in base["cases"]}
    for case_id in missing_ids:
        candidate_by_key = {
            item["candidate_key"]: item for item in candidate_data["cases"][case_id]["candidates"]
        }
        existing_by_hash = {
            item["locator"]["text_sha256"]: item
            for item in by_case[case_id]["selected_evidence"]
        }
        for item in recovered["cases"][case_id]["selected"]:
            candidate = candidate_by_key[item["candidate_key"]]
            locator = _locator(candidate)
            override_key = (
                case_id,
                locator["standard_no"],
                locator["content_type"],
                str(locator.get("table_no") or locator.get("section") or ""),
            )
            label = item["label"]
            reason = item["reason"]
            if override_key in RECOVERY_MAIN_OVERRIDES:
                label, reason = RECOVERY_MAIN_OVERRIDES[override_key]
            quote = item["evidence_quote"]
            verified = bool(quote and quote in candidate["text"])
            recovered_evidence = {
                "label": label,
                "locator": locator,
                "evidence_quote": quote if verified else "",
                "quote_verified": verified,
                "reason": reason,
                "retrieval": {
                    "purpose": "gold_discovery_only",
                    "type_rank": candidate["type_rank"],
                    "route_ranks": candidate["route_ranks"],
                },
            }
            existing = existing_by_hash.get(locator["text_sha256"])
            if existing is not None:
                if LABEL_PRIORITY[label] > LABEL_PRIORITY[existing["label"]]:
                    existing.update(recovered_evidence)
                continue
            by_case[case_id]["selected_evidence"].append(recovered_evidence)
            existing_by_hash[locator["text_sha256"]] = recovered_evidence
        for spec in MANUAL_DIRECT_EVIDENCE.get(case_id, []):
            manual_evidence = _resolve_manual_evidence(spec)
            text_hash = manual_evidence["locator"]["text_sha256"]
            existing = existing_by_hash.get(text_hash)
            if existing is not None:
                if LABEL_PRIORITY[manual_evidence["label"]] > LABEL_PRIORITY[existing["label"]]:
                    existing.update(manual_evidence)
                continue
            by_case[case_id]["selected_evidence"].append(manual_evidence)
            existing_by_hash[text_hash] = manual_evidence
        if any(item["label"] == "direct_candidate" for item in by_case[case_id]["selected_evidence"]):
            by_case[case_id]["gold_status"] = "candidate_gold_recovered_pending_domain_review"
        else:
            by_case[case_id]["gold_status"] = "candidate_gold_missing_direct_evidence"
        by_case[case_id]["recovery_note"] = recovered["cases"][case_id]["review_note"]

    output = {
        "version": 1,
        "source_cases": "evaluation/retrieval_case_pool_v1.json",
        "source_first_pass": "evaluation/retrieval_evidence_candidates_v1.json",
        "status": "candidate_gold_pending_domain_review",
        "warning": "gold_discovery_only queries must never be used as evaluated retrieval inputs",
        "cases": [by_case[case["case_id"]] for case in case_pool],
    }
    invalid = []
    checked = 0
    for case in output["cases"]:
        for evidence in case["selected_evidence"]:
            checked += 1
            count = len(resolve_evidence_locator(db.get_conn(), evidence["locator"]))
            if count != 1:
                invalid.append((case["case_id"], count))
    if invalid:
        raise ValueError(f"invalid locators: {invalid}")
    output["validation"] = {"checked_locators": checked, "invalid_locators": 0}
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--base-review", type=Path, default=BASE_REVIEW)
    parser.add_argument("--original-queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--recovery-prompt", type=Path, default=RECOVERY_PROMPT)
    parser.add_argument("--judge-prompt", type=Path, default=DEFAULT_JUDGE_PROMPT)
    parser.add_argument("--audit-prompt", type=Path, default=AUDIT_PROMPT)
    parser.add_argument("--recovery-queries", type=Path, default=RECOVERY_QUERIES)
    parser.add_argument("--candidates", type=Path, default=RECOVERY_CANDIDATES)
    parser.add_argument("--reviews", type=Path, default=RECOVERY_REVIEWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    case_pool = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    base = json.loads(args.base_review.read_text(encoding="utf-8"))
    missing = _missing_cases(case_pool, base)
    missing_ids = {case["case_id"] for case in missing}
    db.init_db()
    original = _read_json(args.original_queries, {})
    recovery = plan_recovery(
        missing, base, original, args.recovery_prompt.read_text(encoding="utf-8"), args.recovery_queries
    )
    candidates = retrieve_recovery(missing, original, recovery, args.candidates)
    reviews = review_recovery(
        missing,
        candidates,
        args.judge_prompt.read_text(encoding="utf-8"),
        args.audit_prompt.read_text(encoding="utf-8"),
        args.reviews,
    )
    output = merge_review(case_pool, base, candidates, reviews, missing_ids)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "cases": len(output["cases"]),
        "recovery_cases": len(missing),
        "cases_with_direct": sum(_has_direct(case) for case in output["cases"]),
        "still_missing_direct": [case["case_id"] for case in output["cases"] if not _has_direct(case)],
        **output["validation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
