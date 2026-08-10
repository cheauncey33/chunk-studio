from __future__ import annotations

from app import embeddings, retrieval


def test_hybrid_search_keeps_raw_route_when_rewrite_matches_it() -> None:
    query = "original query"

    def vector_searcher(route_query: str, _vector: list[float], **kwargs):
        if kwargs["content_type"] != "section":
            return {"total_candidates": 0, "hits": []}
        return {
            "total_candidates": 1,
            "hits": [{
                "chunk_id": route_query,
                "file_id": "f1",
                "file_name": "std.pdf",
                "page": 1,
                "text": route_query,
                "score": 0.9,
            }],
        }

    result = retrieval.hybrid_search(
        query,
        top_k=1,
        planner=lambda _query: {"semantic": query, "keyword": "keyword route"},
        batch_embedder=lambda queries, **_kwargs: [
            [1.0] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=lambda _query, _documents, _top_n: [(0, 0.9)],
        lexical_enabled=False,
    )

    assert result["degraded"] == []
    assert result["query_routes"] == {
        "production": query,
        "keyword": "keyword route",
    }
