"""Load-test the streaming Agent API and report latency, TTFT, and reliability.

Example:
  uv run python scripts/benchmark_agent_api.py --assistant-id assistant_demo \
    --requests 50 --concurrency 5 --question "这个检测项目的标准要求是什么？"
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any

import httpx


@dataclass
class Sample:
    index: int
    status_code: int
    ok: bool
    headers_ms: float | None
    first_event_ms: float | None
    ttft_ms: float | None
    total_ms: float
    error: str = ""


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def distribution(samples: list[Sample], field: str) -> dict[str, float | int | None]:
    values = [
        float(value)
        for sample in samples
        if (value := getattr(sample, field)) is not None
    ]
    return {
        "count": len(values),
        "min": round(min(values), 3) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 3) if values else None,
        "mean": round(sum(values) / len(values), 3) if values else None,
    }


def summarize(samples: list[Sample], wall_seconds: float) -> dict[str, Any]:
    successes = sum(sample.ok for sample in samples)
    status_counts: dict[str, int] = {}
    for sample in samples:
        key = str(sample.status_code)
        status_counts[key] = status_counts.get(key, 0) + 1
    total = len(samples)
    return {
        "requests": total,
        "successes": successes,
        "errors": total - successes,
        "error_rate": round((total - successes) / total, 6) if total else 0,
        "throughput_rps": round(total / wall_seconds, 3) if wall_seconds > 0 else None,
        "wall_seconds": round(wall_seconds, 3),
        "status_counts": status_counts,
        "headers_ms": distribution(samples, "headers_ms"),
        "first_event_ms": distribution(samples, "first_event_ms"),
        "ttft_ms": distribution(samples, "ttft_ms"),
        "total_ms": distribution(samples, "total_ms"),
    }


def _dispatch_event(event_name: str, data_lines: list[str]) -> tuple[bool, bool]:
    """Return (is_first_token, is_final) for one parsed SSE event."""
    try:
        payload = json.loads("\n".join(data_lines)) if data_lines else {}
    except json.JSONDecodeError:
        return False, False
    is_token = (
        event_name == "agent"
        and isinstance(payload, dict)
        and payload.get("type") == "token"
        and bool(payload.get("content"))
    )
    return is_token, event_name in {"final", "replayed"}


async def run_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    index: int,
    url: str,
    question: str,
    headers: dict[str, str],
) -> Sample:
    async with semaphore:
        started = time.perf_counter()
        status_code = 0
        headers_ms: float | None = None
        first_event_ms: float | None = None
        ttft_ms: float | None = None
        saw_final = False
        try:
            request_headers = {
                **headers,
                "Accept": "text/event-stream",
                "Idempotency-Key": f"benchmark-{index}-{time.time_ns()}",
            }
            async with client.stream(
                "POST",
                url,
                json={"message": question},
                headers=request_headers,
            ) as response:
                status_code = response.status_code
                headers_ms = round((time.perf_counter() - started) * 1000, 3)
                if status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"HTTP {status_code}: {body}")
                event_name = "message"
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif not line:
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                        if first_event_ms is None:
                            first_event_ms = elapsed_ms
                        is_token, is_final = _dispatch_event(event_name, data_lines)
                        if is_token and ttft_ms is None:
                            ttft_ms = elapsed_ms
                        saw_final = saw_final or is_final
                        event_name = "message"
                        data_lines = []
            total_ms = round((time.perf_counter() - started) * 1000, 3)
            return Sample(
                index, status_code, saw_final, headers_ms, first_event_ms, ttft_ms, total_ms,
                "" if saw_final else "stream ended without final event",
            )
        except Exception as exc:
            return Sample(
                index=index,
                status_code=status_code,
                ok=False,
                headers_ms=headers_ms,
                first_event_ms=first_event_ms,
                ttft_ms=ttft_ms,
                total_ms=round((time.perf_counter() - started) * 1000, 3),
                error=f"{type(exc).__name__}: {exc}",
            )


def _parse_headers(raw_headers: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw_headers:
        if ":" not in item:
            raise ValueError("--header must use Name:Value")
        name, value = item.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


async def run(args: argparse.Namespace) -> dict[str, Any]:
    questions = [args.question]
    if args.questions_file:
        questions = [
            line.strip()
            for line in args.questions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if not questions:
        raise ValueError("at least one question is required")
    url = f"{args.base_url.rstrip('/')}/api/assistants/{args.assistant_id}/agent-chat/stream"
    headers = _parse_headers(args.header)
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        samples = await asyncio.gather(*(
            run_one(
                client,
                semaphore,
                index=index,
                url=url,
                question=questions[index % len(questions)],
                headers=headers,
            )
            for index in range(args.requests)
        ))
    wall_seconds = time.perf_counter() - started
    return {
        "manifest": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "assistant_id": args.assistant_id,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "question_count": len(questions),
            "timeout_seconds": args.timeout,
        },
        "summary": summarize(samples, wall_seconds),
        "samples": [asdict(sample) for sample in samples],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--assistant-id", required=True)
    parser.add_argument("--question", default="请概括知识库中的主要要求。")
    parser.add_argument("--questions-file", type=Path)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--max-ttft-p95-ms", type=float)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")
    report = asyncio.run(run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    summary = report["summary"]
    failed = summary["error_rate"] > args.max_error_rate
    if args.max_p95_ms is not None:
        failed = failed or (summary["total_ms"]["p95"] or float("inf")) > args.max_p95_ms
    if args.max_ttft_p95_ms is not None:
        failed = failed or (summary["ttft_ms"]["p95"] or float("inf")) > args.max_ttft_p95_ms
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
