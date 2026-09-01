from __future__ import annotations

from typing import Any

from ..audit_authority import layer_from_judgment


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _decision(
    action: str,
    reason_code: str,
    triggered_by: list[str],
    *,
    failure_class: str,
    resolution_state: str,
    recoverable_by_agent: bool,
    **extra: Any,
) -> dict[str, Any]:
    """Build one stable diagnostic envelope for gate and evaluation output."""
    return {
        "action": action,
        "reason_code": reason_code,
        "failure_class": failure_class,
        "resolution_state": resolution_state,
        "recoverable_by_agent": recoverable_by_agent,
        "triggered_by": triggered_by,
        **extra,
    }


def decide_recovery(
    *,
    judgment: dict[str, Any],
    retrieval_trace: dict[str, Any],
    sample_profile: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic recovery decision for a provisional judgment."""
    status = str(judgment.get("status") or "").strip()
    validation_issues = [
        issue
        for issue in _strings(judgment.get("validation_issues"))
        # A successful second Judge keeps this audit marker. It is history, not
        # an unresolved contract failure and must not block Recovery.
        if not issue.startswith("rejudge triggered:")
    ]
    rejudge = judgment.get("rejudge") if isinstance(judgment.get("rejudge"), dict) else {}
    remaining_issues = _strings(rejudge.get("remaining_issues"))
    if validation_issues or remaining_issues:
        return _decision(
            "rejudge_only",
            "judge_consistency_failure" if remaining_issues else "judge_contract_failure",
            validation_issues + remaining_issues,
            failure_class="judge_semantic_gap" if remaining_issues else "judge_contract_gap",
            resolution_state="judge_invalid",
            recoverable_by_agent=False,
        )

    if status in {"supported", "mismatch"}:
        layer = layer_from_judgment(judgment)
        authority = str(layer.get("authority") or "unknown")
        if layer.get("closed"):
            return _decision(
                "accept_provisional",
                "definitive_with_valid_evidence",
                [f"judgment.status={status}", f"authority={authority}"],
                failure_class="none",
                resolution_state="definitive",
                recoverable_by_agent=False,
                authority=authority,
                bind_state=layer.get("bind_state"),
            )
        if authority == "unknown":
            return _decision(
                "accept_provisional",
                "definitive_with_valid_evidence",
                [f"judgment.status={status}"],
                failure_class="none",
                resolution_state="definitive",
                recoverable_by_agent=False,
            )
        return _decision(
            "accept_provisional",
            "model_judgment_not_closed",
            [f"judgment.status={status}", f"authority={authority}"],
            failure_class="none",
            resolution_state="model_provisional",
            recoverable_by_agent=False,
            authority=authority,
            bind_state=layer.get("bind_state"),
            reason_bind=layer.get("reason_code"),
        )

    output = (
        retrieval_trace.get("output")
        if isinstance(retrieval_trace.get("output"), dict)
        else retrieval_trace
    )
    degraded = _strings(output.get("degraded"))
    candidate_count = int(output.get("candidate_count") or 0)
    delivered_counts = output.get("candidate_counts")
    delivered = (
        sum(int(value or 0) for value in delivered_counts.values())
        if isinstance(delivered_counts, dict)
        else candidate_count
    )
    if degraded and delivered <= 0:
        return _decision(
            "runtime_failure",
            "retrieval_runtime_failure",
            degraded,
            failure_class="runtime_gap",
            resolution_state="runtime_failed",
            recoverable_by_agent=False,
        )

    missing_fields = _strings(judgment.get("missing_context_fields"))
    if missing_fields:
        from_report = (
            sample_profile.get("from_report")
            if isinstance(sample_profile.get("from_report"), dict)
            else {}
        )
        unresolved = [field for field in missing_fields if not from_report.get(field)]
        if unresolved:
            return _decision(
                "agent_recovery",
                "applicability_parameter_missing",
                [f"missing:{field}" for field in unresolved],
                failure_class="applicability_gap",
                resolution_state="recoverable_context",
                recoverable_by_agent=True,
                gap_type="missing_report_parameter",
                missing_context_fields=unresolved,
            )

    if delivered <= 0:
        return _decision(
            "agent_recovery",
            "retrieval_no_candidates",
            ["delivered_candidate_count=0"],
            failure_class="retrieval_gap",
            resolution_state="recoverable_retrieval",
            recoverable_by_agent=True,
            gap_type="missing_nominal_evidence",
        )
    if status == "not_audited":
        return _decision(
            "agent_recovery",
            "provisional_not_audited",
            ["judgment.status=not_audited"],
            failure_class="evidence_scope_gap",
            resolution_state="recoverable_retrieval",
            recoverable_by_agent=True,
            gap_type="missing_nominal_evidence",
        )
    if status == "insufficient_context":
        return _decision(
            "accept_provisional",
            "judge_insufficient_without_explicit_retrieval_gap",
            ["judgment.status=insufficient_context"],
            failure_class="judge_semantic_gap",
            resolution_state="provisional_insufficient",
            recoverable_by_agent=False,
        )
    return _decision(
        "accept_provisional",
        "not_applicable",
        [f"judgment.status={status or 'empty'}"],
        failure_class="none",
        resolution_state="provisional_accepted",
        recoverable_by_agent=False,
    )
