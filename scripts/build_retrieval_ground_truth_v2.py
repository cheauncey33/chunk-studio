"""Build a reviewable retrieval ground-truth draft from the v1 candidate audit."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_POOL = ROOT / "evaluation" / "retrieval_case_pool_v1.json"
DEFAULT_LEGACY_REVIEW = ROOT / "evaluation" / "retrieval_gold_candidates_v1.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "retrieval_ground_truth_v2_draft.json"

# These requirements are computed from values already present in the report and
# therefore belong to the deterministic prefilter, not retrieval recall.
DETERMINISTIC_PREFILTER_CASES = {
    "hbjc-5-r3",
    "ezc-5-r4",
    "whc-5-r4",
    "xyc-5-r4",
}

CONTEXT_REQUIRED_FIELDS = {
    "hbjc-13-r4": ["reactance_metric_definition"],
    "hbjc-9-r3": ["tank_structure_type"],
    "whc-14-1-r4": ["winding_structure_type"],
}

EVIDENCE_CONFLICT_CASES = {"hbjc-13-r4"}

Q_GDW_TABLE_6 = "2e78c1734e20190b0b4791ae4dcaa88a599007295cbfe9580892c69e84ea4ddd"
GB_20052_TABLE_1 = "9c986dc1c0006eaf9bb304e9b75b58444fb63496818cb6dac2eef0b96ba572dc"
NO_LOAD_CURRENT_TOLERANCE = "6017f91d07380fef32e26d5c571727651c38a9a73919659fd3a1c119c24268cc"
IMPEDANCE_TOLERANCE = "5d634879be135111a1f8c80772ed71e4a5c5db580a651c47130764f8ccf15b4c"
INSULATION_LEVEL_TABLE = "e05945ebd9f4e0ea171b6b0d9f0c992228bfe2aba0bb1c264db1dd4372fcabe8"
JB_T_501_INSULATION_LEVEL_TABLE = "76c4e8530c5f6ad181e3256760859a44b10f566e2e8975e6c515fa5dcd048d0f"
LIGHTNING_WAVEFORM_RULE = "c383f3f76a093d1fa601d48c3979faa59ce0733868316988ea9b04df46f4e43e"

CURATED_EVIDENCE = {
    JB_T_501_INSULATION_LEVEL_TABLE: {
        "locator": {
            "standard_no": "JB/T 501-2021",
            "content_type": "table",
            "page_start": 25,
            "page_end": 25,
            "text_sha256": JB_T_501_INSULATION_LEVEL_TABLE,
            "table_no": "11",
            "table_title": "绕组的试验电压水平",
        },
        "evidence_quote": (
            "系统标称电压10 kV、设备最高电压12 kV对应雷电全波冲击(LI)75 kV、"
            "雷电截波冲击(LIC)85 kV、外施电压或线端交流电压35 kV。"
        ),
        "quote_verified": True,
        "reason": (
            "JB/T 501-2021表11直接给出10 kV系统（Um=12 kV）的LI试验电压为75 kV，"
            "可作为同一数值关系的替代real chunk；产品适用性仍待领域审核。"
        ),
        "label": "direct_candidate",
        "retrieval": {"purpose": "curated_exact_corpus_evidence"},
    },
}

ALTERNATIVE_OVERRIDES = {
    Q_GDW_TABLE_6: {
        "reason": (
            "Q/GDW 12126.4-2024表6按额定容量列出S20-NX2系列的空载损耗、负载损耗、"
            "空载电流和短路阻抗，可按案例中的型号与容量定位对应参数。"
        ),
    },
    GB_20052_TABLE_1: {
        "reason": (
            "GB 20052-2024表1按额定容量和能效等级列出10 kV油浸式配电变压器的"
            "空载损耗与负载损耗，可按案例中的容量和产品类别定位对应限值。"
        ),
    },
}

LI_TABLE_OVERRIDE = {
    "evidence_quote": (
        "系统标称电压10 kV、设备最高电压12 kV对应雷电全波冲击(LI)75 kV、"
        "雷电截波冲击(LIC)85 kV、外施耐压或线端交流耐压35 kV。"
    ),
    "quote_verified": True,
    "reason": (
        "GB/T 1094.3-2017表2直接列出10 kV系统（Um=12 kV）的雷电全波冲击(LI)"
        "试验电压为75 kV；35 kV是AV/LTAC列，不能写成LI值。"
    ),
}
AV_TABLE_OVERRIDE = {
    "evidence_quote": LI_TABLE_OVERRIDE["evidence_quote"],
    "quote_verified": True,
    "reason": (
        "GB/T 1094.3-2017表2直接列出10 kV系统（Um=12 kV）的外施耐压或线端交流耐压"
        "(AV/LTAC)为35 kV；75 kV是LI列，不能写成AV值。"
    ),
}
CASE_ALTERNATIVE_OVERRIDES = {
    **{
        case_id: {INSULATION_LEVEL_TABLE: LI_TABLE_OVERRIDE}
        for case_id in ("hbjc-14-r1", "ezc-10-r1", "whc-10-r1", "xyc-10-r1")
    },
    **{
        case_id: {INSULATION_LEVEL_TABLE: AV_TABLE_OVERRIDE}
        for case_id in ("hbjc-7-r1", "whc-6-r1", "xyc-6-r1")
    },
}
def _complete_answer(*hashes: str) -> list[dict[str, Any]]:
    return [{
        "group_id": "complete_normative_answer",
        "requirement": "任一候选 chunk 应能独立给出该检测项目所需的完整规范性答案。",
        "text_sha256": list(hashes),
    }]


def _value_only(value_hash: str) -> list[dict[str, Any]]:
    return [{
        "group_id": "no_load_current_limit",
        "requirement": "给出样品对应的空载电流基准限值。",
        "text_sha256": [value_hash],
    }]


def _lightning_voltage_and_waveform() -> list[dict[str, Any]]:
    return [
        {
            "group_id": "lightning_impulse_voltage",
            "requirement": "给出样品电压等级对应的雷电冲击耐受电压。",
            "text_sha256": [INSULATION_LEVEL_TABLE, JB_T_501_INSULATION_LEVEL_TABLE],
        },
        {
            "group_id": "lightning_impulse_waveform",
            "requirement": "给出雷电全波的波形参数和试验电压偏差。",
            "text_sha256": [LIGHTNING_WAVEFORM_RULE],
        },
    ]


# These cases had stale missing-evidence notes or an incomplete flat label.
# Exact hashes make the resolved OR/AND relationship explicit.
EVIDENCE_GROUP_OVERRIDES = {
    "hbjc-4-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "hbjc-4-r2": _value_only(Q_GDW_TABLE_6),
    "hbjc-5-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "hbjc-7-r1": _complete_answer(INSULATION_LEVEL_TABLE),
    "hbjc-14-r1": _lightning_voltage_and_waveform(),
    "hbjc-15-5-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "ezc-4-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "ezc-4-r2": _value_only(Q_GDW_TABLE_6),
    "ezc-5-r2": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "ezc-10-r1": _complete_answer(INSULATION_LEVEL_TABLE, JB_T_501_INSULATION_LEVEL_TABLE),
    "whc-4-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "whc-4-r2": _value_only(Q_GDW_TABLE_6),
    "whc-5-r2": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "whc-10-r1": _lightning_voltage_and_waveform(),
    "whc-14-2-5-r2": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "xyc-4-r1": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "xyc-5-r2": _complete_answer(Q_GDW_TABLE_6, GB_20052_TABLE_1),
    "xyc-5-r3": [{
        "group_id": "nominal_impedance",
        "requirement": "给出样品型号和容量对应的短路阻抗基准值。",
        "text_sha256": [Q_GDW_TABLE_6],
    }],
    "xyc-10-r1": _lightning_voltage_and_waveform(),
}

DETERMINISTIC_CONTEXT_OVERRIDES = {
    case_id: [{
        "context_id": "no_load_current_tolerance",
        "application_rule": "空载电流基准值命中后，附加设计值+30%的通用允许偏差。",
        "text_sha256": [NO_LOAD_CURRENT_TOLERANCE],
    }]
    for case_id in ("hbjc-4-r2", "ezc-4-r2", "whc-4-r2")
}
DETERMINISTIC_CONTEXT_OVERRIDES["xyc-5-r3"] = [{
    "context_id": "impedance_tolerance",
    "application_rule": "短路阻抗基准值命中后，按阻抗值小于10%的主分接规则附加±10%允许偏差。",
    "text_sha256": [IMPEDANCE_TOLERANCE],
}]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _alternative(evidence: dict[str, Any], case_id: str) -> dict[str, Any]:
    retrieval = evidence.get("retrieval") or {}
    alternative = {
        "locator": evidence["locator"],
        "evidence_quote": evidence.get("evidence_quote") or "",
        "quote_verified": bool(evidence.get("quote_verified")),
        "reason": evidence.get("reason") or "",
        "legacy_label": evidence["label"],
        "discovery_method": retrieval.get("purpose") or retrieval.get("method") or "candidate_review",
    }
    override = ALTERNATIVE_OVERRIDES.get(evidence["locator"]["text_sha256"])
    if override:
        alternative.update(override)
    case_override = CASE_ALTERNATIVE_OVERRIDES.get(case_id, {}).get(
        evidence["locator"]["text_sha256"]
    )
    if case_override:
        alternative.update(case_override)
    return alternative


def _review_flags(case_id: str, legacy: dict[str, Any], direct: list[dict[str, Any]]) -> list[str]:
    flags = ["model_derived_not_human_approved"]
    if direct and legacy.get("missing_evidence"):
        if case_id in EVIDENCE_GROUP_OVERRIDES:
            flags.append("legacy_missing_note_resolved_by_exact_corpus_evidence")
        elif case_id in DETERMINISTIC_PREFILTER_CASES:
            flags.append("legacy_missing_note_irrelevant_to_retrieval_prefilter")
        else:
            flags.append("legacy_missing_note_conflicts_with_direct_candidates")
    if legacy.get("gold_status") == "candidate_gold_recovered_pending_domain_review":
        flags.append("candidate_recovered_by_diagnostic_search")
    return flags


def _build_case(
    case: dict[str, Any],
    legacy: dict[str, Any],
    evidence_by_hash: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_id = case["case_id"]
    selected = legacy.get("selected_evidence", [])
    direct = [item for item in selected if item.get("label") == "direct_candidate"]
    flags = _review_flags(case_id, legacy, direct)

    if case_id in DETERMINISTIC_PREFILTER_CASES:
        evaluation_scope = "deterministic_prefilter"
        answerability_status = "not_applicable_to_retrieval"
        missing_context_fields: list[str] = []
        groups: list[dict[str, Any]] = []
        flags.append("excluded_from_retrieval_denominator")
    elif case_id in CONTEXT_REQUIRED_FIELDS:
        evaluation_scope = "retrieval"
        answerability_status = "context_required"
        missing_context_fields = CONTEXT_REQUIRED_FIELDS[case_id]
        groups = []
        flags.append("not_scoreable_without_report_context")
        if case_id in EVIDENCE_CONFLICT_CASES:
            flags.append("evidence_metric_conflict_pending_domain_review")
    elif case_id in EVIDENCE_GROUP_OVERRIDES:
        evaluation_scope = "retrieval"
        answerability_status = "answerable_candidate"
        missing_context_fields = []
        groups = []
        for definition in EVIDENCE_GROUP_OVERRIDES[case_id]:
            alternatives = []
            for text_sha256 in definition["text_sha256"]:
                if text_sha256 not in evidence_by_hash:
                    raise ValueError(f"{case_id}: grouped evidence {text_sha256} is unavailable")
                alternatives.append(_alternative(evidence_by_hash[text_sha256], case_id))
            groups.append({
                "group_id": definition["group_id"],
                "requirement": definition["requirement"],
                "alternatives": alternatives,
            })
        flags.append("required_group_relation_verified_from_exact_corpus_evidence")
    else:
        if not direct:
            raise ValueError(f"{case_id}: retrieval case has no direct evidence candidate")
        evaluation_scope = "retrieval"
        answerability_status = "answerable_candidate"
        missing_context_fields = []
        groups = [{
            "group_id": "complete_normative_answer",
            "requirement": "任一候选 chunk 应能独立给出该检测项目所需的完整规范性答案。",
            "alternatives": [_alternative(item, case_id) for item in direct],
        }]
        if len(direct) > 1:
            flags.append("or_equivalent_relation_inferred_from_legacy_review")

    deterministic_context = []
    for definition in DETERMINISTIC_CONTEXT_OVERRIDES.get(case_id, []):
        alternatives = []
        for text_sha256 in definition["text_sha256"]:
            if text_sha256 not in evidence_by_hash:
                raise ValueError(f"{case_id}: context evidence {text_sha256} is unavailable")
            alternatives.append(_alternative(evidence_by_hash[text_sha256], case_id))
        deterministic_context.append({
            "context_id": definition["context_id"],
            "application_rule": definition["application_rule"],
            "alternatives": alternatives,
        })
    if deterministic_context:
        flags.append("deterministic_context_excluded_from_retrieval_gold")
    context_hashes = {
        alternative["locator"]["text_sha256"]
        for context in deterministic_context
        for alternative in context["alternatives"]
    }

    relevant_evidence_by_hash = {
        item["locator"]["text_sha256"]: _alternative(item, case_id)
        for item in selected
        if item.get("label") in {"direct_candidate", "supporting_candidate"}
        and item["locator"]["text_sha256"] not in context_hashes
    } if answerability_status == "answerable_candidate" else {}
    for group in groups:
        for alternative in group["alternatives"]:
            relevant_evidence_by_hash.setdefault(alternative["locator"]["text_sha256"], alternative)

    counts = Counter(item.get("label") for item in selected)
    return {
        "case_id": case_id,
        "dataset_split": case["dataset_split"],
        "retrieval_class": case["retrieval_class"],
        "detection_project": {
            "project_name": case["test_item"]["project_name"],
            "reported_requirement": case["reported_requirement"],
            "sample_context": case["sample_context"],
        },
        "evaluation_scope": evaluation_scope,
        "answerability_status": answerability_status,
        "missing_context_fields": missing_context_fields,
        "relevant_evidence": list(relevant_evidence_by_hash.values()),
        "required_evidence_groups": groups,
        "deterministic_context_evidence": deterministic_context,
        "review": {
            "status": "pending_domain_review",
            "migration_basis": "Migrated from v1 direct candidates; evidence sufficiency and OR relation require domain review.",
            "flags": flags,
        },
        "legacy_review_context": {
            "gold_status": legacy.get("gold_status") or "",
            "direct_candidate_count": counts["direct_candidate"],
            "supporting_candidate_count": counts["supporting_candidate"],
            "uncertain_count": counts["uncertain"],
            "missing_evidence": legacy.get("missing_evidence") or "",
            "review_note": legacy.get("review_note") or "",
            "recovery_note": legacy.get("recovery_note") or "",
        },
    }


def build_dataset(case_pool: dict[str, Any], legacy_review: dict[str, Any]) -> dict[str, Any]:
    legacy_by_id = {case["case_id"]: case for case in legacy_review["cases"]}
    case_ids = {case["case_id"] for case in case_pool["cases"]}
    if case_ids != set(legacy_by_id):
        raise ValueError("case pool and legacy review case IDs differ")

    evidence_by_hash: dict[str, dict[str, Any]] = {}
    for legacy in legacy_review["cases"]:
        for evidence in legacy.get("selected_evidence", []):
            if evidence.get("label") != "direct_candidate":
                continue
            evidence_by_hash.setdefault(evidence["locator"]["text_sha256"], evidence)
    evidence_by_hash.update(CURATED_EVIDENCE)

    cases = [
        _build_case(case, legacy_by_id[case["case_id"]], evidence_by_hash)
        for case in case_pool["cases"]
    ]
    alternatives = [
        alternative
        for case in cases
        for group in case["required_evidence_groups"]
        for alternative in group["alternatives"]
    ]
    relevant_evidence = [
        evidence
        for case in cases
        for evidence in case["relevant_evidence"]
    ]
    deterministic_context = [
        context
        for case in cases
        for context in case["deterministic_context_evidence"]
    ]
    deterministic_context_alternatives = [
        alternative
        for context in deterministic_context
        for alternative in context["alternatives"]
    ]
    summary = {
        "case_count": len(cases),
        "retrieval_answerable_candidates": sum(
            case["answerability_status"] == "answerable_candidate" for case in cases
        ),
        "context_required_cases": sum(case["answerability_status"] == "context_required" for case in cases),
        "deterministic_prefilter_cases": sum(case["evaluation_scope"] == "deterministic_prefilter" for case in cases),
        "required_evidence_groups": sum(len(case["required_evidence_groups"]) for case in cases),
        "deterministic_context_groups": len(deterministic_context),
        "evidence_locator_assignments": len(alternatives),
        "unique_evidence_chunks": len({item["locator"]["text_sha256"] for item in alternatives}),
        "deterministic_context_locator_assignments": len(deterministic_context_alternatives),
        "unique_deterministic_context_chunks": len({
            item["locator"]["text_sha256"] for item in deterministic_context_alternatives
        }),
        "relevant_locator_assignments": len(relevant_evidence),
        "unique_relevant_chunks": len({item["locator"]["text_sha256"] for item in relevant_evidence}),
        "pending_domain_review_cases": sum(case["review"]["status"] == "pending_domain_review" for case in cases),
        "scoreable_case_count": sum(
            case["review"]["status"] == "human_approved"
            and case["answerability_status"] == "answerable_candidate"
            for case in cases
        ),
        "legacy_direct_missing_conflicts": sum(
            "legacy_missing_note_conflicts_with_direct_candidates" in case["review"]["flags"]
            for case in cases
        ),
        "resolved_legacy_direct_missing_conflicts": sum(
            "legacy_missing_note_resolved_by_exact_corpus_evidence" in case["review"]["flags"]
            for case in cases
        ),
    }
    return {
        "version": 2,
        "status": "draft_pending_domain_review",
        "unit_of_evaluation": "one reported detection requirement",
        "evidence_semantics": (
            "Every required group must be recalled (AND across groups); one exact chunk locator inside a group is sufficient "
            "(OR within a group). Supporting evidence and deterministic_context_evidence are excluded from retrieval gold; "
            "deterministic context is attached after the primary evidence is selected."
        ),
        "evaluation_profiles": {
            "lenient_relevance": {
                "metric": "any_relevant_chunk_recall_at_k",
                "evidence_field": "relevant_evidence",
                "primary": False,
                "description": "Any direct or supporting chunk is a hit; use for candidate-generation diagnosis only.",
            },
            "strict_answer": {
                "metric": "all_required_evidence_groups_recall_at_k",
                "evidence_field": "required_evidence_groups",
                "primary": True,
                "description": "All AND-required groups must be recalled; OR alternatives inside each group are equivalent.",
            },
        },
        "source_artifacts": {
            "case_pool": "evaluation/retrieval_case_pool_v1.json",
            "legacy_candidate_review": "evaluation/retrieval_gold_candidates_v1.json",
            "legacy_evidence_groups": "evaluation/retrieval_evidence_groups_hbjc_v1.json",
        },
        "summary": summary,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-pool", type=Path, default=DEFAULT_CASE_POOL)
    parser.add_argument("--legacy-review", type=Path, default=DEFAULT_LEGACY_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = build_dataset(_read_json(args.case_pool), _read_json(args.legacy_review))
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dataset["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
