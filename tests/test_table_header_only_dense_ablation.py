from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_table_header_only_dense_ablation.py"
SPEC = importlib.util.spec_from_file_location("evaluate_table_header_only_dense_ablation", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_table_header_only_text_includes_metadata_and_header_but_not_content() -> None:
    row = {
        "text": "DO_NOT_INCLUDE 400 kVA 0.370 kW",
        "business_metadata": json.dumps({
            "standard_no": "GB 20052-2024",
            "section": "5.2",
            "section_title": "变压器能效限定值",
            "table_no": "1",
            "table_title": "10 kV 油浸式变压器能效等级",
            "table_columns": ["额定容量kVA", "空载损耗W", "负载损耗W"],
        }, ensure_ascii=False),
    }

    text = module.table_header_only_text(row)

    assert "GB 20052-2024 | 5.2 | 变压器能效限定值 | 1 | 10 kV 油浸式变压器能效等级" in text
    assert "表头：额定容量kVA | 空载损耗W | 负载损耗W" in text
    assert "DO_NOT_INCLUDE" not in text
    assert "0.370" not in text


def test_table_groups_keeps_only_table_alternatives() -> None:
    case = {
        "required_evidence_groups": [
            {
                "group_id": "mixed",
                "alternatives": [
                    {"locator": {"content_type": "section", "text_sha256": "section-hash"}},
                    {"locator": {"content_type": "table", "text_sha256": "table-hash"}},
                ],
            },
            {
                "group_id": "section-only",
                "alternatives": [
                    {"locator": {"content_type": "section", "text_sha256": "other-section"}},
                ],
            },
        ]
    }

    assert module.table_groups(case) == [
        {"group_id": "mixed", "target_hashes": ["table-hash"]}
    ]


def test_rrf_rank_merges_route_candidates() -> None:
    route_one = [
        {"chunk_id": "a", "text_sha256": "a"},
        {"chunk_id": "b", "text_sha256": "b"},
    ]
    route_two = [
        {"chunk_id": "b", "text_sha256": "b"},
        {"chunk_id": "c", "text_sha256": "c"},
    ]

    ranked = module.rrf_rank([route_one, route_two])

    assert [item["chunk_id"] for item in ranked] == ["b", "a", "c"]


def test_aggregate_cases_scores_complete_table_groups() -> None:
    cases = [{
        "table_group_count": 2,
        "rankings": {
            "header_only": {
                "dense_original": {
                    "groups": [
                        {"best_rank": 3},
                        {"best_rank": 12},
                    ]
                }
            }
        },
    }]

    at_10 = module.aggregate_cases(cases, "header_only", "dense_original")["10"]
    at_20 = module.aggregate_cases(cases, "header_only", "dense_original")["20"]

    assert at_10["recalled_table_groups"] == 1
    assert at_10["complete_case_hits"] == 0
    assert at_20["recalled_table_groups"] == 2
    assert at_20["complete_case_hits"] == 1
