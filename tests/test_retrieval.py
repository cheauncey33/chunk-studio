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


def test_hybrid_search_uses_injected_planner_routes_without_second_rewrite() -> None:
    query = "生产查询"
    routes = {
        "production": query,
        "semantic": "语义查询",
        "keyword": "关键词",
        "table_target": "专用表查询",
        "section_target": "专用章节查询",
    }
    search_calls: list[tuple[str, str]] = []
    planner_calls: list[str] = []

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        search_calls.append((route_query, content_type))
        if content_type == "table" and route_query == "专用表查询":
            return {
                "total_candidates": 1,
                "hits": [_hit("table-special", 0.99, content_type="table", text="table special")],
            }
        if content_type == "section" and route_query == "专用章节查询":
            return {
                "total_candidates": 1,
                "hits": [_hit("section-special", 0.98, content_type="section", text="section special")],
            }
        if content_type == "table" and route_query == query:
            return {
                "total_candidates": 1,
                "hits": [_hit("table-prod", 0.5, content_type="table", text="table prod")],
            }
        return {"total_candidates": 0, "hits": []}

    result = retrieval.hybrid_search(
        query,
        top_k=4,
        query_routes=routes,
        special_route_reserve=1,
        final_per_type=1,
        planner=lambda value: planner_calls.append(value) or {"semantic": "x", "keyword": "y"},
        batch_embedder=lambda queries, **kwargs: [
            [float(index)] * embeddings.DEFAULT_DIMENSION for index, _ in enumerate(queries, 1)
        ],
        vector_searcher=vector_searcher,
        reranker=lambda original_query, documents, top_n: [
            (index, 1.0 - index * 0.01) for index in range(min(top_n, len(documents)))
        ],
        lexical_enabled=False,
    )

    assert planner_calls == []
    assert result["routes_injected"] is True
    assert result["query_routes"]["table_target"] == "专用表查询"
    assert ("专用表查询", "table") in search_calls
    assert ("专用表查询", "section") not in search_calls
    assert ("专用章节查询", "section") in search_calls
    assert ("专用章节查询", "table") not in search_calls
    hit_ids = {hit["chunk_id"] for hit in result["hits"]}
    assert "table-special" in hit_ids
    assert "section-special" in hit_ids
    assert result["final_per_type"] == 1
    assert len(result["hits"]) == 2
    VectorSearchResponse.model_validate(result)


def test_special_route_reserve_keeps_diluted_hits_in_pool() -> None:
    """table_target hit survives even when RRF pool would otherwise drop it."""
    query = "生产"

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        if content_type != "table":
            return {"total_candidates": 0, "hits": []}
        if route_query == "表专用":
            return {
                "total_candidates": 1,
                "hits": [_hit("special", 0.4, content_type="table", text="special table")],
            }
        # Strong general hits that would dominate a tiny candidates_per_type pool.
        return {
            "total_candidates": 3,
            "hits": [
                _hit("g1", 0.99, content_type="table", text="general 1"),
                _hit("g2", 0.98, content_type="table", text="general 2"),
                _hit("g3", 0.97, content_type="table", text="general 3"),
            ],
        }

    seen_docs: list[str] = []

    def reranker(original_query: str, documents: list[str], top_n: int):
        seen_docs.extend(documents)
        return [(index, 0.5) for index in range(min(top_n, len(documents)))]

    retrieval.hybrid_search(
        query,
        top_k=2,
        candidates_per_type=2,
        query_routes={
            "production": query,
            "semantic": "语义",
            "keyword": "关键词",
            "table_target": "表专用",
        },
        special_route_reserve=1,
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=reranker,
        lexical_enabled=False,
    )

    assert any("special table" in doc for doc in seen_docs)


def test_final_per_type_slices_after_rerank() -> None:
    query = "query"

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        if content_type == "table":
            return {
                "total_candidates": 3,
                "hits": [
                    _hit("t1", 0.9, content_type="table", text="table 1"),
                    _hit("t2", 0.8, content_type="table", text="table 2"),
                    _hit("t3", 0.7, content_type="table", text="table 3"),
                ],
            }
        return {
            "total_candidates": 3,
            "hits": [
                _hit("s1", 0.9, content_type="section", text="section 1"),
                _hit("s2", 0.8, content_type="section", text="section 2"),
                _hit("s3", 0.7, content_type="section", text="section 3"),
            ],
        }

    def reranker(original_query: str, documents: list[str], top_n: int):
        # Prefer all tables first to show typed quota still keeps sections.
        ranked = []
        for index, text in enumerate(documents):
            score = 0.9 if "table" in text else 0.1
            ranked.append((index, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:top_n]

    result = retrieval.hybrid_search(
        query,
        top_k=2,
        final_per_type=1,
        planner=lambda value: {"semantic": "semantic", "keyword": "keyword"},
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=reranker,
        lexical_enabled=False,
    )

    types = [
        hit["business_metadata"]["content_type"]
        for hit in result["hits"]
    ]
    assert types.count("table") == 1
    assert types.count("section") == 1
    assert len(result["hits"]) == 2


def test_final_table_section_asymmetric_quota_after_rerank() -> None:
    query = "query"

    def vector_searcher(route_query: str, vector: list[float], **kwargs):
        content_type = kwargs["content_type"]
        if content_type == "table":
            return {
                "total_candidates": 5,
                "hits": [
                    _hit(f"t{i}", 0.9 - i * 0.01, content_type="table", text=f"table {i}")
                    for i in range(1, 6)
                ],
            }
        return {
            "total_candidates": 5,
            "hits": [
                _hit(f"s{i}", 0.9 - i * 0.01, content_type="section", text=f"section {i}")
                for i in range(1, 6)
            ],
        }

    def reranker(original_query: str, documents: list[str], top_n: int):
        ranked = [(index, 1.0 - index * 0.01) for index in range(len(documents))]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:top_n]

    result = retrieval.hybrid_search(
        query,
        top_k=12,
        final_table=3,
        final_section=1,
        planner=lambda value: {"semantic": "semantic", "keyword": "keyword"},
        batch_embedder=lambda queries, **kwargs: [
            [0.1] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=reranker,
        lexical_enabled=False,
    )

    types = [hit["business_metadata"]["content_type"] for hit in result["hits"]]
    assert types.count("table") == 3
    assert types.count("section") == 1
    assert len(result["hits"]) == 4
    assert result["final_table"] == 3
    assert result["final_section"] == 1


def test_enrich_evidence_hits_expands_references_then_aggregates_continuations(monkeypatch) -> None:
    section = {
        "chunk_id": "s1",
        "score": 0.8,
        "file_id": "file-1",
        "file_name": "std.pdf",
        "page": 2,
        "crop_url": None,
        "text": "试验电压见表 7。",
        "business_metadata": {"content_type": "section", "standard_no": "GB/T 1"},
        "source_trace": {},
        "rrf_score": 0.1,
        "rerank_score": 0.9,
        "route_ranks": {"production": 1},
        "retrieval_sources": ["dense"],
        "source_ranks": {},
    }
    table_page = {
        "chunk_id": "t7a",
        "score": 0.7,
        "file_id": "file-1",
        "file_name": "std.pdf",
        "page": 9,
        "crop_url": None,
        "text": "表7 第一页",
        "business_metadata": {
            "content_type": "table",
            "standard_no": "GB/T 1",
            "table_no": "7",
            "table_title": "试验电压",
        },
        "source_trace": {},
        "rrf_score": 0.05,
        "rerank_score": 0.8,
        "route_ranks": {"production": 2},
        "retrieval_sources": ["dense"],
        "source_ranks": {},
    }

    monkeypatch.setattr(
        retrieval,
        "_safe_fetch_table_chunks",
        lambda file_id, table_no: [
            {
                "chunk_id": "t7a",
                "file_id": file_id,
                "file_name": "std.pdf",
                "page": 9,
                "text": "表7 第一页",
                "business_metadata": table_page["business_metadata"],
                "source_trace": {},
                "content_type": "table",
            },
            {
                "chunk_id": "t7b",
                "file_id": file_id,
                "file_name": "std.pdf",
                "page": 10,
                "text": "表7（续）第二页",
                "business_metadata": {
                    **table_page["business_metadata"],
                    "table_title": "(续)",
                    "table_kind": "continued_table",
                },
                "source_trace": {},
                "content_type": "table",
            },
        ] if table_no == "7" else [],
    )
    monkeypatch.setattr(
        retrieval,
        "_safe_table_group_members",
        lambda file_id, standard_no, table_no: [
            {
                "chunk_id": "t7a",
                "file_id": file_id,
                "file_name": "std.pdf",
                "page": 9,
                "text": "表7 第一页",
                "business_metadata": table_page["business_metadata"],
                "source_trace": {},
                "content_type": "table",
            },
            {
                "chunk_id": "t7b",
                "file_id": file_id,
                "file_name": "std.pdf",
                "page": 10,
                "text": "表7（续）第二页",
                "business_metadata": {
                    **table_page["business_metadata"],
                    "table_title": "(续)",
                    "table_kind": "continued_table",
                },
                "source_trace": {},
                "content_type": "table",
            },
        ],
    )

    enriched = retrieval._enrich_evidence_hits([section])
    assert [hit["chunk_id"] for hit in enriched] == ["s1", "t7a"]
    table_hit = enriched[1]
    assert table_hit["added_by"] == "reference_expansion"
    assert "表7 第一页" in table_hit["text"]
    assert "表7（续）第二页" in table_hit["text"]
    assert [m["chunk_id"] for m in table_hit["evidence_unit"]["members"]] == ["t7a", "t7b"]


def test_retrieve_candidate_pool_skips_external_rerank() -> None:
    vector_hit = {
        "chunk_id": "c1",
        "file_id": "f1",
        "text": "same evidence",
        "score": 0.8,
        "business_metadata": {"content_type": "section"},
    }

    result = retrieval.retrieve_candidate_pool(
        "query",
        query_routes={"production": "query"},
        batch_embedder=lambda queries, **kwargs: [[0.1] for _ in queries],
        vector_searcher=lambda query, vector, **kwargs: {
            "total_candidates": 1,
            "hits": [vector_hit],
        },
        lexical_enabled=False,
    )

    assert result["retrieval_mode"] == "dense_candidate_pool"
    assert result["rerank_model"] is None
    assert result["hits"][0]["rerank_score"] is None
    assert result["hits"][0]["source_ranks"]


def test_merge_candidate_pools_deduplicates_text_and_reranks_once() -> None:
    calls = []
    shared = {
        "chunk_id": "c1",
        "file_id": "f1",
        "text": "same evidence",
        "score": 0.8,
        "business_metadata": {"content_type": "section"},
        "source_ranks": {"dense:production": 1},
        "route_ranks": {"production": 1},
        "retrieval_sources": ["dense"],
    }
    duplicate = {**shared, "chunk_id": "c2"}

    def reranker(query, documents, top_n):
        calls.append((query, list(documents), top_n))
        return [(0, 0.9)]

    result = retrieval.merge_and_rerank_candidate_pools(
        "original value-free query",
        [{"hits": [shared]}, {"hits": [duplicate]}],
        top_k=10,
        reranker=reranker,
    )

    assert result["candidate_count"] == 1
    assert result["dedup_strategy"] == "chunk_text_sha256"
    assert len(calls) == 1
    assert calls[0][0] == "original value-free query"
    assert len(result["hits"][0]["source_ranks"]) == 2


def test_merge_candidate_pools_keeps_zero_rerank_score_below_threshold() -> None:
    hit = {
        "chunk_id": "c1",
        "file_id": "f1",
        "text": "irrelevant evidence",
        "score": 0.99,
        "business_metadata": {"content_type": "section"},
        "source_ranks": {"dense:production": 1},
        "route_ranks": {"production": 1},
        "retrieval_sources": ["dense"],
    }

    result = retrieval.merge_and_rerank_candidate_pools(
        "original query",
        [{"hits": [hit]}],
        top_k=10,
        similarity_threshold=0.5,
        reranker=lambda query, documents, top_n: [(0, 0.0)],
    )

    assert result["hits"] == []
