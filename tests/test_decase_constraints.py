from __future__ import annotations

from pathlib import Path

from app.audit_semantics import (
    evaluate_candidate_applicability,
    extract_applicability_constraints,
    resolve_applicability,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = (
    ROOT / "backend/app/audit_semantics.py",
    ROOT / "backend/app/prompt_vars.py",
    ROOT / "backend/app/recovery/gate.py",
    ROOT / "scripts/run_report_audit_workflow.py",
)


def test_runtime_contains_no_retired_case_shaped_hooks() -> None:
    forbidden = (
        "applicability_exact_terms",
        "clause_by_branch",
        "symbolic_ur_formula",
        "_AGGREGATE_SUBGROUP_COUNT_RE",
        "Dyn5",
        "Dyn11",
        "e16",
        "e18",
        "e25",
    )
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_FILES)

    assert not [term for term in forbidden if term in corpus]


def test_applicability_predicates_are_extracted_and_evaluated_generically() -> None:
    candidate = {
        "candidate_key": "c01",
        "text": "system_nominal_voltage <= 30 kV",
    }
    constraints = extract_applicability_constraints(candidate)
    candidate["applicability_constraints"] = constraints
    profile = {
        "from_report": {"rated_voltage": "24/0.4 kV"},
        "from_model_decode": {},
    }
    applicability = resolve_applicability(profile)

    evaluations = evaluate_candidate_applicability([candidate], applicability)

    assert constraints[0]["operator"] == "le"
    assert evaluations[0]["state"] == "applicable"
    assert evaluations[0]["constraints"][0]["actual"] == 24


def test_unknown_constraint_field_remains_unresolved() -> None:
    candidate = {
        "candidate_key": "c01",
        "text": "environment_class >= 3",
    }
    candidate["applicability_constraints"] = extract_applicability_constraints(candidate)

    evaluations = evaluate_candidate_applicability(
        [candidate],
        resolve_applicability({"from_report": {"rated_voltage": "24 kV"}}),
    )

    assert evaluations[0]["state"] == "unresolved"
    assert evaluations[0]["constraints"][0]["reason"] == "parameter_not_bound"
