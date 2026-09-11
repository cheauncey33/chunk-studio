from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_bound_case_retrieval as evaluation  # noqa: E402
import evaluate_miss_normalization_ablation as miss_ablation  # noqa: E402
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


def test_miss_ablation_restores_report_context_without_gold_data() -> None:
    def case(
        case_id: str,
        requirement: str,
        *,
        model: str = "",
        source_hash: str = "",
    ) -> dict:
        context = {}
        if model:
            context = {
                "sample_name": "配电变压器",
                "model": model,
                "rated_capacity": "200 kVA" if "200" in model else "400 kVA",
                "rated_voltage": "10/0.4 kV",
            }
        return {
            "case_id": case_id,
            "detection_project": {
                "reported_requirement": {"text": requirement},
                "sample_context": context,
            },
            "source_report": {"source_sha256": source_hash},
        }

    payload = {
        "legacy": {
            "cases": [
                case("legacy-200", "负载损耗 Pk ≤2.185", model="S20-200"),
                case("legacy-400", "负载损耗 Pk ≤3.615", model="S20-400"),
            ]
        },
        "transformer_extension": {
            "cases": [
                case("new-200", "负载损耗 Pₖ ≤2.185", source_hash="report-200"),
                case("new-200-temp", "参考温度 75℃", source_hash="report-200"),
                case("new-400", "负载损耗 Pₖ ≤3.615", source_hash="report-400"),
            ]
        },
    }

    contexts = miss_ablation.infer_source_contexts(payload)

    assert contexts["report-200"]["model"] == "S20-200"
    assert contexts["report-400"]["model"] == "S20-400"


def test_miss_ablation_routes_parameter_family_to_one_standard(monkeypatch) -> None:
    case = {
        "detection_project": {
            "project_name": "短路阻抗和负载损耗测量",
            "reported_requirement": {"text": "负载损耗Pₖ（kW）：≤2.185"},
            "sample_context": {},
        }
    }
    query = miss_ablation.targeted_query(
        case,
        {
            "sample_name": "配电变压器",
            "model": "S20-M.RL-200/10-NX2",
            "rated_capacity": "200 kVA",
            "rated_voltage": "10/0.4 kV",
        },
    )

    assert "GB/T 6451-2023" in query
    assert "GB/T 1094.1-2013" not in query
    assert "表 3" not in query

    monkeypatch.setitem(case["detection_project"]["reported_requirement"], "text", "参考温度（℃）：75")
    temperature_query = miss_ablation.targeted_query(case, {})
    assert temperature_query == "GB/T 1094.1-2013 变压器试验 负载损耗测量 参考温度 75 ℃"
