"""Warm-start A/B: Python hybrid first-round → sidecar agent (read first, search if needed)."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

GOLD_TO_VERDICT = {
    "correct": "match",
    "incorrect": "mismatch",
    "supported": "match",
    "mismatch": "mismatch",
    "insufficient_context": "unevaluable",
    "unevaluable": "unevaluable",
    "not_applicable_to_retrieval": "out_of_scope",
    "out_of_scope": "out_of_scope",
}

_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*kVA", re.I)
_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/\s*\d+(?:\.\d+)?)?\s*kV", re.I)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _map_status(status: str | None) -> str | None:
    gold = str(status or "").strip()
    return GOLD_TO_VERDICT.get(gold, gold or None)


def _world_gold(case: dict[str, Any]) -> str | None:
    return _map_status((case.get("judgment") or {}).get("status"))


def _corpus_answerable(case: dict[str, Any]) -> bool:
    if "corpus_answerable" in case:
        return bool(case.get("corpus_answerable"))
    return True


def _corpus_gold(case: dict[str, Any]) -> str | None:
    explicit = str(case.get("corpus_gold") or "").strip()
    if explicit:
        return _map_status(explicit)
    if not _corpus_answerable(case):
        return "unevaluable"
    return _world_gold(case)


def _evidence_ids(result: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "").strip()
        if chunk_id and "placeholder" not in chunk_id:
            ids.append(chunk_id)
    return ids


def _production_query(case: dict[str, Any]) -> str:
    nested = ((case.get("queries") or {}).get("production") or "").strip()
    if nested:
        return nested
    context = case.get("sample_context") or {}
    parts = [
        str(context.get(key) or "").strip()
        for key in ("model", "rated_capacity", "rated_voltage", "sample_name")
    ]
    test_item = case.get("test_item") or {}
    reported = case.get("reported_requirement") or {}
    parts.extend([
        str(test_item.get("project_name") or "").strip(),
        str(reported.get("text") or "").strip(),
    ])
    return " ".join(dict.fromkeys(part for part in parts if part))


def _row_filter(case: dict[str, Any]) -> dict[str, Any] | None:
    ctx = case.get("sample_context") or {}
    params: dict[str, float] = {}
    cap = _CAPACITY_RE.search(str(ctx.get("rated_capacity") or ""))
    volt = _VOLTAGE_RE.search(str(ctx.get("rated_voltage") or ""))
    if cap:
        params["capacity_kva"] = float(cap.group(1))
    if volt:
        params["system_nominal_voltage_kv"] = float(volt.group(1))
    return {"parameters": params} if params else None


def _retrieved_pool(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Locator cards: sections keep text; tables drop HTML/cell values."""
    pool: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        chunk_id = str(hit.get("chunk_id") or hit.get("id") or "").strip()
        if not chunk_id:
            continue
        metadata = hit.get("business_metadata") or {}
        binding = hit.get("table_row_binding") if isinstance(hit.get("table_row_binding"), dict) else {}
        if not binding and isinstance(hit.get("row_binding"), dict):
            binding = hit["row_binding"]
        columns = metadata.get("table_columns")
        headers = binding.get("headers") if isinstance(binding.get("headers"), list) else None
        kind = str(hit.get("content_type") or metadata.get("content_type") or "").casefold()
        card: dict[str, Any] = {
            "chunk_id": chunk_id,
            "candidate_key": f"c{rank:02d}",
            "content_type": hit.get("content_type") or metadata.get("content_type"),
            "standard_no": metadata.get("standard_no"),
            "table_no": metadata.get("table_no"),
            "table_title": metadata.get("table_title"),
            "table_columns": columns if isinstance(columns, list) else None,
            "section": metadata.get("section"),
            "section_title": metadata.get("section_title"),
            "headers": headers,
            "bind_state": binding.get("state"),
        }
        if kind != "table":
            card["text"] = str(hit.get("text") or "")
        pool.append(card)
    return pool


def first_round_search(
    *,
    api_base: str,
    query: str,
    row_filter: dict[str, Any] | None,
    top_k: int,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "query_routes": {"production": query},
    }
    if row_filter:
        payload["row_filter"] = row_filter
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.post(f"{api_base.rstrip('/')}/api/search", json=payload, timeout=120.0)
            response.raise_for_status()
            return list((response.json() or {}).get("hits") or [])
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status and status < 500 and status != 429:
                raise
            time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def run_case(
    case: dict[str, Any],
    *,
    source: str,
    api_base: str,
    sidecar_url: str,
    top_k: int,
    tool_budget: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    query = _production_query(case)
    hits = first_round_search(
        api_base=api_base,
        query=query,
        row_filter=_row_filter(case),
        top_k=top_k,
    )
    first_round_ms = int(round((time.perf_counter() - started) * 1000))
    pool = _retrieved_pool(hits)
    agent_started = time.perf_counter()
    response = httpx.post(
        f"{sidecar_url.rstrip('/')}/audit/case",
        json={
            "case_id": case["case_id"],
            "sample_context": case.get("sample_context") or {},
            "test_item": case.get("test_item") or {},
            "reported_requirement": case.get("reported_requirement") or {},
            "production_query": query,
            "retrieved_candidates": pool,
            "tool_budget": tool_budget,
        },
        timeout=480.0,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") or {}
    stats = payload.get("stats") or {}
    gold = str((case.get("judgment") or {}).get("status") or "")
    verdict = result.get("verdict")
    world = _world_gold(case)
    corpus = _corpus_gold(case)
    search_calls = int(stats.get("search_calls") or 0)
    read_chunks = int(stats.get("read_chunks") or 0)
    agent_ms = int(round((time.perf_counter() - agent_started) * 1000))
    duration_ms = int(round((time.perf_counter() - started) * 1000))
    sidecar_ms = int(stats.get("duration_ms") or 0)
    first_round_ids = [str(item["chunk_id"]) for item in pool if item.get("chunk_id")]
    evidence_ids = _evidence_ids(result)
    used_ranks = [
        first_round_ids.index(chunk_id) + 1
        for chunk_id in evidence_ids
        if chunk_id in first_round_ids
    ]
    first_round_hit_used = stats.get("first_round_hit_used")
    if first_round_hit_used is None and evidence_ids and first_round_ids:
        first_round_hit_used = any(chunk_id in first_round_ids for chunk_id in evidence_ids)
    return {
        "case_id": case["case_id"],
        "source": source,
        "edit_kind": case.get("edit_kind"),
        "gold": gold or None,
        "corpus_answerable": _corpus_answerable(case),
        "corpus_gold": corpus,
        "verdict": verdict,
        "kind": result.get("kind"),
        "match": (world == verdict) if world and verdict else None,
        "corpus_match": (corpus == verdict) if corpus and verdict else None,
        "duration_ms": duration_ms,
        "first_round_ms": first_round_ms,
        "agent_ms": agent_ms,
        "sidecar_ms": sidecar_ms,
        "search_calls": search_calls,
        "read_chunks": read_chunks,
        "tool_calls": int(stats.get("tool_calls") or 0),
        "zero_new_searches": int(stats.get("zero_new_searches") or 0),
        "search_blocked": bool(stats.get("search_blocked")),
        "raw_verdict": stats.get("raw_verdict"),
        "closure": stats.get("closure"),
        "closure_mode": stats.get("closure_mode"),
        "first_round_hit_used": first_round_hit_used,
        "first_round_evidence_rank": min(used_ranks) if used_ranks else None,
        "read_before_search": stats.get("read_before_search"),
        "first_round_only": search_calls == 0 and read_chunks >= 1,
        "search_zero": search_calls == 0,
        "first_round_hits": len(pool),
        "production_query": query,
        "parse_mode": payload.get("parse_mode"),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    search = [int(r["search_calls"]) for r in rows]
    read = [int(r["read_chunks"]) for r in rows]
    dur = [int(r["duration_ms"]) for r in rows]
    agent = [int(r["agent_ms"]) for r in rows]
    matched = [r for r in rows if r.get("match") is True]
    corpus_matched = [r for r in rows if r.get("corpus_match") is True]
    first_only = [r for r in rows if r.get("first_round_only")]
    used = [r for r in rows if r.get("first_round_hit_used") is True]
    used_known = [r for r in rows if r.get("first_round_hit_used") is not None]
    zero_new = [int(r.get("zero_new_searches") or 0) for r in rows]
    return {
        "cases": len(rows),
        "exact_match": len(matched),
        "verdict_mismatch": sum(1 for r in rows if r.get("match") is False),
        "corpus_exact_match": len(corpus_matched),
        "corpus_mismatch": sum(1 for r in rows if r.get("corpus_match") is False),
        "unparsed": sum(1 for r in rows if not r.get("verdict")),
        "accuracy": round(len(matched) / len(rows), 3) if rows else None,
        "corpus_accuracy": round(len(corpus_matched) / len(rows), 3) if rows else None,
        "avg_search_calls": _avg(search),
        "avg_read_chunks": _avg(read),
        "avg_latency_ms": _avg(dur),
        "avg_agent_ms": _avg(agent),
        "avg_zero_new_searches": _avg(zero_new),
        "first_round_only_count": len(first_only),
        "first_round_only_rate": round(len(first_only) / len(rows), 3) if rows else None,
        "first_round_hit_used_count": len(used),
        "first_round_hit_used_rate": round(len(used) / len(used_known), 3) if used_known else None,
        "heavy_cases": sum(1 for r in rows if int(r.get("search_calls") or 0) > 1),
        "zero_read_cases": sum(1 for r in rows if int(r.get("read_chunks") or 0) == 0),
        "search_zero_count": sum(1 for r in rows if r.get("search_zero")),
        "coverage_gaps": sum(1 for r in rows if r.get("corpus_answerable") is False),
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"{path} has no cases array")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, help="gold JSON with cases[]")
    parser.add_argument("--out", required=True)
    parser.add_argument("--api-base", default=os.environ.get("CHUNK_STUDIO_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--sidecar", default=os.environ.get("AGENT_SIDECAR_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=14)
    parser.add_argument("--tool-budget", type=int, default=12)
    args = parser.parse_args()

    labeled: list[tuple[str, dict[str, Any]]] = []
    for report in args.report:
        path = Path(report)
        for case in load_cases(path):
            labeled.append((path.name, case))
    print(f"warm-start {len(labeled)} cases sidecar={args.sidecar} concurrency={args.concurrency}")
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [
            pool.submit(
                run_case,
                case,
                source=source,
                api_base=args.api_base,
                sidecar_url=args.sidecar,
                top_k=args.top_k,
                tool_budget=args.tool_budget,
            )
            for source, case in labeled
        ]
        for rank, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            rows.append(row)
            flag = "warm" if row["first_round_only"] else f"search={row['search_calls']}"
            grounded = "corpus+" if row.get("corpus_match") else "corpus-"
            print(
                f"  [{rank}/{len(labeled)}] {row['case_id']} gold={row['gold']} "
                f"verdict={row['verdict']} {row['duration_ms']}ms "
                f"read={row['read_chunks']} {flag} {grounded}"
            )
    rows.sort(key=lambda item: (str(item.get("source") or ""), str(item["case_id"])))
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source") or "unknown"), []).append(row)
    report_out = {
        "mode": "warm_start",
        "sidecar": args.sidecar,
        "reports": args.report,
        "summary": summarize(rows),
        "by_source": {name: summarize(group) for name, group in by_source.items()},
        "rows": rows,
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(report_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report_out["summary"], "by_source": report_out["by_source"]}, ensure_ascii=False, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
