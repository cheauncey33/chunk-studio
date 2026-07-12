from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_retrieval_gold_candidates_keep_stable_locators_and_unresolved_context() -> None:
    payload = json.loads(
        (ROOT / "evaluation" / "retrieval_gold_candidates_v1.json").read_text(encoding="utf-8")
    )
    assert len(payload["cases"]) == 40
    assert payload["validation"] == {"checked_locators": 139, "invalid_locators": 0}

    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"chunk_id"' not in serialized
    assert "gold_discovery_only queries must never be used as evaluated retrieval inputs" in serialized

    direct_case_ids = {
        case["case_id"]
        for case in payload["cases"]
        if any(item["label"] == "direct_candidate" for item in case["selected_evidence"])
    }
    assert len(direct_case_ids) == 38
    assert {case["case_id"] for case in payload["cases"]} - direct_case_ids == {
        "hbjc-9-r3",
        "whc-14-1-r4",
    }

    manual = [
        item
        for case in payload["cases"]
        for item in case["selected_evidence"]
        if item.get("retrieval", {}).get("purpose") == "manual_corpus_recovery"
    ]
    assert manual
    assert all(item["retrieval"]["method"] == "stable_metadata_lookup" for item in manual)
