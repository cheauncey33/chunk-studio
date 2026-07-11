"""Validate audit contracts and resolve every gold evidence locator."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evidence_locator import resolve_evidence_locator  # noqa: E402
from app.evidence_locator import canonicalize_chunk_text  # noqa: E402

DEFAULT_DATASET = ROOT / "evaluation" / "standard_value_audit_v1.json"
DEFAULT_DB = ROOT / "backend" / "data" / "chunkstudio.db"
CARD_SCHEMA = ROOT / "backend" / "schemas" / "standard_value_audit_card.schema.json"
RESULT_SCHEMA = ROOT / "backend" / "schemas" / "standard_value_audit_result.schema.json"


def validate_dataset(
    dataset_path: Path,
    db_path: Path | None,
    *,
    verify_corpus: bool = True,
) -> dict[str, int | bool]:
    dataset = _load_json(dataset_path)
    card_schema = _load_json(CARD_SCHEMA)
    result_schema = _load_json(RESULT_SCHEMA)
    Draft202012Validator.check_schema(card_schema)
    Draft202012Validator.check_schema(result_schema)
    card_validator = Draft202012Validator(card_schema)
    result_validator = Draft202012Validator(result_schema)
    errors: list[str] = []
    locator_count = 0
    resolved = 0

    cases = dataset.get("cases") if isinstance(dataset, dict) else None
    if not isinstance(cases, list) or len(cases) != 12:
        errors.append(f"dataset must contain exactly 12 cases; got {len(cases or [])}")
        cases = cases or []
    if _contains_key(dataset, "chunk_id"):
        errors.append("dataset contains forbidden unstable key 'chunk_id'")
    if verify_corpus:
        source_report = dataset.get("source_report", {})
        source_path = ROOT / "reportFile" / source_report.get("document_name", "")
        if not source_path.is_file():
            errors.append(f"source report fixture does not exist: {source_path}")
        elif hashlib.sha256(source_path.read_bytes()).hexdigest() != source_report.get("document_sha256"):
            errors.append("source report fixture SHA-256 does not match dataset declaration")

    conn = None
    if verify_corpus:
        if db_path is None or not db_path.is_file():
            errors.append(f"chunk database does not exist: {db_path}")
        else:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
    try:
        seen_ids: set[str] = set()
        for index, case in enumerate(cases):
            case_id = case.get("id", f"case[{index}]")
            if case_id in seen_ids:
                errors.append(f"{case_id}: duplicate case id")
            seen_ids.add(case_id)
            card = case.get("audit_card", {})
            result = case.get("expected_result", {})

            errors.extend(_schema_errors(card_validator, card, f"{case_id}.audit_card"))
            errors.extend(_schema_errors(result_validator, result, f"{case_id}.expected_result"))
            errors.extend(_semantic_errors(case_id, card, result, case.get("acceptance", {})))

            for evidence_index, evidence in enumerate(result.get("adopted_evidence", [])):
                locator_count += 1
                if conn is None:
                    continue
                matches = resolve_evidence_locator(conn, evidence["locator"])
                if len(matches) != 1:
                    errors.append(
                        f"{case_id}.adopted_evidence[{evidence_index}] resolves to "
                        f"{len(matches)} chunks, expected exactly 1"
                    )
                else:
                    excerpt = canonicalize_chunk_text(evidence["source_excerpt"])
                    if excerpt not in matches[0]["canonical_text"]:
                        errors.append(
                            f"{case_id}.adopted_evidence[{evidence_index}] source_excerpt "
                            "does not occur in the resolved chunk"
                        )
                    resolved += 1
    finally:
        if conn is not None:
            conn.close()

    if errors:
        raise ValueError("Audit evaluation validation failed:\n- " + "\n- ".join(errors))
    return {
        "cases": len(cases),
        "evidence_locators": locator_count,
        "corpus_verified": verify_corpus and resolved == locator_count,
    }


def _semantic_errors(
    case_id: str,
    card: dict[str, Any],
    result: dict[str, Any],
    acceptance: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if result.get("card_id") != card.get("card_id"):
        errors.append(f"{case_id}: result.card_id must match audit_card.card_id")

    bases = {item.get("basis_id"): item for item in card.get("report", {}).get("declared_bases", [])}
    declared = card.get("report", {}).get("declared_bases", [])
    if len(bases) != len(declared):
        errors.append(f"{case_id}: declared basis_id values must be unique")
    basis_result = result.get("basis_assessment", {})
    adopted_ids = basis_result.get("adopted_basis_ids", [])
    missing_ids = basis_result.get("missing_basis_ids", [])
    for basis_id in adopted_ids + missing_ids:
        if basis_id not in bases:
            errors.append(f"{case_id}: result references undeclared basis {basis_id!r}")
    for basis_id in missing_ids:
        if bases.get(basis_id, {}).get("availability") != "missing":
            errors.append(f"{case_id}: missing basis {basis_id!r} is not marked missing in card")
    for evidence in result.get("adopted_evidence", []):
        if evidence.get("basis_id") not in adopted_ids:
            errors.append(f"{case_id}: evidence basis is not listed in adopted_basis_ids")

    scope = result.get("audit_assessment", {}).get("scope")
    if basis_result.get("status") != "complete" and scope != "available_bases_only":
        errors.append(f"{case_id}: incomplete basis requires available_bases_only scope")
    if basis_result.get("status") == "missing_declared_basis" and not missing_ids:
        errors.append(f"{case_id}: missing_declared_basis requires missing_basis_ids")

    review = result.get("human_review", {})
    if scope == "available_bases_only" and not review.get("required"):
        errors.append(f"{case_id}: provisional-scope result must require human review")
    if review.get("required") and review.get("status") == "not_required":
        errors.append(f"{case_id}: required review cannot have not_required status")
    if not review.get("required") and review.get("status") != "not_required":
        errors.append(f"{case_id}: optional review must have not_required status")

    audit = result.get("audit_assessment", {})
    status = audit.get("status")
    error_types = audit.get("error_types", [])
    if status == "supported" and error_types:
        errors.append(f"{case_id}: supported result cannot contain error_types")
    if status == "mismatch" and not error_types:
        errors.append(f"{case_id}: mismatch result requires at least one error_type")
    if status == "insufficient_context":
        if "missing_context" not in error_types or not audit.get("missing_context_fields"):
            errors.append(f"{case_id}: insufficient_context requires missing context details")

    evidence_count = len(result.get("adopted_evidence", []))
    retrieval = acceptance.get("retrieval", {})
    for group in retrieval.get("required_evidence_groups", []):
        if not group or any(not isinstance(i, int) or i < 0 or i >= evidence_count for i in group):
            errors.append(f"{case_id}: acceptance contains an invalid evidence index group")
    return errors


def _schema_errors(
    validator: Draft202012Validator,
    instance: Any,
    prefix: str,
) -> list[str]:
    errors = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        path = ".".join(str(part) for part in error.absolute_path)
        errors.append(f"{prefix}{'.' + path if path else ''}: {error.message}")
    return errors


def _contains_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(child, target) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, target) for child in value)
    return False


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--skip-corpus",
        action="store_true",
        help="Validate contracts and labels without local report/database fixtures.",
    )
    args = parser.parse_args()
    summary = validate_dataset(args.dataset, args.db, verify_corpus=not args.skip_corpus)
    if summary["corpus_verified"]:
        print(
            f"Validated {summary['cases']} cases and uniquely resolved "
            f"{summary['evidence_locators']} evidence locators."
        )
    else:
        print(
            f"Validated contracts for {summary['cases']} cases and "
            f"{summary['evidence_locators']} declared evidence locators; corpus checks skipped."
        )


if __name__ == "__main__":
    main()
