"""Validate retrieval ground-truth v2 structure and exact corpus locators."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evidence_locator import resolve_evidence_locator  # noqa: E402


DEFAULT_DATASET = ROOT / "evaluation" / "test_set.json"
DEFAULT_DB = ROOT / "backend" / "data" / "chunkstudio.db"
SCHEMA = ROOT / "backend" / "schemas" / "test_set.schema.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def _semantic_errors(dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases = dataset.get("cases", [])
    actual_ids = [case.get("case_id") for case in cases]
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("case IDs must be unique")
    if _contains_key(dataset, "chunk_id"):
        errors.append("dataset contains forbidden unstable key 'chunk_id'")
    if _contains_key(dataset, "manual_rule_id"):
        errors.append("manual rules must not satisfy retrieval evidence groups")

    for case in cases:
        case_id = case.get("case_id", "<missing>")
        scope = case.get("evaluation_scope")
        status = case.get("answerability_status")
        groups = case.get("required_evidence_groups", [])
        contexts = case.get("deterministic_context_evidence", [])
        relevant = case.get("relevant_evidence", [])
        missing_fields = case.get("missing_context_fields", [])
        if scope == "deterministic_prefilter":
            if status != "not_applicable_to_retrieval" or groups or contexts or relevant:
                errors.append(f"{case_id}: deterministic prefilter cases cannot contain retrieval gold or context")
        elif status == "answerable_candidate":
            if not groups or not relevant or missing_fields:
                errors.append(f"{case_id}: answerable retrieval cases require strict and lenient evidence with no missing context")
        elif status == "context_required":
            if groups or contexts or relevant or not missing_fields:
                errors.append(f"{case_id}: context-required cases need missing fields and no declared evidence")

        group_ids = [group.get("group_id") for group in groups]
        if len(group_ids) != len(set(group_ids)):
            errors.append(f"{case_id}: evidence group IDs must be unique within a case")
        context_ids = [context.get("context_id") for context in contexts]
        if len(context_ids) != len(set(context_ids)):
            errors.append(f"{case_id}: deterministic context IDs must be unique within a case")
        strict_hashes = {
            alternative["locator"]["text_sha256"]
            for group in groups
            for alternative in group.get("alternatives", [])
        }
        context_hashes = {
            alternative["locator"]["text_sha256"]
            for context in contexts
            for alternative in context.get("alternatives", [])
        }
        relevant_hashes = {item["locator"]["text_sha256"] for item in relevant}
        if not strict_hashes.issubset(relevant_hashes):
            errors.append(f"{case_id}: strict evidence must also appear in lenient relevance evidence")
        if context_hashes & (strict_hashes | relevant_hashes):
            errors.append(f"{case_id}: deterministic context must be disjoint from retrieval gold")
        if any(
            alternative.get("legacy_label") != "direct_candidate"
            for group in groups
            for alternative in group.get("alternatives", [])
        ):
            errors.append(f"{case_id}: strict evidence groups may contain only direct candidates")
        if any(
            alternative.get("legacy_label") != "direct_candidate"
            for context in contexts
            for alternative in context.get("alternatives", [])
        ):
            errors.append(f"{case_id}: deterministic context may contain only direct candidates")

    alternatives = [
        alternative
        for case in cases
        for group in case.get("required_evidence_groups", [])
        for alternative in group.get("alternatives", [])
    ]
    relevant_evidence = [
        evidence
        for case in cases
        for evidence in case.get("relevant_evidence", [])
    ]
    deterministic_context = [
        context
        for case in cases
        for context in case.get("deterministic_context_evidence", [])
    ]
    deterministic_context_alternatives = [
        alternative
        for context in deterministic_context
        for alternative in context.get("alternatives", [])
    ]
    expected_summary = {
        "case_count": len(cases),
        "retrieval_answerable_candidates": sum(case.get("answerability_status") == "answerable_candidate" for case in cases),
        "context_required_cases": sum(case.get("answerability_status") == "context_required" for case in cases),
        "deterministic_prefilter_cases": sum(case.get("evaluation_scope") == "deterministic_prefilter" for case in cases),
        "required_evidence_groups": sum(len(case.get("required_evidence_groups", [])) for case in cases),
        "deterministic_context_groups": len(deterministic_context),
        "evidence_locator_assignments": len(alternatives),
        "unique_evidence_chunks": len({item["locator"]["text_sha256"] for item in alternatives}),
        "deterministic_context_locator_assignments": len(deterministic_context_alternatives),
        "unique_deterministic_context_chunks": len({
            item["locator"]["text_sha256"] for item in deterministic_context_alternatives
        }),
        "relevant_locator_assignments": len(relevant_evidence),
        "unique_relevant_chunks": len({item["locator"]["text_sha256"] for item in relevant_evidence}),
        "pending_domain_review_cases": sum(case.get("review", {}).get("status") == "pending_domain_review" for case in cases),
        "scoreable_case_count": sum(
            case.get("review", {}).get("status") == "human_approved"
            and case.get("answerability_status") == "answerable_candidate"
            for case in cases
        ),
        "legacy_direct_missing_conflicts": sum(
            "legacy_missing_note_conflicts_with_direct_candidates" in case.get("review", {}).get("flags", [])
            for case in cases
        ),
        "resolved_legacy_direct_missing_conflicts": sum(
            "legacy_missing_note_resolved_by_exact_corpus_evidence" in case.get("review", {}).get("flags", [])
            for case in cases
        ),
    }
    if dataset.get("summary") != expected_summary:
        errors.append("summary does not match case contents")
    return errors


def validate_dataset(
    dataset_path: Path = DEFAULT_DATASET,
    db_path: Path | None = DEFAULT_DB,
    *,
    verify_corpus: bool = True,
) -> dict[str, int | bool]:
    dataset = _read_json(dataset_path)
    schema = _read_json(SCHEMA)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = [
        f"schema {'.'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(validator.iter_errors(dataset), key=lambda item: list(item.absolute_path))
    ]
    errors.extend(_semantic_errors(dataset))

    locators = [
        alternative["locator"]
        for case in dataset.get("cases", [])
        for group in case.get("required_evidence_groups", [])
        for alternative in group.get("alternatives", [])
    ]
    relevant_locators = [
        evidence["locator"]
        for case in dataset.get("cases", [])
        for evidence in case.get("relevant_evidence", [])
    ]
    context_locators = [
        alternative["locator"]
        for case in dataset.get("cases", [])
        for context in case.get("deterministic_context_evidence", [])
        for alternative in context.get("alternatives", [])
    ]
    resolved = 0
    conn: sqlite3.Connection | None = None
    if verify_corpus:
        if db_path is None or not db_path.is_file():
            errors.append(f"chunk database does not exist: {db_path}")
        else:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
    try:
        if conn is not None:
            for index, locator in enumerate(locators + context_locators + relevant_locators):
                matches = resolve_evidence_locator(conn, locator)
                if len(matches) != 1:
                    errors.append(f"evidence locator {index} resolves to {len(matches)} chunks, expected exactly 1")
                else:
                    resolved += 1
    finally:
        if conn is not None:
            conn.close()

    if errors:
        raise ValueError("Retrieval ground-truth v2 validation failed:\n- " + "\n- ".join(errors))
    return {
        "cases": len(dataset["cases"]),
        "evidence_locators": len(locators),
        "deterministic_context_locators": len(context_locators),
        "relevant_locators": len(relevant_locators),
        "corpus_verified": (
            verify_corpus
            and resolved == len(locators) + len(context_locators) + len(relevant_locators)
        ),
        "scoreable_cases": dataset["summary"]["scoreable_case_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--skip-corpus", action="store_true")
    args = parser.parse_args()
    result = validate_dataset(
        args.dataset,
        args.db,
        verify_corpus=not args.skip_corpus,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
