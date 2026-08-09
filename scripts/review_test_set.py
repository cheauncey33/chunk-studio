"""Record a qualified human decision for one test-set case."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "test_set.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", nargs="?")
    parser.add_argument("--decision", choices=("approve", "reject"), default="approve")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--reviewer", default="project_owner")
    parser.add_argument("--note", default="Project-owner review confirmed.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    payload: dict[str, Any] = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not args.approve_all and not args.case_id:
        raise SystemExit("provide case_id or --approve-all")
    cases = payload["cases"] if args.approve_all else [
        item for item in payload["cases"] if item["case_id"] == args.case_id
    ]
    if not cases:
        raise SystemExit(f"unknown case_id: {args.case_id}")
    for case in cases:
        review = case["review"]
        review["status"] = "human_approved" if args.decision == "approve" else "rejected"
        review["reviewer"] = args.reviewer
        review["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        review["decision_note"] = args.note
        review["flags"] = [flag for flag in review["flags"] if flag != "model_derived_not_human_approved"]
    if args.approve_all and args.decision == "approve":
        payload["status"] = "human_reviewed"
    payload["summary"]["pending_domain_review_cases"] = sum(
        item["review"]["status"] == "pending_domain_review" for item in payload["cases"]
    )
    payload["summary"]["scoreable_case_count"] = sum(
        item["review"]["status"] == "human_approved"
        and item["answerability_status"] == "answerable_candidate"
        for item in payload["cases"]
    )
    args.dataset.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"reviewed {len(cases)} case(s): {args.decision}")


if __name__ == "__main__":
    main()
