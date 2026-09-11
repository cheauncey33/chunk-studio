from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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
