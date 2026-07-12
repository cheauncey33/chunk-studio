from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_retrieval_case_pool_has_stratified_shape() -> None:
    path = ROOT / "evaluation" / "retrieval_case_pool_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["case_count"] == 40
    assert summary["by_report"] == {"EZC": 10, "HBJC": 10, "WHC": 10, "XYC": 10}
    assert summary["by_split"] == {"development": 10, "validation": 10, "test": 20}
    assert summary["repeat_routine_cases"] >= 2
    assert all(case["gold_status"] == "candidate_pending_evidence_review" for case in payload["cases"])
    assert len({case["case_id"] for case in payload["cases"]}) == 40
    assert all(len(case["source_locator"]["text_sha256"]) == 64 for case in payload["cases"])

