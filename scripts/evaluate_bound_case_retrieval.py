"""Evaluate every corpus-bound transformer case with production hybrid retrieval."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from evaluate_test_set import build_production_query  # noqa: E402


TOP_K_VALUES = (1, 3, 5, 8, 10, 15, 20, 30)
MAX_TOP_K = max(TOP_K_VALUES)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def query_key(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def compact_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "rank": rank,
        "chunk_id": hit.get("chunk_id"),
        "text_sha256": chunk_text_sha256(hit.get("text")),
        "content_type": metadata.get("content_type") or hit.get("content_type"),
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "table_no": metadata.get("table_no"),
        "rerank_score": hit.get("rerank_score"),
        "rrf_score": hit.get("rrf_score"),
    }


def retrieve_query(query: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = retrieval.hybrid_search(query, top_k=MAX_TOP_K)
    return {
        "query": query,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "candidate_count": result.get("candidate_count"),
        "retrieval_mode": result.get("retrieval_mode"),
        "rerank_model": result.get("rerank_model"),
        "degraded": result.get("degraded") or [],
        "hits": [compact_hit(rank, hit) for rank, hit in enumerate(result["hits"][:MAX_TOP_K], 1)],
    }


def score_case(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    ranks_by_hash: dict[str, int] = {}
    for hit in run["hits"]:
        ranks_by_hash.setdefault(str(hit["text_sha256"]), int(hit["rank"]))
    groups = []
    for group in case["required_evidence_groups"]:
        ranks = sorted(
            ranks_by_hash[alternative["locator"]["text_sha256"]]
            for alternative in group["alternatives"]
            if alternative["locator"]["text_sha256"] in ranks_by_hash
        )
        groups.append(
            {
                "group_id": group["group_id"],
                "best_rank": ranks[0] if ranks else None,
                "matched_ranks": ranks,
                "alternative_count": len(group["alternatives"]),
            }
        )
    return {
        "case_id": case["case_id"],
        "query_key": query_key(run["query"]),
        "asset_number": case.get("asset_number"),
        "phase": case.get("phase"),
        "domain": case.get("domain"),
        "family_id": case.get("family_id"),
        "project_name": case["detection_project"]["project_name"],
        "expected_status": case.get("expected_status"),
        "review_status": (case.get("review") or {}).get("status"),
        "required_groups": groups,
    }


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        recalled_groups = 0
        required_groups = 0
        strict_hits = 0
        any_hits = 0
        for case in cases:
            groups = case["required_groups"]
            group_hits = sum(
                group["best_rank"] is not None and group["best_rank"] <= top_k
                for group in groups
            )
            recalled_groups += group_hits
            required_groups += len(groups)
            strict_hits += int(group_hits == len(groups))
            any_hits += int(group_hits > 0)
        count = len(cases)
        output[str(top_k)] = {
            "cases": count,
            "strict_case_hits": strict_hits,
            "strict_case_recall": strict_hits / count if count else None,
            "any_group_case_hits": any_hits,
            "any_group_case_recall": any_hits / count if count else None,
            "recalled_groups": recalled_groups,
            "required_groups": required_groups,
            "evidence_group_recall": recalled_groups / required_groups if required_groups else None,
        }
    return output


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Corpus-bound transformer retrieval evaluation",
        "",
        f"Status: `{report['status']}`",
        f"Cases: `{report['progress']['scored_cases']}/{report['progress']['eligible_cases']}`; "
        f"unique queries: `{report['progress']['completed_queries']}/{report['progress']['unique_queries']}`; "
        f"query errors: `{report['progress']['query_errors']}`",
        "",
        "> These are AI corpus-verified chunk bindings, not human-approved retrieval gold. "
        "All source report cases remain report_judgment_scoreable=false.",
        "",
        "| K | Strict case recall | Evidence group recall | Any-group case recall |",
        "|---:|---:|---:|---:|",
    ]
    for top_k in TOP_K_VALUES:
        item = report["summary"][str(top_k)]
        lines.append(
            f"| {top_k} | {item['strict_case_recall']:.1%} "
            f"({item['strict_case_hits']}/{item['cases']}) | "
            f"{item['evidence_group_recall']:.1%} "
            f"({item['recalled_groups']}/{item['required_groups']}) | "
            f"{item['any_group_case_recall']:.1%} "
            f"({item['any_group_case_hits']}/{item['cases']}) |"
        )
    lines.extend(
        [
            "",
            "All 440 eligible cases currently have exactly one required evidence group, "
            "so the three recall columns are expected to be identical.",
            "",
            f"Retrieval mode: `{report['retrieval_policy']['mode']}`; "
            f"reranker: `{report['retrieval_policy']['rerank_model']}`; "
            f"maximum K: `{MAX_TOP_K}`.",
            f"Query rewrite fallback: `{report['execution']['degraded_queries']}/"
            f"{report['progress']['completed_queries']}` unique queries, affecting "
            f"`{report['execution']['degraded_cases']}/{report['progress']['scored_cases']}` cases; "
            f"reasons: `{report['execution']['degraded_reasons']}`. All queries were still reranked.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    *,
    input_path: Path,
    input_sha256: str,
    eligible_count: int,
    unique_query_count: int,
    query_runs: dict[str, dict[str, Any]],
    query_errors: dict[str, dict[str, str]],
    scored: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    successful_runs = list(query_runs.values())
    degraded_query_keys = {
        key for key, run in query_runs.items() if run.get("degraded")
    }
    degraded_reasons = Counter(
        reason
        for run in successful_runs
        for reason in run.get("degraded") or []
    )
    return {
        "version": 1,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth": {
            "source": input_path.name,
            "sha256": input_sha256,
            "status": "ai_corpus_verified_not_human_approved",
        },
        "metric_semantics": {
            "strict_case_recall": "Every AND-required group has one OR-equivalent chunk in Top-K.",
            "evidence_group_recall": "Fraction of required groups with one OR-equivalent chunk in Top-K.",
            "any_group_case_recall": "At least one required group is recalled for the case.",
        },
        "retrieval_policy": {
            "mode": "production hybrid_search with query planner and reranker",
            "top_k_values": list(TOP_K_VALUES),
            "route_top_k": retrieval.ROUTE_TOP_K,
            "candidates_per_type": retrieval.CANDIDATES_PER_TYPE,
            "query_rewrite_model": retrieval.QUERY_REWRITE_MODEL,
            "rerank_model": next(
                (run.get("rerank_model") for run in successful_runs if run.get("rerank_model")),
                retrieval.RERANK_MODEL,
            ),
        },
        "progress": {
            "eligible_cases": eligible_count,
            "scored_cases": len(scored),
            "unique_queries": unique_query_count,
            "completed_queries": len(query_runs),
            "query_errors": len(query_errors),
        },
        "execution": {
            "degraded_queries": len(degraded_query_keys),
            "degraded_cases": sum(
                case["query_key"] in degraded_query_keys for case in scored
            ),
            "degraded_reasons": dict(sorted(degraded_reasons.items())),
            "average_query_seconds": (
                sum(float(run["duration_seconds"]) for run in successful_runs) / len(successful_runs)
                if successful_runs
                else None
            ),
        },
        "summary": aggregate(scored),
        "query_errors": query_errors,
        "query_runs": query_runs,
        "cases": scored,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    input_path = args.ground_truth.resolve()
    input_bytes = input_path.read_bytes()
    payload = json.loads(input_bytes.decode("utf-8"))
    extension = payload.get("transformer_extension", payload)
    eligible = [
        case
        for case in extension["cases"]
        if case.get("required_evidence_groups")
        and (case.get("review") or {}).get("status") == "ai_corpus_verified"
    ]
    query_to_cases: dict[str, list[dict[str, Any]]] = {}
    for case in eligible:
        query_to_cases.setdefault(build_production_query(case), []).append(case)

    query_runs: dict[str, dict[str, Any]] = {}
    query_errors: dict[str, dict[str, str]] = {}
    if args.resume and args.output_json.is_file():
        previous = read_json(args.output_json)
        if previous.get("ground_truth", {}).get("sha256") != hashlib.sha256(input_bytes).hexdigest():
            raise ValueError("resume report ground-truth digest does not match the requested input")
        query_runs = previous.get("query_runs") or {}
        query_errors = previous.get("query_errors") or {}

    total = len(query_to_cases)
    for index, query in enumerate(query_to_cases, 1):
        key = query_key(query)
        if key in query_runs:
            print(f"reusing query {index}/{total} {key[:10]}", flush=True)
            continue
        print(f"retrieving query {index}/{total} {key[:10]}", flush=True)
        try:
            query_runs[key] = retrieve_query(query)
            query_errors.pop(key, None)
        except Exception as exc:
            query_errors[key] = {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "query": query,
            }
            print(f"  failed: {type(exc).__name__}: {exc}", flush=True)

        scored = [
            score_case(case, query_runs[query_key(query_text)])
            for query_text, bound_cases in query_to_cases.items()
            if query_key(query_text) in query_runs
            for case in bound_cases
        ]
        partial = build_report(
            input_path=input_path,
            input_sha256=hashlib.sha256(input_bytes).hexdigest(),
            eligible_count=len(eligible),
            unique_query_count=total,
            query_runs=query_runs,
            query_errors=query_errors,
            scored=scored,
            status="running",
        )
        write_json(args.output_json, partial)
        if args.delay > 0:
            time.sleep(args.delay)

    scored = [
        score_case(case, query_runs[query_key(query)])
        for query, bound_cases in query_to_cases.items()
        if query_key(query) in query_runs
        for case in bound_cases
    ]
    status = "complete" if len(scored) == len(eligible) and not query_errors else "complete_with_errors"
    report = build_report(
        input_path=input_path,
        input_sha256=hashlib.sha256(input_bytes).hexdigest(),
        eligible_count=len(eligible),
        unique_query_count=total,
        query_runs=query_runs,
        query_errors=query_errors,
        scored=scored,
        status=status,
    )
    write_json(args.output_json, report)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
