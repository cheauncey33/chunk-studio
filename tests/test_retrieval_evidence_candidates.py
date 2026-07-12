from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_evidence_candidates_use_stable_locators_and_pending_status() -> None:
    payload = json.loads(
        (ROOT / "evaluation" / "retrieval_evidence_candidates_v1.json").read_text(encoding="utf-8")
    )
    assert len(payload["cases"]) == 40
    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"chunk_id"' not in serialized
    evidence = [item for case in payload["cases"] for item in case["selected_evidence"]]
    assert evidence
    assert all(case["gold_status"] == "candidate_model_reviewed_pending_domain_review" for case in payload["cases"])
    assert all(item["label"] in {"direct_candidate", "supporting_candidate", "uncertain"} for item in evidence)
    assert all(len(item["locator"]["text_sha256"]) == 64 for item in evidence)
    assert all(item["quote_verified"] or item["evidence_quote"] == "" for item in evidence)

