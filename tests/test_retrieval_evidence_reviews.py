from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_retrieval_evidence_reviews as evidence_reviews
from build_retrieval_evidence_reviews import _merge_hits, _parse_json_object, _production_query


def test_merge_hits_uses_versioned_retrieval_parameters(monkeypatch) -> None:
    calls: list[int] = []

    def fake_search(query: str, *, top_k: int, content_type: str):
        calls.append(top_k)
        return {
            "hits": [
                {
                    "chunk_id": f"{query}-1",
                    "score": 0.9,
                    "file_id": "f",
                    "file_name": "standard.pdf",
                    "page": 1,
                    "crop_url": None,
                    "text": "evidence",
                    "business_metadata": {},
                    "source_trace": {},
                },
                {
                    "chunk_id": f"{query}-2",
                    "score": 0.8,
                    "file_id": "f",
                    "file_name": "standard.pdf",
                    "page": 2,
                    "crop_url": None,
                    "text": "evidence",
                    "business_metadata": {},
                    "source_trace": {},
                },
            ]
        }

    monkeypatch.setattr(evidence_reviews.embeddings, "vector_search", fake_search)
    hits = _merge_hits(
        {"production": "q1", "semantic": "q2"},
        "table",
        route_top_k=7,
        final_per_type=1,
        rrf_k=20,
    )

    assert calls == [7, 7]
    assert len(hits) == 1


def test_production_query_keeps_context_item_and_requirement() -> None:
    query = _production_query({
        "sample_context": {"model": "S20", "rated_capacity": "200 kVA"},
        "test_item": {"project_name": "负载损耗测量"},
        "reported_requirement": {"text": "负载损耗Pk(kW): ≤2.185"},
    })
    assert query == "S20 200 kVA 负载损耗测量 负载损耗Pk(kW): ≤2.185"


def test_parse_json_object_accepts_fenced_json() -> None:
    assert _parse_json_object('```json\n{"selected":[]}\n```') == {"selected": []}
