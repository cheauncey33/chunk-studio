from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import embeddings, retrieval
from app.models import VectorSearchResponse


def _hit(chunk_id: str, score: float, *, content_type: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "score": score,
        "file_id": "file-1",
        "file_name": "standard.pdf",
        "page": 1,
        "crop_url": None,
        "text": text,
        "business_metadata": {
            "standard_no": "GB/T 1-2024",
            "content_type": content_type,
            "table_title": "参数表" if content_type == "table" else None,
            "section_title": "试验规则" if content_type == "section" else None,
        },
        "source_trace": {"page_start": 1, "page_end": 1},
    }


def test_hybrid_search_uses_typed_rrf_candidates_then_reranker() -> None:
    query = "原始查询 10 kV"
    planned = {"semantic": "语义查询 10 kV", "keyword": "关键词 10 kV"}
    embed_calls: list[list[str]] = []
    search_calls: list[tuple[str, str, int]] = []

    table_hits = {
        query: [
            _hit("a", 0.91, content_type="table", text="document A"),
            _hit("b", 0.80, content_type="table", text="document B"),
        ],
        planned["semantic"]: [_hit("b", 0.95, content_type="table", text="document B")],
        planned["keyword"]: [_hit("b", 0.93, content_type="table", text="document B")],
    }

    def batch_embedder(queries: list[str], **kwargs):
        embed_calls.append(queries)
        return [[float(index)] * embeddings.DEFAULT_DIMENSION for index, _ in enumerate(queries, 1)]

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        search_calls.append((route_query, content_type, kwargs["top_k"]))
        hits = table_hits.get(route_query, []) if content_type == "table" else []
        if content_type == "section" and route_query == query:
            hits = [_hit("c", 0.88, content_type="section", text="document C")]
        return {
            "total_candidates": 2 if content_type == "table" else 1,
            "hits": hits,
        }

    def reranker(original_query: str, documents: list[str], top_n: int):
        assert original_query == query
        assert top_n == 2
        index_a = next(index for index, text in enumerate(documents) if "document A" in text)
        index_c = next(index for index, text in enumerate(documents) if "document C" in text)
        return [(index_a, 0.99), (index_c, 0.75)]

    result = retrieval.hybrid_search(
        query,
        top_k=2,
        planner=lambda value: planned,
        batch_embedder=batch_embedder,
        vector_searcher=vector_searcher,
        reranker=reranker,
        lexical_enabled=False,
    )

    assert embed_calls == [[query, planned["semantic"], planned["keyword"]]]
    assert len(search_calls) == 6
    assert all(top_k == retrieval.ROUTE_TOP_K for _, _, top_k in search_calls)
    assert result["query_routes"] == {"production": query, **planned}
    assert result["candidate_count"] == 3
    assert result["total_candidates"] == 3
    assert result["retrieval_mode"] == "dense_rerank"
    assert result["degraded"] == []
    assert [hit["chunk_id"] for hit in result["hits"]] == ["a", "c"]
    assert result["hits"][0]["rerank_score"] == 0.99
    assert result["hits"][0]["route_ranks"] == {"production": 1}
    VectorSearchResponse.model_validate(result)


def test_hybrid_search_falls_back_to_original_query_when_rewrite_fails() -> None:
    query = "原始查询"

    def fail_planner(value: str) -> dict[str, str]:
        raise RuntimeError("planner unavailable")

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        return {
            "total_candidates": 1 if content_type == "table" else 0,
            "hits": (
                [_hit("a", 0.8, content_type="table", text="document A")]
                if content_type == "table"
                else []
            ),
        }

    result = retrieval.hybrid_search(
        query,
        planner=fail_planner,
        batch_embedder=lambda queries, **kwargs: [[1.0] * embeddings.DEFAULT_DIMENSION],
        vector_searcher=vector_searcher,
        reranker=lambda query, documents, top_n: [(0, 0.9)],
        lexical_enabled=False,
    )

    assert result["query_routes"] == {"production": query}
    assert result["degraded"] == ["query_rewrite_failed"]
    assert result["retrieval_mode"] == "dense_rerank"
    assert [hit["chunk_id"] for hit in result["hits"]] == ["a"]


def test_hybrid_search_falls_back_to_rrf_when_reranker_fails() -> None:
    query = "原始查询"
    planned = {"semantic": "语义查询", "keyword": "关键词查询"}

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        if content_type == "section":
            return {"total_candidates": 0, "hits": []}
        hits = (
            [_hit("a", 0.9, content_type="table", text="document A")]
            if route_query == query
            else [_hit("b", 0.8, content_type="table", text="document B")]
        )
        return {"total_candidates": 2, "hits": hits}

    def fail_reranker(query: str, documents: list[str], top_n: int):
        raise RuntimeError("reranker unavailable")

    result = retrieval.hybrid_search(
        query,
        top_k=1,
        planner=lambda value: planned,
        batch_embedder=lambda queries, **kwargs: [
            [float(index)] * embeddings.DEFAULT_DIMENSION for index, _ in enumerate(queries, 1)
        ],
        vector_searcher=vector_searcher,
        reranker=fail_reranker,
        lexical_enabled=False,
    )

    assert result["retrieval_mode"] == "dense_rrf_fallback"
    assert result["rerank_model"] is None
    assert result["degraded"] == ["rerank_failed"]
    assert [hit["chunk_id"] for hit in result["hits"]] == ["b"]
    assert result["hits"][0]["rerank_score"] is None
    assert result["hits"][0]["route_ranks"] == {"semantic": 1, "keyword": 1}


def test_hybrid_search_merges_dense_and_lexical_candidates_before_rerank() -> None:
    query = "200 kVA 空载损耗"
    planned = {"semantic": "200 kVA变压器空载损耗标准证据", "keyword": "200 kVA 空载损耗 kW"}
    lexical_calls: list[tuple[str, str, int, bool]] = []

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        if content_type == "section":
            return {"total_candidates": 1, "hits": []}
        return {
            "total_candidates": 3,
            "hits": [
                _hit("shared", 0.9, content_type="table", text="shared document"),
                _hit("dense-only", 0.8, content_type="table", text="dense document"),
            ],
        }

    def lexical_searcher(route_query: str, **kwargs):
        lexical_calls.append((route_query, kwargs["content_type"], kwargs["top_k"], kwargs["sync"]))
        if kwargs["content_type"] == "section":
            return {"hits": []}
        return {
            "hits": [
                _hit("shared", 12.0, content_type="table", text="shared document"),
                _hit("lexical-only", 10.0, content_type="table", text="lexical document"),
            ]
        }

    rerank_documents_seen: list[str] = []

    def reranker(original_query: str, documents: list[str], top_n: int):
        rerank_documents_seen.extend(documents)
        return [(index, 1.0 - index / 10) for index in range(top_n)]

    result = retrieval.hybrid_search(
        query,
        top_k=3,
        planner=lambda value: planned,
        batch_embedder=lambda queries, **kwargs: [
            [float(index)] * embeddings.DEFAULT_DIMENSION for index, _ in enumerate(queries, 1)
        ],
        vector_searcher=vector_searcher,
        lexical_searcher=lexical_searcher,
        lexical_enabled=True,
        reranker=reranker,
    )

    assert len(lexical_calls) == 4
    assert {call[0] for call in lexical_calls} == {query, planned["keyword"]}
    assert all(call[2] == retrieval.ROUTE_TOP_K for call in lexical_calls)
    assert [call[3] for call in lexical_calls] == [True, False, False, False]
    assert result["retrieval_mode"] == "dual_rerank"
    assert result["candidate_count"] == 3
    assert len(rerank_documents_seen) == 3
    shared = next(hit for hit in result["hits"] if hit["chunk_id"] == "shared")
    assert shared["retrieval_sources"] == ["dense", "lexical"]
    assert set(shared["source_ranks"]) == {
        "dense:production", "dense:semantic", "dense:keyword",
        "lexical:production", "lexical:keyword",
    }


def test_hybrid_search_falls_back_to_dense_when_lexical_fails() -> None:
    query = "原始查询"
    result = retrieval.hybrid_search(
        query,
        planner=lambda value: {"semantic": "语义查询", "keyword": "关键词查询"},
        batch_embedder=lambda queries, **kwargs: [
            [float(index)] * embeddings.DEFAULT_DIMENSION for index, _ in enumerate(queries, 1)
        ],
        vector_searcher=lambda route_query, vector, **kwargs: {
            "total_candidates": 1,
            "hits": [
                _hit("dense", 0.8, content_type=kwargs["content_type"], text="dense document")
            ],
        },
        lexical_searcher=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("fts unavailable")
        ),
        lexical_enabled=True,
        reranker=lambda query, documents, top_n: [(0, 0.9)],
    )

    assert result["retrieval_mode"] == "dense_rerank"
    assert result["degraded"] == ["lexical_retrieval_failed"]
    assert result["hits"][0]["retrieval_sources"] == ["dense"]


def test_query_rewrite_validation_blocks_new_facts() -> None:
    query = "S20 200 kVA 负载损耗 2.40 kW"

    assert retrieval._validate_rewrite(
        query,
        "S20 200 kVA负载损耗2.400 kW的标准要求",
        route="semantic",
    )
    with pytest.raises(ValueError, match="introduced numbers"):
        retrieval._validate_rewrite(query, "负载损耗2.40 kW，允许偏差15%", route="semantic")
    with pytest.raises(ValueError, match="standard reference"):
        retrieval._validate_rewrite(
            query,
            "Q/GDW 12126.4中S20 200 kVA负载损耗2.40 kW",
            route="semantic",
        )


def test_query_rewrite_normalizes_split_ocr_equation_before_validation(monkeypatch) -> None:
    query = (
        "S20-M.RL-200/10-NX2 200 kVA 10/0.4 kV 配电变压器 "
        "感应耐压试验(IVW) 持续时间(s): <eq>15 \\leq t \\leq 6</eq>0"
    )
    captured: dict = {}

    def chat_json(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {
            "semantic": "配电变压器感应耐压试验持续时间 15 ≤ t ≤ 60 s 的标准证据",
            "keyword": "配电变压器 IVW 感应耐压试验 持续时间 15 ≤ t ≤ 60 s",
        }

    monkeypatch.setattr(retrieval.llm, "chat_json", chat_json)

    rewrites = retrieval.plan_query_rewrites(query)

    planner_query = captured["messages"][1]["content"]
    assert captured["model"] == "deepseek-v4-flash"
    assert planner_query.endswith("持续时间(s): 15 ≤ t ≤ 60")
    assert "<eq>" not in planner_query
    assert "\\leq" not in planner_query
    assert rewrites["semantic"] == "配电变压器感应耐压试验持续时间 15 ≤ t ≤ 60 s 的标准证据"


def test_qwen_reranker_does_not_fall_back_to_deepseek_key(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(retrieval.llm.db, "get_setting", lambda key, default="": "deepseek-key")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        retrieval.rerank_documents("query", ["document"], 1)


def test_query_rewrite_rejects_replaced_domain_term() -> None:
    query = "配电变压器 空载电流限值 0.18%"

    assert retrieval._query_domain_anchors(query) == ["配电变压器", "空载电流"]
    with pytest.raises(ValueError, match="omitted domain terms"):
        retrieval._validate_rewrite(
            query,
            "配电变压器短路阻抗限值0.18%的标准要求",
            route="semantic",
        )


def test_query_rewrite_normalization_preserves_normal_text() -> None:
    query = "S20 200 kVA 负载损耗 2.185 kW，允许偏差±10%"

    assert retrieval._normalize_query_for_rewrite(query) == query


def test_rerank_document_strips_table_markup_before_truncation() -> None:
    filler = "".join(f"<tr><td>{index}</td><td>普通值</td></tr>" for index in range(100))
    target = "<tr><td>200</td><td>2 185</td></tr>"
    candidate = {
        "hit": _hit(
            "table",
            0.8,
            content_type="table",
            text=f"<table>{filler}{target}</table>",
        )
    }

    document = retrieval._rerank_document(candidate)

    assert "<td>" not in document
    assert "200 2 185" in document
    assert len(document) <= retrieval.MAX_RERANK_DOCUMENT_CHARS


def test_hybrid_search_passes_knowledge_base_file_scope_to_dense_search() -> None:
    seen_scopes: list[list[str]] = []

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        seen_scopes.append(kwargs["file_ids"])
        content_type = kwargs["content_type"]
        return {
            "total_candidates": 1 if content_type == "table" else 0,
            "hits": (
                [_hit("inside", 0.9, content_type="table", text="scoped")]
                if content_type == "table"
                else []
            ),
        }

    result = retrieval.hybrid_search(
        "query",
        top_k=1,
        planner=lambda value: {"semantic": "semantic", "keyword": "keyword"},
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=lambda query, documents, top_n: [(0, 0.9)],
        lexical_enabled=False,
        file_ids=["file-in-kb"],
    )

    assert seen_scopes == [["file-in-kb"]] * 6
    assert [hit["chunk_id"] for hit in result["hits"]] == ["inside"]


def test_hybrid_search_honors_route_top_k_and_similarity_threshold() -> None:
    seen_top_k: list[int] = []

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        seen_top_k.append(kwargs["top_k"])
        content_type = kwargs["content_type"]
        if content_type != "table":
            return {"total_candidates": 0, "hits": []}
        return {
            "total_candidates": 2,
            "hits": [
                _hit("keep", 0.9, content_type="table", text="keep me"),
                _hit("drop", 0.4, content_type="table", text="drop me"),
            ],
        }

    def reranker(original_query: str, documents: list[str], top_n: int):
        ranked = []
        for index, text in enumerate(documents):
            score = 0.95 if "keep" in text else 0.1
            ranked.append((index, score))
        return ranked[:top_n]

    result = retrieval.hybrid_search(
        "query",
        top_k=5,
        route_top_k=7,
        candidates_per_type=5,
        rrf_k=40,
        similarity_threshold=0.5,
        planner=lambda value: {"semantic": "semantic", "keyword": "keyword"},
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=reranker,
        lexical_enabled=False,
    )

    assert seen_top_k
    assert all(value == 7 for value in seen_top_k)
    assert [hit["chunk_id"] for hit in result["hits"]] == ["keep"]


def test_similarity_threshold_does_not_replace_zero_rerank_score_with_dense_score() -> None:
    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        if kwargs["content_type"] != "table":
            return {"total_candidates": 0, "hits": []}
        return {
            "total_candidates": 1,
            "hits": [_hit("rejected", 0.9, content_type="table", text="candidate")],
        }

    result = retrieval.hybrid_search(
        "query",
        top_k=1,
        similarity_threshold=0.5,
        planner=lambda value: {"semantic": "semantic", "keyword": "keyword"},
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=lambda query, documents, top_n: [(0, 0.0)],
        lexical_enabled=False,
    )

    assert result["hits"] == []
