"""Extract report test items and reported requirements with an API model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import db, llm  # noqa: E402


DEFAULT_PROMPT = ROOT / "evaluation" / "prompts" / "report_test_item_extraction_v1.md"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "report_test_items_v1.json"
DEFAULT_MODEL = llm.DEFAULT_MODEL
PHASES = {"initial", "repeat_routine"}


def _parse_json_object(content: Any) -> dict[str, Any]:
    return llm.parse_json_object(content)


def _report_id(path: Path) -> str:
    name = path.stem
    for marker in ("EZC-", "WHC-", "XYC-", "HBJC-"):
        if marker in name:
            return marker.rstrip("-")
    return name


def _coerce_requirement(value: Any) -> dict[str, Any] | None:
    """Normalize one requirement row; drop blank / non-object rows."""
    if isinstance(value, str):
        text = value.strip()
        return {"requirement_text": text} if text else None
    if not isinstance(value, dict):
        return None
    text = str(
        value.get("requirement_text")
        or value.get("requirement")
        or value.get("text")
        or ""
    ).strip()
    if not text:
        return None
    cleaned = dict(value)
    cleaned["requirement_text"] = text
    cleaned.pop("requirement", None)
    cleaned.pop("text", None)
    # Models sometimes leak measured values into requirement rows; strip so a
    # single polluted row does not abort the whole extraction.
    cleaned.pop("measured_result", None)
    cleaned.pop("result", None)
    return cleaned


def _normalize_report(result: dict[str, Any]) -> dict[str, Any]:
    """Drop empty / blank requirement rows and items that have none left.

    Model output occasionally includes a project shell without requirements;
    failing the whole extraction for one bad row blocks the audit. Keep valid
    items so the run can continue.
    """
    items = result.get("items")
    if not isinstance(items, list):
        return result
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_requirements = item.get("requirements")
        if raw_requirements is None and isinstance(item.get("requirement"), (list, str)):
            raw_requirements = item.get("requirement")
        if isinstance(raw_requirements, str):
            raw_requirements = [raw_requirements]
        if not isinstance(raw_requirements, list):
            continue
        requirements: list[dict[str, Any]] = []
        for raw in raw_requirements:
            coerced = _coerce_requirement(raw)
            if coerced is not None:
                requirements.append(coerced)
        if not requirements:
            continue
        cleaned = dict(item)
        cleaned["requirements"] = requirements
        cleaned.pop("requirement", None)
        normalized_items.append(cleaned)
    result["items"] = normalized_items
    return result


def _validate_report(result: dict[str, Any], expected_report_id: str) -> None:
    if result.get("report_id") != expected_report_id:
        raise ValueError(f"response report_id must be {expected_report_id!r}")
    items = result.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("response contains no test items")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"item[{index}] must be an object")
        if not str(item.get("project_name") or "").strip():
            raise ValueError(f"item[{index}] has no project_name")
        if item.get("phase") not in PHASES:
            raise ValueError(f"item[{index}] has invalid phase")
        requirements = item.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"item[{index}] has no requirements")
        for req_index, requirement in enumerate(requirements):
            if not str(requirement.get("requirement_text") or "").strip():
                raise ValueError(f"item[{index}].requirements[{req_index}] is blank")
            if "measured_result" in requirement or "result" in requirement:
                raise ValueError(f"item[{index}].requirements[{req_index}] contains a result field")


def extract_report(path: Path, *, prompt: str, model: str) -> dict[str, Any]:
    report_id = _report_id(path)
    payload = json.dumps(
        {
            "report_id": report_id,
            "report_markdown": path.read_text(encoding="utf-8"),
        },
        ensure_ascii=False,
    )
    result = llm.chat_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": payload},
        ],
        model=model,
        temperature=0,
    )
    if not isinstance(result, dict):
        raise ValueError("response must be a JSON object")
    _normalize_report(result)
    _validate_report(result, report_id)
    result["source_file"] = path.name
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    prompt = args.prompt.read_text(encoding="utf-8")
    db.init_db()
    reports = [extract_report(path, prompt=prompt, model=args.model) for path in args.reports]
    output = {
        "version": 1,
        "model": args.model,
        "prompt": str(args.prompt),
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([
        {
            "report_id": report["report_id"],
            "items": len(report["items"]),
            "requirements": sum(len(item["requirements"]) for item in report["items"]),
            "phases": {
                phase: sum(item["phase"] == phase for item in report["items"])
                for phase in sorted(PHASES)
            },
        }
        for report in reports
    ], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
