"""Compare dense, RRF, and model-reranked retrieval on the frozen 40-case set."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import embeddings, retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402


DEFAULT_FREEZE = ROOT / "evaluation" / "frozen" / "retrieval_eval_v1_candidate_2026-07-13"
DEFAULT_QUERIES = DEFAULT_FREEZE / "reports" / "retrieval_queries_40_v1.json"
DEFAULT_REPORTS = ROOT / "backend" / "data" / "reports"
DEFAULT_JSON = DEFAULT_REPORTS / "hybrid_retrieval_ablation.json"
DEFAULT_MD = DEFAULT_REPORTS / "hybrid_retrieval_ablation.md"
TOP_K_VALUES = (1, 3, 5, 10, 20, 40)
MAX_TOP_K = max(TOP_K_VALUES)
POLICIES = ("dense_original", "hybrid_rrf", "hybrid_rerank")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class CaseRetrievalCache:
    """Reuse one planner, embedding, and typed recall pass across both hybrid policies."""

    def __init__(self) -> None:
        self.planned: dict[str, str] | None = None
        self.planner_error: Exception | None = None
        self.embedding_batches: dict[tuple[str, ...], list[list[float]]] = {}
        self.query_vectors: dict[str, list[float]] = {}
        self.vector_results: dict[tuple[str, str | None, int], dict[str, Any]] = {}

    def planner(self, query: str) -> dict[str, str]:
        if self.planner_error is not None:
            raise self.planner_error
        if self.planned is None:
            try:
                self.planned = retrieval.plan_query_rewrites(query)
            except Exception as exc:
                self.planner_error = exc
                raise
        return self.planned

    def batch_embedder(self, queries: list[str], **kwargs: Any) -> list[list[float]]:
        key = tuple(queries)
        if key not in self.embedding_batches:
            vectors = embeddings.embed_queries_with_dashscope(queries, **kwargs)
            self.embedding_batches[key] = vectors
            self.query_vectors.update(zip(queries, vectors, strict=True))
        return self.embedding_batches[key]

    def vector_searcher(
        self,
        query: str,
        vector: list[float],
        *,
        top_k: int,
        content_type: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        key = (query, content_type, top_k)
        if key not in self.vector_results:
            self.vector_results[key] = embeddings.vector_search_by_vector(
                query,
                vector,
                top_k=top_k,
                content_type=content_type,
                **kwargs,
            )
        return self.vector_results[key]


def _disable_reranker(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    raise RuntimeError("reranker intentionally disabled for ablation")


def direct_gold(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["locator"]["text_sha256"]: item
        for item in case.get("selected_evidence", [])
        if item.get("label") == "direct_candidate"
    }


def evaluate_ranking(hits: list[dict[str, Any]], gold: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranks_by_hash: dict[str, int] = {}
    for rank, hit in enumerate(hits, start=1):
        text_hash = chunk_text_sha256(hit.get("text"))
        if text_hash in gold and text_hash not in ranks_by_hash:
            ranks_by_hash[text_hash] = rank

    return {
        "best_direct_rank": min(ranks_by_hash.values(), default=None),
        "matched_direct": [
            {"text_sha256": text_hash, "rank": rank}
            for text_hash, rank in sorted(ranks_by_hash.items(), key=lambda item: item[1])
        ],
        "top_hits": [summarize_hit(rank, hit) for rank, hit in enumerate(hits[:10], start=1)],
    }


def summarize_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "rank": rank,
        "chunk_id": hit["chunk_id"],
        "text_sha256": chunk_text_sha256(hit.get("text")),
        "score": hit.get("score"),
        "rrf_score": hit.get("rrf_score"),
        "rerank_score": hit.get("rerank_score"),
        "route_ranks": hit.get("route_ranks") or {},
        "content_type": metadata.get("content_type"),
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "page": hit.get("page"),
    }


def evaluate_case(
    case: dict[str, Any],
    gold_case: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    cache = CaseRetrievalCache()
    gold = direct_gold(gold_case)

    recall_started = time.perf_counter()
    rrf_result = retrieval.hybrid_search(
        query,
        top_k=MAX_TOP_K,
        planner=cache.planner,
        batch_embedder=cache.batch_embedder,
        vector_searcher=cache.vector_searcher,
        reranker=_disable_reranker,
    )
    recall_seconds = time.perf_counter() - recall_started

    rerank_started = time.perf_counter()
    rerank_result = retrieval.hybrid_search(
        query,
        top_k=MAX_TOP_K,
        planner=cache.planner,
        batch_embedder=cache.batch_embedder,
        vector_searcher=cache.vector_searcher,
        reranker=retrieval.rerank_documents,
    )
    rerank_seconds = time.perf_counter() - rerank_started

    query_vector = cache.query_vectors[query]
    dense_started = time.perf_counter()
    dense_result = embeddings.vector_search_by_vector(
        query,
        query_vector,
        top_k=MAX_TOP_K,
        model=embeddings.DEFAULT_MODEL,
        dimension=embeddings.DEFAULT_DIMENSION,
    )
    dense_seconds = time.perf_counter() - dense_started

    return {
        "case_id": case["case_id"],
        "dataset_split": case["dataset_split"],
        "retrieval_class": case["retrieval_class"],
        "expected_evidence_type": case["expected_evidence_type"],
        "gold_status": gold_case.get("gold_status"),
        "direct_gold_count": len(gold),
        "query": query,
        "query_routes": rerank_result["query_routes"],
        "candidate_count": rerank_result["candidate_count"],
        "degraded": {
            "initial_recall": [
                value for value in rrf_result["degraded"] if value != "rerank_failed"
            ],
            "rerank": rerank_result["degraded"],
        },
        "timing_seconds": {
            "initial_hybrid_recall": recall_seconds,
            "reranker_only_cached_recall": rerank_seconds,
            "dense_search_reused_embedding": dense_seconds,
        },
        "rankings": {
            "dense_original": evaluate_ranking(dense_result["hits"], gold),
            "hybrid_rrf": evaluate_ranking(rrf_result["hits"], gold),
            "hybrid_rerank": evaluate_ranking(rerank_result["hits"], gold),
        },
    }


def aggregate_policy(cases: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    eligible = [case for case in cases if case["direct_gold_count"] > 0]
    gold_evidence = sum(case["direct_gold_count"] for case in eligible)
    metrics: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        case_hits = 0
        evidence_hits = 0
        reciprocal_rank = 0.0
        for case in eligible:
            ranking = case["rankings"][policy]
            matched_ranks = [item["rank"] for item in ranking["matched_direct"]]
            hits_at_k = sum(rank <= top_k for rank in matched_ranks)
            evidence_hits += hits_at_k
            best_rank = ranking["best_direct_rank"]
            if best_rank is not None and best_rank <= top_k:
                case_hits += 1
                reciprocal_rank += 1 / best_rank
        metrics[str(top_k)] = {
            "direct_gold_cases": len(eligible),
            "direct_case_hits": case_hits,
            "direct_case_recall": case_hits / len(eligible) if eligible else None,
            "direct_gold_evidence": gold_evidence,
            "direct_evidence_hits": evidence_hits,
            "direct_evidence_recall": evidence_hits / gold_evidence if gold_evidence else None,
            "mrr": reciprocal_rank / len(eligible) if eligible else None,
        }
    return metrics


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_policy = {policy: aggregate_policy(cases, policy) for policy in POLICIES}
    by_class: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["retrieval_class"]].append(case)
    for retrieval_class, group in sorted(grouped.items()):
        by_class[retrieval_class] = {
            policy: aggregate_policy(group, policy) for policy in POLICIES
        }

    return {
        "by_policy": by_policy,
        "by_retrieval_class": by_class,
        "rerank_vs_rrf": compare_policies(cases, "hybrid_rrf", "hybrid_rerank"),
        "degraded_cases": sum(
            1 for case in cases if case["degraded"]["initial_recall"] or case["degraded"]["rerank"]
        ),
        "average_timing_seconds": average_timings(cases),
    }


def compare_policies(cases: list[dict[str, Any]], baseline: str, treatment: str) -> dict[str, Any]:
    eligible = [case for case in cases if case["direct_gold_count"] > 0]
    comparison: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        wins = losses = both_hit = both_miss = 0
        for case in eligible:
            baseline_rank = case["rankings"][baseline]["best_direct_rank"]
            treatment_rank = case["rankings"][treatment]["best_direct_rank"]
            baseline_hit = baseline_rank is not None and baseline_rank <= top_k
            treatment_hit = treatment_rank is not None and treatment_rank <= top_k
            if treatment_hit and not baseline_hit:
                wins += 1
            elif baseline_hit and not treatment_hit:
                losses += 1
            elif treatment_hit:
                both_hit += 1
            else:
                both_miss += 1
        comparison[str(top_k)] = {
            "wins": wins,
            "losses": losses,
            "both_hit": both_hit,
            "both_miss": both_miss,
            "net_case_hits": wins - losses,
        }
    return comparison


def average_timings(cases: list[dict[str, Any]]) -> dict[str, float]:
    if not cases:
        return {}
    keys = cases[0]["timing_seconds"]
    return {
        key: sum(case["timing_seconds"][key] for case in cases) / len(cases)
        for key in keys
    }


def build_report(
    cases: list[dict[str, Any]],
    errors: list[dict[str, str]],
    *,
    total_cases: int,
    status: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "freeze_dir": str(DEFAULT_FREEZE.relative_to(ROOT)),
        "gold_warning": (
            "All frozen labels are pending domain review; cases without direct gold are excluded "
            "from direct-case metrics."
        ),
        "retrieval_policy": {
            "policies": list(POLICIES),
            "top_k_values": list(TOP_K_VALUES),
            "route_top_k": retrieval.ROUTE_TOP_K,
            "candidates_per_type": retrieval.CANDIDATES_PER_TYPE,
            "rrf_k": retrieval.RRF_K,
            "rerank_model": retrieval.RERANK_MODEL,
            "query_routes": ["production", "semantic", "keyword"],
            "excluded_oracle_routes": [
                "table_target",
                "section_target",
                "gold_discovery_only",
                "manual_corpus_recovery",
            ],
        },
        "progress": {
            "completed_cases": len(cases),
            "total_cases": total_cases,
            "error_cases": len(errors),
        },
        "summary": aggregate(cases),
        "errors": errors,
        "cases": cases,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hybrid Retrieval Ablation",
        "",
        f"Status: `{report['status']}`",
        f"Cases: `{report['progress']['completed_cases']}/{report['progress']['total_cases']}`; errors: `{report['progress']['error_cases']}`",
        "",
        "> Gold limitation: all labels are pending domain review. These numbers compare retrieval policies; they are not final business accuracy.",
        "",
        "## Overall",
        "",
        "| Policy | K | Case recall | Evidence recall | MRR |",
        "|---|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        for top_k in TOP_K_VALUES:
            item = report["summary"]["by_policy"][policy][str(top_k)]
            lines.append(
                f"| {policy} | {top_k} | {fmt_rate(item['direct_case_recall'])} "
                f"({item['direct_case_hits']}/{item['direct_gold_cases']}) | "
                f"{fmt_rate(item['direct_evidence_recall'])} | {fmt_rate(item['mrr'])} |"
            )

    lines.extend([
        "",
        "## Rerank vs RRF",
        "",
        "| K | Wins | Losses | Net case hits | Both hit | Both miss |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for top_k in TOP_K_VALUES:
        item = report["summary"]["rerank_vs_rrf"][str(top_k)]
        lines.append(
            f"| {top_k} | {item['wins']} | {item['losses']} | {item['net_case_hits']} | "
            f"{item['both_hit']} | {item['both_miss']} |"
        )

    lines.extend([
        "",
        "## By Retrieval Class at K=10",
        "",
        "| Class | Dense | RRF | Rerank |",
        "|---|---:|---:|---:|",
    ])
    for retrieval_class, policies in report["summary"]["by_retrieval_class"].items():
        lines.append(
            f"| {retrieval_class} | "
            f"{fmt_rate(policies['dense_original']['10']['direct_case_recall'])} | "
            f"{fmt_rate(policies['hybrid_rrf']['10']['direct_case_recall'])} | "
            f"{fmt_rate(policies['hybrid_rerank']['10']['direct_case_recall'])} |"
        )

    timing = report["summary"]["average_timing_seconds"]
    lines.extend([
        "",
        "## Timing",
        "",
        f"- Initial hybrid recall average: `{timing.get('initial_hybrid_recall', 0):.3f}s`",
        f"- Reranker average with cached recall: `{timing.get('reranker_only_cached_recall', 0):.3f}s`",
        f"- Dense search average with reused embedding: `{timing.get('dense_search_reused_embedding', 0):.3f}s`",
        "",
    ])
    return "\n".join(lines)


def fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--delay", type=float, default=0.1)
    args = parser.parse_args()

    freeze = args.freeze.resolve()
    case_payload = read_json(freeze / "retrieval_case_pool_v1.json")
    gold_payload = read_json(freeze / "retrieval_gold_candidates_v1.json")
    query_payload = read_json(args.queries.resolve())
    gold_by_id = {case["case_id"]: case for case in gold_payload["cases"]}
    queries_by_id = query_payload["cases"]
    cases = case_payload["cases"]

    completed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        print(f"evaluating {index}/{len(cases)} {case_id}", flush=True)
        try:
            completed.append(
                evaluate_case(case, gold_by_id[case_id], queries_by_id[case_id]["production"])
            )
        except Exception as exc:
            errors.append({
                "case_id": case_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            print(f"  failed: {type(exc).__name__}: {exc}", flush=True)

        partial = build_report(
            completed,
            errors,
            total_cases=len(cases),
            status="running",
        )
        write_json(args.output_json, partial)
        if args.delay > 0:
            time.sleep(args.delay)

    status = "complete" if not errors else "complete_with_errors"
    report = build_report(completed, errors, total_cases=len(cases), status=status)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
