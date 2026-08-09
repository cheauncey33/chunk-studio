from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_test_set import validate_dataset  # noqa: E402


DATASET = ROOT / "evaluation" / "test_set.json"


def _case(case_id: str) -> dict:
    return next(case for case in _payload()["cases"] if case["case_id"] == case_id)


def _hashes(case_id: str, group_id: str | None = None) -> set[str]:
    groups = _case(case_id)["required_evidence_groups"]
    if group_id:
        groups = [group for group in groups if group["group_id"] == group_id]
    return {
        alternative["locator"]["text_sha256"]
        for group in groups
        for alternative in group["alternatives"]
    }


def _payload() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_v2_separates_retrieval_context_and_deterministic_prefilter() -> None:
    payload = _payload()
    cases = {case["case_id"]: case for case in payload["cases"]}

    assert len(cases) == 40
    assert payload["summary"] == {
        "case_count": 40,
        "retrieval_answerable_candidates": 33,
        "context_required_cases": 3,
        "deterministic_prefilter_cases": 4,
        "required_evidence_groups": 36,
        "deterministic_context_groups": 4,
        "evidence_locator_assignments": 51,
        "unique_evidence_chunks": 14,
        "deterministic_context_locator_assignments": 4,
        "unique_deterministic_context_chunks": 2,
        "relevant_locator_assignments": 115,
        "unique_relevant_chunks": 38,
        "pending_domain_review_cases": 0,
        "scoreable_case_count": 33,
        "legacy_direct_missing_conflicts": 0,
        "resolved_legacy_direct_missing_conflicts": 15,
    }
    deterministic_prefilter_cases = {case_id for case_id, case in cases.items() if case["evaluation_scope"] == "deterministic_prefilter"}
    assert deterministic_prefilter_cases == {"hbjc-5-r3", "ezc-5-r4", "whc-5-r4", "xyc-5-r4"}
    assert all(not cases[case_id]["required_evidence_groups"] for case_id in deterministic_prefilter_cases)

    context_required = {
        case_id for case_id, case in cases.items() if case["answerability_status"] == "context_required"
    }
    assert context_required == {"hbjc-9-r3", "whc-14-1-r4", "hbjc-13-r4"}
    assert all(not cases[case_id]["required_evidence_groups"] for case_id in context_required)
    assert "evidence_metric_conflict_pending_domain_review" in cases["hbjc-13-r4"]["review"]["flags"]


def test_v2_separates_lenient_relevance_from_strict_answer_evidence() -> None:
    payload = _payload()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"chunk_id"' not in serialized
    assert '"manual_rule_id"' not in serialized

    answerable = [case for case in payload["cases"] if case["answerability_status"] == "answerable_candidate"]
    assert all(case["required_evidence_groups"] for case in answerable)
    assert all(case["relevant_evidence"] for case in answerable)
    assert len(next(case for case in answerable if case["case_id"] == "hbjc-4-r2")["required_evidence_groups"]) == 1
    assert len(next(case for case in answerable if case["case_id"] == "hbjc-14-r1")["required_evidence_groups"]) == 2
    assert len(next(case for case in answerable if case["case_id"] == "ezc-4-r2")["required_evidence_groups"]) == 1
    assert len(next(case for case in answerable if case["case_id"] == "whc-10-r1")["required_evidence_groups"]) == 2
    assert len(next(case for case in answerable if case["case_id"] == "xyc-10-r1")["required_evidence_groups"]) == 2
    xyc_ratio = next(case for case in answerable if case["case_id"] == "xyc-3-r1")
    assert len(xyc_ratio["required_evidence_groups"][0]["alternatives"]) == 2
    assert all(
        alternative["legacy_label"] == "direct_candidate"
        for case in answerable
        for group in case["required_evidence_groups"]
        for alternative in group["alternatives"]
    )
    assert any(
        evidence["legacy_label"] == "supporting_candidate"
        for case in answerable
        for evidence in case["relevant_evidence"]
    )
    assert payload["evaluation_profiles"]["lenient_relevance"]["primary"] is False
    assert payload["evaluation_profiles"]["strict_answer"]["primary"] is True


def test_v2_declares_real_chunk_alternatives_and_deterministic_tolerances() -> None:
    cases = {case["case_id"]: case for case in _payload()["cases"]}

    for case_id in ("ezc-4-r1", "whc-4-r1", "xyc-5-r2"):
        hashes = {
            alternative["locator"]["text_sha256"]
            for alternative in cases[case_id]["required_evidence_groups"][0]["alternatives"]
        }
        assert len(hashes) == 2

    for case_id in ("hbjc-4-r2", "ezc-4-r2", "whc-4-r2"):
        case = cases[case_id]
        assert len(case["required_evidence_groups"]) == 1
        assert len(case["deterministic_context_evidence"]) == 1
        assert len(case["deterministic_context_evidence"][0]["alternatives"][0]["locator"]["text_sha256"]) == 64

    impedance = cases["xyc-5-r3"]
    assert len(impedance["required_evidence_groups"][0]["alternatives"][0]["locator"]["text_sha256"]) == 64
    assert len(impedance["deterministic_context_evidence"][0]["alternatives"][0]["locator"]["text_sha256"]) == 64


def test_v2_li_voltage_gold_uses_75_kv_and_jbt_alternative() -> None:
    cases = {case["case_id"]: case for case in _payload()["cases"]}
    insulation_level_table = next(
        alternative["locator"]["text_sha256"]
        for group in cases["hbjc-14-r1"]["required_evidence_groups"]
        if group["group_id"] == "lightning_impulse_voltage"
        for alternative in group["alternatives"]
        if alternative["locator"]["standard_no"] == "GB/T 1094.3-2017"
    )
    INSULATION_LEVEL_TABLE = insulation_level_table

    for case_id in ("hbjc-14-r1", "whc-10-r1"):
        voltage_group = next(
            group for group in cases[case_id]["required_evidence_groups"]
            if group["group_id"] == "lightning_impulse_voltage"
        )
        by_hash = {
            alternative["locator"]["text_sha256"]: alternative
            for alternative in voltage_group["alternatives"]
        }
        assert len(by_hash) == 2
        assert "LI" in by_hash[insulation_level_table]["reason"]
        assert "75 kV" in by_hash[insulation_level_table]["reason"]
        assert "35 kV是AV/LTAC列" in by_hash[INSULATION_LEVEL_TABLE]["reason"]

    for case_id in ("hbjc-7-r1", "whc-6-r1", "xyc-6-r1"):
        alternative = cases[case_id]["required_evidence_groups"][0]["alternatives"][0]
        assert len(alternative["locator"]["text_sha256"]) == 64
        assert "AV/LTAC)为35 kV" in alternative["reason"]
        assert "75 kV是LI列" in alternative["reason"]


def test_v2_contract_validates_without_local_corpus() -> None:
    result = validate_dataset(verify_corpus=False)
    assert result == {
        "cases": 40,
        "evidence_locators": 51,
        "deterministic_context_locators": 4,
        "relevant_locators": 115,
        "corpus_verified": False,
        "scoreable_cases": 33,
    }
