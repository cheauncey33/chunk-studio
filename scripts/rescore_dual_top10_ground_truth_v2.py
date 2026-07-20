"""Rescore an archived dual-retrieval Top-10 report against the current v2 labels."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evidence_locator import chunk_text_sha256  # noqa: E402


DEFAULT_SOURCE = ROOT / "backend" / "data" / "reports" / "dual_retrieval_v2_2026-07-15.json"
DEFAULT_GROUND_TRUTH = ROOT / "evaluation" / "retrieval_ground_truth_v2_draft.json"
DEFAULT_DB = ROOT / "backend" / "data" / "chunkstudio.db"
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "dual_retrieval_v2_relabel_2026-07-16.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "dual_retrieval_v2_relabel_2026-07-16.md"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def score_case(
    case: dict[str, Any],
    top_hits: list[dict[str, Any]],
    hashes_by_chunk_id: dict[str, str],
) -> dict[str, Any]:
    recalled_hashes = {
        hashes_by_chunk_id[hit["chunk_id"]]
        for hit in top_hits
        if hit["chunk_id"] in hashes_by_chunk_id
    }
    groups = []
    for group in case["required_evidence_groups"]:
        target_hashes = {
            alternative["locator"]["text_sha256"]
            for alternative in group["alternatives"]
        }
        groups.append({
            "group_id": group["group_id"],
            "recalled": bool(target_hashes & recalled_hashes),
        })
    return {
        "strict_complete": all(group["recalled"] for group in groups),
        "groups": groups,
    }


def _load_hashes(conn: sqlite3.Connection, chunk_ids: set[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT id, text FROM chunks WHERE id IN ({placeholders})",
        sorted(chunk_ids),
    ).fetchall()
    return {row["id"]: chunk_text_sha256(row["text"]) for row in rows}


def rescore(source: dict[str, Any], ground_truth: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    source_cases = {case["case_id"]: case for case in source["cases"]}
    cases = [
        case for case in ground_truth["cases"]
        if case["answerability_status"] == "answerable_candidate"
    ]
    policies = tuple(source["summary"]["reranked"])
    chunk_ids = {
        hit["chunk_id"]
        for case in source_cases.values()
        for policy in policies
        for hit in case["reranked"][policy]["top_hits"]
    }
    hashes_by_chunk_id = _load_hashes(conn, chunk_ids)
    missing_chunks = sorted(chunk_ids - set(hashes_by_chunk_id))
    if missing_chunks:
        raise ValueError(f"{len(missing_chunks)} archived Top-10 chunks are missing from the current corpus")

    policy_results = {}
    for policy in policies:
        case_results = []
        for case in cases:
            case_id = case["case_id"]
            if case_id not in source_cases:
                raise ValueError(f"{case_id} is missing from the archived report")
            score = score_case(
                case,
                source_cases[case_id]["reranked"][policy]["top_hits"],
                hashes_by_chunk_id,
            )
            case_results.append({"case_id": case_id, **score})
        required_groups = sum(len(case["groups"]) for case in case_results)
        recalled_groups = sum(
            group["recalled"]
            for case in case_results
            for group in case["groups"]
        )
        strict_hits = sum(case["strict_complete"] for case in case_results)
        policy_results[policy] = {
            "cases": len(case_results),
            "required_groups": required_groups,
            "recalled_groups": recalled_groups,
            "evidence_group_recall_top10": recalled_groups / required_groups,
            "strict_complete_hits_top10": strict_hits,
            "strict_complete_recall_top10": strict_hits / len(case_results),
            "misses": [case for case in case_results if not case["strict_complete"]],
        }
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "label-only rescore of archived Top-10 hits; retrieval and reranking were not rerun",
        "source_report": "backend/data/reports/dual_retrieval_v2_2026-07-15.json",
        "ground_truth": "evaluation/retrieval_ground_truth_v2_draft.json",
        "summary": policy_results,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Dual retrieval Top-10 label rescore",
        "",
        "This is a label-only rescore of archived Top-10 hits. Retrieval and reranking were not rerun.",
        "",
        "| Policy | Strict hits | Strict recall | Group recall | Misses |",
        "|---|---:|---:|---:|---|",
    ]
    for policy, summary in report["summary"].items():
        misses = ", ".join(case["case_id"] for case in summary["misses"]) or "None"
        lines.append(
            f"| `{policy}` | {summary['strict_complete_hits_top10']}/{summary['cases']} | "
            f"{summary['strict_complete_recall_top10']:.1%} | "
            f"{summary['evidence_group_recall_top10']:.1%} | {misses} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        report = rescore(_read_json(args.source), _read_json(args.ground_truth), conn)
    finally:
        conn.close()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.json}")
    print(f"Wrote {args.markdown}")


if __name__ == "__main__":
    main()
