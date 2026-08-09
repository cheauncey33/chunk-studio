from __future__ import annotations

from app.rolling_evidence_selection import select_judge_evidence_rolling


def _candidate(index: int) -> dict:
    return {
        "candidate_key": f"c{index:02d}",
        "content_type": "section",
        "business_metadata": {"standard_no": f"STD-{index}"},
        "standard_priority": {"rank": index},
        "evidence_roles": [],
        "text": f"candidate {index} exact rule text",
    }


def _judge_input(candidates: list[dict]) -> dict:
    return {
        "reported_requirement": {"text": "reported claim"},
        "test_item": {"project_name": "generic project"},
        "sample_profile": {},
        "deterministic_applicability": {"candidate_evaluations": []},
        "deterministic_table_bindings": [],
        "deterministic_comparisons": [],
        "candidates": candidates,
    }


def test_rolling_selector_scans_every_batch_and_can_replace_evidence() -> None:
    candidates = [_candidate(index) for index in range(1, 7)]
    calls: list[dict] = []

    def call_model(_prompt: str, payload: dict) -> dict:
        calls.append(payload)
        if payload["round"] == 1:
            return {
                "retained": [{
                    "candidate_key": "c01",
                    "quote": "candidate 1 exact rule text",
                    "reason": "initial evidence",
                }],
                "unresolved": [],
                "conflicts": [],
                "evidence_sufficient": True,
            }
        return {
            "retained": [{
                "candidate_key": "c06",
                "quote": "candidate 6 exact rule text",
                "reason": "later higher-priority evidence",
            }],
            "unresolved": [],
            "conflicts": [],
            "evidence_sufficient": True,
        }

    selected, trace = select_judge_evidence_rolling(
        _judge_input(candidates),
        candidates,
        call_model=call_model,
        batch_size=5,
        batch_chars=100000,
    )

    assert len(calls) == 2
    assert trace["applied"] is True
    assert selected["candidates"][0]["candidate_key"] == "c06"
    assert selected["rolling_evidence_selection"]["processed_candidate_count"] == 6
    assert "unresolved" not in selected["rolling_evidence_selection"]
    assert "selection_note" not in selected["candidates"][0]


def test_rolling_selector_falls_back_on_ungrounded_quote() -> None:
    candidates = [_candidate(1)]
    original = _judge_input(candidates)

    selected, trace = select_judge_evidence_rolling(
        original,
        candidates,
        call_model=lambda _prompt, _payload: {
            "retained": [{
                "candidate_key": "c01",
                "quote": "fabricated rule text",
                "reason": "invalid",
            }],
            "unresolved": [],
            "conflicts": [],
            "evidence_sufficient": False,
        },
    )

    assert selected is original
    assert trace["fallback"] is True
    assert any(
        "not a source substring" in issue
        for issue in trace["rounds"][0]["discarded"]
    )


def test_rolling_selector_cannot_restore_a_dropped_earlier_candidate() -> None:
    candidates = [_candidate(index) for index in range(1, 3)]

    def call_model(_prompt: str, payload: dict) -> dict:
        if payload["round"] == 1:
            return {
                "retained": [],
                "unresolved": ["need evidence"],
                "conflicts": [],
                "evidence_sufficient": False,
            }
        return {
            "retained": [{
                "candidate_key": "c01",
                "quote": "candidate 1 exact rule text",
                "reason": "attempt to restore dropped evidence",
            }],
            "unresolved": [],
            "conflicts": [],
            "evidence_sufficient": True,
        }

    _selected, trace = select_judge_evidence_rolling(
        _judge_input(candidates),
        candidates,
        call_model=call_model,
        batch_size=1,
        batch_chars=100000,
    )

    assert trace["fallback"] is True
    assert any(
        "unavailable candidate_key" in issue
        for issue in trace["rounds"][-1]["discarded"]
    )


def test_rolling_selector_discards_one_bad_quote_and_keeps_valid_evidence() -> None:
    candidates = [_candidate(1), _candidate(2)]

    selected, trace = select_judge_evidence_rolling(
        _judge_input(candidates),
        candidates,
        call_model=lambda _prompt, _payload: {
            "retained": [
                {
                    "candidate_key": "c01",
                    "quote": "candidate 1 exact rule text",
                    "reason": "grounded",
                },
                {
                    "candidate_key": "c02",
                    "quote": "candidate 2 rewritten rule",
                    "reason": "ungrounded",
                },
            ],
            "unresolved": [],
            "conflicts": [],
            "evidence_sufficient": True,
        },
    )

    assert trace["applied"] is True
    assert [item["candidate_key"] for item in selected["candidates"]] == ["c01"]
    assert trace["rounds"][0]["discarded"] == [
        "retained[1] quote is not a source substring"
    ]
