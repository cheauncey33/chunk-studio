"""Night Batch load-test summarizer."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_night_batch import (  # noqa: E402
    extract_case_agent_ms,
    max_overlap,
    percentile,
    summarize_night_batch,
)


def test_percentile_and_overlap() -> None:
    assert percentile([10, 20, 30, 40], 0.50) == 20
    assert percentile([10, 20, 30, 40], 0.95) == 40
    jobs = [
        {
            "started_at": "2026-09-09T15:00:00Z",
            "finished_at": "2026-09-09T15:20:00Z",
        },
        {
            "started_at": "2026-09-09T15:00:00Z",
            "finished_at": "2026-09-09T15:10:00Z",
        },
        {
            "started_at": "2026-09-09T15:10:00Z",
            "finished_at": "2026-09-09T15:30:00Z",
        },
    ]
    assert max_overlap(jobs) == 2


def test_extract_sidecar_case_duration() -> None:
    report = {
        "summary": {"cases": 2},
        "cases": [
            {
                "workflow_trace": {
                    "agent_audit": {"output": {"stats": {"duration_ms": 41000}}}
                }
            },
            {
                "workflow_trace": {
                    "agent_audit": {"output": {"stats": {"duration_ms": 12000}}}
                }
            },
        ],
    }
    assert extract_case_agent_ms(report) == [41000.0, 12000.0]


def test_summarize_night_batch_throughput_and_retries() -> None:
    jobs = [
        {
            "id": "j1",
            "status": "done",
            "attempts": 1,
            "started_at": "2026-09-09T15:00:00Z",
            "finished_at": "2026-09-09T15:20:00Z",
            "error": "",
            "result": {},
        },
        {
            "id": "j2",
            "status": "done",
            "attempts": 2,
            "started_at": "2026-09-09T15:00:00Z",
            "finished_at": "2026-09-09T15:30:00Z",
            "error": "agent sidecar 5xx (429): rate limited",
            "result": {},
        },
        {
            "id": "j3",
            "status": "failed",
            "attempts": 3,
            "started_at": "2026-09-09T15:20:00Z",
            "finished_at": "2026-09-09T15:40:00Z",
            "error": "timeout",
            "result": {},
        },
    ]
    events = [
        {"stage": "audit_agent", "status": "success", "total_tokens": 1000},
        {"stage": "audit_agent", "status": "429", "total_tokens": 0},
        {"stage": "retrieval_embedding", "status": "success", "total_tokens": 20},
        {"stage": "rerank", "status": "failed", "total_tokens": 0},
    ]
    reports = [
        {
            "summary": {"cases": 2},
            "cases": [
                {"workflow_trace": {"agent_audit": {"output": {"stats": {"duration_ms": 40000}}}}},
                {"workflow_trace": {"agent_audit": {"output": {"stats": {"duration_ms": 50000}}}}},
            ],
        }
    ]
    summary = summarize_night_batch(
        batch={
            "id": "batch-1",
            "status": "partial_failed",
            "total": 3,
            "queued": 0,
            "running": 0,
            "usage": {"total_tokens": 1020, "known_cost_microunits": 2_500_000},
        },
        jobs_rows=jobs,
        events=events,
        reports=reports,
        wall_seconds=2400,
        config_snapshot={"audit_batch_global_slots": 3},
    )
    assert summary["reports"]["completed"] == 2
    assert summary["reports"]["failed"] == 1
    assert summary["reports"]["per_hour"] == 3.0
    assert summary["reports"]["realized_concurrency"] == 2
    assert summary["reliability"]["retried_jobs"] == 2
    assert summary["reliability"]["job_errors_mentioning_429"] == 1
    assert summary["reliability"]["usage_rate_limited_count"] == 1
    assert summary["cases"]["agent_duration_ms"]["p50"] == 40000
    assert summary["usage"]["cost_usd"] == 2.5
    assert summary["usage"]["tokens_per_second"] == 0.425
