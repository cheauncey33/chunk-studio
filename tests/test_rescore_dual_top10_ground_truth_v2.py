from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rescore_dual_top10_ground_truth_v2 import score_case  # noqa: E402


def test_score_case_uses_or_within_groups_and_and_across_groups() -> None:
    case = {"required_evidence_groups": [
        {"group_id": "voltage", "alternatives": [
            {"locator": {"text_sha256": "voltage-a"}},
            {"locator": {"text_sha256": "voltage-b"}},
        ]},
        {"group_id": "waveform", "alternatives": [
            {"locator": {"text_sha256": "waveform"}},
        ]},
    ]}
    hits = [{"chunk_id": "chunk-a"}, {"chunk_id": "chunk-b"}]

    result = score_case(
        case,
        hits,
        {"chunk-a": "voltage-b", "chunk-b": "waveform"},
    )

    assert result["strict_complete"] is True
    assert [group["recalled"] for group in result["groups"]] == [True, True]
