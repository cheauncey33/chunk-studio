"""Build a fixed 10-table plus 10-section candidate set for each HBJC case."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import db, embeddings  # noqa: E402


DEFAULT_CASES = ROOT / "evaluation" / "retrieval_seed_cases_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "retrieval_candidates_typed_v1.json"
RRF_K = 60

CONTENT_TYPES = ("table", "section")
TITLE_BONUS_WEIGHT = 0.015
COLUMNS_BONUS_WEIGHT = 0.025
TERM_STOPWORDS = {"标准值", "限值", "标准", "试验", "测量", "表", "kva", "kv", "kw"}


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _keyword_terms(case: dict[str, Any]) -> list[str]:
    raw = str(case["queries"].get("keyword") or "")
    terms = []
    for token in re.split(r"[\s,，:：;；/()（）]+", raw):
        normalized = _normalize_match_text(token)
        if len(normalized) < 2 or normalized in TERM_STOPWORDS or normalized.isdigit():
            continue
        if normalized not in terms:
            terms.append(normalized)
    return terms


def _term_coverage(term: str, field: str) -> float:
    if term in field:
        return 1.0
    if len(term) < 4 or len(field) < 2:
        return 0.0
    grams = {term[index:index + 2] for index in range(len(term) - 1)}
    matched = sum(gram in field for gram in grams)
    coverage = matched / len(grams)
    return coverage if coverage >= 0.5 else 0.0


def _field_match(terms: list[str], fields: list[Any]) -> tuple[float, list[dict[str, Any]]]:
    matches = []
    for field_value in fields:
        field = _normalize_match_text(field_value)
        if not field:
            continue
        for term in terms:
            coverage = _term_coverage(term, field)
            if coverage:
                matches.append({"term": term, "field": str(field_value), "coverage": round(coverage, 4)})
    matches.sort(key=lambda item: item["coverage"], reverse=True)
    unique_terms = []
    selected = []
    for match in matches:
        if match["term"] in unique_terms:
            continue
        unique_terms.append(match["term"])
        selected.append(match)
        if len(selected) == 2:
            break
    score = sum(item["coverage"] for item in selected) / len(selected) if selected else 0.0
    return score, selected


def _apply_table_metadata_bonus(case: dict[str, Any], candidate: dict[str, Any]) -> None:
    terms = _keyword_terms(case)
    metadata = candidate["business_metadata"]
    title_signal, title_matches = _field_match(terms, [metadata.get("table_title")])
    columns = metadata.get("table_columns")
    column_values = columns if isinstance(columns, list) else []
    columns_signal, column_matches = _field_match(terms, column_values)
    candidate["metadata_rerank"] = {
        "terms": terms,
        "title_signal": round(title_signal, 6),
        "columns_signal": round(columns_signal, 6),
        "title_bonus": round(title_signal * TITLE_BONUS_WEIGHT, 6),
        "columns_bonus": round(columns_signal * COLUMNS_BONUS_WEIGHT, 6),
        "title_matches": title_matches,
        "column_matches": column_matches,
    }
    candidate["rerank_score"] = (
        candidate["rrf_score"]
        + candidate["metadata_rerank"]["title_bonus"]
        + candidate["metadata_rerank"]["columns_bonus"]
    )


def build_candidates(
    case: dict[str, Any], top_k_by_type: dict[str, int], limit_per_type: int
) -> dict[str, Any]:
    merged_by_type: dict[str, dict[str, dict[str, Any]]] = {}
    final = []
    for content_type in CONTENT_TYPES:
        merged: dict[str, dict[str, Any]] = {}
        merged_by_type[content_type] = merged
        for route, query in case["queries"].items():
            result = embeddings.vector_search(
                query,
                top_k=top_k_by_type[content_type],
                content_type=content_type,
            )
            for rank, hit in enumerate(result["hits"], start=1):
                chunk_id = hit["chunk_id"]
                candidate = merged.setdefault(
                    chunk_id,
                    {
                        "chunk_id": chunk_id,
                        "file_id": hit["file_id"],
                        "file_name": hit["file_name"],
                        "page": hit["page"],
                        "crop_url": hit["crop_url"],
                        "text": hit["text"],
                        "business_metadata": hit["business_metadata"],
                        "source_trace": hit["source_trace"],
                        "route_ranks": {},
                        "route_scores": {},
                        "rrf_score": 0.0,
                        "content_type": content_type,
                    },
                )
                candidate["route_ranks"][route] = rank
                candidate["route_scores"][route] = hit["score"]
                candidate["rrf_score"] += 1 / (RRF_K + rank)

        raw_ranked = sorted(
            merged.values(),
            key=lambda item: (item["rrf_score"], max(item["route_scores"].values())),
            reverse=True,
        )
        for raw_rank, candidate in enumerate(raw_ranked, start=1):
            candidate["raw_type_rank"] = raw_rank
            candidate["rerank_score"] = candidate["rrf_score"]
            if content_type == "table":
                _apply_table_metadata_bonus(case, candidate)
        reranked_all = sorted(
            raw_ranked,
            key=lambda item: (item["rerank_score"], item["rrf_score"]),
            reverse=True,
        )
        for reranked_type_rank, candidate in enumerate(reranked_all, start=1):
            candidate["reranked_type_rank"] = reranked_type_rank
        ranked_for_type = reranked_all[:limit_per_type]
        for type_rank, candidate in enumerate(ranked_for_type, start=1):
            candidate["type_rank"] = type_rank
        final.extend(ranked_for_type)

    final.sort(key=lambda item: (item["rerank_score"], item["rrf_score"]), reverse=True)
    for rank, candidate in enumerate(final, start=1):
        candidate["candidate_rank"] = rank

    probe_results = []
    all_candidates = [
        item for candidates in merged_by_type.values() for item in candidates.values()
    ]
    for target in case.get("probe_targets", []):
        pool_matches = [
            item for item in all_candidates
            if all(
                item["business_metadata"].get(field) == value
                for field, value in target.items()
            )
        ]
        pool_matches.sort(key=lambda item: item["raw_type_rank"])
        final_matches = [item for item in final if item in pool_matches]
        probe_results.append({
            "target": target,
            "in_retrieval_pool": bool(pool_matches),
            "present": bool(final_matches),
            "candidate_rank": final_matches[0]["candidate_rank"] if final_matches else None,
            "type_rank": final_matches[0]["type_rank"] if final_matches else None,
            "raw_type_rank": pool_matches[0]["raw_type_rank"] if pool_matches else None,
            "reranked_type_rank": pool_matches[0]["reranked_type_rank"] if pool_matches else None,
            "rerank_score": pool_matches[0]["rerank_score"] if pool_matches else None,
            "metadata_rerank": pool_matches[0].get("metadata_rerank") if pool_matches else None,
        })

    return {
        "case_id": case["id"],
        "evidence_profile": case["evidence_profile"],
        "queries": case["queries"],
        "route_top_k_per_type": top_k_by_type,
        "merged_candidate_count": sum(len(items) for items in merged_by_type.values()),
        "merged_candidate_count_by_type": {
            content_type: len(items) for content_type, items in merged_by_type.items()
        },
        "final_candidate_count": len(final),
        "final_type_counts": {
            content_type: sum(item["content_type"] == content_type for item in final)
            for content_type in CONTENT_TYPES
        },
        "probe_results": probe_results,
        "candidates": final,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    spec = json.loads(args.cases.read_text(encoding="utf-8"))
    top_k_by_type = {
        content_type: int(value)
        for content_type, value in spec["retrieval_top_k_per_type_per_route"].items()
    }
    limit_per_type = int(spec["final_candidate_limit_per_type"])
    if top_k_by_type != {"table": 20, "section": 10} or limit_per_type != 10:
        raise ValueError("v1 rerank experiment requires table Top 20, section Top 10, final Top 10 per type")

    db.init_db()
    cases = [build_candidates(case, top_k_by_type, limit_per_type) for case in spec["cases"]]
    report = {
        "version": 1,
        "source_cases": str(args.cases),
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "fusion": {
            "method": "typed_rrf_then_deterministic_table_metadata_rerank",
            "rrf_k": RRF_K,
            "content_types": list(CONTENT_TYPES),
            "quota_per_type": limit_per_type,
            "table_title_bonus_weight": TITLE_BONUS_WEIGHT,
            "table_columns_bonus_weight": COLUMNS_BONUS_WEIGHT,
            "note": "Only table candidates receive deterministic title and column bonuses; section scores are unchanged.",
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            [
                {
                    "case_id": case["case_id"],
                    "merged": case["merged_candidate_count"],
                    "final": case["final_candidate_count"],
                    "types": case["final_type_counts"],
                    "probes": case["probe_results"],
                }
                for case in cases
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
