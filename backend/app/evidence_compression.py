"""Source-grounded LLM compression for fixed-Judge evidence input."""
from __future__ import annotations

import json
import re
from typing import Any, Callable


MAX_CARDS = 5
MAX_QUOTE_CHARS = 1200
ALLOWED_ROLES = {
    "nominal_rule",
    "tolerance_rule",
    "method_rule",
    "applicability_rule",
}
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

COMPRESSOR_PROMPT = """You are an evidence extractor for a report-audit workflow.
You do not decide supported, mismatch, compliance, or the final audit status.
Select at most five evidence cards needed to judge the reported claim.

Rules:
1. Every card must use an existing candidate_key and one evidence_role already
   listed on that candidate.
2. quote must be one contiguous verbatim substring copied from candidate.text,
   at most 1200 characters. Preserve raw table HTML when needed.
3. Never use the report's target value as a standard value unless that value is
   literally present in quote.
4. Preserve nominal evidence for a numeric claim, tolerance evidence for a
   tolerance claim, and applicability evidence when it controls the rule.
5. Include every mandatory_candidate_key.
6. Prefer a small complete evidence chain over several repetitive chunks.
7. Output one JSON object only. Do not add markdown.

Output schema:
{
  "cards": [{
    "candidate_key": "c01",
    "evidence_role": "nominal_rule|tolerance_rule|method_rule|applicability_rule",
    "quote": "",
    "property": "",
    "standard_value": "",
    "unit": "",
    "operator": "eq|le|lt|ge|gt|range|tolerance|unknown",
    "scope": "",
    "conditions": [],
    "selection_reason": ""
  }],
  "missing_roles": []
}"""


def _numbers(value: Any) -> set[str]:
    return {
        match.group(0).replace(",", ".").lstrip("+")
        for match in _NUMBER_RE.finditer(str(value or ""))
    }


def _required_roles(report_text: str, candidates: list[dict[str, Any]]) -> set[str]:
    available = {
        str(role)
        for candidate in candidates
        for role in candidate.get("evidence_roles") or []
    }
    required: set[str] = set()
    if _NUMBER_RE.search(report_text) and "nominal_rule" in available:
        required.add("nominal_rule")
    if any(marker in report_text for marker in ("±", "偏差", "公差", "容差")):
        if "tolerance_rule" in available:
            required.add("tolerance_rule")
    return required


def _mandatory_keys(judge_input: dict[str, Any]) -> set[str]:
    return {
        str(item.get("candidate_key") or "")
        for item in judge_input.get("deterministic_comparisons") or []
        if isinstance(item, dict) and str(item.get("candidate_key") or "")
    }


def _validate_cards(
    raw: Any,
    *,
    candidates: list[dict[str, Any]],
    report_text: str,
    mandatory_keys: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    fatal_issues: list[str] = []
    discarded_card_issues: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("cards"), list):
        return [], ["compressor output must contain cards array"], []
    by_key = {str(candidate.get("candidate_key") or ""): candidate for candidate in candidates}
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, card in enumerate(raw["cards"][: MAX_CARDS + 1]):
        if len(accepted) >= MAX_CARDS:
            fatal_issues.append("compressor returned more than five cards")
            break
        if not isinstance(card, dict):
            discarded_card_issues.append(f"card[{index}] is not an object")
            continue
        key = str(card.get("candidate_key") or "").strip()
        role = str(card.get("evidence_role") or "").strip()
        quote = str(card.get("quote") or "").strip()
        source = by_key.get(key)
        if source is None:
            discarded_card_issues.append(f"card[{index}] has unknown candidate_key")
            continue
        source_roles = {str(item) for item in source.get("evidence_roles") or []}
        if role not in ALLOWED_ROLES or role not in source_roles:
            discarded_card_issues.append(f"card[{index}] has unsupported evidence_role")
            continue
        if not quote or len(quote) > MAX_QUOTE_CHARS:
            discarded_card_issues.append(f"card[{index}] quote length is invalid")
            continue
        if quote not in str(source.get("text") or ""):
            discarded_card_issues.append(
                f"card[{index}] quote is not a source substring"
            )
            continue
        emitted_numbers = _numbers(card.get("standard_value"))
        quote_numbers = _numbers(quote)
        if not emitted_numbers <= quote_numbers:
            discarded_card_issues.append(
                f"card[{index}] standard_value emits numbers absent from quote"
            )
            continue
        raw_conditions = (
            card.get("conditions") if isinstance(card.get("conditions"), list) else []
        )
        verified_conditions = [
            condition
            for condition in raw_conditions
            if str(condition or "").strip()
            and str(condition).strip() in quote
        ]
        if len(verified_conditions) != len(raw_conditions):
            discarded_card_issues.append(
                f"card[{index}] dropped ungrounded optional conditions"
            )
        identity = (key, quote)
        if identity in seen:
            continue
        seen.add(identity)
        accepted.append({
            "candidate_key": key,
            "evidence_role": role,
            "quote": quote,
            "property": str(card.get("property") or "").strip(),
            "standard_value": str(card.get("standard_value") or "").strip(),
            "unit": str(card.get("unit") or "").strip(),
            "operator": str(card.get("operator") or "unknown").strip(),
            "scope": str(card.get("scope") or "").strip(),
            "conditions": verified_conditions,
            "selection_reason": str(card.get("selection_reason") or "").strip(),
        })
    selected_keys = {card["candidate_key"] for card in accepted}
    missing_mandatory = sorted(mandatory_keys - selected_keys)
    if missing_mandatory:
        fatal_issues.append(
            "mandatory candidates omitted: " + ", ".join(missing_mandatory)
        )
    selected_roles = {card["evidence_role"] for card in accepted}
    missing_roles = sorted(_required_roles(report_text, candidates) - selected_roles)
    if missing_roles:
        fatal_issues.append(
            "required evidence roles omitted: " + ", ".join(missing_roles)
        )
    if not accepted:
        fatal_issues.append("no valid evidence cards")
    return accepted, fatal_issues, discarded_card_issues


def _compressed_candidate(source: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    compressed = {
        "candidate_key": source["candidate_key"],
        "content_type": source.get("content_type"),
        "business_metadata": source.get("business_metadata") or {},
        "standard_priority": source.get("standard_priority"),
        "evidence_roles": list(source.get("evidence_roles") or []),
        "text": card["quote"],
        "evidence_card": card,
    }
    if isinstance(source.get("table_row_binding"), dict):
        compressed["table_row_binding"] = source["table_row_binding"]
    return compressed


def compress_judge_input(
    judge_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    call_model: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return validated compressed input, or the original input on failure."""
    before_chars = len(json.dumps(judge_input, ensure_ascii=False, separators=(",", ":")))
    report_text = str((judge_input.get("reported_requirement") or {}).get("text") or "")
    mandatory = _mandatory_keys(judge_input)
    payload = {
        "reported_requirement": judge_input.get("reported_requirement") or {},
        "test_item": judge_input.get("test_item") or {},
        "sample_profile": judge_input.get("sample_profile") or {},
        "mandatory_candidate_keys": sorted(mandatory),
        "candidates": judge_input.get("candidates") or [],
    }
    compressor_input_chars = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    if len(mandatory) > MAX_CARDS:
        return judge_input, {
            "mode": "active",
            "applied": False,
            "fallback": True,
            "issues": [
                f"mandatory candidate count {len(mandatory)} exceeds card limit {MAX_CARDS}"
            ],
            "discarded_card_issues": [],
            "judge_input_chars_before": before_chars,
            "compressor_input_chars": 0,
            "additional_model_calls": 0,
        }
    try:
        raw = call_model(COMPRESSOR_PROMPT, payload)
    except Exception as exc:
        return judge_input, {
            "mode": "active",
            "applied": False,
            "fallback": True,
            "issues": [f"compressor call failed: {type(exc).__name__}: {exc}"],
            "judge_input_chars_before": before_chars,
            "compressor_input_chars": compressor_input_chars,
            "additional_model_calls": 1,
        }
    cards, issues, discarded_card_issues = _validate_cards(
        raw,
        candidates=candidates,
        report_text=report_text,
        mandatory_keys=mandatory,
    )
    if issues:
        return judge_input, {
            "mode": "active",
            "applied": False,
            "fallback": True,
            "issues": issues,
            "discarded_card_issues": discarded_card_issues,
            "raw_output": raw,
            "judge_input_chars_before": before_chars,
            "compressor_input_chars": compressor_input_chars,
            "additional_model_calls": 1,
        }
    by_key = {
        str(candidate.get("candidate_key") or ""): candidate
        for candidate in judge_input.get("candidates") or []
    }
    compressed_candidates = [
        _compressed_candidate(by_key[card["candidate_key"]], card)
        for card in cards
    ]
    selected_keys = {card["candidate_key"] for card in cards}
    compressed = {
        **judge_input,
        "candidates": compressed_candidates,
        "deterministic_table_bindings": [
            item
            for item in judge_input.get("deterministic_table_bindings") or []
            if str(item.get("candidate_key") or "") in selected_keys
        ],
        "deterministic_comparisons": [
            item
            for item in judge_input.get("deterministic_comparisons") or []
            if str(item.get("candidate_key") or "") in selected_keys
        ],
        "evidence_compression": {
            "applied": True,
            "card_count": len(cards),
            "selected_candidate_keys": sorted(selected_keys),
        },
    }
    applicability = compressed.get("deterministic_applicability")
    if isinstance(applicability, dict):
        compressed["deterministic_applicability"] = {
            **applicability,
            "candidate_evaluations": [
                item
                for item in applicability.get("candidate_evaluations") or []
                if str(item.get("candidate_key") or "") in selected_keys
            ],
        }
    after_chars = len(json.dumps(compressed, ensure_ascii=False, separators=(",", ":")))
    return compressed, {
        "mode": "active",
        "applied": True,
        "fallback": False,
        "issues": [],
        "discarded_card_issues": discarded_card_issues,
        "cards": cards,
        "judge_input_chars_before": before_chars,
        "judge_input_chars_after": after_chars,
        "compressor_input_chars": compressor_input_chars,
        "additional_model_calls": 1,
    }
