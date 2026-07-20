"""Evaluate the jieba lexical demo against the frozen retrieval gold set.

This is an offline diagnostic, not production search behavior.

Run from the repository root:
    $env:PYTHONIOENCODING='utf-8'
    uv run --with jieba python scripts/evaluate_lexical_retrieval.py
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.evidence_locator import chunk_text_sha256  # noqa: E402
from demo_lexical_retrieval import (  # noqa: E402
    ChunkDoc,
    DEFAULT_DB,
    DEFAULT_TERMS,
    active_weights,
    load_docs,
    load_domain_terms,
    tokens_for_search,
)


DEFAULT_FREEZE = ROOT / "evaluation" / "frozen" / "retrieval_eval_v1_candidate_2026-07-13"
DEFAULT_CASES = DEFAULT_FREEZE / "retrieval_case_pool_v1.json"
DEFAULT_GOLD = DEFAULT_FREEZE / "retrieval_gold_candidates_v1.json"
DEFAULT_QUERIES = DEFAULT_FREEZE / "reports" / "retrieval_queries_40_v1.json"
DEFAULT_OUTPUT = ROOT / "backend" / "data" / "reports" / "lexical_retrieval_eval.json"
CONTENT_TYPES = ("table", "section")
RRF_K = 60


@dataclass
class PreparedDoc:
    doc: ChunkDoc
    field_counts: dict[str, dict[str, int]]
    text_sha256: str


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--terms", type=Path, default=DEFAULT_TERMS)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--route-top-k", type=int, default=20)
    parser.add_argument("--final-per-type", type=int, default=10)
    parser.add_argument("--include-llm-suggestions", action="store_true")
    args = parser.parse_args()

    load_domain_terms(args.terms)
    raw_docs_by_type = {
        content_type: load_docs(
            args.db,
            content_type=content_type,
            include_llm_suggestions=args.include_llm_suggestions,
        )
        for content_type in CONTENT_TYPES
    }
    docs_by_type = {
        content_type: prepare_docs(docs, include_llm_suggestions=args.include_llm_suggestions)
        for content_type, docs in raw_docs_by_type.items()
    }
    idf_by_type = {content_type: build_idf(docs) for content_type, docs in docs_by_type.items()}

    cases_by_id = {case["case_id"]: case for case in read_json(args.cases)["cases"]}
    gold_by_id = {case["case_id"]: case for case in read_json(args.gold)["cases"]}
    queries_by_id = read_json(args.queries)["cases"]

    details = []
    for case_id, case in cases_by_id.items():
        if case_id not in gold_by_id or case_id not in queries_by_id:
            continue
        for route_set, route_queries in route_sets_for(queries_by_id[case_id]).items():
            candidates = retrieve_candidates(
                route_queries,
                docs_by_type=docs_by_type,
                idf_by_type=idf_by_type,
                route_top_k=args.route_top_k,
                final_per_type=args.final_per_type,
                include_llm_suggestions=args.include_llm_suggestions,
            )
            details.append(evaluate_case(case, gold_by_id[case_id], route_set, route_queries, candidates))

    report = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "jieba_token_overlap_idf_typed_rrf",
        "terms": str(args.terms.relative_to(ROOT)) if args.terms.is_relative_to(ROOT) else str(args.terms),
        "queries": str(args.queries.relative_to(ROOT)) if args.queries.is_relative_to(ROOT) else str(args.queries),
        "route_top_k": args.route_top_k,
        "final_per_type": args.final_per_type,
        "include_llm_suggestions": args.include_llm_suggestions,
        "summary": aggregate(details),
        "cases": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def route_sets_for(queries: dict[str, str]) -> dict[str, dict[str, str]]:
    all_routes = {key: value for key, value in queries.items() if str(value).strip()}
    return {
        "all_routes": all_routes,
        "production_only": {
            key: value for key, value in all_routes.items() if key == "production"
        },
    }


def retrieve_candidates(
    queries: dict[str, str],
    *,
    docs_by_type: dict[str, list[PreparedDoc]],
    idf_by_type: dict[str, dict[str, float]],
    route_top_k: int,
    final_per_type: int,
    include_llm_suggestions: bool,
) -> list[dict[str, Any]]:
    candidates = []
    for content_type in CONTENT_TYPES:
        merged: dict[str, dict[str, Any]] = {}
        docs = docs_by_type[content_type]
        idf = idf_by_type[content_type]
        for route, query in queries.items():
            query_tokens = tokens_for_search(query)
            scored = [
                score_prepared_doc(doc, query_tokens, idf, include_llm_suggestions=include_llm_suggestions)
                for doc in docs
            ]
            scored = [item for item in scored if item["score"] > 0]
            scored.sort(key=lambda item: item["score"], reverse=True)
            for rank, item in enumerate(scored[:route_top_k], start=1):
                prepared: PreparedDoc = item["doc"]
                doc = prepared.doc
                candidate = merged.setdefault(
                    doc.chunk_id,
                    {
                        "chunk_id": doc.chunk_id,
                        "content_type": content_type,
                        "file_name": doc.file_name,
                        "page": doc.page,
                        "metadata": doc.metadata,
                        "text_sha256": prepared.text_sha256,
                        "route_ranks": {},
                        "route_scores": {},
                        "rrf_score": 0.0,
                        "evidence": {},
                    },
                )
                candidate["route_ranks"][route] = rank
                candidate["route_scores"][route] = item["score"]
                candidate["rrf_score"] += 1 / (RRF_K + rank)
                candidate["evidence"][route] = item["evidence"]
        ranked = sorted(
            merged.values(),
            key=lambda item: (item["rrf_score"], max(item["route_scores"].values())),
            reverse=True,
        )[:final_per_type]
        for type_rank, candidate in enumerate(ranked, start=1):
            candidate["type_rank"] = type_rank
        candidates.extend(ranked)
    candidates.sort(key=lambda item: (item["rrf_score"], max(item["route_scores"].values())), reverse=True)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates


def prepare_docs(docs: list[ChunkDoc], *, include_llm_suggestions: bool) -> list[PreparedDoc]:
    fields = active_weights(include_llm_suggestions)
    prepared = []
    for doc in docs:
        prepared.append(
            PreparedDoc(
                doc=doc,
                field_counts={
                    field: dict(token_counts(tokens_for_search(doc.fields.get(field, ""))))
                    for field in fields
                },
                text_sha256=chunk_text_sha256(doc.fields.get("text", "")),
            )
        )
    return prepared


def build_idf(docs: list[PreparedDoc]) -> dict[str, float]:
    doc_freq: dict[str, int] = defaultdict(int)
    for doc in docs:
        seen = set()
        for counts in doc.field_counts.values():
            seen.update(counts)
        for token in seen:
            doc_freq[token] += 1
    total = len(docs)
    return {
        token: math.log((total + 1) / (freq + 1)) + 1
        for token, freq in doc_freq.items()
    }


def score_prepared_doc(
    doc: PreparedDoc,
    query_tokens: list[str],
    idf: dict[str, float],
    *,
    include_llm_suggestions: bool,
) -> dict[str, Any]:
    weights = active_weights(include_llm_suggestions)
    query_counts = token_counts(query_tokens)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score = 0.0
    for field, weight in weights.items():
        field_counts = doc.field_counts.get(field, {})
        for token, qtf in query_counts.items():
            tf = field_counts.get(token, 0)
            if not tf:
                continue
            contribution = weight * min(tf, 3) * idf.get(token, 1.0) * min(qtf, 2)
            score += contribution
            evidence[field].append({
                "token": token,
                "tf": tf,
                "idf": round(idf.get(token, 1.0), 4),
                "score": round(contribution, 4),
            })
    return {
        "doc": doc,
        "score": score,
        "evidence": dict(evidence),
    }


def token_counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for token in tokens:
        counts[token] += 1
    return counts


def evaluate_case(
    case: dict[str, Any],
    gold_case: dict[str, Any],
    route_set: str,
    queries: dict[str, str],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    by_hash = {candidate["text_sha256"]: candidate for candidate in candidates}
    recalled = []
    missed = []
    gold = gold_items(gold_case)
    for item in gold:
        candidate = by_hash.get(item["text_sha256"])
        if candidate:
            recalled.append({
                **item,
                "rank": candidate["rank"],
                "type_rank": candidate["type_rank"],
                "route_ranks": candidate["route_ranks"],
            })
        else:
            missed.append(item)
    direct_recalled = [item for item in recalled if item["label"] == "direct_candidate"]
    direct_missed = [item for item in missed if item["label"] == "direct_candidate"]
    return {
        "case_id": case["case_id"],
        "dataset_split": case.get("dataset_split"),
        "retrieval_class": case.get("retrieval_class"),
        "expected_evidence_type": case.get("expected_evidence_type"),
        "route_set": route_set,
        "query_routes": list(queries),
        "gold_counts": count_by_label(gold),
        "recalled_counts": count_by_label(recalled),
        "direct_gold_available": any(item["label"] == "direct_candidate" for item in gold),
        "direct_recalled": bool(direct_recalled),
        "direct_best_rank": min((item["rank"] for item in direct_recalled), default=None),
        "direct_missed": bool(direct_missed),
        "recalled_gold": recalled,
        "missed_gold": missed,
        "candidates": summarize_candidates(candidates[:10]),
    }


def gold_items(gold_case: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for index, evidence in enumerate(gold_case.get("selected_evidence", []), start=1):
        locator = evidence["locator"]
        if locator.get("content_type") not in CONTENT_TYPES:
            continue
        items.append({
            "gold_index": index,
            "label": evidence["label"],
            "content_type": locator.get("content_type"),
            "standard_no": locator.get("standard_no"),
            "section": locator.get("section"),
            "table_no": locator.get("table_no"),
            "text_sha256": locator.get("text_sha256"),
        })
    return items


def summarize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for candidate in candidates:
        metadata = candidate["metadata"]
        out.append({
            "rank": candidate["rank"],
            "type_rank": candidate["type_rank"],
            "chunk_id": candidate["chunk_id"],
            "content_type": candidate["content_type"],
            "standard_no": metadata.get("standard_no"),
            "section": metadata.get("section"),
            "section_title": metadata.get("section_title"),
            "table_no": metadata.get("table_no"),
            "table_title": metadata.get("table_title"),
            "rrf_score": candidate["rrf_score"],
            "route_ranks": candidate["route_ranks"],
            "text_sha256": candidate["text_sha256"],
        })
    return out


def count_by_label(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item["label"]] += 1
    return dict(counts)


def aggregate(details: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for route_set in sorted({detail["route_set"] for detail in details}):
        route_details = [detail for detail in details if detail["route_set"] == route_set]
        out[route_set] = {
            "overall": aggregate_subset(route_details),
            "by_split": aggregate_group(route_details, "dataset_split"),
            "by_retrieval_class": aggregate_group(route_details, "retrieval_class"),
        }
    return out


def aggregate_group(details: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for detail in details:
        groups[str(detail.get(field) or "unknown")].append(detail)
    return {key: aggregate_subset(value) for key, value in sorted(groups.items())}


def aggregate_subset(details: list[dict[str, Any]]) -> dict[str, Any]:
    direct_gold_cases = [detail for detail in details if detail["direct_gold_available"]]
    direct_recalled_cases = [detail for detail in direct_gold_cases if detail["direct_recalled"]]
    direct_gold_evidence = sum(detail["gold_counts"].get("direct_candidate", 0) for detail in details)
    direct_recalled_evidence = sum(detail["recalled_counts"].get("direct_candidate", 0) for detail in details)
    all_gold_evidence = sum(sum(detail["gold_counts"].values()) for detail in details)
    all_recalled_evidence = sum(sum(detail["recalled_counts"].values()) for detail in details)
    reciprocal_ranks = [
        1 / detail["direct_best_rank"] if detail["direct_best_rank"] else 0.0
        for detail in direct_gold_cases
    ]
    return {
        "cases": len(details),
        "direct_gold_cases": len(direct_gold_cases),
        "direct_recalled_cases": len(direct_recalled_cases),
        "direct_case_recall": ratio(len(direct_recalled_cases), len(direct_gold_cases)),
        "direct_gold_evidence": direct_gold_evidence,
        "direct_recalled_evidence": direct_recalled_evidence,
        "direct_evidence_recall": ratio(direct_recalled_evidence, direct_gold_evidence),
        "all_gold_evidence": all_gold_evidence,
        "all_recalled_evidence": all_recalled_evidence,
        "all_evidence_recall": ratio(all_recalled_evidence, all_gold_evidence),
        "direct_case_mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
    }


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


if __name__ == "__main__":
    main()
