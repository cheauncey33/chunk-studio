from __future__ import annotations

from app.recovery.gate import decide_recovery


def test_gate_accepts_valid_definitive_judgment() -> None:
    decision = decide_recovery(
        judgment={"status": "supported", "evidence_candidate_keys": ["c01"]},
        retrieval_trace={"output": {"candidate_count": 10}},
        sample_profile={},
    )
    assert decision["action"] == "accept_provisional"


def test_gate_rejudges_contract_failure_without_agent() -> None:
    decision = decide_recovery(
        judgment={
            "status": "insufficient_context",
            "validation_issues": ["dropped unknown evidence keys"],
        },
        retrieval_trace={"output": {"candidate_count": 10}},
        sample_profile={},
    )
    assert decision["action"] == "rejudge_only"
    assert decision["reason_code"] == "judge_contract_failure"
    assert decision["failure_class"] == "judge_contract_gap"
    assert decision["resolution_state"] == "judge_invalid"
    assert decision["recoverable_by_agent"] is False


def test_gate_recovers_missing_applicability_parameter() -> None:
    decision = decide_recovery(
        judgment={
            "status": "insufficient_context",
            "missing_context_fields": ["equipment_highest_voltage_um"],
        },
        retrieval_trace={"output": {"candidate_count": 10}},
        sample_profile={"from_report": {}},
    )
    assert decision["action"] == "agent_recovery"
    assert decision["reason_code"] == "applicability_parameter_missing"


def test_gate_recovers_missing_fields_without_keyword_suppression() -> None:
    decision = decide_recovery(
        judgment={
            "status": "insufficient_context",
            "missing_context_fields": ["绝缘类型", "用户特殊要求"],
        },
        retrieval_trace={"output": {"candidate_count": 10}},
        sample_profile={
            "from_report": {"rated_voltage": "10/0.4 kV"},
            "deterministic_applicability": {"state": "parameters_only"},
        },
    )

    assert decision["action"] == "agent_recovery"
    assert decision["reason_code"] == "applicability_parameter_missing"
    assert decision["recoverable_by_agent"] is True


def test_gate_does_not_call_missing_candidates_a_knowledge_rule_absence() -> None:
    decision = decide_recovery(
        judgment={"status": "not_audited"},
        retrieval_trace={"output": {"candidate_count": 0, "degraded": ["rerank_failed"]}},
        sample_profile={},
    )
    assert decision["action"] == "runtime_failure"
    assert decision["reason_code"] == "retrieval_runtime_failure"


def test_gate_recovers_provisional_not_audited_when_retrieval_completed() -> None:
    decision = decide_recovery(
        judgment={"status": "not_audited"},
        retrieval_trace={"output": {"candidate_count": 8, "degraded": []}},
        sample_profile={},
    )
    assert decision["action"] == "agent_recovery"
    assert decision["reason_code"] == "provisional_not_audited"
    assert decision["failure_class"] == "evidence_scope_gap"


def test_gate_ignores_successful_rejudge_audit_marker() -> None:
    decision = decide_recovery(
        judgment={
            "status": "insufficient_context",
            "validation_issues": ["rejudge triggered: old reason/status conflict"],
            "rejudge": {"triggered": True, "remaining_issues": []},
        },
        retrieval_trace={"output": {"candidate_count": 8, "degraded": []}},
        sample_profile={},
    )

    assert decision["action"] == "accept_provisional"
    assert decision["reason_code"] == "judge_insufficient_without_explicit_retrieval_gap"
    assert decision["failure_class"] == "judge_semantic_gap"
    assert decision["recoverable_by_agent"] is False


def test_gate_does_not_expand_retrieval_for_untyped_judge_uncertainty() -> None:
    decision = decide_recovery(
        judgment={"status": "insufficient_context", "missing_context_fields": []},
        retrieval_trace={"output": {"candidate_count": 14, "degraded": []}},
        sample_profile={},
    )

    assert decision["action"] == "accept_provisional"
    assert decision["recoverable_by_agent"] is False
