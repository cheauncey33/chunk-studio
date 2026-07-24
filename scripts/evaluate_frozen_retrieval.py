"""Evaluate current retrieval recall against a frozen retrieval gold snapshot."""
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

from app import embeddings, retrieval_experiments  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402


DEFAULT_FREEZE = ROOT / "evaluation" / "frozen" / "retrieval_eval_v1_candidate_2026-07-13"
DEFAULT_QUERIES = DEFAULT_FREEZE / "reports" / "retrieval_queries_40_v1.json"
DEFAULT_REPORTS = ROOT / "backend" / "data" / "reports"
DEFAULT_JSON = DEFAULT_REPORTS / "baseline_current_retrieval.json"
DEFAULT_MD = DEFAULT_REPORTS / "baseline_current_retrieval.md"
CONTENT_TYPES = ("table", "section")
DEFAULT_ROUTE_TOP_K = 20
DEFAULT_FINAL_PER_TYPE = 10
RRF_K = 60
TABLE_AWARE_SCORE_SCALE = 0.006


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def route_sets_for(queries: dict[str, str]) -> dict[str, dict[str, str]]:
    all_routes = {key: value for key, value in queries.items() if str(value).strip()}
    return {
        "all_routes": all_routes,
        "no_table_target": {
            key: value for key, value in all_routes.items() if key != "table_target"
        },
        "production_only": {
            key: value for key, value in all_routes.items() if key == "production"
        },
    }


def retrieve_candidates(
    case: dict[str, Any],
    queries: dict[str, str],
    *,
    route_top_k: int,
    final_per_type: int,
    preserve_routes: dict[str, int],
    table_aware_rerank: bool,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for content_type in CONTENT_TYPES:
        candidates.extend(
            retrieve_content_type(
                case,
                queries,
                content_type=content_type,
                route_top_k=route_top_k,
                final_per_type=final_per_type,
                preserve_routes=preserve_routes,
                table_aware_rerank=table_aware_rerank,
            )
        )
    candidates.sort(
        key=lambda item: (item["rank_score"], max(item["route_scores"].values())),
        reverse=True,
    )
    if aggregate_continuation_tables:
        candidates = retrieval_experiments.aggregate_continuation_tables(
            candidates,
            complete_groups=retrieval_experiments.db_table_group_members,
        )
    if expand_references:
        candidates = retrieval_experiments.expand_table_references(
            candidates,
            fetch_table_chunks=retrieval_experiments.db_fetch_table_chunks,
        )
    for candidate in candidates:
        # Candidates added by experiment transforms have no route provenance.
        candidate.setdefault("route_ranks", {})
        candidate.setdefault("route_scores", {"experiment": 0.0})
        candidate.setdefault("rrf_score", 0.0)
        candidate.setdefault("rank_score", 0.0)
        candidate.setdefault("type_rank", None)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates


def retrieve_content_type(
    case: dict[str, Any],
    queries: dict[str, str],
    *,
    content_type: str,
    route_top_k: int,
    final_per_type: int,
    preserve_routes: dict[str, int],
    table_aware_rerank: bool,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, query in queries.items():
        result = embeddings.vector_search(query, top_k=route_top_k, content_type=content_type)
        for rank, hit in enumerate(result["hits"], start=1):
            candidate = merged.setdefault(
                hit["chunk_id"],
                {
                    **hit,
                    "content_type": content_type,
                    "route_ranks": {},
                    "route_scores": {},
                    "rrf_score": 0.0,
                },
            )
            candidate["route_ranks"][route] = rank
            candidate["route_scores"][route] = hit["score"]
            candidate["rrf_score"] += 1 / (RRF_K + rank)

    for candidate in merged.values():
        candidate["table_aware_score"] = (
            table_aware_score(case, candidate) if table_aware_rerank else 0.0
        )
        candidate["rank_score"] = candidate["rrf_score"] + candidate["table_aware_score"]

    ranked = select_ranked_candidates(
        list(merged.values()),
        final_per_type=final_per_type,
        preserve_routes=preserve_routes,
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate["type_rank"] = rank
    return ranked


def select_ranked_candidates(
    candidates: list[dict[str, Any]],
    *,
    final_per_type: int,
    preserve_routes: dict[str, int],
) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda item: (item["rank_score"], max(item["route_scores"].values())),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    for route, keep_count in preserve_routes.items():
        if keep_count <= 0:
            continue
        route_hits = sorted(
            (
                item for item in candidates
                if route in item["route_ranks"] and item["route_ranks"][route] <= keep_count
            ),
            key=lambda item: item["route_ranks"][route],
        )
        for item in route_hits:
            if item["chunk_id"] in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item["chunk_id"])

    for item in ranked:
        if len(selected) >= final_per_type:
            break
        if item["chunk_id"] in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item["chunk_id"])

    return sorted(
        selected,
        key=lambda item: (item["rank_score"], max(item["route_scores"].values())),
        reverse=True,
    )


def table_aware_score(case: dict[str, Any], candidate: dict[str, Any]) -> float:
    metadata = candidate["business_metadata"]
    text = " ".join(
        str(part or "")
        for part in (
            metadata.get("standard_no"),
            metadata.get("table_no"),
            metadata.get("table_title"),
            metadata.get("section"),
            metadata.get("section_title"),
            candidate.get("text"),
        )
    )
    score = 0
    expected_type = str(case.get("expected_evidence_type") or "")
    retrieval_class = str(case.get("retrieval_class") or "")
    requirement = str((case.get("reported_requirement") or {}).get("text") or "")
    test_name = str((case.get("test_item") or {}).get("project_name") or "")
    sample = case.get("sample_context") or {}

    if candidate["content_type"] == "table" and expected_type in {"table", "mixed"}:
        score += 1
    if candidate["content_type"] == "section" and expected_type in {"section", "mixed"}:
        score += 1

    for value in sample.values():
        for token in normalized_tokens(str(value)):
            if token and token in text:
                score += 1

    if "RL" in str(sample.get("model") or "").upper() and "立体卷铁心" in text:
        score += 3
    if "NX2" in str(sample.get("model") or "").upper() and (
        "能效" in text or "Q/GDW" in text or "12126.4" in text
    ):
        score += 2
    if any(term in requirement + test_name for term in ("空载损耗", "负载损耗", "总损耗")):
        if any(term in text for term in ("性能参数", "能效等级", "负载损耗", "空载损耗")):
            score += 2
    if "总损耗" in requirement and any(term in text for term in ("温升的试验方法", "总损耗", "空载损耗", "负载损耗")):
        score += 3
    if any(term in requirement + test_name for term in ("外施", "全波", "冲击", "试验电压")):
        if "绕组的试验电压水平" in text:
            score += 5
        if "中性点端" in text:
            score -= 2
    if retrieval_class == "numeric_parameter_table" and "性能参数" in text:
        score += 1
    if retrieval_class == "insulation_voltage" and "试验电压" in text:
        score += 2

    return score * TABLE_AWARE_SCORE_SCALE


def normalized_tokens(value: str) -> list[str]:
    tokens = []
    for raw in value.replace("/", " ").replace("-", " ").replace("_", " ").split():
        token = raw.strip(" ,，()（）")
        if len(token) >= 2:
            tokens.append(token)
    return tokens


def summarize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate["business_metadata"]
    return {
        "rank": candidate["rank"],
        "type_rank": candidate["type_rank"],
        "chunk_id": candidate["chunk_id"],
        "content_type": candidate["content_type"],
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "page": candidate.get("page"),
        "rrf_score": candidate["rrf_score"],
        "table_aware_score": candidate.get("table_aware_score", 0.0),
        "rank_score": candidate.get("rank_score", candidate["rrf_score"]),
        "route_ranks": candidate["route_ranks"],
        "text_sha256": chunk_text_sha256(candidate.get("text")),
        **(
            {"evidence_unit_members": len((candidate.get("evidence_unit") or {}).get("members") or [])}
            if candidate.get("evidence_unit")
            else {}
        ),
        **({"added_by": candidate["added_by"]} if candidate.get("added_by") else {}),
    }


def gold_items(case: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for index, evidence in enumerate(case.get("selected_evidence", []), start=1):
        locator = evidence["locator"]
        items.append(
            {
                "gold_index": index,
                "label": evidence["label"],
                "content_type": locator["content_type"],
                "standard_no": locator["standard_no"],
                "section": locator.get("section"),
                "table_no": locator.get("table_no"),
                "text_sha256": locator["text_sha256"],
            }
        )
    return items


def candidate_text_hashes(candidate: dict[str, Any]) -> list[str]:
    """All gold-matchable hashes for a candidate.

    Aggregated evidence units match through any member fragment's original
    text; plain candidates match through their own text.
    """
    members = (candidate.get("evidence_unit") or {}).get("members") or []
    if members:
        return [chunk_text_sha256(member.get("text")) for member in members]
    return [chunk_text_sha256(candidate.get("text"))]


def evaluate_case(
    case: dict[str, Any],
    gold_case: dict[str, Any],
    queries: dict[str, str],
    *,
    route_top_k: int,
    final_per_type: int,
    preserve_routes: dict[str, int],
    table_aware_rerank: bool,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
) -> list[dict[str, Any]]:
    results = []
    gold = gold_items(gold_case)
    for route_set, route_queries in route_sets_for(queries).items():
        candidates = retrieve_candidates(
            case,
            route_queries,
            route_top_k=route_top_k,
            final_per_type=final_per_type,
            preserve_routes=preserve_routes,
            table_aware_rerank=table_aware_rerank,
            aggregate_continuation_tables=aggregate_continuation_tables,
            expand_references=expand_references,
        )
        candidates_by_hash: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            for text_hash in candidate_text_hashes(candidate):
                candidates_by_hash.setdefault(text_hash, candidate)
        recalled = []
        missed = []
        for item in gold:
            candidate = candidates_by_hash.get(item["text_sha256"])
            if candidate is None:
                missed.append(item)
            else:
                recalled.append(
                    {
                        **item,
                        "rank": candidate["rank"],
                        "type_rank": candidate["type_rank"],
                        "route_ranks": candidate["route_ranks"],
                    }
                )
        direct_recalled = [item for item in recalled if item["label"] == "direct_candidate"]
        direct_missed = [item for item in missed if item["label"] == "direct_candidate"]
        results.append(
            {
                "case_id": case["case_id"],
                "report_id": case["report_id"],
                "dataset_split": case["dataset_split"],
                "retrieval_class": case["retrieval_class"],
                "expected_evidence_type": case["expected_evidence_type"],
                "test_item": case["test_item"],
                "reported_requirement": case["reported_requirement"],
                "route_set": route_set,
                "query_routes": list(route_queries),
                "gold_counts": count_by_label(gold),
                "recalled_counts": count_by_label(recalled),
                "direct_gold_available": any(item["label"] == "direct_candidate" for item in gold),
                "direct_recalled": bool(direct_recalled),
                "direct_best_rank": min((item["rank"] for item in direct_recalled), default=None),
                "direct_missed": bool(direct_missed),
                "recalled_gold": recalled,
                "missed_gold": missed,
                "candidate_count": len(candidates),
                "candidates": [summarize_candidate(candidate) for candidate in candidates],
            }
        )
    return results


def count_by_label(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = item["label"]
        counts[label] = counts.get(label, 0) + 1
    return counts


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_route[result["route_set"]].append(result)

    return {
        route_set: {
            "overall": aggregate_group(route_results),
            "by_split": aggregate_breakdown(route_results, "dataset_split"),
            "by_retrieval_class": aggregate_breakdown(route_results, "retrieval_class"),
            "by_expected_evidence_type": aggregate_breakdown(route_results, "expected_evidence_type"),
        }
        for route_set, route_results in sorted(by_route.items())
    }


def aggregate_breakdown(results: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result[key])].append(result)
    return {group_key: aggregate_group(group_results) for group_key, group_results in sorted(grouped.items())}


def aggregate_group(results: list[dict[str, Any]]) -> dict[str, Any]:
    cases = len(results)
    direct_gold_cases = sum(1 for result in results if result["direct_gold_available"])
    direct_recalled_cases = sum(1 for result in results if result["direct_recalled"])
    direct_gold_evidence = sum(result["gold_counts"].get("direct_candidate", 0) for result in results)
    direct_recalled_evidence = sum(result["recalled_counts"].get("direct_candidate", 0) for result in results)
    all_gold_evidence = sum(sum(result["gold_counts"].values()) for result in results)
    all_recalled_evidence = sum(sum(result["recalled_counts"].values()) for result in results)
    reciprocal_ranks = [
        1 / result["direct_best_rank"]
        for result in results
        if result["direct_gold_available"] and result["direct_best_rank"]
    ]
    direct_mrr_denominator = direct_gold_cases or 1
    return {
        "cases": cases,
        "direct_gold_cases": direct_gold_cases,
        "direct_recalled_cases": direct_recalled_cases,
        "direct_case_recall": direct_recalled_cases / direct_gold_cases if direct_gold_cases else None,
        "direct_gold_evidence": direct_gold_evidence,
        "direct_recalled_evidence": direct_recalled_evidence,
        "direct_evidence_recall": direct_recalled_evidence / direct_gold_evidence if direct_gold_evidence else None,
        "all_gold_evidence": all_gold_evidence,
        "all_recalled_evidence": all_recalled_evidence,
        "all_evidence_recall": all_recalled_evidence / all_gold_evidence if all_gold_evidence else None,
        "direct_case_mrr": sum(reciprocal_ranks) / direct_mrr_denominator,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Frozen Retrieval Baseline",
        "",
        f"Freeze: `{report['freeze_dir']}`",
        f"Generated at: `{report['generated_at']}`",
        f"Embedding model: `{report['embedding_model']}`",
        "",
        "This report measures retrieval recall against the frozen candidate gold. It does not judge numerical correctness.",
        "",
        "## Summary",
        "",
        "| Route set | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | Direct MRR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for route_set, data in report["summary"].items():
        overall = data["overall"]
        lines.append(
            "| "
            + " | ".join(
                [
                    route_set,
                    f"{overall['direct_recalled_cases']}/{overall['direct_gold_cases']}",
                    fmt_rate(overall["direct_case_recall"]),
                    fmt_rate(overall["direct_evidence_recall"]),
                    fmt_rate(overall["all_evidence_recall"]),
                    fmt_rate(overall["direct_case_mrr"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## all_routes By Split", ""])
    lines.extend(render_breakdown(report["summary"]["all_routes"]["by_split"]))
    lines.extend(["", "## all_routes By Retrieval Class", ""])
    lines.extend(render_breakdown(report["summary"]["all_routes"]["by_retrieval_class"]))
    lines.extend(["", "## all_routes Direct Misses", ""])
    misses = [
        result for result in report["cases"]
        if result["route_set"] == "all_routes" and result["direct_gold_available"] and not result["direct_recalled"]
    ]
    if not misses:
        lines.append("No direct-gold case misses.")
    else:
        lines.append("| Case | Split | Class | Evidence type | Requirement | Query routes |")
        lines.append("|---|---|---|---|---|---|")
        for result in misses:
            requirement = str(result["reported_requirement"].get("text") or "").replace("|", "\\|")
            lines.append(
                "| "
                + " | ".join(
                    [
                        result["case_id"],
                        result["dataset_split"],
                        result["retrieval_class"],
                        result["expected_evidence_type"],
                        requirement,
                        ", ".join(result["query_routes"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    return "\n".join(lines)


def render_breakdown(items: dict[str, Any]) -> list[str]:
    lines = [
        "| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |",
        "|---|---:|---:|---:|---:|",
    ]
    for group, metrics in items.items():
        lines.append(
            "| "
            + " | ".join(
                [
                    group,
                    f"{metrics['direct_recalled_cases']}/{metrics['direct_gold_cases']}",
                    fmt_rate(metrics["direct_case_recall"]),
                    fmt_rate(metrics["direct_evidence_recall"]),
                    fmt_rate(metrics["all_evidence_recall"]),
                ]
            )
            + " |"
        )
    return lines


def fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def parse_preserve_routes(values: list[str]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for value in values:
        if ":" not in value:
            raise ValueError(f"preserve route must use ROUTE:COUNT, got {value!r}")
        route, count = value.split(":", 1)
        route = route.strip()
        if not route:
            raise ValueError("preserve route name must not be blank")
        parsed[route] = int(count)
    return parsed


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    freeze = args.freeze.resolve()
    case_pool = read_json(freeze / "retrieval_case_pool_v1.json")
    gold_payload = read_json(freeze / "retrieval_gold_candidates_v1.json")
    query_path = args.queries.resolve()
    query_payload = read_json(query_path)
    cases_by_id = {case["case_id"]: case for case in case_pool["cases"]}
    gold_by_id = {case["case_id"]: case for case in gold_payload["cases"]}
    queries_by_id = query_payload["cases"]
    preserve_routes = parse_preserve_routes(args.preserve_route)

    details = []
    for index, case_id in enumerate(cases_by_id, start=1):
        print(f"evaluating {index}/{len(cases_by_id)} {case_id}", flush=True)
        details.extend(
            evaluate_case(
                cases_by_id[case_id],
                gold_by_id[case_id],
                queries_by_id[case_id],
                route_top_k=args.route_top_k,
                final_per_type=args.final_per_type,
                preserve_routes=preserve_routes,
                table_aware_rerank=args.table_aware_rerank,
                aggregate_continuation_tables=args.aggregate_continuation_tables,
                expand_references=args.expand_references,
            )
        )

    return {
        "version": 1,
        "freeze_dir": str(freeze.relative_to(ROOT)),
        "queries": str(query_path.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "embedding_model": embeddings.DEFAULT_MODEL,
        "embedding_dimension": embeddings.DEFAULT_DIMENSION,
        "retrieval_policy": {
            "route_top_k": args.route_top_k,
            "content_types": list(CONTENT_TYPES),
            "final_per_type": args.final_per_type,
            "rrf_k": RRF_K,
            "preserve_routes": preserve_routes,
            "table_aware_rerank": args.table_aware_rerank,
            "table_aware_score_scale": TABLE_AWARE_SCORE_SCALE,
            "aggregate_continuation_tables": args.aggregate_continuation_tables,
            "expand_references": args.expand_references,
            "route_sets": ["all_routes", "no_table_target", "production_only"],
        },
        "summary": aggregate(details),
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--route-top-k", type=int, default=DEFAULT_ROUTE_TOP_K)
    parser.add_argument("--final-per-type", type=int, default=DEFAULT_FINAL_PER_TYPE)
    parser.add_argument(
        "--preserve-route",
        action="append",
        default=[],
        metavar="ROUTE:COUNT",
        help="Force-include top COUNT hits from a route before filling the per-type candidate list.",
    )
    parser.add_argument(
        "--table-aware-rerank",
        action="store_true",
        help="Apply a lightweight case-aware metadata/title/text boost before candidate cutoff.",
    )
    parser.add_argument(
        "--aggregate-continuation-tables",
        action="store_true",
        help="Ablation (experiment B): merge continued-table fragments into one "
             "evidence unit and pull missing sibling fragments from the database.",
    )
    parser.add_argument(
        "--expand-references",
        action="store_true",
        help="Ablation (experiment C): when a retrieved section references 表 N, "
             "append the corresponding table chunks as extra candidates.",
    )
    args = parser.parse_args()

    report = evaluate(args)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
