from __future__ import annotations

from app.agent_runtime import AgentPolicy
from app.recovery import runner
from app.recovery.tools import RecoveryToolEnvironment


def test_runner_uses_recovery_pools_and_one_unified_rerank(monkeypatch) -> None:
    actions = iter([
        {
            "type": "tool_call",
            "tool": "search_kb_candidates",
            "arguments": {"query": "绝缘电阻适用条件"},
        },
        {
            "type": "tool_call",
            "tool": "finish_recovery",
            "arguments": {"outcome": "evidence_found"},
        },
    ])
    monkeypatch.setattr(
        runner.DeepSeekActionProvider,
        "__call__",
        lambda self, context, tools: next(actions),
    )
    captured = {}

    def merge(query, pools, **kwargs):
        captured.update(query=query, pools=pools, kwargs=kwargs)
        return {"hits": [{"chunk_id": "recovered"}], "degraded": []}

    monkeypatch.setattr(runner.retrieval, "merge_and_rerank_candidate_pools", merge)
    env = RecoveryToolEnvironment(
        report_markdown="",
        allowed_file_ids=["f1"],
        requirement_text="绝缘电阻应符合规定",
        original_query="绝缘电阻标准要求",
        candidate_search=lambda *args, **kwargs: {
            "hits": [{
                "chunk_id": "c2",
                "file_id": "f1",
                "text": "recovered evidence",
                "business_metadata": {"content_type": "section"},
            }],
            "degraded": [],
        },
    )

    result = runner.run_recovery_agent(
        immutable_state={"case_id": "case-1"},
        environment=env,
        initial_pool={"hits": [{"chunk_id": "c1", "text": "initial"}]},
        policy=AgentPolicy(max_turns=3),
    )

    assert result["result"]["outcome"] == "evidence_found"
    assert result["usage"]["recovery_pool_count"] == 1
    assert len(captured["pools"]) == 2
    assert captured["query"] == "绝缘电阻标准要求"
    assert captured["kwargs"]["top_k"] == 10
    assert captured["kwargs"]["final_table"] is None
    assert captured["kwargs"]["final_section"] is None


def test_runner_does_not_rerank_when_agent_adds_no_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        runner.DeepSeekActionProvider,
        "__call__",
        lambda self, context, tools: {
            "type": "tool_call",
            "tool": "finish_recovery",
            "arguments": {"outcome": "exhausted"},
        },
    )
    monkeypatch.setattr(
        runner.retrieval,
        "merge_and_rerank_candidate_pools",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not rerank")),
    )
    env = RecoveryToolEnvironment(
        "",
        ["f1"],
        "requirement",
        "query",
        candidate_search=lambda *args, **kwargs: {"hits": []},
    )
    result = runner.run_recovery_agent(
        immutable_state={"case_id": "case-1"},
        environment=env,
        initial_pool={"hits": []},
    )
    assert result["merged_retrieval"] is None


def test_runner_uses_one_deep_pool_fallback_when_agent_adds_no_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        runner.DeepSeekActionProvider,
        "__call__",
        lambda self, context, tools: {
            "type": "tool_call",
            "tool": "finish_recovery",
            "arguments": {"outcome": "exhausted"},
        },
    )
    captured = {}
    monkeypatch.setattr(
        runner.retrieval,
        "merge_and_rerank_candidate_pools",
        lambda query, pools, **kwargs: captured.update(query=query, pools=pools)
        or {"hits": []},
    )
    env = RecoveryToolEnvironment(
        "",
        ["f1"],
        "requirement",
        "value-free query",
        candidate_search=lambda *args, **kwargs: {
            "hits": [{"chunk_id": "deep-1", "text": "deep evidence"}]
        },
    )

    result = runner.run_recovery_agent(
        immutable_state={"case_id": "case-1"},
        environment=env,
        initial_pool={"hits": []},
    )

    assert result["usage"]["fallback_deep_pool"] is True
    assert result["usage"]["recovery_pool_count"] == 1
    assert len(captured["pools"]) == 2


def test_runner_registers_initial_candidates_for_context_expansion(monkeypatch) -> None:
    actions = iter([
        {
            "type": "tool_call",
            "tool": "expand_evidence_context",
            "arguments": {"candidate_keys": ["c08"]},
        },
        {
            "type": "tool_call",
            "tool": "finish_recovery",
            "arguments": {"outcome": "evidence_found"},
        },
    ])
    monkeypatch.setattr(
        runner.DeepSeekActionProvider,
        "__call__",
        lambda self, context, tools: next(actions),
    )
    monkeypatch.setattr(
        runner.retrieval,
        "merge_and_rerank_candidate_pools",
        lambda *args, **kwargs: {"hits": []},
    )
    monkeypatch.setattr(
        runner.retrieval,
        "_enrich_evidence_hits",
        lambda hits, **kwargs: hits,
    )
    env = RecoveryToolEnvironment("", ["f1"], "requirement", "query")

    result = runner.run_recovery_agent(
        immutable_state={"case_id": "case-1"},
        environment=env,
        initial_pool={
            "hits": [
                {
                    "candidate_key": "c08",
                    "chunk_id": "chunk-8",
                    "text": "initial evidence",
                    "business_metadata": {"content_type": "section"},
                }
            ]
        },
        policy=AgentPolicy(max_turns=2),
    )

    assert result["result"]["outcome"] == "evidence_found"
    assert result["mutable_state"]["tool_errors"] == []
    assert result["usage"]["recovery_pool_count"] == 1
