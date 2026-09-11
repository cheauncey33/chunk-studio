"""Analyze an existing corpus-bound retrieval report without rerunning retrieval."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable


TOP_K_VALUES = (1, 3, 5, 8, 10, 15, 20, 30)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pct(value: float) -> str:
    return f"{value:.1%}"


def recall_at(cases: Iterable[dict[str, Any]], top_k: int) -> dict[str, Any]:
    items = list(cases)
    hits = sum(case["best_rank"] is not None and case["best_rank"] <= top_k for case in items)
    return {"hits": hits, "cases": len(items), "recall": hits / len(items) if items else None}


def dimension_rows(cases: list[dict[str, Any]], field: str, top_k: int = 8) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case.get(field) or "unknown")].append(case)
    rows = []
    for value, group in grouped.items():
        item = recall_at(group, top_k)
        rows.append({field: value, **item})
    return sorted(rows, key=lambda row: (-row["cases"], row[field]))


def analyze(report: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
    extension = ground_truth.get("transformer_extension", ground_truth)
    source_by_id = {
        case["case_id"]: case
        for case in extension["cases"]
        if case.get("required_evidence_groups")
    }
    degraded_keys = {
        key for key, run in report["query_runs"].items() if run.get("degraded")
    }
    locator_by_hash: dict[str, dict[str, Any]] = {}
    cases = []
    for scored in report["cases"]:
        source = source_by_id[scored["case_id"]]
        alternatives = source["required_evidence_groups"][0]["alternatives"]
        locators = [alternative["locator"] for alternative in alternatives]
        locator_by_hash.update({locator["text_sha256"]: locator for locator in locators})
        ranks_by_hash = {
            hit["text_sha256"]: hit["rank"]
            for hit in report["query_runs"][scored["query_key"]]["hits"]
        }
        cases.append(
            {
                **scored,
                "best_rank": scored["required_groups"][0]["best_rank"],
                "rewrite_mode": "fallback" if scored["query_key"] in degraded_keys else "rewritten",
                "gold_hashes": [locator["text_sha256"] for locator in locators],
                "gold_ranks": {
                    locator["text_sha256"]: ranks_by_hash.get(locator["text_sha256"])
                    for locator in locators
                },
                "gold_content_type": "+".join(sorted({str(locator.get("content_type") or "unknown") for locator in locators})),
                "gold_standard": "+".join(sorted({str(locator.get("standard_no") or "unknown") for locator in locators})),
            }
        )

    hash_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        for text_hash in case["gold_hashes"]:
            hash_cases[text_hash].append({"best_rank": case["gold_ranks"][text_hash]})

    unique_gold = {}
    for top_k in TOP_K_VALUES:
        per_hash = [recall_at(bound_cases, top_k)["recall"] for bound_cases in hash_cases.values()]
        covered = sum(
            any(case["best_rank"] is not None and case["best_rank"] <= top_k for case in bound_cases)
            for bound_cases in hash_cases.values()
        )
        unique_gold[str(top_k)] = {
            "unique_gold_chunks": len(hash_cases),
            "covered_chunks": covered,
            "chunk_coverage": covered / len(hash_cases),
            "chunk_macro_case_recall": mean(per_hash),
        }

    duplicate_slots = {}
    for top_k in (8, 30):
        used_slots = []
        duplicate_counts = []
        for run in report["query_runs"].values():
            hashes = [hit["text_sha256"] for hit in run["hits"][:top_k]]
            used_slots.append(len(hashes))
            duplicate_counts.append(len(hashes) - len(set(hashes)))
        duplicate_slots[str(top_k)] = {
            "average_returned_slots": mean(used_slots),
            "average_duplicate_hash_slots": mean(duplicate_counts),
            "queries_with_duplicates": sum(count > 0 for count in duplicate_counts),
            "queries": len(duplicate_counts),
        }

    misses30 = [case for case in cases if case["best_rank"] is None or case["best_rank"] > 30]
    missed_hash_counts = Counter(text_hash for case in misses30 for text_hash in case["gold_hashes"])
    family_misses = Counter(case["family_id"] for case in misses30)
    rank_bands = {
        "1": sum(case["best_rank"] == 1 for case in cases),
        "2-3": sum(case["best_rank"] is not None and 2 <= case["best_rank"] <= 3 for case in cases),
        "4-5": sum(case["best_rank"] is not None and 4 <= case["best_rank"] <= 5 for case in cases),
        "6-8": sum(case["best_rank"] is not None and 6 <= case["best_rank"] <= 8 for case in cases),
        "9-15": sum(case["best_rank"] is not None and 9 <= case["best_rank"] <= 15 for case in cases),
        "16-30": sum(case["best_rank"] is not None and 16 <= case["best_rank"] <= 30 for case in cases),
        ">30/not returned": len(misses30),
    }

    return {
        "stage_diagnostics_available": any(
            bool(run.get("diagnostics")) for run in report.get("query_runs", {}).values()
        ),
        "case_weighted": {str(top_k): recall_at(cases, top_k) for top_k in TOP_K_VALUES},
        "unique_gold_chunk": unique_gold,
        "top8_slices": {
            "rewrite_mode": dimension_rows(cases, "rewrite_mode"),
            "gold_content_type": dimension_rows(cases, "gold_content_type"),
            "expected_status": dimension_rows(cases, "expected_status"),
            "domain": dimension_rows(cases, "domain"),
            "asset_number": dimension_rows(cases, "asset_number"),
        },
        "rank_bands": rank_bands,
        "duplicate_slots": duplicate_slots,
        "top30_misses": {
            "cases": len(misses30),
            "unique_queries": len({case["query_key"] for case in misses30}),
            "unique_gold_chunks": len(missed_hash_counts),
            "rewrite_mode": dimension_rows(misses30, "rewrite_mode", top_k=30),
            "gold_content_type_counts": dict(Counter(case["gold_content_type"] for case in misses30)),
            "expected_status_counts": dict(Counter(case["expected_status"] for case in misses30)),
            "most_repeated_missed_gold": missed_hash_counts.most_common(10),
            "families": family_misses.most_common(15),
            "gold_chunks": [
                {
                    "text_sha256": text_hash,
                    "missed_case_bindings": count,
                    "total_case_bindings": len(hash_cases[text_hash]),
                    "covered_elsewhere_at_30": any(
                        item["best_rank"] is not None and item["best_rank"] <= 30
                        for item in hash_cases[text_hash]
                    ),
                    "standard_no": locator_by_hash[text_hash].get("standard_no"),
                    "content_type": locator_by_hash[text_hash].get("content_type"),
                    "section": locator_by_hash[text_hash].get("section"),
                    "table_no": locator_by_hash[text_hash].get("table_no"),
                }
                for text_hash, count in missed_hash_counts.most_common()
            ],
        },
        "top30_slices": {
            "rewrite_mode": dimension_rows(cases, "rewrite_mode", top_k=30),
            "gold_content_type": dimension_rows(cases, "gold_content_type", top_k=30),
            "domain": dimension_rows(cases, "domain", top_k=30),
        },
        "weighting": {
            "cases": len(cases),
            "unique_queries": len({case["query_key"] for case in cases}),
            "unique_gold_chunks": len(hash_cases),
            "largest_cases_per_gold_chunk": max(map(len, hash_cases.values())),
            "median_cases_per_gold_chunk": sorted(map(len, hash_cases.values()))[len(hash_cases) // 2],
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Bound retrieval error analysis",
        "",
        "This analysis reuses the saved Top-30 result set; it does not call the query planner, retriever, or reranker again.",
        "",
        "## Main metrics",
        "",
        "| K | Case-weighted recall | Unique-gold coverage | Gold-chunk macro recall |",
        "|---:|---:|---:|---:|",
    ]
    for top_k in TOP_K_VALUES:
        case = result["case_weighted"][str(top_k)]
        chunk = result["unique_gold_chunk"][str(top_k)]
        lines.append(
            f"| {top_k} | {pct(case['recall'])} ({case['hits']}/{case['cases']}) | "
            f"{pct(chunk['chunk_coverage'])} ({chunk['covered_chunks']}/{chunk['unique_gold_chunks']}) | "
            f"{pct(chunk['chunk_macro_case_recall'])} |"
        )

    lines.extend(["", "## Top-8 slices", ""])
    for title, key in (
        ("Query rewrite path", "rewrite_mode"),
        ("Gold content type", "gold_content_type"),
        ("Expected status", "expected_status"),
        ("Domain", "domain"),
    ):
        lines.extend([f"### {title}", "", "| Value | Recall | Cases |", "|---|---:|---:|"])
        for row in result["top8_slices"][key]:
            lines.append(f"| {row[key]} | {pct(row['recall'])} | {row['cases']} |")
        lines.append("")

    dup8 = result["duplicate_slots"]["8"]
    dup30 = result["duplicate_slots"]["30"]
    misses = result["top30_misses"]
    lines.extend(
        [
            "## Top-30 diagnostic slices",
            "",
            "| Slice | Value | Recall | Cases |",
            "|---|---|---:|---:|",
        ]
    )
    for key in ("rewrite_mode", "gold_content_type", "domain"):
        for row in result["top30_slices"][key]:
            lines.append(f"| {key} | {row[key]} | {pct(row['recall'])} | {row['cases']} |")
    lines.extend(
        [
            "",
            "## Confirmed failure signals",
            "",
            f"- Top-30 misses: {misses['cases']} cases, {misses['unique_queries']} queries, "
            f"covering {misses['unique_gold_chunks']} distinct gold chunks.",
            f"- Exact-content duplicates consume an average of {dup8['average_duplicate_hash_slots']:.2f} "
            f"of 8 slots and {dup30['average_duplicate_hash_slots']:.2f} of 30 slots; "
            f"{dup8['queries_with_duplicates']}/{dup8['queries']} queries contain duplicates in Top-8.",
            f"- Dataset weighting: {result['weighting']['cases']} cases collapse to "
            f"{result['weighting']['unique_queries']} queries and {result['weighting']['unique_gold_chunks']} gold chunks; "
            f"one gold chunk is repeated by as many as {result['weighting']['largest_cases_per_gold_chunk']} cases.",
            "- A case-level Top-K summary cannot prove whether a miss was absent from first-stage candidates "
            "or was pushed down by fusion/reranking; use the stage-level diagnostics for that split.",
            "",
            "### Largest missed families",
            "",
            "| Family | Missed cases |",
            "|---|---:|",
        ]
    )
    for family, count in misses["families"]:
        lines.append(f"| {family} | {count} |")
    lines.extend(["", "### Gold chunks involved in Top-30 misses", "", "| Standard locator | Type | Missed/total bindings | Covered by another query |", "|---|---|---:|---:|"])
    for chunk in misses["gold_chunks"]:
        locator = chunk.get("section") or chunk.get("table_no") or "-"
        lines.append(
            f"| {chunk.get('standard_no')} {locator} | {chunk.get('content_type')} | "
            f"{chunk['missed_case_bindings']}/{chunk['total_case_bindings']} | "
            f"{'yes' if chunk['covered_elsewhere_at_30'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "1. Query rewrite degradation is strongly associated with misses, but this observational split is not an A/B test: "
            "the fallback queries may also be intrinsically harder.",
            "2. Table evidence and the electrical_parameters domain are the dominant weak slices at Top-8.",
            "3. Duplicate corpus rows consume retrieval slots and are a concrete efficiency defect, though they do not alone explain all misses.",
            "4. Case-weighted recall overstates performance relative to equal-weight gold-chunk macro recall because a small set of chunks is reused heavily.",
            (
                "5. The stage-level rerun now separates first-stage, fusion, and reranker loss; see diagnosis.md."
                if result["stage_diagnostics_available"]
                else "5. To separate first-stage recall from reranker loss, the next evaluation must persist the pre-rerank candidate list and score both stages from the same run."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(read_json(args.report), read_json(args.ground_truth))
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(result), encoding="utf-8")


if __name__ == "__main__":
    main()
