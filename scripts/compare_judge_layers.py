"""Compare the legacy programmatic judge layer against the caliber engine.

Both layers see the same inputs: the reported requirement and the structured
standard fact recorded on each gold case. The question this script answers is
how much of the judge work each layer closes on its own, and how the two agree
with the constructed defect contract, so the switch to ``audit_caliber.derive``
can be argued from numbers instead of intent.

The legacy layer is deliberately given the benefit of the doubt: both claims
carry the same property text, so its 0.35 property-similarity gate never
rejects a pair that the caliber would have compared.

    $env:PYTHONPATH='backend'; uv run python scripts/compare_judge_layers.py
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.audit_caliber import derive_case, load_caliber  # noqa: E402
from app.audit_semantics import (  # noqa: E402
    _status_for_table_comparison,
    compare_claims,
    extract_requirement_claim,
    parse_claim,
)


DEFAULT_GOLD = ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "gold.json"
DEFAULT_MANIFEST = ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "manifest.json"
DEFAULT_OUT = ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "judge_layer_comparison.json"

# The caliber closes on these; anything else hands the case back to the model.
_CALIBER_CLOSING = {"match", "mismatch", "out_of_scope"}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_status(case: dict[str, Any]) -> tuple[str | None, str]:
    """Run today's programmatic layer on one case; None means 'ask the model'."""
    project = case.get("detection_project") or {}
    reported = project.get("reported_requirement") or {}
    text = str(reported.get("text") or "")
    if not text.strip():
        return None, "requirement_text_missing"
    real = case.get("real") or {}
    raw_standard = str(real.get("raw_value") or "").strip()
    if not raw_standard and real.get("numeric_value") is None:
        return None, "standard_fact_missing"

    property_name = str(project.get("project_name") or "")
    bundle = extract_requirement_claim(
        text,
        unit=reported.get("unit"),
        project_name=property_name,
    )
    if not bundle.get("program_ready"):
        # Production never lets a non-program-ready requirement reach the
        # programmatic comparison, so neither does this baseline.
        return None, str(bundle.get("reason_code") or "requirement_not_program_ready")
    report_claim = bundle.get("claim") or {}
    property_text = str((report_claim.get("property") or {}).get("source_text") or property_name)
    standard_text = raw_standard or str(real.get("numeric_value"))
    if real.get("unit"):
        standard_text = f"{standard_text} {real['unit']}"
    evidence_claim = parse_claim(
        f"{property_text or '限值'}: {standard_text}",
        evidence_role="nominal_rule",
    )
    relation = compare_claims(report_claim, evidence_claim)
    status = _status_for_table_comparison(relation)
    reason = str(relation.get("reason") or relation.get("relation") or "")
    return status, reason if status is None else "closed"


def _rows(cases: list[dict[str, Any]], caliber: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        legacy, legacy_reason = _legacy_status(case)
        derived = derive_case(case, caliber=caliber)
        rows.append(
            {
                "case_id": case.get("case_id"),
                "legacy_status": legacy,
                "legacy_closed": legacy is not None,
                "legacy_open_reason": None if legacy is not None else legacy_reason,
                "caliber_verdict": derived["verdict"],
                "caliber_status": derived["legacy_status"],
                "caliber_kind": derived["kind"],
                "caliber_closed": derived["verdict"] in _CALIBER_CLOSING,
                "caliber_open_reason": (
                    None
                    if derived["verdict"] in _CALIBER_CLOSING
                    else (
                        derived["underivable_reason"]
                        or derived["kind"]
                        or derived["derivation"]["comparison"].get("reason")
                    )
                ),
                "labelled_status": case.get("expected_status"),
            }
        )
    return rows


def _closure(rows: list[dict[str, Any]], layer: str) -> dict[str, Any]:
    closed = [row for row in rows if row[f"{layer}_closed"]]
    return {
        "closed": len(closed),
        "open": len(rows) - len(closed),
        "closure_rate": round(len(closed) / len(rows), 4) if rows else 0.0,
        "closed_statuses": dict(Counter(row[f"{layer}_status"] for row in closed)),
        "open_reasons": dict(
            Counter(row[f"{layer}_open_reason"] for row in rows if not row[f"{layer}_closed"]).most_common(8)
        ),
    }


def _agreement(rows: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    """Score both layers against one reference label, on what each one closes."""
    result: dict[str, Any] = {}
    for layer in ("legacy", "caliber"):
        scored = [
            row
            for row in rows
            if row[f"{layer}_closed"] and row[reference] is not None
        ]
        hits = sum(1 for row in scored if row[f"{layer}_status"] == row[reference])
        result[layer] = {
            "scored": len(scored),
            "agree": hits,
            "agreement_rate": round(hits / len(scored), 4) if scored else None,
            "confusion": dict(
                Counter(
                    f"{row[reference]}->{row[f'{layer}_status']}"
                    for row in scored
                    if row[f"{layer}_status"] != row[reference]
                ).most_common(10)
            ),
        }
    return result


def _defect_contract(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    caliber: dict[str, Any],
) -> dict[str, Any]:
    """Score both layers against the constructed defect contract, the one true gold."""
    crosswalk = caliber.get("crosswalk", {}).get("defect_edit_kind", {})
    legacy_map = caliber.get("crosswalk", {}).get("verdict_to_legacy_status", {})
    by_id = {row["case_id"]: row for row in rows}
    detail: list[dict[str, Any]] = []
    for edit in manifest.get("edits", []):
        row = by_id.get(edit.get("edit_id"))
        if row is None:
            continue
        contract = crosswalk.get(str(edit.get("kind")), {})
        expected = legacy_map.get(str(contract.get("verdict")))
        detail.append(
            {
                "edit_id": edit.get("edit_id"),
                "edit_kind": edit.get("kind"),
                "expected_status": expected,
                "legacy_status": row["legacy_status"],
                "legacy_correct": row["legacy_status"] == expected,
                "caliber_status": row["caliber_status"],
                "caliber_correct": row["caliber_status"] == expected,
            }
        )
    return {
        "total": len(detail),
        "legacy_closed": sum(1 for row in detail if row["legacy_status"] is not None),
        "legacy_correct": sum(1 for row in detail if row["legacy_correct"]),
        "caliber_closed": sum(1 for row in detail if row["caliber_status"] is not None),
        "caliber_correct": sum(1 for row in detail if row["caliber_correct"]),
        "rows": detail,
    }


def _model_calls_avoided(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Every closed case is one judge call, and one possible rejudge, not made."""
    legacy = sum(1 for row in rows if row["legacy_closed"])
    caliber = sum(1 for row in rows if row["caliber_closed"])
    return {
        "cases": len(rows),
        "judge_calls_legacy": len(rows) - legacy,
        "judge_calls_caliber": len(rows) - caliber,
        "reduction": legacy and round((caliber - legacy) / (len(rows) - legacy), 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--caliber", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    caliber = load_caliber(str(args.caliber) if args.caliber else None)
    gold = _read_json(args.gold)
    manifest = _read_json(args.manifest) if args.manifest.exists() else {"edits": []}

    report_rows = _rows(gold.get("cases", []), caliber)
    defect_rows = _rows(gold.get("synthetic_cases", []), caliber)

    payload = {
        "caliber_version": caliber.get("caliber_version"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "legacy = compare_claims + _status_for_table_comparison, the layer that closes "
            "a case programmatically today. caliber = audit_caliber.derive. Both read the "
            "same reported requirement and the same structured standard fact."
        ),
        "report_cases": {
            "total": len(report_rows),
            "legacy": _closure(report_rows, "legacy"),
            "caliber": _closure(report_rows, "caliber"),
            "vs_labels": _agreement(report_rows, "labelled_status"),
            "model_calls": _model_calls_avoided(report_rows),
        },
        "defect_cases": {
            "total": len(defect_rows),
            "legacy": _closure(defect_rows, "legacy"),
            "caliber": _closure(defect_rows, "caliber"),
            "contract": _defect_contract(manifest, defect_rows, caliber),
        },
        "rows": {"report_cases": report_rows, "defect_cases": defect_rows},
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for section in ("report_cases", "defect_cases"):
        block = {key: value for key, value in payload[section].items() if key != "rows"}
        if "contract" in block:
            block["contract"] = {
                key: value for key, value in block["contract"].items() if key != "rows"
            }
        print(f"\n== {section}")
        print(json.dumps(block, ensure_ascii=False, indent=2))
    print(f"\nwrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
