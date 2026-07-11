"""Evaluate dense-vector recall against the versioned audit gold set."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, embeddings  # noqa: E402
from app.evidence_locator import resolve_evidence_locator  # noqa: E402
from validate_audit_eval import DEFAULT_DATASET, validate_dataset  # noqa: E402


DEFAULT_REPORT = BACKEND / "data" / "reports" / "vector_retrieval_eval.json"


def build_case_query(case: dict[str, Any]) -> str:
    card = case["audit_card"]
    parts: list[str] = []
    for basis in card.get("report", {}).get("declared_bases", []):
        if basis.get("availability") == "available" and basis.get("standard_no"):
            parts.append(str(basis["standard_no"]))
    parts.extend(_fact_texts(card.get("sample_context", {}).get("facts", [])))
    test_context = card.get("test_context", {})
    if test_context.get("project_name"):
        parts.append(str(test_context["project_name"]))
    parts.extend(_fact_texts(test_context.get("facts", [])))
    rule = card.get("pending_rule", {})
    for key in ("parameter_name", "raw_text"):
        if rule.get(key):
            parts.append(str(rule[key]))
    return "；".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def evaluate(dataset_path: Path, db_path: Path) -> dict[str, Any]:
    validate_dataset(dataset_path, db_path, verify_corpus=True)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    locator_conn = sqlite3.connect(db_path)
    locator_conn.row_factory = sqlite3.Row
    details = []
    passed_cases = 0
    hit_groups = 0
    total_groups = 0
    reciprocal_ranks = []
    try:
        for case in dataset["cases"]:
            evidence = case["expected_result"].get("adopted_evidence", [])
            gold_ids = []
            for item in evidence:
                matches = resolve_evidence_locator(locator_conn, item["locator"])
                gold_ids.append(matches[0]["current_chunk_id"])

            retrieval = case["acceptance"]["retrieval"]
            top_k = int(retrieval["top_k"])
            query = build_case_query(case)
            result = embeddings.vector_search(query, top_k=top_k)
            hit_ids = [hit["chunk_id"] for hit in result["hits"]]
            ranks = [hit_ids.index(chunk_id) + 1 if chunk_id in hit_ids else None for chunk_id in gold_ids]
            group_results = []
            for group in retrieval.get("required_evidence_groups", []):
                total_groups += 1
                group_ranks = [ranks[index] for index in group if ranks[index] is not None]
                group_hit = bool(group_ranks)
                if group_hit:
                    hit_groups += 1
                    reciprocal_ranks.append(1 / min(group_ranks))
                else:
                    reciprocal_ranks.append(0.0)
                group_results.append({"evidence_indexes": group, "hit": group_hit, "best_rank": min(group_ranks) if group_ranks else None})
            case_passed = all(group["hit"] for group in group_results)
            if case_passed:
                passed_cases += 1
            details.append({
                "case_id": case["id"],
                "query": query,
                "top_k": top_k,
                "passed": case_passed,
                "gold_chunk_ids": gold_ids,
                "gold_ranks": ranks,
                "required_groups": group_results,
                "hits": [
                    {
                        "rank": index + 1,
                        "chunk_id": hit["chunk_id"],
                        "score": hit["score"],
                        "standard_no": hit["business_metadata"].get("standard_no"),
                        "section": hit["business_metadata"].get("section"),
                        "table_no": hit["business_metadata"].get("table_no"),
                    }
                    for index, hit in enumerate(result["hits"])
                ],
            })
    finally:
        locator_conn.close()

    case_count = len(details)
    return {
        "version": 1,
        "dataset": str(dataset_path),
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "top_k_policy": "per-case acceptance.retrieval.top_k",
        "summary": {
            "cases": case_count,
            "passed_cases": passed_cases,
            "case_pass_rate": passed_cases / case_count if case_count else 0.0,
            "required_groups": total_groups,
            "hit_groups": hit_groups,
            "group_recall": hit_groups / total_groups if total_groups else 0.0,
            "group_mrr": sum(reciprocal_ranks) / total_groups if total_groups else 0.0,
        },
        "cases": details,
    }


def _fact_texts(facts: list[dict[str, Any]]) -> list[str]:
    values = []
    for fact in facts:
        if fact.get("raw_text"):
            values.append(str(fact["raw_text"]))
        elif fact.get("value") is not None:
            unit = f" {fact['unit']}" if fact.get("unit") else ""
            values.append(f"{fact.get('field', '')} {fact['value']}{unit}".strip())
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=db.config.DB_PATH)
    parser.add_argument("--json", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    db.init_db()
    report = evaluate(args.dataset, args.db)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
