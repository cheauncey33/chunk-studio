from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_hybrid_retrieval_ablation import (
    aggregate_policy,
    compare_policies,
    evaluate_ranking,
)


def _hit(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "business_metadata": {"content_type": "table"},
    }


def test_evaluate_ranking_matches_gold_by_stable_text_hash() -> None:
    from app.evidence_locator import chunk_text_sha256

    gold_hash = chunk_text_sha256("target evidence")
    result = evaluate_ranking(
        [_hit("a", "other"), _hit("b", "target evidence")],
        {gold_hash: {"label": "direct_candidate"}},
    )

    assert result["best_direct_rank"] == 2
    assert result["matched_direct"] == [{"text_sha256": gold_hash, "rank": 2}]


def test_aggregate_and_comparison_respect_top_k() -> None:
    cases = [
        {
            "direct_gold_count": 1,
            "rankings": {
                "dense_original": {"best_direct_rank": 12, "matched_direct": [{"rank": 12}]},
                "hybrid_rrf": {"best_direct_rank": 8, "matched_direct": [{"rank": 8}]},
                "hybrid_rerank": {"best_direct_rank": 3, "matched_direct": [{"rank": 3}]},
            },
        },
        {
            "direct_gold_count": 1,
            "rankings": {
                "dense_original": {"best_direct_rank": None, "matched_direct": []},
                "hybrid_rrf": {"best_direct_rank": 2, "matched_direct": [{"rank": 2}]},
                "hybrid_rerank": {"best_direct_rank": 20, "matched_direct": [{"rank": 20}]},
            },
        },
    ]

    rerank = aggregate_policy(cases, "hybrid_rerank")
    comparison = compare_policies(cases, "hybrid_rrf", "hybrid_rerank")

    assert rerank["5"]["direct_case_recall"] == 0.5
    assert rerank["20"]["direct_case_recall"] == 1.0
    assert comparison["5"]["wins"] == 1
    assert comparison["5"]["losses"] == 1
    assert comparison["5"]["net_case_hits"] == 0
