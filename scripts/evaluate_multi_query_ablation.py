"""Compare deterministic 1/2/3-query retrieval routes on the frozen test set."""
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
sys.path.insert(0, str(ROOT / "scripts"))

from app import retrieval  # noqa: E402
from evaluate_test_set import metrics_at_k, score_ranking  # noqa: E402

MODES = ("single", "dual", "triple")


def build_routes(case: dict[str, Any], mode: str) -> dict[str, str]:
    project = str(case["detection_project"]["project_name"]).strip()
    item = case["detection_project"]
    requirement = str((item.get("reported_requirement") or {}).get("text") or "").strip()
    context = " ".join(
        str(value).strip() for value in (item.get("sample_context") or {}).values() if value
    )
    routes = {"production": project}
    if mode in {"dual", "triple"}:
        routes["keyword"] = " ".join(value for value in (project, requirement) if value)
    if mode == "triple":
        routes["semantic"] = " ".join(value for value in (project, context) if value)
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    return routes


def summarize(rows: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    scored = [row["metrics"] for row in rows]
    required = sum(int(item["required_groups"]) for item in scored)
    durations = sorted(float(row["duration_ms"]) for row in rows)
    p95_index = max(0, min(len(durations) - 1, int((len(durations) * 0.95) + 0.999) - 1))
    return {
        "cases": len(rows),
        "top_k": top_k,
        "strict_complete_recall": (
            sum(bool(item["strict_complete_hit"]) for item in scored) / len(scored)
            if scored else 0
        ),
        "evidence_group_recall": (
            sum(int(item["recalled_groups"]) for item in scored) / required if required else 0
        ),
        "latency_ms": {
            "mean": round(sum(durations) / len(durations), 3) if durations else None,
            "p95": round(durations[p95_index], 3) if durations else None,
        },
        "mean_candidate_count": (
            round(sum(row["candidate_count"] for row in rows) / len(rows), 3) if rows else 0
        ),
        "degraded_cases": sum(bool(row["degraded"]) for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "experiments" / "multi_query_ablation.json",
    )
    args = parser.parse_args()
    modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
    if any(mode not in MODES for mode in modes):
        parser.error(f"--modes must be selected from {', '.join(MODES)}")
    test_set = json.loads((ROOT / "evaluation" / "test_set.json").read_text(encoding="utf-8"))
    cases = [
        case for case in test_set["cases"]
        if case.get("answerability_status") == "answerable_candidate"
    ]
    if args.limit:
        cases = cases[:args.limit]
    report: dict[str, Any] = {
        "manifest": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "test_set_version": test_set.get("version"),
            "modes": modes,
            "top_k": args.top_k,
            "cases": len(cases),
            "query_generation": "deterministic_v1",
        },
        "modes": {},
    }
    for mode in modes:
        rows = []
        for index, case in enumerate(cases, start=1):
            routes = build_routes(case, mode)
            started = time.perf_counter()
            result = retrieval.hybrid_search(
                routes["production"],
                top_k=args.top_k,
                query_routes=routes,
            )
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            ranking = score_ranking(result["hits"], case)
            rows.append({
                "case_id": case["case_id"],
                "routes": routes,
                "duration_ms": duration_ms,
                "timings_ms": result.get("timings_ms") or {},
                "candidate_count": result.get("candidate_count", 0),
                "degraded": result.get("degraded") or [],
                "metrics": metrics_at_k(ranking, args.top_k),
            })
            print(f"{mode} {index}/{len(cases)} {case['case_id']}", flush=True)
        report["modes"][mode] = {
            "summary": summarize(rows, args.top_k),
            "cases": rows,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({mode: value["summary"] for mode, value in report["modes"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
