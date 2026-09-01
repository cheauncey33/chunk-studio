from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_agent_api import Sample, _dispatch_event, percentile, summarize  # noqa: E402
from evaluate_multi_query_ablation import build_routes, summarize as summarize_ablation  # noqa: E402


def test_benchmark_summary_reports_tail_latency_and_errors() -> None:
    samples = [
        Sample(0, 200, True, 10, 12, 100, 200),
        Sample(1, 200, True, 20, 22, 200, 400),
        Sample(2, 500, False, 30, None, None, 800, "failed"),
    ]

    result = summarize(samples, wall_seconds=1.0)

    assert percentile([100, 200, 300], 0.95) == 300
    assert result["error_rate"] == 0.333333
    assert result["throughput_rps"] == 3
    assert result["ttft_ms"]["count"] == 2
    assert result["total_ms"]["p95"] == 800


def test_sse_dispatch_only_counts_display_tokens() -> None:
    assert _dispatch_event("agent", ['{"type":"token","content":"a"}']) == (True, False)
    assert _dispatch_event("agent", ['{"type":"tool_call_delta"}']) == (False, False)
    assert _dispatch_event("final", ["{}"]) == (False, True)


def test_multi_query_ablation_routes_are_incremental() -> None:
    case = {
        "detection_project": {
            "project_name": "空载损耗",
            "reported_requirement": {"text": "≤0.370 kW"},
            "sample_context": {"model": "S20", "capacity": "400 kVA"},
        }
    }

    assert list(build_routes(case, "single")) == ["production"]
    assert list(build_routes(case, "dual")) == ["production", "keyword"]
    assert list(build_routes(case, "triple")) == ["production", "keyword", "semantic"]


def test_multi_query_summary_keeps_quality_and_latency_together() -> None:
    rows = [{
        "metrics": {
            "strict_complete_hit": True,
            "recalled_groups": 1,
            "required_groups": 2,
        },
        "duration_ms": 12.5,
        "candidate_count": 20,
        "degraded": [],
    }]

    result = summarize_ablation(rows, top_k=10)

    assert result["strict_complete_recall"] == 1
    assert result["evidence_group_recall"] == 0.5
    assert result["latency_ms"]["p95"] == 12.5
