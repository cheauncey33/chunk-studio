from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import config, lexical


router = APIRouter(prefix="/audit", tags=["audit"])

REPORTS_DIR = config.DATA_DIR / "reports"
MANUAL_RULES_PATH = config.PROJECT_ROOT / "evaluation" / "manual_knowledge_rules_v1.json"


def _safe_report_path(name: str) -> Path:
    if not name or Path(name).name != name or "\\" in name or "/" in name:
        raise HTTPException(status_code=400, detail="Invalid report name")
    path = (REPORTS_DIR / name).resolve()
    reports_dir = REPORTS_DIR.resolve()
    if path.parent != reports_dir or path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Invalid report name")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return path


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Report is not valid JSON: line {exc.lineno}, column {exc.colno}",
        ) from exc


def _report_kind(name: str) -> str:
    if name.startswith("hbjc_end_to_end_audit"):
        return "end_to_end_audit"
    if name.startswith("hbjc_retrieval_group_eval"):
        return "retrieval_group_eval"
    if "retrieval" in name:
        return "retrieval"
    return "report"


def _report_list_item(path: Path) -> dict[str, Any]:
    stat = path.stat()
    item: dict[str, Any] = {
        "name": path.name,
        "kind": _report_kind(path.name),
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
        "summary": None,
        "case_count": None,
        "parse_error": None,
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        item["parse_error"] = f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        return item

    if isinstance(payload, dict):
        summary = payload.get("summary")
        item["summary"] = summary if isinstance(summary, dict) else None
        cases = payload.get("cases")
        item["case_count"] = len(cases) if isinstance(cases, list) else None
        item["version"] = payload.get("version")
        item["scope"] = payload.get("scope")
        item["retrieval_policy"] = payload.get("retrieval_policy")
    return item


@router.get("/reports")
def list_audit_reports() -> dict[str, Any]:
    if not REPORTS_DIR.exists():
        return {"reports": []}
    reports = [
        _report_list_item(path)
        for path in REPORTS_DIR.glob("*.json")
        if path.is_file() and not path.name.endswith(".checkpoint.json")
    ]
    reports.sort(key=lambda item: item["modified_at"], reverse=True)
    return {"reports": reports}


@router.get("/reports/{name}")
def get_audit_report(name: str) -> dict[str, Any]:
    path = _safe_report_path(name)
    stat = path.stat()
    return {
        "name": path.name,
        "kind": _report_kind(path.name),
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
        "payload": _load_json(path),
    }


@router.get("/manual-rules")
def get_manual_rules() -> dict[str, Any]:
    if not MANUAL_RULES_PATH.exists():
        raise HTTPException(status_code=404, detail="Manual rules file not found")
    return _load_json(MANUAL_RULES_PATH)


@router.get("/lexical-index")
def get_lexical_index_status() -> dict[str, Any]:
    return lexical.index_status()


@router.get("/shadow-runs")
def list_shadow_runs(limit: int = 25) -> dict[str, Any]:
    return {"runs": lexical.list_shadow_runs(limit=limit)}


@router.get("/shadow-runs/{run_id}")
def get_shadow_run(run_id: str) -> dict[str, Any]:
    run = lexical.get_shadow_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Shadow run not found")
    return run
