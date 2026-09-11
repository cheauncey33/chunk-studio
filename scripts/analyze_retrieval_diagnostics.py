"""Summarize per-gold retrieval diagnostics at evidence-group granularity."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from typing import Any


def _rank(value: str) -> int | None:
    return int(value) if value.strip() else None


def _minimum(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def read_groups(path: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            grouped[(row["case_id"], row["group_id"])].append(row)

    output = []
    for (case_id, group_id), alternatives in grouped.items():
        route_ranks: dict[str, list[int]] = defaultdict(list)
        for alternative in alternatives:
            for route, rank in json.loads(alternative["source_ranks"]).items():
                route_ranks[route].append(int(rank))
        fusion_rank = _minimum([_rank(item["fusion_rank"]) for item in alternatives])
        rerank_rank = _minimum([_rank(item["rerank_rank"]) for item in alternatives])
        if rerank_rank is not None and rerank_rank <= 8:
            outcome = "top8"
        elif rerank_rank is not None and rerank_rank <= 30:
            outcome = "reranked_9_30"
        elif fusion_rank is not None:
            outcome = "reranked_below_30"
        elif route_ranks:
            outcome = "dropped_before_fusion"
        else:
            outcome = "not_recalled"
        output.append(
            {
                "case_id": case_id,
                "group_id": group_id,
                "query": alternatives[0]["query"],
                "rewrite_status": alternatives[0]["rewrite_status"],
                "evidence_type": "+".join(sorted({row["evidence_type"] for row in alternatives})),
                "alternative_count": len(alternatives),
                "route_ranks": {route: min(ranks) for route, ranks in route_ranks.items()},
                "fusion_rank": fusion_rank,
                "rerank_rank": rerank_rank,
                "outcome": outcome,
            }
        )
    return output


def hit(rank: int | None, top_k: int) -> bool:
    return rank is not None and rank <= top_k


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def analyze(groups: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(groups)
    outcomes = Counter(group["outcome"] for group in groups)
    all_routes = sorted({route for group in groups for route in group["route_ranks"]})
    route_recall = {}
    for route in all_routes:
        eligible = (
            [group for group in groups if group["rewrite_status"] == "rewritten"]
            if ":semantic:" in route or ":keyword:" in route
            else groups
        )
        route_recall[route] = {
            "hits": sum(route in group["route_ranks"] for group in eligible),
            "groups": len(eligible),
        }
    for item in route_recall.values():
        item["recall"] = rate(item["hits"], item["groups"])

    movement = Counter()
    for group in groups:
        fusion8 = hit(group["fusion_rank"], 8)
        rerank8 = hit(group["rerank_rank"], 8)
        fusion30 = hit(group["fusion_rank"], 30)
        rerank30 = hit(group["rerank_rank"], 30)
        movement["promoted_into_top8"] += rerank8 and not fusion8
        movement["demoted_out_of_top8"] += fusion8 and not rerank8
        movement["promoted_into_top30"] += rerank30 and not fusion30
        movement["demoted_out_of_top30"] += fusion30 and not rerank30

    slices = {}
    for field in ("rewrite_status", "evidence_type"):
        rows = []
        for value in sorted({group[field] for group in groups}):
            subset = [group for group in groups if group[field] == value]
            rows.append(
                {
                    "value": value,
                    "groups": len(subset),
                    "source_recall": rate(sum(bool(group["route_ranks"]) for group in subset), len(subset)),
                    "fusion_pool_recall": rate(sum(group["fusion_rank"] is not None for group in subset), len(subset)),
                    "fusion_top8": rate(sum(hit(group["fusion_rank"], 8) for group in subset), len(subset)),
                    "rerank_top8": rate(sum(hit(group["rerank_rank"], 8) for group in subset), len(subset)),
                    "rerank_top30": rate(sum(hit(group["rerank_rank"], 30) for group in subset), len(subset)),
                }
            )
        slices[field] = rows

    failure_groups = [
        group
        for group in groups
        if group["outcome"] in {"dropped_before_fusion", "not_recalled"}
    ]
    failure_by_query_counter = Counter(
        (
            group["outcome"],
            group["query"],
            group["rewrite_status"],
            group["evidence_type"],
        )
        for group in failure_groups
    )
    failure_by_query = [
        {
            "outcome": outcome,
            "groups": groups_count,
            "query": query,
            "rewrite_status": rewrite_status,
            "evidence_type": evidence_type,
        }
        for (outcome, query, rewrite_status, evidence_type), groups_count in sorted(
            failure_by_query_counter.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    dropped_source_ranks = [
        rank
        for group in failure_groups
        if group["outcome"] == "dropped_before_fusion"
        for rank in group["route_ranks"].values()
    ]

    return {
        "groups": count,
        "stage_recall": {
            "any_source_top30": rate(sum(bool(group["route_ranks"]) for group in groups), count),
            "fusion_pool": rate(sum(group["fusion_rank"] is not None for group in groups), count),
            "fusion_top8": rate(sum(hit(group["fusion_rank"], 8) for group in groups), count),
            "fusion_top30": rate(sum(hit(group["fusion_rank"], 30) for group in groups), count),
            "rerank_top8": rate(sum(hit(group["rerank_rank"], 8) for group in groups), count),
            "rerank_top30": rate(sum(hit(group["rerank_rank"], 30) for group in groups), count),
        },
        "outcomes": dict(outcomes),
        "reranker_movement": dict(movement),
        "route_recall": route_recall,
        "slices": slices,
        "failure_by_query": failure_by_query,
        "dropped_source_rank_range": {
            "min": min(dropped_source_ranks) if dropped_source_ranks else None,
            "max": max(dropped_source_ranks) if dropped_source_ranks else None,
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    stage = result["stage_recall"]
    lines = [
        "# Retrieval stage diagnosis",
        "",
        f"Evidence groups: `{result['groups']}`. Alternatives inside each group are merged with OR semantics.",
        "",
        "## Stage recall",
        "",
        "| Stage | Recall |",
        "|---|---:|",
        f"| Any raw source Top-30 | {stage['any_source_top30']:.1%} |",
        f"| RRF fusion pool | {stage['fusion_pool']:.1%} |",
        f"| RRF fusion Top-8 | {stage['fusion_top8']:.1%} |",
        f"| RRF fusion Top-30 | {stage['fusion_top30']:.1%} |",
        f"| Reranker Top-8 | {stage['rerank_top8']:.1%} |",
        f"| Reranker Top-30 | {stage['rerank_top30']:.1%} |",
        "",
        "## Final outcome attribution",
        "",
        "| Outcome | Groups |",
        "|---|---:|",
    ]
    for outcome, count in sorted(result["outcomes"].items(), key=lambda item: -item[1]):
        lines.append(f"| {outcome} | {count} |")
    lines.extend(["", "## Reranker movement", "", "| Movement | Groups |", "|---|---:|"])
    for name, count in result["reranker_movement"].items():
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## Raw route recall", "", "| Route | Recall | Groups |", "|---|---:|---:|"])
    for route, row in result["route_recall"].items():
        lines.append(f"| {route} | {row['recall']:.1%} | {row['groups']} |")
    for field, title in (("rewrite_status", "Rewrite path"), ("evidence_type", "Evidence type")):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Value | Groups | Raw source | Fusion pool | Fusion Top-8 | Rerank Top-8 | Rerank Top-30 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in result["slices"][field]:
            lines.append(
                f"| {row['value']} | {row['groups']} | {row['source_recall']:.1%} | "
                f"{row['fusion_pool_recall']:.1%} | {row['fusion_top8']:.1%} | "
                f"{row['rerank_top8']:.1%} | {row['rerank_top30']:.1%} |"
            )
    lines.extend(
        [
            "",
            "## Failed Group patterns",
            "",
            "| Outcome | Groups | Rewrite | Evidence | Query |",
            "|---|---:|---|---|---|",
        ]
    )
    for row in result["failure_by_query"]:
        lines.append(
            f"| {row['outcome']} | {row['groups']} | {row['rewrite_status']} | "
            f"{row['evidence_type']} | {row['query']} |"
        )
    rank_range = result["dropped_source_rank_range"]
    lines.extend(
        [
            "",
            f"Dropped-before-fusion raw source ranks span `{rank_range['min']}`–`{rank_range['max']}` "
            "among the retained source-route hits; the production per-type candidate cap is 20.",
            "",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(read_groups(args.diagnostics_csv))
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(result), encoding="utf-8")


if __name__ == "__main__":
    main()
