"""Evaluate temporary table-title and table-header vector reranking."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import db, embeddings  # noqa: E402


DEFAULT_CASES = ROOT / "evaluation" / "retrieval_seed_cases_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "table_metadata_vector_rerank_v1.json"
FOCUS_CASES = {
    "hbjc-load-loss-pk",
    "hbjc-applied-withstand-voltage",
    "hbjc-lightning-impulse-voltage",
}
ROUTE_TOP_K = 20
FINAL_TABLE_LIMIT = 10
RRF_K = 60
FUSION_K = 20
WEIGHTS = {"title": 0.55, "base": 0.30, "header": 0.15}


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _embed_documents(texts: list[str]) -> dict[str, list[float]]:
    unique = list(dict.fromkeys(text for text in texts if text.strip()))
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(unique), embeddings.MAX_BATCH_SIZE):
        batch = unique[start:start + embeddings.MAX_BATCH_SIZE]
        batch_vectors, _ = embeddings.embed_with_dashscope(batch)
        vectors.update(zip(batch, batch_vectors, strict=True))
    return vectors


def _candidate_pool(case: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, query in case["queries"].items():
        result = embeddings.vector_search(query, top_k=ROUTE_TOP_K, content_type="table")
        for rank, hit in enumerate(result["hits"], start=1):
            candidate = merged.setdefault(
                hit["chunk_id"],
                {
                    **hit,
                    "route_ranks": {},
                    "route_scores": {},
                    "base_rrf_score": 0.0,
                },
            )
            candidate["route_ranks"][route] = rank
            candidate["route_scores"][route] = hit["score"]
            candidate["base_rrf_score"] += 1 / (RRF_K + rank)
    ranked = sorted(merged.values(), key=lambda item: item["base_rrf_score"], reverse=True)
    for rank, candidate in enumerate(ranked, start=1):
        candidate["base_rank"] = rank
    return ranked


def _rerank(case: dict[str, Any]) -> dict[str, Any]:
    candidates = _candidate_pool(case)
    query_vectors = {
        route: embeddings.embed_query_with_dashscope(query)
        for route, query in case["queries"].items()
    }
    for candidate in candidates:
        metadata = candidate["business_metadata"]
        candidate["title_text"] = str(metadata.get("table_title") or "").strip()
        columns = metadata.get("table_columns")
        candidate["header_text"] = " | ".join(str(value) for value in columns) if isinstance(columns, list) else ""

    document_vectors = _embed_documents([
        text
        for candidate in candidates
        for text in (candidate["title_text"], candidate["header_text"])
        if text
    ])
    for candidate in candidates:
        for field in ("title", "header"):
            text = candidate[f"{field}_text"]
            scores = {
                route: _cosine(query_vector, document_vectors[text])
                for route, query_vector in query_vectors.items()
            } if text else {}
            candidate[f"{field}_route_scores"] = scores
            candidate[f"{field}_similarity"] = max(scores.values()) if scores else -1.0

    for field in ("title", "header"):
        ranked = sorted(candidates, key=lambda item: item[f"{field}_similarity"], reverse=True)
        for rank, candidate in enumerate(ranked, start=1):
            candidate[f"{field}_rank"] = rank

    for candidate in candidates:
        candidate["fusion_score"] = (
            WEIGHTS["title"] / (FUSION_K + candidate["title_rank"])
            + WEIGHTS["base"] / (FUSION_K + candidate["base_rank"])
            + WEIGHTS["header"] / (FUSION_K + candidate["header_rank"])
        )
    reranked = sorted(candidates, key=lambda item: item["fusion_score"], reverse=True)
    for rank, candidate in enumerate(reranked, start=1):
        candidate["reranked_rank"] = rank

    probes = []
    for target in case.get("probe_targets", []):
        matches = [
            candidate for candidate in candidates
            if all(candidate["business_metadata"].get(field) == value for field, value in target.items())
        ]
        matches.sort(key=lambda item: item["reranked_rank"])
        best = matches[0] if matches else None
        probes.append({
            "target": target,
            "in_pool": best is not None,
            "base_rank": best["base_rank"] if best else None,
            "title_rank": best["title_rank"] if best else None,
            "header_rank": best["header_rank"] if best else None,
            "reranked_rank": best["reranked_rank"] if best else None,
            "entered_final_top_10": bool(best and best["reranked_rank"] <= FINAL_TABLE_LIMIT),
            "title_similarity": best["title_similarity"] if best else None,
            "header_similarity": best["header_similarity"] if best else None,
        })

    return {
        "case_id": case["id"],
        "queries": case["queries"],
        "pool_size": len(candidates),
        "probes": probes,
        "top_10": [
            {
                "rank": candidate["reranked_rank"],
                "base_rank": candidate["base_rank"],
                "title_rank": candidate["title_rank"],
                "header_rank": candidate["header_rank"],
                "fusion_score": candidate["fusion_score"],
                "standard_no": candidate["business_metadata"].get("standard_no"),
                "table_no": candidate["business_metadata"].get("table_no"),
                "table_title": candidate["business_metadata"].get("table_title"),
                "page": candidate["page"],
            }
            for candidate in reranked[:FINAL_TABLE_LIMIT]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    spec = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = [case for case in spec["cases"] if case["id"] in FOCUS_CASES]
    db.init_db()
    results = [_rerank(case) for case in cases]
    report = {
        "version": 1,
        "scope": "temporary_no_database_writes",
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "route_top_k": ROUTE_TOP_K,
        "final_table_limit": FINAL_TABLE_LIMIT,
        "fusion": {
            "method": "weighted_reciprocal_rank_fusion",
            "k": FUSION_K,
            "weights": WEIGHTS,
            "query_similarity": "maximum similarity across every configured query route, including optional table_target",
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{"case_id": item["case_id"], "probes": item["probes"]} for item in results], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
