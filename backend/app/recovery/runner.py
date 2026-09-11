from __future__ import annotations

import json
from typing import Any

from app import llm, retrieval
from app.agent_runtime import AgentPolicy, run_agent

from .tools import RecoveryToolEnvironment, build_recovery_tools


_RECOVERY_SYSTEM_PROMPT = """你是检测报告审查中的证据恢复 Agent，不负责最终合规判定。
你的任务仅限：补找报告适用参数、生成不含报告目标数值的检索问题、精确定位标准条款/表格、展开已有证据上下文。
必须遵守 immutable_state 中的报告、知识库和文件范围；不得使用常识补值，不得输出 supported 或 mismatch。
优先复用已有结果，不得重复相同工具参数。证据足够或预算不足时调用 finish_recovery。
每轮严格返回一个 JSON 对象：
1. 调用工具：{"type":"tool_call","tool":"工具名","arguments":{...}}
2. 仅当没有可用 finish_recovery 工具时才可直接结束：{"type":"finish","result":{"outcome":"exhausted"}}
不要输出 JSON 之外的文字。"""
_RECOVERY_SYSTEM_PROMPT += """

Operational constraints: every terms/fields array has at most 8 items and
candidate_keys has at most 6. KB queries and exact terms must omit every numeric
literal copied from reported_requirement. After finding useful candidates, call
finish_recovery instead of repeating another broad search. When a candidate
explicitly cites another clause, prefer follow_evidence_references over guessing
the clause and issuing another broad search."""


class DeepSeekActionProvider:
    def __init__(self, *, model: str) -> None:
        self.model = model

    def __call__(
        self,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return llm.chat_json(
            [
                {"role": "system", "content": _RECOVERY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"context": context, "tools": tools},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            model=self.model,
            temperature=0,
        )


def _reduce_recovery_state(
    state: dict[str, Any],
    tool_name: str,
    result: dict[str, Any],
) -> None:
    if not result.get("ok"):
        state.setdefault("tool_errors", []).append({
            "tool": tool_name,
            "error": result.get("error"),
        })
        return
    pool_id = result.get("pool_id")
    if pool_id:
        state.setdefault("pool_ids", []).append(str(pool_id))
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        state.setdefault("candidate_summaries", []).extend(candidates)
        state["candidate_summaries"] = state["candidate_summaries"][-20:]
    query = result.get("query")
    if query:
        state.setdefault("attempted_queries", []).append(str(query))
    parameters = result.get("parameters")
    if isinstance(parameters, list):
        state.setdefault("recovered_parameters", []).extend(parameters)


def run_recovery_agent(
    *,
    immutable_state: dict[str, Any],
    environment: RecoveryToolEnvironment,
    initial_pool: dict[str, Any],
    policy: AgentPolicy | None = None,
) -> dict[str, Any]:
    """Run recovery and return trace plus one unified-rerank candidate result."""
    for rank, hit in enumerate(initial_pool.get("hits") or [], start=1):
        candidate_key = str(hit.get("candidate_key") or f"initial_c{rank:02d}")
        environment.candidate_store.setdefault(candidate_key, hit)
    result = run_agent(
        provider=DeepSeekActionProvider(model=environment.model),
        tools=build_recovery_tools(environment),
        immutable_state=immutable_state,
        mutable_state={
            "pool_ids": [],
            "candidate_summaries": [],
            "recovered_parameters": [],
            "attempted_queries": [],
            "tool_errors": [],
        },
        policy=policy or AgentPolicy(),
        reduce_state=_reduce_recovery_state,
    )

    recovery_pools = [
        environment.pool_store[pool_id]
        for pool_id in result.mutable_state.get("pool_ids") or []
        if pool_id in environment.pool_store
    ]
    fallback_used = False
    if not recovery_pools and environment.original_query.strip():
        config = retrieval.normalize_retrieval_config(environment.retrieval_config)
        fallback_kwargs: dict[str, Any] = {
            "query_routes": {"production": environment.original_query},
            "route_top_k": int(config.get("route_top_k") or retrieval.ROUTE_TOP_K),
            "candidates_per_type": int(
                config.get("candidate_count_per_type")
                or config.get("candidates_per_type")
                or retrieval.CANDIDATES_PER_TYPE
            ),
            "rrf_k": int(config.get("rrf_k") or retrieval.RRF_K),
            "dense_threshold": float(config.get("dense_threshold") or 0.0),
            "special_route_reserve": int(config.get("special_route_reserve") or 0),
            "file_ids": environment.allowed_file_ids,
        }
        if environment.workspace_id:
            fallback_kwargs["workspace_id"] = environment.workspace_id
        fallback_pool = environment.candidate_search(
            environment.original_query,
            **fallback_kwargs,
        )
        if fallback_pool.get("hits"):
            recovery_pools.append(fallback_pool)
            fallback_used = True
    merged = None
    if recovery_pools:
        config = retrieval.normalize_retrieval_config(environment.retrieval_config)
        final_table = config.get("final_table")
        final_section = config.get("final_section")
        typed_delivery_n = (
            int(final_table) + int(final_section)
            if final_table is not None and final_section is not None
            else 0
        )
        merged = retrieval.merge_and_rerank_candidate_pools(
            environment.original_query,
            [initial_pool, *recovery_pools],
            top_k=max(
                int(config.get("top_k") or 10),
                typed_delivery_n,
            ),
            rrf_k=int(config.get("rrf_k") or retrieval.RRF_K),
            final_table=int(final_table) if final_table is not None else None,
            final_section=int(final_section) if final_section is not None else None,
            rerank_threshold=float(
                config.get("rerank_threshold")
                if config.get("rerank_threshold") is not None
                else retrieval.DEFAULT_RERANK_THRESHOLD
            ),
            aggregate_continuation_tables=bool(
                config.get("aggregate_continuation_tables", False)
            ),
            expand_references=bool(config.get("expand_references", False)),
        )

    return {
        "stop_reason": result.stop_reason,
        "result": result.result,
        "mutable_state": result.mutable_state,
        "events": result.events,
        "usage": {
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "search_calls": result.search_calls,
            "elapsed_seconds": result.elapsed_seconds,
            "recovery_pool_count": len(recovery_pools),
            "fallback_deep_pool": fallback_used,
        },
        "merged_retrieval": merged,
    }
