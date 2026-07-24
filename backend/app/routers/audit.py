from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, db, lexical, llm


router = APIRouter(prefix="/audit", tags=["audit"])

JUDGE_STATUSES = ("supported", "mismatch", "insufficient_context", "not_audited")

REPORTS_DIR = config.DATA_DIR / "reports"
PROMPT_PATHS = {
    "report_parameters": config.PROJECT_ROOT / "evaluation" / "prompts" / "report_parameter_extraction_v1.md",
    "test_items": config.PROJECT_ROOT / "evaluation" / "prompts" / "report_test_item_extraction_v1.md",
    "model_decode": config.PROJECT_ROOT / "evaluation" / "prompts" / "model_naming_decode_v1.md",
    "query_planner": config.PROJECT_ROOT / "evaluation" / "prompts" / "retrieval_query_planner_v1.md",
    "audit_judge": config.PROJECT_ROOT / "evaluation" / "prompts" / "standard_value_audit_judge_v1.md",
}

WORKFLOW_NODE_SPECS = (
    ("report_parameters", "报告参数提取", "llm", False),
    ("test_items", "检测项目提取", "llm", False),
    ("model_decode", "型号规则解析", "llm", False),
    ("query_planner", "检索 Query 规划", "llm", False),
    ("retrieval", "候选证据检索", "retrieval", False),
    ("audit_judge", "标准值审查", "llm", False),
    ("gold_comparison", "Gold 对照", "diagnostic", True),
)


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
    if name.startswith(("hbjc_end_to_end_audit", "end_to_end_audit")):
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


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _find_case(payload: dict[str, Any], case_id: str) -> dict[str, Any]:
    for item in payload.get("cases") or []:
        if isinstance(item, dict) and str(item.get("case_id") or item.get("id")) == case_id:
            return item
    raise HTTPException(status_code=404, detail="Case not found")


def _prompt_payload(
    workflow_definition: dict[str, Any],
    prompt_key: str,
) -> dict[str, Any] | None:
    recorded = _record(_record(workflow_definition.get("prompts")).get(prompt_key))
    if recorded.get("content"):
        return {**recorded, "source": "recorded_report"}
    path = PROMPT_PATHS.get(prompt_key)
    if path is None or not path.exists():
        return None
    return {
        "path": str(path.relative_to(config.PROJECT_ROOT)).replace("\\", "/"),
        "content": path.read_text(encoding="utf-8"),
        "source": "current_repository",
    }


def _legacy_node_io(
    node_id: str,
    payload: dict[str, Any],
    case: dict[str, Any],
) -> tuple[Any, Any, str]:
    judgment = _record(case.get("judgment"))
    runtime_case = {
        "sample_context": case.get("sample_context") or payload.get("parameters"),
        "test_item": case.get("test_item"),
        "reported_requirement": case.get("reported_requirement"),
    }
    if node_id == "report_parameters":
        return (
            {"report": payload.get("report"), "report_markdown": "旧报告未记录原始 Markdown"},
            payload.get("parameters"),
            "由旧报告字段还原；原始 Markdown 未写入该报告。",
        )
    if node_id == "test_items":
        return (
            {"report": payload.get("report"), "report_markdown": "旧报告未记录原始 Markdown"},
            {
                "extraction_summary": payload.get("extraction_summary"),
                "selected_test_item": case.get("test_item"),
                "selected_requirement": case.get("reported_requirement"),
            },
            "旧报告只保留了提取摘要和当前 case，不包含完整项目提取输出。",
        )
    if node_id == "model_decode":
        return (
            {
                "report_parameters": payload.get("parameters"),
                "naming_rule": payload.get("naming_rule"),
            },
            payload.get("model_decode"),
            "旧报告未记录型号规则 Markdown 原文。",
        )
    if node_id == "query_planner":
        return (
            {**runtime_case, "decoded_model": payload.get("model_decode")},
            case.get("queries"),
            "输入由报告字段重建；输出是报告记录的最终查询集合。",
        )
    if node_id == "retrieval":
        return (
            {"queries": case.get("queries")},
            {
                "candidate_counts": case.get("candidate_counts"),
                "selected_evidence_only": judgment.get("evidence"),
            },
            "旧报告只保留 Judge 采用的证据，没有保留完整候选池。",
        )
    if node_id == "audit_judge":
        return (
            {
                **runtime_case,
                "decoded_model": payload.get("model_decode"),
                "peer_report_context": case.get("peer_report_context"),
                "manual_knowledge_rules": case.get("manual_knowledge_rules"),
                "candidates": judgment.get("evidence"),
            },
            judgment,
            "旧报告只可还原被采用的候选，不能还原 Judge 当时看到的完整候选池。",
        )
    return (
        {
            "case_id": case.get("case_id"),
            "gold_source": "evaluation/retrieval_gold_candidates_v1.json",
        },
        {
            "direct_gold_available": case.get("direct_gold_available"),
            "direct_gold_recalled": case.get("direct_gold_recalled"),
        },
        "Gold 只用于离线对照，不会输入 Query Planner 或 Judge。",
    )


def _build_workflow_trace(
    payload: dict[str, Any],
    case: dict[str, Any],
    *,
    current_llm_config: dict[str, Any],
) -> dict[str, Any]:
    workflow_definition = _record(payload.get("workflow_definition"))
    global_trace = _record(workflow_definition.get("global_trace"))
    case_trace = _record(case.get("workflow_trace"))
    recorded = bool(global_trace or case_trace)
    provider_config = _record(workflow_definition.get("provider_config")) or current_llm_config
    retrieval_config = _record(workflow_definition.get("retrieval_config")) or {
        "content_types": ["table", "section"],
        "route_top_k": 20,
        "final_per_type": 20,
        "rrf_k": 60,
        "config_source": "current end-to-end workflow defaults",
    }

    nodes = []
    for node_id, label, kind, diagnostic_only in WORKFLOW_NODE_SPECS:
        trace = _record(global_trace.get(node_id)) or _record(case_trace.get(node_id))
        # Full-report runs record traces but have no gold comparison; omit the
        # diagnostic node instead of showing a reconstructed placeholder.
        if node_id == "gold_comparison" and recorded and not trace:
            continue
        if trace:
            node_input = trace.get("input")
            node_output = trace.get("output")
            note = "输入输出由报告运行时 trace 记录。"
        else:
            node_input, node_output, note = _legacy_node_io(node_id, payload, case)
        prompt = _prompt_payload(workflow_definition, node_id)
        configuration = (
            provider_config
            if kind == "llm"
            else retrieval_config
            if kind == "retrieval"
            else {
                "mode": "deterministic_post_run_comparison",
                "model_input": False,
            }
        )
        nodes.append({
            "id": node_id,
            "label": label,
            "kind": kind,
            "diagnostic_only": diagnostic_only,
            "configuration": configuration,
            "prompt": prompt,
            "input": node_input,
            "output": node_output,
            "note": note,
        })

    warnings = []
    if not recorded:
        warnings.append(
            "这是旧报告的兼容视图：部分输入根据报告字段还原，未记录内容会明确标注。"
        )
    if any(
        _record(node.get("prompt")).get("source") == "current_repository"
        for node in nodes
    ):
        warnings.append("提示词来自当前仓库；旧报告没有保存运行时提示词快照。")
    return {
        "version": 1,
        "case_id": str(case.get("case_id") or case.get("id") or ""),
        "trace_source": "recorded" if recorded else "reconstructed",
        "warnings": warnings,
        "nodes": nodes,
    }


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
        "reviews": _case_reviews(path.name),
    }


class CaseReviewRequest(BaseModel):
    status: Literal["confirmed", "corrected"]
    corrected_status: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="", max_length=128)


def _case_reviews(report_name: str) -> dict[str, dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM audit_case_reviews WHERE report_name=?",
        (report_name,),
    ).fetchall()
    return {row["case_id"]: dict(row) for row in rows}


@router.get("/reports/{name}/reviews")
def list_case_reviews(name: str) -> dict[str, Any]:
    path = _safe_report_path(name)
    return {"report_name": path.name, "reviews": _case_reviews(path.name)}


@router.put("/reports/{name}/reviews/{case_id}")
def upsert_case_review(name: str, case_id: str, body: CaseReviewRequest) -> dict[str, Any]:
    path = _safe_report_path(name)
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Report payload must be an object")
    _find_case(payload, case_id)

    corrected_status = body.corrected_status.strip()
    if body.status == "corrected":
        if corrected_status not in JUDGE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"corrected_status must be one of {', '.join(JUDGE_STATUSES)}",
            )
    else:
        corrected_status = ""

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO audit_case_reviews
               (report_name, case_id, status, corrected_status, note, reviewer,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(report_name, case_id) DO UPDATE SET
                 status=excluded.status,
                 corrected_status=excluded.corrected_status,
                 note=excluded.note,
                 reviewer=excluded.reviewer,
                 updated_at=excluded.updated_at""",
            (
                path.name, case_id, body.status, corrected_status,
                body.note.strip(), body.reviewer.strip(), now, now,
            ),
        )
    row = db.get_conn().execute(
        "SELECT * FROM audit_case_reviews WHERE report_name=? AND case_id=?",
        (path.name, case_id),
    ).fetchone()
    return dict(row)


@router.delete("/reports/{name}/reviews/{case_id}")
def delete_case_review(name: str, case_id: str) -> dict[str, Any]:
    path = _safe_report_path(name)
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM audit_case_reviews WHERE report_name=? AND case_id=?",
            (path.name, case_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"ok": True}


@router.get("/reports/{name}/workflow/{case_id}")
def get_audit_workflow(name: str, case_id: str) -> dict[str, Any]:
    payload = _load_json(_safe_report_path(name))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Report payload must be an object")
    case = _find_case(payload, case_id)
    return _build_workflow_trace(
        payload,
        case,
        current_llm_config=llm.public_config(model=str(payload.get("judge_model") or "") or None),
    )


@router.get("/manual-rules")
def get_manual_rules(assistant_id: str = "assistant_oil_transformer_audit") -> dict[str, Any]:
    """Runtime manual rules resolved from the assistant's bound knowledge bases.

    Reads the same DB source the audit workflow uses, so this view can no
    longer drift from what the judge actually receives. The repository file
    ``evaluation/manual_knowledge_rules_v1.json`` remains a versioned seed only.
    """
    row = db.get_conn().execute(
        "SELECT id FROM audit_assistants WHERE id=?", (assistant_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Assistant not found")
    payload = db.resolve_assistant_manual_rules(assistant_id)
    return {**payload, "source": "knowledge_bases.manual_rules", "assistant_id": assistant_id}


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
