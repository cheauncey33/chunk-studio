from __future__ import annotations

from app.audit_authority import (
    apply_status_layer,
    build_status_layer,
    layer_from_case,
    layer_from_judgment,
    summarize_status_layers,
)


def test_programmatic_table_is_closed_supported() -> None:
    layer = build_status_layer(
        verdict="supported",
        judge_source="programmatic_table",
        reason_code="unique_bound_comparable",
    )
    assert layer["authority"] == "programmatic_table"
    assert layer["closed"] is True
    assert layer["bind_state"] == "unique"
    assert layer["verdict"] == "supported"


def test_fallback_llm_supported_is_not_closed() -> None:
    layer = build_status_layer(
        verdict="supported",
        judge_source="fallback_llm",
        reason_code="no_authoritative_table_claim",
    )
    assert layer["authority"] == "model"
    assert layer["closed"] is False
    assert layer["bind_state"] == "unbound"


def test_requirement_not_ready_is_not_ready_bind_state() -> None:
    layer = build_status_layer(
        verdict="not_audited",
        judge_source="fallback_llm",
        reason_code="requirement_not_program_ready",
    )
    assert layer["bind_state"] == "not_ready"
    assert layer["closed"] is False


def test_layer_from_case_reads_legacy_judge_source() -> None:
    layer = layer_from_case({
        "judgment": {
            "status": "supported",
            "deterministic_judge": {
                "applied": False,
                "mode": "fallback_llm",
                "reason_code": "no_authoritative_table_claim",
            },
        },
        "workflow_trace": {
            "audit_judge": {
                "judge_source": "fallback_llm",
                "table_claim_path": {
                    "mode": "fallback_llm",
                    "reason_code": "no_authoritative_table_claim",
                },
            }
        },
    })
    assert layer["authority"] == "model"
    assert layer["closed"] is False
    assert layer["bind_state"] == "unbound"


def test_attached_layer_wins_over_stale_judge_source() -> None:
    judgment = apply_status_layer(
        {"status": "supported"},
        build_status_layer(
            verdict="supported",
            judge_source="programmatic_formula",
            reason_code="derived_sum_comparable",
        ),
    )
    layer = layer_from_judgment(judgment, judge_source="fallback_llm")
    assert layer["authority"] == "programmatic_formula"
    assert layer["closed"] is True


def test_summarize_splits_closed_and_model_verdicts() -> None:
    summary = summarize_status_layers([
        {
            "judgment": {
                "status": "supported",
                "status_layer": {
                    "authority": "programmatic_table",
                    "closed": True,
                    "bind_state": "unique",
                    "reason_code": "unique_bound_comparable",
                },
            }
        },
        {
            "judgment": {
                "status": "supported",
                "status_layer": {
                    "authority": "model",
                    "closed": False,
                    "bind_state": "unbound",
                    "reason_code": "no_authoritative_table_claim",
                },
            }
        },
        {
            "judgment": {
                "status": "insufficient_context",
                "status_layer": {
                    "authority": "model",
                    "closed": False,
                    "bind_state": "not_ready",
                    "reason_code": "requirement_not_program_ready",
                },
            }
        },
    ])
    assert summary["closed_count"] == 1
    assert summary["model_count"] == 2
    assert summary["closed_verdicts"] == {"supported": 1}
    assert summary["model_verdicts"] == {"supported": 1, "insufficient_context": 1}
    assert summary["authority"]["programmatic_table"] == 1
    assert summary["authority"]["model"] == 2
