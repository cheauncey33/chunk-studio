from __future__ import annotations

from app.evidence_compression import compress_judge_input


def _candidate(key: str, role: str, text: str) -> dict:
    return {
        "candidate_key": key,
        "content_type": "section",
        "business_metadata": {"standard_no": "STD-X"},
        "standard_priority": {"rank": 1},
        "evidence_roles": [role],
        "text": text,
    }


def _input(candidates: list[dict]) -> dict:
    return {
        "reported_requirement": {"text": "温度上限: 60 K", "unit": "K"},
        "test_item": {"project_name": "项目甲"},
        "sample_profile": {"from_report": {"model": "M-1"}},
        "deterministic_applicability": {"candidate_evaluations": []},
        "deterministic_table_bindings": [],
        "deterministic_comparisons": [],
        "candidates": candidates,
    }


def test_compressor_builds_verified_cards_and_reduces_judge_input() -> None:
    candidates = [
        _candidate(
            "c01",
            "nominal_rule",
            "标准规定温度上限不应超过55 K。" + ("后续文字无需提交。" * 100),
        ),
        _candidate("c02", "method_rule", "按照规定方法进行测量。" * 100),
    ]

    def call_model(_prompt: str, _payload: dict) -> dict:
        return {
            "cards": [{
                "candidate_key": "c01",
                "evidence_role": "nominal_rule",
                "quote": "标准规定温度上限不应超过55 K。",
                "property": "温度上限",
                "standard_value": "55",
                "unit": "K",
                "operator": "le",
                "scope": "",
                "conditions": [],
                "selection_reason": "supplies the prescribed limit",
            }],
            "missing_roles": [],
        }

    compressed, trace = compress_judge_input(
        _input(candidates),
        candidates,
        call_model=call_model,
    )

    assert trace["applied"] is True
    assert trace["judge_input_chars_after"] < trace["judge_input_chars_before"]
    assert compressed["candidates"][0]["text"] == "标准规定温度上限不应超过55 K。"
    assert compressed["candidates"][0]["evidence_card"]["standard_value"] == "55"


def test_compressor_falls_back_when_quote_is_not_grounded() -> None:
    candidates = [_candidate("c01", "nominal_rule", "标准规定温度上限为55 K。")]
    original = _input(candidates)

    compressed, trace = compress_judge_input(
        original,
        candidates,
        call_model=lambda _prompt, _payload: {
            "cards": [{
                "candidate_key": "c01",
                "evidence_role": "nominal_rule",
                "quote": "标准规定温度上限为50 K。",
                "standard_value": "50",
            }]
        },
    )

    assert compressed is original
    assert trace["fallback"] is True
    assert any(
        "not a source substring" in issue
        for issue in trace["discarded_card_issues"]
    )


def test_compressor_falls_back_when_numeric_claim_loses_nominal_role() -> None:
    candidates = [
        _candidate("c01", "nominal_rule", "标准规定温度上限为55 K。"),
        _candidate("c02", "method_rule", "按照规定方法进行测量。"),
    ]

    _compressed, trace = compress_judge_input(
        _input(candidates),
        candidates,
        call_model=lambda _prompt, _payload: {
            "cards": [{
                "candidate_key": "c02",
                "evidence_role": "method_rule",
                "quote": "按照规定方法进行测量。",
                "standard_value": "",
                "conditions": [],
            }]
        },
    )

    assert trace["fallback"] is True
    assert any("nominal_rule" in issue for issue in trace["issues"])


def test_compressor_discards_invalid_optional_card_without_losing_valid_chain() -> None:
    candidates = [
        _candidate("c01", "nominal_rule", "标准限值为55 K。"),
        _candidate("c02", "method_rule", "按照规定方法进行测量。"),
    ]

    compressed, trace = compress_judge_input(
        _input(candidates),
        candidates,
        call_model=lambda _prompt, _payload: {
            "cards": [
                {
                    "candidate_key": "c01",
                    "evidence_role": "nominal_rule",
                    "quote": "标准限值为55 K。",
                    "standard_value": "55",
                    "conditions": [],
                },
                {
                    "candidate_key": "c02",
                    "evidence_role": "method_rule",
                    "quote": "模型改写而非原文。",
                    "conditions": [],
                },
            ]
        },
    )

    assert trace["applied"] is True
    assert len(compressed["candidates"]) == 1
    assert trace["discarded_card_issues"] == [
        "card[1] quote is not a source substring"
    ]


def test_compressor_drops_ungrounded_optional_condition_but_keeps_quote() -> None:
    candidates = [_candidate("c01", "nominal_rule", "标准限值为55 K。")]

    compressed, trace = compress_judge_input(
        _input(candidates),
        candidates,
        call_model=lambda _prompt, _payload: {
            "cards": [{
                "candidate_key": "c01",
                "evidence_role": "nominal_rule",
                "quote": "标准限值为55 K。",
                "standard_value": "55",
                "conditions": ["容量400 kVA"],
            }]
        },
    )

    assert trace["applied"] is True
    assert compressed["candidates"][0]["evidence_card"]["conditions"] == []
    assert trace["discarded_card_issues"] == [
        "card[0] dropped ungrounded optional conditions"
    ]
