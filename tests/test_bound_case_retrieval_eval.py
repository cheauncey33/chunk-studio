from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_bound_case_retrieval as evaluation  # noqa: E402
from evaluate_bound_case_retrieval import aggregate  # noqa: E402


def test_aggregate_reports_requested_top_k_values() -> None:
    cases = [
        {"required_groups": [{"best_rank": 1}, {"best_rank": 8}]},
        {"required_groups": [{"best_rank": 3}]},
        {"required_groups": [{"best_rank": None}]},
    ]

    result = aggregate(cases)

    assert list(result) == ["1", "3", "5", "8", "10", "15", "20", "30"]
    assert result["1"]["strict_case_hits"] == 0
    assert result["1"]["recalled_groups"] == 1
    assert result["3"]["strict_case_hits"] == 1
    assert result["8"]["strict_case_hits"] == 2
    assert result["30"]["evidence_group_recall"] == 3 / 4


def test_diagnostic_rows_classify_reranker_loss(monkeypatch) -> None:
    monkeypatch.setattr(evaluation, "build_production_query", lambda _case: "query")
    key = evaluation.query_key("query")
    cases = [
        {
            "case_id": "case-1",
            "required_evidence_groups": [
                {
                    "group_id": "required_1",
                    "alternatives": [
                        {
                            "locator": {
                                "text_sha256": "gold",
                                "standard_no": "GB/T 1-2024",
                                "section": "4.1",
                                "content_type": "section",
                            }
                        }
                    ],
                }
            ],
        }
    ]
    runs = {
        key: {
            "degraded": [],
            "diagnostics": {
                "sources": {"dense:semantic:section": [{"rank": 2, "text_sha256": "gold"}]},
                "fusion": [{"rank": 4, "text_sha256": "gold"}],
                "rerank": [{"rank": 31, "text_sha256": "gold"}],
            },
        }
    }

    rows = evaluation.build_diagnostic_rows(cases, runs)

    assert rows[0]["source_ranks"] == '{"dense:semantic:section": 2}'
    assert rows[0]["fusion_rank"] == 4
    assert rows[0]["rerank_rank"] == 31
    assert rows[0]["outcome"] == "reranked_below_30"
