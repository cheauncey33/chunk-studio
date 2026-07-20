from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.evidence_locator import chunk_text_sha256  # noqa: E402
from evaluate_retrieval_ground_truth_v2 import metrics_at_k, score_ranking  # noqa: E402


def _evidence(text: str, label: str = "direct_candidate") -> dict:
    return {
        "locator": {"text_sha256": chunk_text_sha256(text)},
        "legacy_label": label,
    }


def _hit(text: str, chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "business_metadata": {},
    }


def test_lenient_relevance_can_hit_before_strict_groups_are_complete() -> None:
    case = {
        "relevant_evidence": [
            _evidence("background", "supporting_candidate"),
            _evidence("value"),
            _evidence("rule"),
        ],
        "required_evidence_groups": [
            {"group_id": "value", "alternatives": [_evidence("value")]},
            {"group_id": "rule", "alternatives": [_evidence("rule")]},
        ],
    }
    ranking = score_ranking(
        [_hit("background", "c1"), _hit("value", "c2"), _hit("rule", "c3")],
        case,
    )

    assert metrics_at_k(ranking, 1) == {
        "lenient_relevance_hit": True,
        "recalled_groups": 0,
        "required_groups": 2,
        "strict_complete_hit": False,
    }
    assert metrics_at_k(ranking, 2)["recalled_groups"] == 1
    assert metrics_at_k(ranking, 3)["strict_complete_hit"] is True


def test_or_alternatives_need_only_one_chunk_for_the_group() -> None:
    case = {
        "relevant_evidence": [_evidence("alternative-b")],
        "required_evidence_groups": [{
            "group_id": "value",
            "alternatives": [_evidence("alternative-a"), _evidence("alternative-b")],
        }],
    }
    ranking = score_ranking([_hit("alternative-b", "c1")], case)

    assert metrics_at_k(ranking, 1)["strict_complete_hit"] is True
