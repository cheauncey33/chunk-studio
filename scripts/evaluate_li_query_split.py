"""Compare original and evidence-group-split queries for the two LI cases."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app import retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402


DEFAULT_GROUND_TRUTH = ROOT / "evaluation" / "test_set.json"
DEFAULT_QUERIES = (
    ROOT
    / "evaluation"
    / "frozen"
    / "retrieval_eval_v1_candidate_2026-07-13"
    / "reports"
    / "retrieval_queries_40_v1.json"
)
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "li_query_split_2026-07-16.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "li_query_split_2026-07-16.md"
CASE_IDS = ("hbjc-14-r1", "whc-10-r1")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_groups(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "group_id": group["group_id"],
            "target_hashes": [
                alternative["locator"]["text_sha256"]
                for alternative in group["alternatives"]
            ],
        }
        for group in case["required_evidence_groups"]
    ]


def score_groups(hits: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranks = {
        chunk_text_sha256(hit.get("text")): rank
        for rank, hit in enumerate(hits, start=1)
    }
    return [
        {
            "group_id": group["group_id"],
            "best_rank": min(
                (ranks[text_hash] for text_hash in group["target_hashes"] if text_hash in ranks),
                default=None,
            ),
        }
        for group in groups
    ]


def split_is_complete(split_runs: dict[str, dict[str, Any]]) -> bool:
    expected_run = {
        "lightning_impulse_voltage": "voltage",
        "lightning_impulse_waveform": "waveform",
    }
    return all(
        any(
            group["group_id"] == group_id and group["best_rank"] is not None
            for group in split_runs[run_name]["groups"]
        )
        for group_id, run_name in expected_run.items()
    )


def _summarize_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "rank": rank,
        "chunk_id": hit.get("chunk_id"),
        "text_sha256": chunk_text_sha256(hit.get("text")),
        "file_name": hit.get("file_name"),
        "page": hit.get("page"),
        "content_type": metadata.get("content_type"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "rerank_score": hit.get("rerank_score"),
        "retrieval_sources": hit.get("retrieval_sources"),
        "text_preview": str(hit.get("text") or "")[:240],
    }


def _run_query(query: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    result = retrieval.hybrid_search(query, top_k=10)
    hits = result["hits"]
    group_scores = score_groups(hits, groups)
    return {
        "query": query,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "retrieval_mode": result["retrieval_mode"],
        "candidate_count": result["candidate_count"],
        "degraded": result["degraded"],
        "query_routes": result["query_routes"],
        "groups": group_scores,
        "strict_complete_top10": all(group["best_rank"] is not None for group in group_scores),
        "top_hits": [_summarize_hit(rank, hit) for rank, hit in enumerate(hits, start=1)],
    }


def _split_queries(case: dict[str, Any]) -> dict[str, str]:
    context = case["detection_project"]["sample_context"]
    prefix = " ".join(str(context.get(key) or "") for key in ("model", "rated_capacity", "rated_voltage"))
    return {
        "voltage": f"{prefix} 配电变压器 线端雷电全波冲击试验 LI 高压线端试验电压 75 kV",
        "waveform": (
            f"{prefix} 配电变压器 雷电全波冲击试验 LI 波前时间 T1 1.2 μs ±30% "
            "半峰值时间 T2 50 μs ±20% 试验电压偏差 ±3%"
        ),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LI query split diagnostic",
        "",
        "| Case | Baseline complete@10 | Voltage rank | Waveform rank | Split complete@10 |",
        "|---|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        baseline = {group["group_id"]: group["best_rank"] for group in case["baseline"]["groups"]}
        voltage = {group["group_id"]: group["best_rank"] for group in case["split"]["voltage"]["groups"]}
        waveform = {group["group_id"]: group["best_rank"] for group in case["split"]["waveform"]["groups"]}
        lines.append(
            f"| `{case['case_id']}` | {case['baseline']['strict_complete_top10']} | "
            f"{voltage.get('lightning_impulse_voltage')} | "
            f"{waveform.get('lightning_impulse_waveform')} | {case['split_complete_top10']} |"
        )
    lines.extend([
        "",
        "The split score uses two independent production retrieval calls and unions their evidence groups. "
        "It is diagnostic only and does not change the production query planner.",
        "",
    ])
    return "\n".join(lines)


def evaluate(ground_truth_path: Path, queries_path: Path) -> dict[str, Any]:
    ground_truth = _read_json(ground_truth_path)
    queries = _read_json(queries_path)["cases"]
    cases_by_id = {case["case_id"]: case for case in ground_truth["cases"]}
    results = []
    for case_id in CASE_IDS:
        case = cases_by_id[case_id]
        groups = _required_groups(case)
        split_queries = _split_queries(case)
        split_runs = {
            name: _run_query(query, groups)
            for name, query in split_queries.items()
        }
        results.append({
            "case_id": case_id,
            "baseline": _run_query(queries[case_id]["production"], groups),
            "split": split_runs,
            "split_complete_top10": split_is_complete(split_runs),
        })
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": list(CASE_IDS),
        "method": "two independent production dual-retrieval calls; union evidence groups at Top-10",
        "summary": {
            "cases": len(results),
            "baseline_complete_top10": sum(case["baseline"]["strict_complete_top10"] for case in results),
            "split_complete_top10": sum(case["split_complete_top10"] for case in results),
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    report = evaluate(args.ground_truth, args.queries)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.json}")
    print(f"Wrote {args.markdown}")


if __name__ == "__main__":
    main()
