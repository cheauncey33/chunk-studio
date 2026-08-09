"""Bounded LLM evidence selection over an immutable reranked candidate pool."""
from __future__ import annotations

import json
from typing import Any, Callable


MAX_RETAINED = 8
MAX_QUOTE_CHARS = 1600
DEFAULT_BATCH_SIZE = 5
DEFAULT_BATCH_CHARS = 14000

SELECTOR_PROMPT = """You select evidence for a report-audit Judge from a fixed candidate pool.
You never decide the final audit verdict and cannot request new retrieval.

For this round, reconsider the retained exact excerpts together with every new
candidate. Return the smallest evidence set that preserves all material rules,
values, applicability conditions, exceptions, definitions, formulas, methods,
and conflicts needed to judge the reported requirement. Different standards or
apparently conflicting requirements must not be silently collapsed. If evidence
already appears sufficient, use later candidates to check for higher-priority
rules, narrower applicability, exceptions, or counter-evidence.

Rules:
1. retained has at most eight items.
2. candidate_key must belong to retained_before or new_candidates.
3. quote is one contiguous verbatim substring copied only from that candidate's
   text field, at most 1600 characters. Do not prepend metadata or a title.
4. Keep exact source language; do not copy a target value from the report into
   evidence and do not paraphrase source values.
5. unresolved and conflicts describe evidence state, not the final verdict.
6. Output one JSON object only, without markdown.

Schema:
{
  "retained": [{"candidate_key":"c01","quote":"","reason":""}],
  "unresolved": [""],
  "conflicts": [{"candidate_keys":["c01","c07"],"description":""}],
  "evidence_sufficient": false
}"""


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    view = {
        "candidate_key": str(candidate.get("candidate_key") or ""),
        "content_type": candidate.get("content_type"),
        "business_metadata": candidate.get("business_metadata") or {},
        "standard_priority": candidate.get("standard_priority"),
        "text": str(candidate.get("text") or ""),
    }
    if isinstance(candidate.get("table_row_binding"), dict):
        view["table_row_binding"] = candidate["table_row_binding"]
    return view


def _batches(
    candidates: list[dict[str, Any]],
    *,
    batch_size: int,
    batch_chars: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for candidate in candidates:
        view = _candidate_view(candidate)
        size = _json_chars(view)
        if current and (len(current) >= batch_size or current_chars + size > batch_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(view)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _validate_state(
    raw: Any,
    *,
    sources: dict[str, dict[str, Any]],
    allowed_keys: set[str],
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    if not isinstance(raw, dict):
        return None, ["selector output is not an object"], []
    retained = raw.get("retained")
    if not isinstance(retained, list):
        return None, ["selector output has no retained array"], []
    if len(retained) > MAX_RETAINED:
        return None, [f"retained count exceeds {MAX_RETAINED}"], []
    discarded: list[str] = []
    accepted: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(retained):
        if not isinstance(item, dict):
            discarded.append(f"retained[{index}] is not an object")
            continue
        key = str(item.get("candidate_key") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if key not in allowed_keys or key not in sources:
            discarded.append(f"retained[{index}] has unavailable candidate_key")
            continue
        if not quote or len(quote) > MAX_QUOTE_CHARS:
            discarded.append(f"retained[{index}] has invalid quote length")
            continue
        if quote not in str(sources[key].get("text") or ""):
            discarded.append(f"retained[{index}] quote is not a source substring")
            continue
        identity = (key, quote)
        if identity in seen:
            continue
        seen.add(identity)
        accepted.append({
            "candidate_key": key,
            "quote": quote,
            "reason": str(item.get("reason") or "").strip(),
        })
    if retained and not accepted:
        return None, ["selector retained no grounded evidence"], discarded

    unresolved = raw.get("unresolved")
    conflicts = raw.get("conflicts")
    if not isinstance(unresolved, list) or not all(isinstance(item, str) for item in unresolved):
        return None, ["unresolved must be a string array"], discarded
    if not isinstance(conflicts, list):
        return None, ["conflicts must be an array"], discarded
    retained_keys = {item["candidate_key"] for item in accepted}
    normalized_conflicts: list[dict[str, Any]] = []
    for index, conflict in enumerate(conflicts):
        if not isinstance(conflict, dict):
            return None, [f"conflicts[{index}] is not an object"], discarded
        keys = conflict.get("candidate_keys")
        if not isinstance(keys, list) or not all(
            str(key) in sources and str(key) in allowed_keys for key in keys
        ):
            return None, [f"conflicts[{index}] has invalid candidate_keys"], discarded
        if not all(str(key) in retained_keys for key in keys):
            discarded.append(
                f"conflicts[{index}] dropped because its evidence was not retained"
            )
            continue
        normalized_conflicts.append({
            "candidate_keys": [str(key) for key in keys],
            "description": str(conflict.get("description") or "").strip(),
        })
    return {
        "retained": accepted,
        "unresolved": [str(item).strip() for item in unresolved if str(item).strip()],
        "conflicts": normalized_conflicts,
        "evidence_sufficient": bool(raw.get("evidence_sufficient")),
    }, [], discarded


def _selected_candidate(source: dict[str, Any], card: dict[str, str]) -> dict[str, Any]:
    selected = {
        "candidate_key": source["candidate_key"],
        "content_type": source.get("content_type"),
        "business_metadata": source.get("business_metadata") or {},
        "standard_priority": source.get("standard_priority"),
        "evidence_roles": list(source.get("evidence_roles") or []),
        "text": card["quote"],
    }
    if isinstance(source.get("table_row_binding"), dict):
        selected["table_row_binding"] = source["table_row_binding"]
    return selected


def select_judge_evidence_rolling(
    judge_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    call_model: Callable[[str, dict[str, Any]], dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_chars: int = DEFAULT_BATCH_CHARS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scan all candidates in bounded batches and return a verified Judge view."""
    before_chars = _json_chars(judge_input)
    sources = {str(item.get("candidate_key") or ""): item for item in candidates}
    state: dict[str, Any] = {
        "retained": [],
        "unresolved": [],
        "conflicts": [],
        "evidence_sufficient": False,
    }
    processed: set[str] = set()
    round_traces: list[dict[str, Any]] = []
    total_input_chars = 0
    batches = _batches(candidates, batch_size=batch_size, batch_chars=batch_chars)
    for round_index, batch in enumerate(batches, start=1):
        new_keys = {str(item["candidate_key"]) for item in batch}
        allowed_keys = {
            str(item.get("candidate_key") or "")
            for item in state["retained"]
        } | new_keys
        payload = {
            "reported_requirement": judge_input.get("reported_requirement") or {},
            "test_item": judge_input.get("test_item") or {},
            "sample_profile": judge_input.get("sample_profile") or {},
            "round": round_index,
            "round_count": len(batches),
            "retained_before": state["retained"],
            "unresolved_before": state["unresolved"],
            "conflicts_before": state["conflicts"],
            "new_candidates": batch,
        }
        input_chars = _json_chars(payload)
        total_input_chars += input_chars
        try:
            raw = call_model(SELECTOR_PROMPT, payload)
        except Exception as exc:
            return judge_input, {
                "mode": "rolling",
                "applied": False,
                "fallback": True,
                "issues": [f"round {round_index} call failed: {type(exc).__name__}: {exc}"],
                "rounds": round_traces,
                "additional_model_calls": round_index,
                "selector_input_chars": total_input_chars,
                "judge_input_chars_before": before_chars,
            }
        validated, issues, discarded = _validate_state(
            raw,
            sources=sources,
            allowed_keys=allowed_keys,
        )
        round_traces.append({
            "round": round_index,
            "new_candidate_keys": sorted(new_keys),
            "input_chars": input_chars,
            "issues": issues,
            "discarded": discarded,
            "output": raw,
        })
        if validated is None:
            return judge_input, {
                "mode": "rolling",
                "applied": False,
                "fallback": True,
                "issues": [f"round {round_index}: {issue}" for issue in issues],
                "rounds": round_traces,
                "additional_model_calls": round_index,
                "selector_input_chars": total_input_chars,
                "judge_input_chars_before": before_chars,
            }
        state = validated
        processed.update(new_keys)

    if not state["retained"]:
        return judge_input, {
            "mode": "rolling",
            "applied": False,
            "fallback": True,
            "issues": ["selector retained no grounded evidence"],
            "rounds": round_traces,
            "additional_model_calls": len(batches),
            "selector_input_chars": total_input_chars,
            "judge_input_chars_before": before_chars,
        }

    selected_keys = {item["candidate_key"] for item in state["retained"]}
    selected = [
        _selected_candidate(sources[item["candidate_key"]], item)
        for item in state["retained"]
    ]
    compressed = {
        **judge_input,
        "candidates": selected,
        "deterministic_table_bindings": [
            item for item in judge_input.get("deterministic_table_bindings") or []
            if str(item.get("candidate_key") or "") in selected_keys
        ],
        "deterministic_comparisons": [
            item for item in judge_input.get("deterministic_comparisons") or []
            if str(item.get("candidate_key") or "") in selected_keys
        ],
        "rolling_evidence_selection": {
            "applied": True,
            "processed_candidate_count": len(processed),
            "selected_candidate_keys": sorted(selected_keys),
        },
    }
    applicability = compressed.get("deterministic_applicability")
    if isinstance(applicability, dict):
        compressed["deterministic_applicability"] = {
            **applicability,
            "candidate_evaluations": [
                item for item in applicability.get("candidate_evaluations") or []
                if str(item.get("candidate_key") or "") in selected_keys
            ],
        }
    after_chars = _json_chars(compressed)
    return compressed, {
        "mode": "rolling",
        "applied": True,
        "fallback": False,
        "issues": [],
        "state": state,
        "rounds": round_traces,
        "additional_model_calls": len(batches),
        "selector_input_chars": total_input_chars,
        "judge_input_chars_before": before_chars,
        "judge_input_chars_after": after_chars,
    }
