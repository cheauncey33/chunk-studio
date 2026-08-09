"""Evaluate dense, RRF, and reranked retrieval against ground-truth v2."""
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
from evaluate_hybrid_retrieval_ablation import (  # noqa: E402
    CaseRetrievalCache,
    _disable_reranker,
    summarize_hit,
)


DEFAULT_GROUND_TRUTH = ROOT / "evaluation" / "test_set.json"
DEFAULT_QUERIES: Path | None = None
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "retrieval_v2_multi_metric_2026-07-15.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "retrieval_v2_multi_metric_2026-07-15.md"
TOP_K_VALUES = (1, 3, 5, 10, 20, 40)
MAX_TOP_K = max(TOP_K_VALUES)
POLICIES = ("dense_original", "hybrid_rrf", "hybrid_rerank")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_production_query(case: dict[str, Any]) -> str:
    project = case["detection_project"]
    context = project.get("sample_context") or {}
    parts = [
        str(context.get(key) or "").strip()
        for key in ("model", "rated_capacity", "rated_voltage", "sample_name")
    ]
    parts.extend([
        str(project.get("project_name") or "").strip(),
        str((project.get("reported_requirement") or {}).get("text") or "").strip(),
    ])
    return " ".join(dict.fromkeys(part for part in parts if part))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def score_ranking(hits: list[dict[str, Any]], ground_truth_case: dict[str, Any]) -> dict[str, Any]:
    ranks_by_hash: dict[str, int] = {}
    for rank, hit in enumerate(hits, start=1):
        text_hash = chunk_text_sha256(hit.get("text"))
        ranks_by_hash.setdefault(text_hash, rank)

    relevant = {
        item["locator"]["text_sha256"]: item
        for item in ground_truth_case["relevant_evidence"]
    }
    matched_relevant = [
        {
            "text_sha256": text_hash,
            "rank": ranks_by_hash[text_hash],
            "legacy_label": item["legacy_label"],
        }
        for text_hash, item in relevant.items()
        if text_hash in ranks_by_hash
    ]
    matched_relevant.sort(key=lambda item: item["rank"])

    group_results = []
    for group in ground_truth_case["required_evidence_groups"]:
        matched_alternatives = [
            {
                "text_sha256": alternative["locator"]["text_sha256"],
                "rank": ranks_by_hash[alternative["locator"]["text_sha256"]],
            }
            for alternative in group["alternatives"]
            if alternative["locator"]["text_sha256"] in ranks_by_hash
        ]
        matched_alternatives.sort(key=lambda item: item["rank"])
        group_results.append({
            "group_id": group["group_id"],
            "best_rank": matched_alternatives[0]["rank"] if matched_alternatives else None,
            "matched_alternatives": matched_alternatives,
        })

    return {
        "best_relevant_rank": matched_relevant[0]["rank"] if matched_relevant else None,
        "matched_relevant": matched_relevant,
        "required_groups": group_results,
        "top_hits": [summarize_hit(rank, hit) for rank, hit in enumerate(hits[:10], start=1)],
    }


def metrics_at_k(ranking: dict[str, Any], top_k: int) -> dict[str, int | bool]:
    groups = ranking["required_groups"]
    recalled_groups = sum(
        group["best_rank"] is not None and group["best_rank"] <= top_k
        for group in groups
    )
    best_relevant_rank = ranking["best_relevant_rank"]
    return {
        "lenient_relevance_hit": best_relevant_rank is not None and best_relevant_rank <= top_k,
        "recalled_groups": recalled_groups,
        "required_groups": len(groups),
        "strict_complete_hit": recalled_groups == len(groups),
    }


def evaluate_case(ground_truth_case: dict[str, Any], query: str) -> dict[str, Any]:
    cache = CaseRetrievalCache()

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
        "case_id": ground_truth_case["case_id"],
        "dataset_split": ground_truth_case["dataset_split"],
        "retrieval_class": ground_truth_case["retrieval_class"],
        "review_status": ground_truth_case["review"]["status"],
        "relevant_evidence_count": len(ground_truth_case["relevant_evidence"]),
        "required_group_count": len(ground_truth_case["required_evidence_groups"]),
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
            "dense_original": score_ranking(dense_result["hits"], ground_truth_case),
            "hybrid_rrf": score_ranking(rrf_result["hits"], ground_truth_case),
            "hybrid_rerank": score_ranking(rerank_result["hits"], ground_truth_case),
        },
    }


def aggregate_policy(cases: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        lenient_hits = strict_hits = recalled_groups = required_groups = 0
        lenient_reciprocal_rank = 0.0
        for case in cases:
            ranking = case["rankings"][policy]
            at_k = metrics_at_k(ranking, top_k)
            lenient_hits += int(at_k["lenient_relevance_hit"])
            strict_hits += int(at_k["strict_complete_hit"])
            recalled_groups += int(at_k["recalled_groups"])
            required_groups += int(at_k["required_groups"])
            best_rank = ranking["best_relevant_rank"]
            if best_rank is not None and best_rank <= top_k:
                lenient_reciprocal_rank += 1 / best_rank
        case_count = len(cases)
        metrics[str(top_k)] = {
            "cases": case_count,
            "lenient_case_hits": lenient_hits,
            "lenient_case_recall": lenient_hits / case_count if case_count else None,
            "lenient_mrr": lenient_reciprocal_rank / case_count if case_count else None,
            "required_groups": required_groups,
            "recalled_groups": recalled_groups,
            "evidence_group_recall": recalled_groups / required_groups if required_groups else None,
            "strict_complete_hits": strict_hits,
            "strict_complete_recall": strict_hits / case_count if case_count else None,
        }
    return metrics


def compare_policies(
    cases: list[dict[str, Any]],
    baseline: str,
    treatment: str,
) -> dict[str, Any]:
    comparison = {}
    for top_k in TOP_K_VALUES:
        wins = losses = both_hit = both_miss = 0
        for case in cases:
            baseline_hit = bool(metrics_at_k(case["rankings"][baseline], top_k)["strict_complete_hit"])
            treatment_hit = bool(metrics_at_k(case["rankings"][treatment], top_k)["strict_complete_hit"])
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
            "net_strict_hits": wins - losses,
        }
    return comparison


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["retrieval_class"]].append(case)
    return {
        "by_policy": {policy: aggregate_policy(cases, policy) for policy in POLICIES},
        "by_retrieval_class": {
            retrieval_class: {
                policy: aggregate_policy(group, policy) for policy in POLICIES
            }
            for retrieval_class, group in sorted(grouped.items())
        },
        "rerank_vs_rrf_strict": compare_policies(cases, "hybrid_rrf", "hybrid_rerank"),
        "degraded_cases": sum(
            bool(case["degraded"]["initial_recall"] or case["degraded"]["rerank"])
            for case in cases
        ),
        "average_timing_seconds": {
            key: sum(case["timing_seconds"][key] for case in cases) / len(cases)
            for key in cases[0]["timing_seconds"]
        } if cases else {},
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
        "ground_truth": str(DEFAULT_GROUND_TRUTH.relative_to(ROOT)),
        "gold_status": "human_reviewed_test_set",
        "metric_semantics": {
            "lenient_relevance": "Any direct or supporting chunk in relevant_evidence is recalled.",
            "evidence_group": "Each group is recalled when any OR-equivalent chunk is recalled.",
            "strict_answer": "Every AND-required evidence group is recalled.",
        },
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
        "# Test Set Retrieval Evaluation",
        "",
        f"Status: `{report['status']}`",
        f"Cases: `{report['progress']['completed_cases']}/{report['progress']['total_cases']}`; errors: `{report['progress']['error_cases']}`",
        "",
        "> 33 human-reviewed, answerable cases. Context-required and deterministic-prefilter cases are excluded.",
        "",
        "## Overall",
        "",
        "| Policy | K | Lenient relevance | Evidence groups | Strict complete | Lenient MRR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        for top_k in TOP_K_VALUES:
            item = report["summary"]["by_policy"][policy][str(top_k)]
            lines.append(
                f"| {policy} | {top_k} | {fmt_rate(item['lenient_case_recall'])} "
                f"({item['lenient_case_hits']}/{item['cases']}) | "
                f"{fmt_rate(item['evidence_group_recall'])} "
                f"({item['recalled_groups']}/{item['required_groups']}) | "
                f"{fmt_rate(item['strict_complete_recall'])} "
                f"({item['strict_complete_hits']}/{item['cases']}) | "
                f"{fmt_rate(item['lenient_mrr'])} |"
            )

    lines.extend([
        "",
        "## By Retrieval Class at K=10",
        "",
        "| Class | Policy | Lenient | Groups | Strict |",
        "|---|---|---:|---:|---:|",
    ])
    for retrieval_class, policies in report["summary"]["by_retrieval_class"].items():
        for policy in POLICIES:
            item = policies[policy]["10"]
            lines.append(
                f"| {retrieval_class} | {policy} | {fmt_rate(item['lenient_case_recall'])} | "
                f"{fmt_rate(item['evidence_group_recall'])} | {fmt_rate(item['strict_complete_recall'])} |"
            )
    lines.extend([
        "",
        "## Rerank vs RRF Strict Complete",
        "",
        "| K | Wins | Losses | Net strict hits | Both hit | Both miss |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for top_k in TOP_K_VALUES:
        item = report["summary"]["rerank_vs_rrf_strict"][str(top_k)]
        lines.append(
            f"| {top_k} | {item['wins']} | {item['losses']} | {item['net_strict_hits']} | "
            f"{item['both_hit']} | {item['both_miss']} |"
        )

    lines.extend(["", "## Strict Misses", ""])
    for top_k in (10, 40):
        misses = []
        for case in report["cases"]:
            missing_groups = [
                group["group_id"]
                for group in case["rankings"]["hybrid_rerank"]["required_groups"]
                if group["best_rank"] is None or group["best_rank"] > top_k
            ]
            if missing_groups:
                misses.append(f"{case['case_id']} ({', '.join(missing_groups)})")
        lines.append(f"- Top-{top_k}: `{len(misses)}` cases: " + "; ".join(misses))

    degraded = [
        case
        for case in report["cases"]
        if case["degraded"]["initial_recall"] or case["degraded"]["rerank"]
    ]
    timing = report["summary"]["average_timing_seconds"]
    lines.extend([
        "",
        "## Execution",
        "",
        f"- Degraded cases: `{len(degraded)}`"
        + (": " + "; ".join(
            f"{case['case_id']}={case['degraded']}" for case in degraded
        ) if degraded else ""),
        f"- Initial hybrid recall average: `{timing.get('initial_hybrid_recall', 0):.3f}s`",
        f"- Reranker average with cached recall: `{timing.get('reranker_only_cached_recall', 0):.3f}s`",
        f"- Dense search average with reused embedding: `{timing.get('dense_search_reused_embedding', 0):.3f}s`",
    ])
    return "\n".join(lines) + "\n"


def fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES,
                        help="Optional historical query file; omit to build current production queries from test_set.")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    ground_truth = read_json(args.ground_truth.resolve())
    eligible = [
        case
        for case in ground_truth["cases"]
        if case["answerability_status"] == "answerable_candidate"
    ]
    queries_by_id = read_json(args.queries.resolve())["cases"] if args.queries else {}

    completed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if args.resume and args.output_json.is_file():
        previous = read_json(args.output_json)
        completed = previous.get("cases", [])
        errors = previous.get("errors", [])
    completed_ids = {case["case_id"] for case in completed}
    errors = [error for error in errors if error.get("case_id") not in completed_ids]

    for index, case in enumerate(eligible, start=1):
        case_id = case["case_id"]
        if case_id in completed_ids:
            print(f"reusing {index}/{len(eligible)} {case_id}", flush=True)
            continue
        print(f"evaluating {index}/{len(eligible)} {case_id}", flush=True)
        try:
            query = (
                queries_by_id[case_id]["production"]
                if case_id in queries_by_id
                else build_production_query(case)
            )
            completed.append(evaluate_case(case, query))
            completed_ids.add(case_id)
            errors = [error for error in errors if error.get("case_id") != case_id]
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
            total_cases=len(eligible),
            status="running",
        )
        write_json(args.output_json, partial)
        if args.delay > 0:
            time.sleep(args.delay)

    completed.sort(key=lambda item: next(
        index for index, case in enumerate(eligible) if case["case_id"] == item["case_id"]
    ))
    status = "complete" if len(completed) == len(eligible) and not errors else "complete_with_errors"
    report = build_report(completed, errors, total_cases=len(eligible), status=status)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(json.dumps(report["summary"]["by_policy"], ensure_ascii=False, indent=2))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
