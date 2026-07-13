from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_retrieval_evidence_groups import evaluate_groups


def _candidate(key: str, standard: str, content_type: str, **metadata: str) -> dict:
    return {
        "candidate_key": key,
        "content_type": content_type,
        "business_metadata": {"standard_no": standard, "content_type": content_type, **metadata},
    }


def test_evidence_groups_use_or_inside_and_across_groups() -> None:
    candidates = [
        _candidate("c01", "A", "table", table_no="1"),
        _candidate("c02", "C", "section", section="2"),
    ]
    groups = [
        {
            "group_id": "value",
            "alternatives": [
                {"standard_no": "A", "content_type": "table", "table_no": "1"},
                {"standard_no": "B", "content_type": "table", "table_no": "9"},
            ],
        },
        {
            "group_id": "rule",
            "alternatives": [
                {"standard_no": "C", "content_type": "section", "section": "2"}
            ],
        },
    ]
    result = evaluate_groups(candidates, groups)
    assert result["recalled_group_count"] == 2
    assert result["all_required_groups_recalled"] is True


def test_missing_one_required_group_makes_case_incomplete() -> None:
    candidates = [_candidate("c01", "A", "table", table_no="1")]
    groups = [
        {"group_id": "value", "alternatives": [{"standard_no": "A", "content_type": "table", "table_no": "1"}]},
        {"group_id": "rule", "alternatives": [{"standard_no": "C", "content_type": "section", "section": "2"}]},
    ]
    result = evaluate_groups(candidates, groups)
    assert result["recalled_group_count"] == 1
    assert result["all_required_groups_recalled"] is False


def test_manual_rule_can_satisfy_required_group() -> None:
    result = evaluate_groups(
        candidates=[],
        groups=[
            {
                "group_id": "total_loss_sum_rule",
                "alternatives": [
                    {"manual_rule_id": "transformer_total_loss_sum_v1"}
                ],
            }
        ],
        manual_rule_ids={"transformer_total_loss_sum_v1"},
    )

    assert result["recalled_group_count"] == 1
    assert result["all_required_groups_recalled"] is True
    assert result["groups"][0]["matched_manual_rules"] == ["transformer_total_loss_sum_v1"]
