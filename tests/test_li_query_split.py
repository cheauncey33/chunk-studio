from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_li_query_split import score_groups, split_is_complete  # noqa: E402


def test_score_groups_accepts_any_real_chunk_alternative() -> None:
    groups = [{"group_id": "lightning_impulse_voltage", "target_hashes": ["hash-a", "hash-b"]}]
    hits = [{"text": "matching chunk"}]

    from app.evidence_locator import chunk_text_sha256

    groups[0]["target_hashes"][1] = chunk_text_sha256("matching chunk")
    assert score_groups(hits, groups) == [{"group_id": "lightning_impulse_voltage", "best_rank": 1}]


def test_split_complete_requires_voltage_and_waveform_hits() -> None:
    split_runs = {
        "voltage": {"groups": [
            {"group_id": "lightning_impulse_voltage", "best_rank": 3},
            {"group_id": "lightning_impulse_waveform", "best_rank": None},
        ]},
        "waveform": {"groups": [
            {"group_id": "lightning_impulse_voltage", "best_rank": None},
            {"group_id": "lightning_impulse_waveform", "best_rank": 2},
        ]},
    }
    assert split_is_complete(split_runs) is True
    split_runs["waveform"]["groups"][1]["best_rank"] = None
    assert split_is_complete(split_runs) is False
