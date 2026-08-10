from __future__ import annotations

from app import agentic_rag, retrieval


def _hit(*, content_type: str = "section", text: str = "direct evidence") -> dict:
    return {
        "chunk_id": text,
        "content_type": content_type,
        "text": text,
        "rerank_score": 0.9,
    }


def test_agentic_rag_stops_after_sufficient_initial_retrieval() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    def search(query: str, routes: dict[str, str] | None) -> dict:
        calls.append((query, routes))
        return {"candidate_count": 1, "hits": [_hit()]}

    result = agentic_rag.run_agentic_rag(
        "额定容量定义",
        config={"agentic_rag_max_rounds": 3, "agentic_rag_max_search_calls": 3},
        searcher=search,
    )

    assert len(calls) == 1
    assert calls[0] == ("额定容量定义", None)
    assert result["trace"]["stop_reason"] == "evidence_sufficient"
    assert result["trace"]["pool_count"] == 1


def test_agentic_rag_recovers_a_missing_table_and_merges_once() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    merge_calls: list[tuple[str, list[dict]]] = []

    def search(query: str, routes: dict[str, str] | None) -> dict:
        calls.append((query, routes))
        if routes is None:
            return {
                "candidate_count": 1,
                "hits": [_hit(text="额定容量定义")],
            }
        return {
            "candidate_count": 1,
            "hits": [_hit(content_type="table", text="额定容量 100 kVA")],
        }

    def merge(query: str, pools: list[dict], **_kwargs) -> dict:
        merge_calls.append((query, pools))
        return {"candidate_count": 1, "hits": pools[-1]["hits"], "degraded": []}

    result = agentic_rag.run_agentic_rag(
        "额定容量表格",
        config={"agentic_rag_max_rounds": 3, "agentic_rag_max_search_calls": 3},
        searcher=search,
        merger=merge,
    )

    assert len(calls) == 2
    assert calls[0] == ("额定容量表格", None)
    assert calls[1][0] == "额定容量表格"
    assert calls[1][1]["production"] == "额定容量表格"
    assert "table_target" in calls[1][1]
    assert len(merge_calls) == 1
    assert merge_calls[0][0] == "额定容量表格"
    assert len(merge_calls[0][1]) == 2
    assert result["trace"]["stop_reason"] == "evidence_sufficient_after_recovery"
    assert result["retrieval"]["fixed_judge"]["sufficient"] is True


def test_agentic_rag_stops_at_search_budget() -> None:
    calls = 0

    def search(_query: str, _routes: dict[str, str] | None) -> dict:
        nonlocal calls
        calls += 1
        return {"candidate_count": 0, "hits": []}

    result = agentic_rag.run_agentic_rag(
        "没有命中",
        config={"agentic_rag_max_rounds": 3, "agentic_rag_max_search_calls": 2},
        searcher=search,
    )

    assert calls == 2
    assert result["trace"]["search_calls"] == 2
    assert result["trace"]["stop_reason"] == "max_search_calls"
    assert result["retrieval"]["fixed_judge"]["gap_type"] == "no_evidence"


def test_agentic_rag_stops_before_second_search_on_timeout() -> None:
    calls = 0
    tick = 0

    def clock() -> float:
        return float(tick)

    def search(_query: str, _routes: dict[str, str] | None) -> dict:
        nonlocal calls, tick
        calls += 1
        tick = 2
        return {"candidate_count": 0, "hits": []}

    result = agentic_rag.run_agentic_rag(
        "超时问题",
        config={"agentic_rag_timeout_seconds": 1},
        searcher=search,
        clock=clock,
    )

    assert calls == 1
    assert result["trace"]["stop_reason"] == "timeout"


def test_retrieval_config_contains_bounded_agentic_rag_settings() -> None:
    normalized = retrieval.normalize_retrieval_config({
        "agentic_rag_enabled": "false",
        "agentic_rag_max_rounds": 99,
        "agentic_rag_max_search_calls": 0,
        "agentic_rag_timeout_seconds": 999,
    })

    assert normalized["agentic_rag_enabled"] is False
    assert normalized["agentic_rag_max_rounds"] == 3
    assert normalized["agentic_rag_max_search_calls"] == 1
    assert normalized["agentic_rag_timeout_seconds"] == 120.0
