from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


pytest.importorskip("jieba")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_dual_retrieval_v2.py"
SPEC = importlib.util.spec_from_file_location("evaluate_dual_retrieval_v2", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def lexical_doc(chunk_id: str, *, standard_no: str, title: str, text: str):
    from demo_lexical_retrieval import ChunkDoc

    fields = {field: "" for field in module.active_weights(False)}
    fields.update({"standard_no": standard_no, "table_title": title, "text": text})
    return ChunkDoc(
        chunk_id=chunk_id,
        file_name="standard.pdf",
        page=1,
        fields=fields,
        metadata={"content_type": "table", "standard_no": standard_no, "table_title": title},
    )


def test_bm25_prefers_exact_standard_and_title_terms() -> None:
    exact = lexical_doc(
        "exact",
        standard_no="GB 20052-2024",
        title="10 kV油浸式变压器能效等级",
        text="额定容量200 kVA",
    )
    generic = lexical_doc(
        "generic",
        standard_no="GB/T 1094.1-2013",
        title="变压器通用要求",
        text="额定容量200 kVA",
    )

    ranking = module.score_bm25(
        module.build_lexical_index([generic, exact]),
        "GB 20052-2024 10 kV油浸式变压器",
    )

    assert [item[1].doc.chunk_id for item in ranking] == ["exact", "generic"]


def test_merge_candidate_lists_preserves_family_unique_hits_and_deduplicates() -> None:
    def candidate(chunk_id: str, source: str):
        return {
            "hit": {"chunk_id": chunk_id},
            "content_type": "table",
            "sources": [source],
            "source_ranks": {f"{source}:production": 1},
            "source_scores": {f"{source}:production": 1.0},
            "prior_score": 0.1,
        }

    merged = module.merge_candidate_lists(
        [candidate("shared", "dense"), candidate("dense-only", "dense")],
        [candidate("shared", "lexical"), candidate("lexical-only", "lexical")],
    )

    assert [item["hit"]["chunk_id"] for item in merged] == [
        "shared", "dense-only", "lexical-only"
    ]
    assert merged[0]["sources"] == ["dense", "lexical"]
    assert merged[0]["prior_score"] == pytest.approx(0.2)


def test_paired_candidate_comparison_counts_wins_and_losses() -> None:
    cases = [
        {
            "candidates": {
                "dense_40_per_type": {"strict_complete": False},
                "dual_dense20_lexical20_per_type": {"strict_complete": True},
            }
        },
        {
            "candidates": {
                "dense_40_per_type": {"strict_complete": True},
                "dual_dense20_lexical20_per_type": {"strict_complete": False},
            }
        },
    ]

    comparison = module.paired_candidate_comparison(
        cases, "dual_dense20_lexical20_per_type"
    )

    assert comparison["wins"] == 1
    assert comparison["losses"] == 1
    assert comparison["net_strict_hits"] == 0
