"""Evaluate production hybrid_search delivery against frozen retrieval gold.

Uses frozen planner queries (not live planner) with current production
delivery quotas: final_table=8, final_section=6, route_top_k=30,
special_route_reserve=3, expand_references/continuation off by default.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import embeddings, retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402

DEFAULT_FREEZE = ROOT / "evaluation" / "frozen" / "retrieval_eval_v1_candidate_2026-07-13"
DEFAULT_QUERIES = DEFAULT_FREEZE / "reports" / "retrieval_queries_40_v1.json"
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "production_retrieval_eval.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "production_retrieval_eval.md"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def route_sets_for(
    queries: dict[str, str],
    *,
    enabled: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    all_routes = {key: value for key, value in queries.items() if str(value).strip()}
    mapping = {
        "all_routes": all_routes,
        "no_table_target": {
            key: value for key, value in all_routes.items() if key != "table_target"
        },
        "production_only": {
            key: value for key, value in all_routes.items() if key == "production"
        },
    }
    if enabled is None:
        return mapping
    return {key: value for key, value in mapping.items() if key in enabled}


def candidate_hashes(hit: dict[str, Any]) -> list[str]:
    members = (hit.get("evidence_unit") or {}).get("members") or []
    if members:
        return [chunk_text_sha256(member.get("text")) for member in members]
    return [chunk_text_sha256(hit.get("text"))]


def direct_gold_items(gold_case: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in gold_case.get("selected_evidence") or []:
        if item.get("label") != "direct_candidate":
            continue
        locator = item.get("locator") or {}
        text_hash = locator.get("text_sha256")
        if not text_hash:
            continue
        items.append({**item, "text_sha256": text_hash})
    return items


def evaluate_case(
    case: dict[str, Any],
    gold_case: dict[str, Any],
    queries: dict[str, str],
    *,
    route_top_k: int,
    final_table: int,
    final_section: int,
    special_route_reserve: int,
    expand_references: bool,
    aggregate_continuation_tables: bool,
    enabled_route_sets: set[str] | None = None,
) -> list[dict[str, Any]]:
    gold = direct_gold_items(gold_case)
    gold_hashes = {item["text_sha256"] for item in gold}
    results = []
    delivery_n = final_table + final_section
    for route_set, route_queries in route_sets_for(
        queries, enabled=enabled_route_sets
    ).items():
        if not route_queries:
            continue
        production = route_queries.get("production") or next(iter(route_queries.values()))
        result = retrieval.hybrid_search(
            production,
            top_k=max(10, delivery_n),
            route_top_k=route_top_k,
            query_routes=route_queries,
            special_route_reserve=special_route_reserve,
            final_table=final_table,
            final_section=final_section,
            expand_references=expand_references,
            aggregate_continuation_tables=aggregate_continuation_tables,
        )
        hits = list(result.get("hits") or [])
        ranks: dict[str, int] = {}
        type_counts = {"table": 0, "section": 0}
        for rank, hit in enumerate(hits, start=1):
            meta = hit.get("business_metadata") or {}
            ctype = str(meta.get("content_type") or hit.get("content_type") or "")
            if ctype in type_counts:
                type_counts[ctype] += 1
            for text_hash in candidate_hashes(hit):
                ranks.setdefault(text_hash, rank)
        matched = sorted(
            (
                {"text_sha256": text_hash, "rank": ranks[text_hash]}
                for text_hash in gold_hashes
                if text_hash in ranks
            ),
            key=lambda item: item["rank"],
        )
        best = matched[0]["rank"] if matched else None
        results.append(
            {
                "case_id": case["case_id"],
                "report_id": case["report_id"],
                "dataset_split": case["dataset_split"],
                "retrieval_class": case["retrieval_class"],
                "route_set": route_set,
                "query_routes": list(route_queries),
                "direct_gold_available": bool(gold),
                "direct_gold_count": len(gold),
                "direct_recalled": bool(matched),
                "direct_best_rank": best,
                "matched_direct": matched,
                "candidate_count": len(hits),
                "candidate_counts": type_counts,
                "final_table": result.get("final_table"),
                "final_section": result.get("final_section"),
                "degraded": result.get("degraded") or [],
            }
        )
    return results


def aggregate_group(results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in results if item["direct_gold_available"]]
    gold_cases = len(eligible)
    recalled = sum(1 for item in eligible if item["direct_recalled"])
    gold_evidence = sum(item["direct_gold_count"] for item in eligible)
    evidence_hits = sum(len(item["matched_direct"]) for item in eligible)
    mrr = (
        sum(1 / item["direct_best_rank"] for item in eligible if item["direct_best_rank"])
        / gold_cases
        if gold_cases
        else None
    )
    return {
        "cases": len(results),
        "direct_gold_cases": gold_cases,
        "direct_recalled_cases": recalled,
        "direct_case_recall": recalled / gold_cases if gold_cases else None,
        "direct_gold_evidence": gold_evidence,
        "direct_recalled_evidence": evidence_hits,
        "direct_evidence_recall": evidence_hits / gold_evidence if gold_evidence else None,
        "direct_case_mrr": mrr,
        "avg_candidate_count": (
            sum(item["candidate_count"] for item in results) / len(results) if results else 0
        ),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_route[item["route_set"]].append(item)
    output = {}
    for route_set, route_results in sorted(by_route.items()):
        by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in route_results:
            by_split[item["dataset_split"]].append(item)
        output[route_set] = {
            "overall": aggregate_group(route_results),
            "by_split": {
                split: aggregate_group(items) for split, items in sorted(by_split.items())
            },
        }
    return output


def render_markdown(report: dict[str, Any]) -> str:
    policy = report["retrieval_policy"]
    lines = [
        "# Production Retrieval Eval (frozen queries)",
        "",
        f"Generated at: `{report['generated_at']}`",
        "",
        "Delivery matches production hybrid_search quotas; queries are frozen "
        "(not live planner).",
        "",
        "## Policy",
        "",
        f"- route_top_k: {policy['route_top_k']}",
        f"- final_table / final_section: {policy['final_table']} / {policy['final_section']}",
        f"- special_route_reserve: {policy['special_route_reserve']}",
        f"- expand_references: {policy['expand_references']}",
        f"- aggregate_continuation_tables: {policy['aggregate_continuation_tables']}",
        "",
        "## Summary",
        "",
        "| Route set | Direct cases | Case recall | Evidence recall | MRR | Avg cands |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for route_set, block in report["summary"].items():
        o = block["overall"]
        lines.append(
            f"| {route_set} | {o['direct_recalled_cases']}/{o['direct_gold_cases']} | "
            f"{o['direct_case_recall']:.3f} | {o['direct_evidence_recall']:.3f} | "
            f"{o['direct_case_mrr']:.3f} | {o['avg_candidate_count']:.1f} |"
        )
    primary = "all_routes" if "all_routes" in report["summary"] else next(iter(report["summary"]))
    lines.extend(["", f"## {primary} By Split", ""])
    lines.append("| Split | Direct cases | Case recall | Evidence recall | MRR |")
    lines.append("|---|---:|---:|---:|---:|")
    for split, o in report["summary"][primary]["by_split"].items():
        lines.append(
            f"| {split} | {o['direct_recalled_cases']}/{o['direct_gold_cases']} | "
            f"{o['direct_case_recall']:.3f} | {o['direct_evidence_recall']:.3f} | "
            f"{o['direct_case_mrr']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--route-top-k", type=int, default=30)
    parser.add_argument("--final-table", type=int, default=8)
    parser.add_argument("--final-section", type=int, default=6)
    parser.add_argument("--special-route-reserve", type=int, default=3)
    parser.add_argument("--expand-references", action="store_true")
    parser.add_argument("--aggregate-continuation-tables", action="store_true")
    parser.add_argument("--only-split", choices=["development", "validation", "test"])
    parser.add_argument(
        "--route-sets",
        default="all_routes",
        help="Comma-separated route sets: all_routes,no_table_target,production_only",
    )
    args = parser.parse_args()
    enabled_route_sets = {
        item.strip() for item in str(args.route_sets).split(",") if item.strip()
    }

    freeze = args.freeze.resolve()
    case_pool = read_json(freeze / "retrieval_case_pool_v1.json")
    gold_payload = read_json(freeze / "retrieval_gold_candidates_v1.json")
    query_payload = read_json(args.queries.resolve())
    cases_by_id = {case["case_id"]: case for case in case_pool["cases"]}
    gold_by_id = {case["case_id"]: case for case in gold_payload["cases"]}
    queries_by_id = query_payload["cases"]

    case_ids = list(cases_by_id)
    if args.only_split:
        case_ids = [
            case_id
            for case_id in case_ids
            if cases_by_id[case_id]["dataset_split"] == args.only_split
        ]

    details: list[dict[str, Any]] = []
    for index, case_id in enumerate(case_ids, start=1):
        print(f"evaluating {index}/{len(case_ids)} {case_id}", flush=True)
        details.extend(
            evaluate_case(
                cases_by_id[case_id],
                gold_by_id[case_id],
                queries_by_id[case_id],
                route_top_k=args.route_top_k,
                final_table=args.final_table,
                final_section=args.final_section,
                special_route_reserve=args.special_route_reserve,
                expand_references=args.expand_references,
                aggregate_continuation_tables=args.aggregate_continuation_tables,
                enabled_route_sets=enabled_route_sets,
            )
        )

    report = {
        "version": 1,
        "freeze_dir": str(freeze.relative_to(ROOT)),
        "queries": str(args.queries.resolve().relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "embedding_model": embeddings.DEFAULT_MODEL,
        "embedding_dimension": embeddings.DEFAULT_DIMENSION,
        "retrieval_policy": {
            "backend": "hybrid_search",
            "route_top_k": args.route_top_k,
            "final_table": args.final_table,
            "final_section": args.final_section,
            "special_route_reserve": args.special_route_reserve,
            "expand_references": args.expand_references,
            "aggregate_continuation_tables": args.aggregate_continuation_tables,
            "route_sets": sorted(enabled_route_sets),
            "only_split": args.only_split,
        },
        "summary": aggregate(details),
        "cases": details,
    }
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(args.output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
