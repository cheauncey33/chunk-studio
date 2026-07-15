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
    )

    assert embed_calls == [[query, planned["semantic"], planned["keyword"]]]
    assert len(search_calls) == 6
    assert all(top_k == retrieval.ROUTE_TOP_K for _, _, top_k in search_calls)
    assert result["query_routes"] == {"production": query, **planned}
    assert result["candidate_count"] == 3
    assert result["total_candidates"] == 3
    assert result["retrieval_mode"] == "hybrid_rerank"
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
    )

    assert result["query_routes"] == {"production": query}
    assert result["degraded"] == ["query_rewrite_failed"]
    assert result["retrieval_mode"] == "hybrid_rerank"
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
    )

    assert result["retrieval_mode"] == "rrf_fallback"
    assert result["rerank_model"] is None
    assert result["degraded"] == ["rerank_failed"]
    assert [hit["chunk_id"] for hit in result["hits"]] == ["b"]
    assert result["hits"][0]["rerank_score"] is None
    assert result["hits"][0]["route_ranks"] == {"semantic": 1, "keyword": 1}


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
