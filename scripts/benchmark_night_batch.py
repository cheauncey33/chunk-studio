"""Measure Night Batch throughput on real reports already in the workspace.

This does not guess a 3x5 vs 2x6 mix. It creates (or reuses) a Night Batch,
waits until child audit jobs finish, and writes the metrics needed to choose
a concurrency mix: reports/hour, case/agent latency, retries, 429/failures,
tokens, and cost.

API, audit worker, and the Pi sidecar must already be running. Sidecar
``AGENT_CONCURRENCY`` should be at least
``AUDIT_BATCH_GLOBAL_SLOTS * AUDIT_BATCH_CASE_CONCURRENCY`` or cases queue
inside the sidecar instead of running in parallel.

Examples:

  $env:PYTHONPATH='backend'
  uv run python scripts/benchmark_night_batch.py --dry-run --limit 10

  $env:PYTHONPATH='backend'
  uv run python scripts/benchmark_night_batch.py --limit 10 --output tmp/night_batch_load.json

  $env:PYTHONPATH='backend'
  uv run python scripts/benchmark_night_batch.py --analyze-only --batch-id <id>
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import audit_batches, config, current_user, db, jobs  # noqa: E402
from app.storage.usage_repository import compact_usage_summary, get_usage_repository  # noqa: E402


TERMINAL_BATCH = frozenset({"completed", "failed", "partial_failed"})


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": round(min(values), 3) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 3) if values else None,
        "mean": round(sum(values) / len(values), 3) if values else None,
    }


def parse_time(value: Any) -> datetime | None:
    parsed = jobs.parse_job_schedule_time(value if value not in (None, "") else None)
    return parsed


def seconds_between(start: Any, end: Any) -> float | None:
    left = parse_time(start)
    right = parse_time(end)
    if left is None or right is None:
        return None
    return max(0.0, (right - left).total_seconds())


def max_overlap(jobs_rows: list[dict[str, Any]]) -> int:
    events: list[tuple[datetime, int]] = []
    for job in jobs_rows:
        started = parse_time(job.get("started_at"))
        finished = parse_time(job.get("finished_at")) or parse_time(job.get("updated_at"))
        if started is None:
            continue
        events.append((started, 1))
        if finished is not None:
            events.append((finished, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    running = 0
    peak = 0
    for _when, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def mentions_429(value: Any) -> bool:
    return "429" in str(value or "")


def extract_case_agent_ms(report: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for case in report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        trace = case.get("workflow_trace") if isinstance(case.get("workflow_trace"), dict) else {}
        agent = trace.get("agent_audit") if isinstance(trace.get("agent_audit"), dict) else {}
        output = agent.get("output") if isinstance(agent.get("output"), dict) else {}
        stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
        raw = stats.get("duration_ms")
        try:
            if raw is not None:
                values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return values


def extract_search_timings_ms(report: dict[str, Any]) -> dict[str, list[float]]:
    buckets: dict[str, list[float]] = {}
    for case in report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        timings = case.get("timings_ms")
        if not isinstance(timings, dict):
            retrieval = case.get("retrieval") if isinstance(case.get("retrieval"), dict) else {}
            timings = retrieval.get("timings_ms") if isinstance(retrieval.get("timings_ms"), dict) else {}
        if not isinstance(timings, dict):
            continue
        for key, raw in timings.items():
            try:
                buckets.setdefault(str(key), []).append(float(raw))
            except (TypeError, ValueError):
                continue
    return buckets


def load_report_json(job: dict[str, Any]) -> dict[str, Any] | None:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    relative = str(result.get("report_path") or "").strip()
    if not relative:
        return None
    path = config.from_rel(relative)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def summarize_usage_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, int] = {}
    by_status: dict[str, int] = {}
    failed = 0
    rate_limited = 0
    total_tokens = 0
    for event in events:
        stage = str(event.get("stage") or "other")
        status = str(event.get("status") or "success")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        if status != "success":
            failed += 1
        if mentions_429(status) or mentions_429(event.get("error")):
            rate_limited += 1
        try:
            total_tokens += int(event.get("total_tokens") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "request_count": len(events),
        "failed_count": failed,
        "rate_limited_count": rate_limited,
        "by_stage": by_stage,
        "by_status": by_status,
        "total_tokens": total_tokens,
    }


def summarize_night_batch(
    *,
    batch: dict[str, Any],
    jobs_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    wall_seconds: float,
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    completed = [job for job in jobs_rows if str(job.get("status") or "") == "done"]
    failed_jobs = [job for job in jobs_rows if str(job.get("status") or "") == "failed"]
    retried = [job for job in jobs_rows if int(job.get("attempts") or 0) > 1]
    report_seconds = [
        value
        for job in completed
        if (value := seconds_between(job.get("started_at"), job.get("finished_at"))) is not None
    ]
    case_ms: list[float] = []
    search_timings: dict[str, list[float]] = {}
    case_count = 0
    for report in reports:
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        case_count += int(summary.get("cases") or len(report.get("cases") or []))
        case_ms.extend(extract_case_agent_ms(report))
        for key, values in extract_search_timings_ms(report).items():
            search_timings.setdefault(key, []).extend(values)
    usage = summarize_usage_events(events)
    job_429 = sum(
        1
        for job in jobs_rows
        if mentions_429(job.get("error")) or mentions_429((job.get("result") or {}).get("error"))
    )
    wall = max(0.0, float(wall_seconds))
    finished = len(completed) + len(failed_jobs)
    known_cost = int((batch.get("usage") or {}).get("known_cost_microunits") or 0)
    total_tokens = int((batch.get("usage") or {}).get("total_tokens") or usage["total_tokens"])
    return {
        "batch_id": batch.get("id"),
        "status": batch.get("status"),
        "config": config_snapshot,
        "reports": {
            "total": int(batch.get("total") or len(jobs_rows)),
            "completed": len(completed),
            "failed": len(failed_jobs),
            "queued": int(batch.get("queued") or 0),
            "running": int(batch.get("running") or 0),
            "duration_seconds": distribution(report_seconds),
            "realized_concurrency": max_overlap(jobs_rows),
            "per_hour": round(len(completed) / (wall / 3600.0), 3) if wall > 0 else None,
        },
        "cases": {
            "count": case_count,
            "agent_duration_ms": distribution(case_ms),
        },
        "search_timings_ms": {key: distribution(values) for key, values in sorted(search_timings.items())},
        "reliability": {
            "jobs": len(jobs_rows),
            "retried_jobs": len(retried),
            "retry_rate": round(len(retried) / len(jobs_rows), 6) if jobs_rows else 0,
            "mean_attempts": round(
                sum(int(job.get("attempts") or 0) for job in jobs_rows) / len(jobs_rows),
                3,
            )
            if jobs_rows
            else 0,
            "job_errors_mentioning_429": job_429,
            "usage_failed_count": usage["failed_count"],
            "usage_rate_limited_count": usage["rate_limited_count"],
            "usage_429_rate": round(usage["rate_limited_count"] / usage["request_count"], 6)
            if usage["request_count"]
            else 0,
        },
        "usage": {
            **compact_usage_summary(batch.get("usage") or {}),
            "by_stage": usage["by_stage"],
            "by_status": usage["by_status"],
            "tokens_per_second": round(total_tokens / wall, 3) if wall > 0 else None,
            "cost_usd": round(known_cost / 1_000_000, 6) if known_cost else 0,
            "cost_usd_per_report": round((known_cost / 1_000_000) / len(completed), 6)
            if known_cost and completed
            else None,
        },
        "wall_seconds": round(wall, 3),
        "finished_jobs": finished,
    }


def git_head() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True)
            .strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return ""


def sidecar_health(url: str) -> dict[str, Any]:
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=5.0)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return {"ok": response.status_code == 200, "status_code": response.status_code, **payload}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def config_snapshot(*, sidecar: dict[str, Any] | None = None) -> dict[str, Any]:
    slots = int(config.AUDIT_BATCH_GLOBAL_SLOTS)
    case_cap = int(config.AUDIT_BATCH_CASE_CONCURRENCY)
    sidecar_payload = sidecar if sidecar is not None else sidecar_health(config.AGENT_SIDECAR_URL)
    sidecar_conc = sidecar_payload.get("concurrency")
    needed = slots * case_cap
    warning = ""
    try:
        if sidecar_conc is not None and int(sidecar_conc) < needed:
            warning = (
                f"sidecar AGENT_CONCURRENCY={sidecar_conc} is below "
                f"{slots}x{case_cap}={needed}; extra cases will queue in the sidecar"
            )
    except (TypeError, ValueError):
        warning = ""
    return {
        "git": git_head(),
        "audit_worker_concurrency": int(config.AUDIT_WORKER_CONCURRENCY),
        "audit_batch_global_slots": slots,
        "audit_batch_case_concurrency": case_cap,
        "sidecar": sidecar_payload,
        "sidecar_budget_warning": warning,
    }


def list_report_file_ids(*, limit: int) -> list[str]:
    workspace = current_user.get_current_user().workspace_id
    rows = db.get_conn().execute(
        """SELECT id, metadata FROM files
           WHERE workspace_id=?
           ORDER BY created_at""",
        (workspace,),
    ).fetchall()
    ids: list[str] = []
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata or "{}")
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        role = str(metadata.get("doc_role") or metadata.get("doc_type") or "").strip().lower()
        if role != "report":
            continue
        ids.append(str(row["id"]))
        if len(ids) >= limit:
            break
    return ids


def load_child_jobs(detail: dict[str, Any]) -> list[dict[str, Any]]:
    jobs_rows: list[dict[str, Any]] = []
    for item in detail.get("items") or []:
        job_id = str(item.get("audit_job_id") or "").strip()
        if not job_id:
            continue
        try:
            jobs_rows.append(jobs.get_job(job_id))
        except KeyError:
            continue
    return jobs_rows


def load_usage_events(jobs_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repo = get_usage_repository()
    events: list[dict[str, Any]] = []
    for job in jobs_rows:
        workspace = str(job.get("workspace_id") or current_user.get_current_user().workspace_id)
        events.extend(
            repo.list_usage_events(
                workspace_id=workspace,
                job_id=str(job.get("id") or ""),
                limit=5000,
            )
        )
    return events


def analyze_batch(batch_id: str, *, wall_seconds: float | None = None) -> dict[str, Any]:
    detail = audit_batches.get_batch_detail(batch_id)
    jobs_rows = load_child_jobs(detail)
    started = [parse_time(job.get("started_at")) for job in jobs_rows]
    finished = [parse_time(job.get("finished_at")) for job in jobs_rows]
    known_start = [item for item in started if item is not None]
    known_end = [item for item in finished if item is not None]
    derived_wall = 0.0
    if known_start and known_end:
        derived_wall = max(0.0, (max(known_end) - min(known_start)).total_seconds())
    reports = [payload for job in jobs_rows if (payload := load_report_json(job))]
    return summarize_night_batch(
        batch=detail,
        jobs_rows=jobs_rows,
        events=load_usage_events(jobs_rows),
        reports=reports,
        wall_seconds=derived_wall if wall_seconds is None else wall_seconds,
        config_snapshot=config_snapshot(),
    )


def poll_batch(batch_id: str, *, interval: float, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = audit_batches.get_batch_detail(batch_id)
        status = str(last.get("status") or "")
        print(
            f"{status} completed={last.get('completed')} running={last.get('running')} "
            f"queued={last.get('queued')} failed={last.get('failed')}/{last.get('total')}",
            flush=True,
        )
        if status in TERMINAL_BATCH:
            return last
        time.sleep(max(1.0, interval))
    raise TimeoutError(f"batch {batch_id} still {last.get('status')} after {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant-id", default="assistant_oil_transformer_audit")
    parser.add_argument("--report-file-ids", default="", help="comma-separated file ids")
    parser.add_argument("--limit", type=int, default=10, help="report count when ids are omitted")
    parser.add_argument("--max-concurrency", type=int, default=None)
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=float, default=8 * 60 * 60)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "night_batch_load.json")
    args = parser.parse_args()

    snapshot = config_snapshot()
    if snapshot.get("sidecar_budget_warning"):
        print(f"warning: {snapshot['sidecar_budget_warning']}", flush=True)
    if not snapshot["sidecar"].get("ok"):
        print(f"warning: sidecar health failed: {snapshot['sidecar']}", flush=True)

    if args.analyze_only:
        batch_id = str(args.batch_id or "").strip()
        if not batch_id:
            raise SystemExit("--analyze-only requires --batch-id")
        report = analyze_batch(batch_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"wrote {args.output}")
        return 0

    ids = [item.strip() for item in str(args.report_file_ids).split(",") if item.strip()]
    if not ids:
        ids = list_report_file_ids(limit=max(1, int(args.limit)))
    if not ids:
        raise SystemExit("no report files found; pass --report-file-ids")
    print(f"reports={len(ids)} ids={ids[:8]}{'…' if len(ids) > 8 else ''}", flush=True)
    if args.dry_run:
        print(json.dumps({"reports": ids, "config": snapshot}, ensure_ascii=False, indent=2))
        return 0

    created = audit_batches.create_night_batch(
        assistant_id=args.assistant_id,
        report_file_ids=ids,
        scheduled_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        max_concurrency=args.max_concurrency
        if args.max_concurrency is not None
        else int(config.AUDIT_BATCH_GLOBAL_SLOTS),
    )
    batch_id = str(created["id"])
    print(f"created batch {batch_id} total={created['total']} max_concurrency={created['max_concurrency']}", flush=True)
    wall_started = time.perf_counter()
    poll_batch(batch_id, interval=args.poll_seconds, timeout=args.timeout_seconds)
    wall_seconds = time.perf_counter() - wall_started
    report = analyze_batch(batch_id, wall_seconds=wall_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
