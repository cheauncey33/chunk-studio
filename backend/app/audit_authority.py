"""Split audit outcome into verdict, authority, and bind reason.

``status`` stays the four-way verdict. Authority says whether that verdict is a
closed programmatic comparison or a model fallback. Bind state is why the
programmatic path did or did not close; it must not be folded back into status.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


PROGRAMMATIC_AUTHORITIES = (
    "programmatic_table",
    "programmatic_formula",
    "programmatic_caliber",
)
MODEL_AUTHORITIES = ("model",)
KNOWN_AUTHORITIES = PROGRAMMATIC_AUTHORITIES + MODEL_AUTHORITIES + ("unknown",)

_SOURCE_TO_AUTHORITY = {
    "programmatic_table": "programmatic_table",
    "programmatic_formula": "programmatic_formula",
    "programmatic_caliber": "programmatic_caliber",
    "fallback_llm": "model",
    "fallback_llm_rejudge": "model",
    "llm": "model",
    "model": "model",
    "agent_evidence": "model",
    "unbound": "unknown",
    "agent": "model",
    "agent_error": "model",
}

_REASON_TO_BIND_STATE = {
    "unique_bound_comparable": "unique",
    "derived_sum_comparable": "unique",
    "conflicting_table_bindings": "conflict",
    "no_authoritative_table_claim": "unbound",
    "derived_sum_incomplete": "unbound",
    "requirement_not_program_ready": "not_ready",
    "agent_retrieved": "unique",
}

CLOSED_VERDICTS = {"supported", "mismatch"}
# C-05 rules a clause out of standard-value scope from the reported text alone,
# which is as final as a closed comparison. The table paths can never reach it.
_CALIBER_CLOSED_VERDICTS = CLOSED_VERDICTS | {"not_audited"}


def normalize_authority(source: Any) -> str:
    text = str(source or "").strip()
    return _SOURCE_TO_AUTHORITY.get(text, "unknown" if not text else "model")


def bind_state_for_reason(reason_code: Any) -> str:
    text = str(reason_code or "").strip()
    return _REASON_TO_BIND_STATE.get(text, "unknown")


def is_closed_authority(authority: str, verdict: str) -> bool:
    if authority == "programmatic_caliber":
        return verdict in _CALIBER_CLOSED_VERDICTS
    return authority in PROGRAMMATIC_AUTHORITIES and verdict in CLOSED_VERDICTS


def build_status_layer(
    *,
    verdict: Any = None,
    judge_source: Any = None,
    reason_code: Any = None,
) -> dict[str, Any]:
    """Return the three-axis layer: verdict / authority / bind_state."""
    normalized_verdict = str(verdict or "").strip() or "unknown"
    authority = normalize_authority(judge_source)
    reason = str(reason_code or "").strip() or None
    return {
        "verdict": normalized_verdict,
        "authority": authority,
        "closed": is_closed_authority(authority, normalized_verdict),
        "bind_state": bind_state_for_reason(reason),
        "reason_code": reason,
    }


def apply_status_layer(judgment: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """Write the layer onto a judgment without changing ``status``."""
    judgment["status_layer"] = dict(layer)
    judgment["authority"] = layer.get("authority")
    judgment["authority_closed"] = bool(layer.get("closed"))
    judgment["bind_state"] = layer.get("bind_state")
    judgment["bind_reason_code"] = layer.get("reason_code")
    return judgment


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def layer_from_judgment(
    judgment: dict[str, Any] | None,
    *,
    judge_source: Any = None,
    reason_code: Any = None,
) -> dict[str, Any]:
    """Prefer an already-attached layer; otherwise reconstruct from judge traces."""
    payload = _dict(judgment)
    existing = _dict(payload.get("status_layer"))
    dj = _dict(payload.get("deterministic_judge"))
    source = (
        existing.get("authority")
        or payload.get("authority")
        or dj.get("mode")
        or judge_source
    )
    reason = (
        existing.get("reason_code")
        or payload.get("bind_reason_code")
        or dj.get("reason_code")
        or reason_code
    )
    return build_status_layer(
        verdict=payload.get("status") or existing.get("verdict"),
        judge_source=source,
        reason_code=reason,
    )


def layer_from_case(case: dict[str, Any] | None) -> dict[str, Any]:
    """Read a case payload, including older traces that only stored judge_source."""
    payload = _dict(case)
    if isinstance(payload.get("status_layer"), dict) and payload["status_layer"].get("authority"):
        layer = dict(payload["status_layer"])
        verdict = str((_dict(payload.get("judgment")).get("status") or layer.get("verdict") or "")).strip()
        layer["verdict"] = verdict or str(layer.get("verdict") or "unknown")
        layer["closed"] = is_closed_authority(str(layer.get("authority") or ""), layer["verdict"])
        return layer
    judgment = _dict(payload.get("judgment"))
    trace = _dict(_dict(payload.get("workflow_trace")).get("audit_judge"))
    path = _dict(trace.get("table_claim_path"))
    return layer_from_judgment(
        judgment,
        judge_source=trace.get("judge_source") or path.get("mode"),
        reason_code=path.get("reason_code"),
    )


def summarize_status_layers(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Count closed vs model verdicts for a report or replay."""
    authorities: Counter[str] = Counter()
    bind_states: Counter[str] = Counter()
    closed_verdicts: Counter[str] = Counter()
    model_verdicts: Counter[str] = Counter()
    for case in cases:
        layer = layer_from_case(case)
        authority = str(layer.get("authority") or "unknown")
        authorities[authority] += 1
        bind_states[str(layer.get("bind_state") or "unknown")] += 1
        verdict = str(layer.get("verdict") or "unknown")
        if layer.get("closed"):
            closed_verdicts[verdict] += 1
        else:
            model_verdicts[verdict] += 1
    return {
        "authority": dict(authorities),
        "bind_state": dict(bind_states),
        "closed_verdicts": dict(closed_verdicts),
        "model_verdicts": dict(model_verdicts),
        "closed_count": sum(closed_verdicts.values()),
        "model_count": sum(model_verdicts.values()),
    }
