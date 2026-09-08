"""Re-derive gold verdicts from the active caliber and diff them against labels.

The caliber change policy forbids per-case relabeling: gold must be recomputed
from ``evaluation/caliber/caliber_vN.json`` and the resulting diff reviewed
before a new caliber version becomes active.  This script is that recomputation
step.  It never writes gold; it writes a diff report for human confirmation.

    $env:PYTHONPATH='backend'; uv run python scripts/derive_caliber_gold.py
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


DEFAULT_GOLD = ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "gold.json"
DEFAULT_MANIFEST = ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "manifest.json"
DEFAULT_OUT = (
    ROOT / "data" / "evaluation_private" / "transformer_reports_v2" / "caliber_v0_to_v1_diff.json"
)
_SOURCE_GATES = ("source_present", "source_hash_matches", "extraction_verified")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_verified(case: dict[str, Any]) -> bool:
    block = case.get("source_report") or {}
    return all(bool(block.get(gate)) for gate in _SOURCE_GATES)


def _comparison_digest(derived: dict[str, Any]) -> dict[str, Any]:
    comparison = derived["derivation"]["comparison"]
    keys = (
        "claim_shape",
        "evidence_state",
        "unit_state",
        "comparator_state",
        "relation",
        "tightness",
        "interval_relation",
        "numeric_delta_class",
        "property_family",
        "left_normalized",
        "right_normalized",
        "left_raw",
        "right_raw",
        "reason",
    )
    return {key: comparison.get(key) for key in keys if comparison.get(key) is not None}


def _evaluate(cases: list[dict[str, Any]], caliber: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        derived = derive_case(case, caliber=caliber)
        before = case.get("expected_status")
        after = derived["legacy_status"]
        records.append(
            {
                "case_id": case.get("case_id"),
                "domain": case.get("domain"),
                "before_status": before,
                "after_status": after,
                "verdict": derived["verdict"],
                "kind": derived["kind"],
                "flags": derived["flags"],
                "rules": derived["derivation"]["rules"],
                "kind_confidence": derived["derivation"]["kind_confidence"],
                "notes": derived["derivation"]["notes"],
                "underivable_reason": derived["underivable_reason"],
                "derived_scoreable": derived["scoreable"],
                "scoreable_reason": derived["derivation"]["scoreable_reason"],
                "labelled_scoreable": bool(case.get("report_judgment_scoreable")),
                "source_verified": _source_verified(case),
                "comparison": _comparison_digest(derived),
                "requirement_text": (
                    ((case.get("detection_project") or {}).get("reported_requirement") or {})
                    .get("text")
                ),
                "changed": after is not None and after != before,
            }
        )
    return {"records": records}


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    changed = [record for record in records if record["changed"]]
    underivable = [record for record in records if record["verdict"] is None]
    rules = Counter(rule for record in records for rule in record["rules"])
    return {
        "total": len(records),
        "derived": len(records) - len(underivable),
        "unchanged": len(records) - len(changed) - len(underivable),
        "changed": len(changed),
        "underivable": len(underivable),
        "before_status": dict(Counter(record["before_status"] for record in records)),
        "after_status": dict(Counter(record["after_status"] for record in records)),
        "transitions": {
            f"{before}->{after}": count
            for (before, after), count in Counter(
                (record["before_status"], record["after_status"]) for record in changed
            ).most_common()
        },
        "verdicts": dict(Counter(record["verdict"] for record in records)),
        "kinds": dict(Counter(record["kind"] for record in records)),
        "flags": dict(Counter(flag for record in records for flag in record["flags"])),
        "rules": dict(rules.most_common()),
        "kind_confidence": dict(Counter(record["kind_confidence"] for record in records)),
        "underivable_reasons": dict(
            Counter(record["underivable_reason"] for record in underivable)
        ),
        "scoreable": {
            "derived_true": sum(1 for record in records if record["derived_scoreable"]),
            "labelled_true": sum(1 for record in records if record["labelled_scoreable"]),
            "reasons": dict(Counter(record["scoreable_reason"] for record in records)),
            "source_verified": sum(1 for record in records if record["source_verified"]),
        },
    }


def _defect_crosswalk(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    caliber: dict[str, Any],
) -> dict[str, Any]:
    expected = caliber.get("crosswalk", {}).get("defect_edit_kind", {})
    by_id = {record["case_id"]: record for record in records}
    rows: list[dict[str, Any]] = []
    for edit in manifest.get("edits", []):
        record = by_id.get(edit.get("edit_id"))
        if record is None:
            rows.append({"edit_id": edit.get("edit_id"), "state": "missing_from_gold"})
            continue
        contract = expected.get(str(edit.get("kind")), {})
        allowed_kinds = [kind for kind in str(contract.get("kind") or "").split("|") if kind]
        rows.append(
            {
                "edit_id": edit.get("edit_id"),
                "edit_kind": edit.get("kind"),
                "contract_verdict": contract.get("verdict"),
                "contract_kind": contract.get("kind"),
                "before_status": record["before_status"],
                "after_status": record["after_status"],
                "verdict": record["verdict"],
                "kind": record["kind"],
                "flags": record["flags"],
                "verdict_agrees": record["verdict"] == contract.get("verdict"),
                "label_agrees": record["after_status"] == record["before_status"],
                "kind_agrees": record["kind"] in allowed_kinds if allowed_kinds else None,
                "flags_agree": set(contract.get("flags") or []) <= set(record["flags"]),
                "rules": record["rules"],
                "comparison": record["comparison"],
            }
        )
    return {
        "total": len(rows),
        "verdict_agrees": sum(1 for row in rows if row.get("verdict_agrees")),
        "label_agrees": sum(1 for row in rows if row.get("label_agrees")),
        "kind_agrees": sum(1 for row in rows if row.get("kind_agrees")),
        "flags_agree": sum(1 for row in rows if row.get("flags_agree")),
        "disagreeing": [
            row["edit_id"]
            for row in rows
            if not row.get("verdict_agrees") or row.get("kind_agrees") is False
        ],
        "rows": rows,
    }


def _print_block(title: str, payload: dict[str, Any]) -> None:
    print(f"\n== {title}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--caliber", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--changed-samples",
        type=int,
        default=40,
        help="how many changed cases to print per transition group",
    )
    parser.add_argument(
        "--fail-on-underivable",
        action="store_true",
        help="exit non-zero when any case yields no verdict",
    )
    args = parser.parse_args(argv)

    caliber = load_caliber(str(args.caliber) if args.caliber else None)
    gold = _read_json(args.gold)
    manifest = _read_json(args.manifest) if args.manifest.exists() else {"edits": []}

    report_records = _evaluate(gold.get("cases", []), caliber)["records"]
    synthetic_records = _evaluate(gold.get("synthetic_cases", []), caliber)["records"]

    diff = {
        "caliber_version": caliber.get("caliber_version"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "gold": str(args.gold.relative_to(ROOT)),
            "manifest": str(args.manifest.relative_to(ROOT)),
            "caliber": str((args.caliber or Path("evaluation/caliber/caliber_v1.json"))),
            "gold_schema_version": gold.get("schema_version"),
        },
        "note": (
            "Derived from the caliber rules only. Labels are not modified; this diff is the "
            "artifact a human confirms before the caliber version becomes active."
        ),
        "blocking_precondition": {
            "requirement": " && ".join(f"source_report.{gate}" for gate in _SOURCE_GATES),
            "report_cases_satisfying": sum(
                1 for record in report_records if record["source_verified"]
            ),
            "consequence": (
                "no case may contribute to a score until the source reports are re-archived "
                "and verified against transformer_reports_v1/reports.json sha256"
            ),
        },
        "report_cases": {
            "summary": _summarize(report_records),
            "changed": [record for record in report_records if record["changed"]],
            "underivable": [record for record in report_records if record["verdict"] is None],
        },
        "synthetic_cases": {
            "summary": _summarize(synthetic_records),
            "changed": [record for record in synthetic_records if record["changed"]],
            "defect_crosswalk": _defect_crosswalk(manifest, synthetic_records, caliber),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_block("report cases (611)", diff["report_cases"]["summary"])
    _print_block("synthetic defect cases (19)", diff["synthetic_cases"]["summary"])
    _print_block(
        "defect crosswalk",
        {
            key: value
            for key, value in diff["synthetic_cases"]["defect_crosswalk"].items()
            if key != "rows"
        },
    )
    _print_block("blocking precondition", diff["blocking_precondition"])

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in diff["report_cases"]["changed"]:
        key = f"{record['before_status']}->{record['after_status']}"
        grouped.setdefault(key, []).append(record)
    for key, group in sorted(grouped.items(), key=lambda item: -len(item[1])):
        print(f"\n-- {key} ({len(group)} cases)")
        for record in group[: args.changed_samples]:
            print(
                f"   {record['case_id']}  kind={record['kind']}  rules={record['rules']}  "
                f"flags={record['flags']}\n"
                f"     req: {record['requirement_text']}\n"
                f"     cmp: {json.dumps(record['comparison'], ensure_ascii=False)}"
            )

    print(f"\nwrote {args.out.relative_to(ROOT)}")
    if args.fail_on_underivable and diff["report_cases"]["underivable"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
