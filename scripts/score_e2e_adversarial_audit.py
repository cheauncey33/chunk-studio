"""Score an end-to-end audit report against the HBJC adversarial manifest."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evaluation" / "e2e_hbjc_adversarial" / "manifest.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def case_status(case: dict[str, Any]) -> str:
    judgment = case.get("judgment") or {}
    return str(judgment.get("status") or "").strip()


def case_requirement(case: dict[str, Any]) -> str:
    req = case.get("reported_requirement") or {}
    return str(req.get("text") or "").strip()


def case_phase(case: dict[str, Any]) -> str:
    item = case.get("test_item") or {}
    return str(item.get("phase") or "").strip()


def pick_case(cases: list[dict[str, Any]], match_contains: str) -> dict[str, Any] | None:
    matched = [
        case
        for case in cases
        if match_contains in case_requirement(case)
    ]
    if not matched:
        return None
    initial = [case for case in matched if case_phase(case) == "initial"]
    return (initial or matched)[0]


def rate(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row["correct"]) / len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_report", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = read_json(args.manifest)
    audit = read_json(args.audit_report)
    cases = list(audit.get("cases") or [])

    rows: list[dict[str, Any]] = []
    for edit in manifest.get("edits") or []:
        case = pick_case(cases, edit["match_contains"])
        actual = case_status(case) if case else "missing_case"
        expected = edit["expected_status"]
        rows.append(
            {
                "edit_id": edit["edit_id"],
                "kind": edit["kind"],
                "expected_status": expected,
                "actual_status": actual,
                "correct": actual == expected,
                "requirement": case_requirement(case) if case else "",
                "project_name": ((case or {}).get("test_item") or {}).get("project_name"),
                "phase": case_phase(case) if case else "",
                "note": edit.get("note"),
            }
        )

    mismatch_expected = [row for row in rows if row["expected_status"] == "mismatch"]
    tracked_pred_mismatch = [row for row in rows if row["actual_status"] == "mismatch"]
    equivalence = [row for row in rows if row["kind"] == "unit_equivalence"]
    positives = [row for row in rows if row["kind"] == "positive_control"]
    tighter = [row for row in rows if row["kind"] == "numeric_tighter"]

    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    accuracy_by_kind = {
        kind: {
            "n": len(kind_rows),
            "correct": sum(1 for row in kind_rows if row["correct"]),
            "accuracy": rate(kind_rows),
        }
        for kind, kind_rows in sorted(by_kind.items())
    }

    summary = {
        "audit_report": str(args.audit_report),
        "manifest": str(args.manifest),
        "n_edits": len(rows),
        "n_matched_cases": sum(1 for row in rows if row["actual_status"] != "missing_case"),
        "attack_mismatch_recall": rate(
            [
                {**row, "correct": row["actual_status"] == "mismatch"}
                for row in mismatch_expected
            ]
        ),
        "attack_mismatch_precision_on_tracked": (
            sum(1 for row in tracked_pred_mismatch if row["expected_status"] == "mismatch")
            / len(tracked_pred_mismatch)
            if tracked_pred_mismatch
            else None
        ),
        "equivalence_accuracy": rate(equivalence),
        "positive_control_accuracy": rate(positives),
        "tighter_accuracy": rate(tighter),
        "overall_edit_accuracy": rate(rows),
        "accuracy_by_kind": accuracy_by_kind,
        "failed_edits": [row["edit_id"] for row in rows if not row["correct"]],
        "rows": rows,
    }

    text_lines = [
        "# HBJC adversarial end-to-end score",
        "",
        f"- n_edits: {summary['n_edits']}",
        f"- attack_mismatch_recall: {summary['attack_mismatch_recall']}",
        f"- attack_mismatch_precision_on_tracked: {summary['attack_mismatch_precision_on_tracked']}",
        f"- equivalence_accuracy: {summary['equivalence_accuracy']}",
        f"- positive_control_accuracy: {summary['positive_control_accuracy']}",
        f"- tighter_accuracy: {summary['tighter_accuracy']}",
        f"- overall_edit_accuracy: {summary['overall_edit_accuracy']}",
        f"- failed_edits: {summary['failed_edits']}",
        "",
        "## By kind",
        "",
        "| kind | n | correct | accuracy |",
        "|---|---:|---:|---:|",
    ]
    for kind, stats in accuracy_by_kind.items():
        text_lines.append(
            f"| {kind} | {stats['n']} | {stats['correct']} | {stats['accuracy']} |"
        )
    text_lines.extend(
        [
            "",
            "| edit_id | kind | expected | actual | ok | requirement |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        text_lines.append(
            f"| {row['edit_id']} | {row['kind']} | {row['expected_status']} | "
            f"{row['actual_status']} | {'Y' if row['correct'] else 'N'} | "
            f"{row['requirement'][:50]} |"
        )
    text_lines.append("")
    print("\n".join(text_lines))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
