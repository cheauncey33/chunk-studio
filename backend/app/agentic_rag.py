"""Bounded Agentic RAG recovery around the fixed retrieval pipeline.

The recovery layer is intentionally deterministic at the control boundary:
the first retrieval is always the user's original question, the fixed judge
only decides whether evidence is sufficient, and every recovery query keeps
that original question as the production route.  Model/tool routing remains
outside this module.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable


DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_SEARCH_CALLS = 3
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0

_TABLE_MARKERS = ("表格", "表号", "表.", "表 ", "表中", "数值表")
_NUMERIC_MARKERS = ("数值", "限值", "最小", "最大", "多少", "范围")
_CLAUSE_MARKERS = ("条款", "第", "依据", "标准号", "规范", "规定")

_RECOVERY_SUFFIXES: dict[str, tuple[str, ...]] = {
    "no_evidence": ("适用范围 条款 要求", "关键术语 规范条款"),
    "low_confidence": ("直接要求 定义 适用范围", "原文关键词 条款"),
    "missing_structured_evidence": ("表格 表头 数值", "表号 表头 目标行"),
    "missing_numeric_evidence": ("数值 单位 限值", "参数 数值 规定"),
    "missing_clause_evidence": ("条款 规定 适用范围", "标准条款 原文"),
}


SearchFn = Callable[[str, dict[str, str] | None], dict[str, Any]]
MergeFn = Callable[..., dict[str, Any]]
Clock = Callable[[], float]


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _bounded_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(timeout, MAX_TIMEOUT_SECONDS))


def resolve_policy(config: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the persisted Agentic RAG budget at the execution boundary."""
    value = config if isinstance(config, dict) else {}
    return {
        "enabled": _as_bool(value.get("agentic_rag_enabled"), True),
        "max_rounds": _bounded_int(
            value.get("agentic_rag_max_rounds"),
            DEFAULT_MAX_ROUNDS,
            minimum=1,
            maximum=DEFAULT_MAX_ROUNDS,
        ),
        "max_search_calls": _bounded_int(
            value.get("agentic_rag_max_search_calls"),
            DEFAULT_MAX_SEARCH_CALLS,
            minimum=1,
            maximum=DEFAULT_MAX_SEARCH_CALLS,
        ),
        "timeout_seconds": _bounded_timeout(
            value.get("agentic_rag_timeout_seconds")
        ),
    }


def _content_type(hit: dict[str, Any]) -> str:
    metadata = hit.get("business_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return str(
        hit.get("content_type")
        or metadata.get("content_type")
        or ""
    ).casefold()


def _score(hit: dict[str, Any]) -> float | None:
    for key in ("rerank_score", "score"):
        value = hit.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def judge_evidence_sufficiency(
    query: str,
    retrieval_result: dict[str, Any],
    *,
    rerank_threshold: float = 0.2,
) -> dict[str, Any]:
    """Apply the fixed, inspectable sufficiency Judge.

    This is deliberately not another free-form LLM call.  It uses retrieval
    output, score availability, and explicit query shape markers so the stop
    or recovery decision is reproducible and testable.
    """
    hits = [item for item in retrieval_result.get("hits") or [] if isinstance(item, dict)]
    if not hits:
        return {
            "sufficient": False,
            "gap_type": "no_evidence",
            "reason": "没有召回可用证据片段",
            "missing_evidence": "需要与原始问题直接相关的知识库片段",
        }

    scored = [_score(hit) for hit in hits]
    available_scores = [score for score in scored if score is not None]
    if available_scores and max(available_scores) < float(rerank_threshold):
        return {
            "sufficient": False,
            "gap_type": "low_confidence",
            "reason": "召回片段存在，但最高有效分数低于结果阈值",
            "missing_evidence": "需要更直接的原文证据或适用范围说明",
            "max_score": max(available_scores),
        }

    normalized_query = str(query or "").casefold()
    wants_table = any(marker.casefold() in normalized_query for marker in _TABLE_MARKERS)
    has_table = any(_content_type(hit) == "table" for hit in hits)
    if wants_table and not has_table:
        return {
            "sufficient": False,
            "gap_type": "missing_structured_evidence",
            "reason": "问题明确要求表格或表中数值，但当前证据没有表格片段",
            "missing_evidence": "需要表头、目标行或对应表号的证据",
        }

    wants_numeric = any(marker.casefold() in normalized_query for marker in _NUMERIC_MARKERS)
    if wants_numeric:
        has_number = any(re.search(r"\d", str(hit.get("text") or hit.get("content") or "")) for hit in hits)
        if not has_number:
            return {
                "sufficient": False,
                "gap_type": "missing_numeric_evidence",
                "reason": "问题要求参数或限值，但当前片段没有可核对数值",
                "missing_evidence": "需要带单位、限值或表格行的原文证据",
            }

    wants_clause = any(marker.casefold() in normalized_query for marker in _CLAUSE_MARKERS)
    if wants_clause:
        has_clause_context = any(
            re.search(r"(第\s*[0-9一二三四五六七八九十]+\s*条|条款|规定|标准)", str(hit.get("text") or hit.get("content") or ""))
            for hit in hits
        )
        if not has_clause_context:
            return {
                "sufficient": False,
                "gap_type": "missing_clause_evidence",
                "reason": "问题要求条款或依据，但当前片段缺少条款上下文",
                "missing_evidence": "需要标准号、章节或条款原文",
            }

    return {
        "sufficient": True,
        "gap_type": "",
        "reason": "当前证据满足固定 Judge 的最低回答条件",
        "missing_evidence": "",
        "hit_count": len(hits),
        "max_score": max(available_scores) if available_scores else None,
    }


def _recovery_routes(
    query: str,
    *,
    gap_type: str,
    recovery_round: int,
) -> tuple[str, dict[str, str]]:
    suffixes = _RECOVERY_SUFFIXES.get(gap_type) or _RECOVERY_SUFFIXES["no_evidence"]
    suffix = suffixes[(recovery_round - 1) % len(suffixes)]
    target = f"{query} {suffix}".strip()[:2000]
    route_name = "table_target" if gap_type == "missing_structured_evidence" else (
        "keyword" if recovery_round % 2 == 0 else "semantic"
    )
    return target, {"production": query, route_name: target}


def _fallback_merge(query: str, pools: list[dict[str, Any]]) -> dict[str, Any]:
    """Small deterministic fallback for isolated callers and unit tests."""
    merged: dict[str, dict[str, Any]] = {}
    for pool_index, pool in enumerate(pools, start=1):
        for rank, raw_hit in enumerate(pool.get("hits") or [], start=1):
            if not isinstance(raw_hit, dict):
                continue
            key = str(raw_hit.get("chunk_id") or raw_hit.get("text") or f"p{pool_index}-{rank}")
            item = merged.setdefault(key, dict(raw_hit))
            item.setdefault("source_ranks", {})[f"pool_{pool_index}"] = rank
    hits = list(merged.values())
    return {
        "query": query,
        "candidate_count": len(hits),
        "retrieval_mode": "agentic_fallback_merge",
        "rerank_model": None,
        "degraded": ["unified_merge_fallback"],
        "hits": hits,
    }


def run_agentic_rag(
    query: str,
    *,
    config: dict[str, Any] | None,
    searcher: SearchFn,
    merger: MergeFn | None = None,
    top_k: int = 10,
    rerank_threshold: float = 0.2,
    final_table: int | None = None,
    final_section: int | None = None,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Run initial fixed RAG plus bounded evidence recovery."""
    original_query = str(query or "").strip()
    if not original_query:
        raise ValueError("query must not be blank")
    active_clock = clock or time.monotonic
    policy = resolve_policy(config)
    started = active_clock()
    trace: dict[str, Any] = {
        "mode": "agentic_rag" if policy["enabled"] else "fixed_rag",
        "initial_query": original_query,
        "max_rounds": policy["max_rounds"],
        "max_search_calls": policy["max_search_calls"],
        "timeout_seconds": policy["timeout_seconds"],
        "rounds": [],
        "search_calls": 0,
        "stop_reason": "",
    }
    pools: list[dict[str, Any]] = []
    last_judgment: dict[str, Any] = {
        "sufficient": False,
        "gap_type": "no_evidence",
        "reason": "尚未执行检索",
        "missing_evidence": "",
    }
    recovery_round = 0
    seen_routes: set[str] = set()

    for round_no in range(1, policy["max_rounds"] + 1):
        elapsed = active_clock() - started
        if elapsed >= policy["timeout_seconds"]:
            trace["stop_reason"] = "timeout"
            break
        if trace["search_calls"] >= policy["max_search_calls"]:
            trace["stop_reason"] = "max_search_calls"
            break

        query_routes: dict[str, str] | None = None
        search_query = original_query
        if round_no > 1:
            recovery_round += 1
            search_query, query_routes = _recovery_routes(
                original_query,
                gap_type=str(last_judgment.get("gap_type") or "no_evidence"),
                recovery_round=recovery_round,
            )
            route_signature = repr(sorted(query_routes.items()))
            if route_signature in seen_routes:
                trace["stop_reason"] = "duplicate_recovery_route"
                break
            seen_routes.add(route_signature)

        trace["search_calls"] += 1
        try:
            result = searcher(original_query, query_routes)
            if not isinstance(result, dict):
                raise TypeError("searcher must return an object")
        except Exception as exc:
            trace["rounds"].append({
                "round": round_no,
                "query": search_query,
                "query_routes": query_routes or {},
                "search_call": trace["search_calls"],
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            trace["stop_reason"] = "retrieval_error"
            break

        pools.append(result)
        last_judgment = judge_evidence_sufficiency(
            original_query,
            result,
            rerank_threshold=rerank_threshold,
        )
        trace["rounds"].append({
            "round": round_no,
            "query": search_query,
            "query_routes": query_routes or {},
            "search_call": trace["search_calls"],
            "candidate_count": result.get("candidate_count"),
            "hit_count": len(result.get("hits") or []),
            "judgment": last_judgment,
        })

        if last_judgment.get("sufficient"):
            trace["stop_reason"] = (
                "evidence_sufficient" if round_no == 1 else "evidence_sufficient_after_recovery"
            )
            break
        if not policy["enabled"]:
            trace["stop_reason"] = "agentic_rag_disabled"
            break

    if not trace["stop_reason"]:
        if active_clock() - started >= policy["timeout_seconds"]:
            trace["stop_reason"] = "timeout"
        elif trace["search_calls"] >= policy["max_search_calls"]:
            trace["stop_reason"] = "max_search_calls"
        else:
            trace["stop_reason"] = "max_rounds"

    if len(pools) > 1:
        if merger is None:
            final_result = _fallback_merge(original_query, pools)
        else:
            final_result = merger(
                original_query,
                pools,
                top_k=top_k,
                final_table=final_table,
                final_section=final_section,
                rerank_threshold=rerank_threshold,
                aggregate_continuation_tables=aggregate_continuation_tables,
                expand_references=expand_references,
            )
    elif pools:
        final_result = dict(pools[0])
    else:
        final_result = {
            "query": original_query,
            "candidate_count": 0,
            "retrieval_mode": "agentic_empty",
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    final_judgment = judge_evidence_sufficiency(
        original_query,
        final_result,
        rerank_threshold=rerank_threshold,
    )
    trace["final_judgment"] = final_judgment
    trace["elapsed_ms"] = round(max(0.0, active_clock() - started) * 1000, 3)
    trace["pool_count"] = len(pools)
    final_result["agentic_rag_trace"] = trace
    final_result["fixed_judge"] = final_judgment
    return {"retrieval": final_result, "trace": trace}
