"""Audit jobs resume from a fixed run_id and skip retry on business errors."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import audit_run, db, jobs
from app.job_errors import (
    AUDIT_EXIT_NON_RETRYABLE,
    AUDIT_EXIT_RETRYABLE,
    RetryableJobError,
)
import run_report_audit_workflow as workflow


def _init_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _insert_running_job(
    *,
    job_id: str,
    attempts: int = 1,
    max_attempts: int = 3,
    result: dict | None = None,
) -> dict:
    payload = result or {
        "assistant_id": "assistant_oil_transformer_audit",
        "report_file_id": "report",
        "run_id": job_id,
        "report_name": f"end_to_end_audit_oil_transformer_audit_{job_id}.json",
        "report_path": f"reports/end_to_end_audit_oil_transformer_audit_{job_id}.json",
        "checkpoint_path": f"reports/end_to_end_audit_oil_transformer_audit_{job_id}.checkpoint.json",
    }
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at, started_at)
               VALUES (?, 'audit', 'assistant', 'assistant_oil_transformer_audit',
                       'running', 5, ?, ?, '', ?, ?, ?)""",
            (
                job_id,
                attempts,
                max_attempts,
                json.dumps(payload, ensure_ascii=False),
                jobs.now_iso(),
                jobs.now_iso(),
            ),
        )
    return jobs.get_job(job_id)


def test_fail_job_does_not_retry_business_errors(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    job = _insert_running_job(job_id="job_biz", attempts=1, max_attempts=3)
    jobs._fail_job(
        job,
        "报告检测依据中的标准未在当前知识库找到：GB/T 7595",
        retryable=False,
        error_code="business",
    )
    stored = jobs.get_job("job_biz")
    assert stored["status"] == "failed"
    assert stored["dead_letter"] in (1, True)
    assert stored["result"]["error_class"] == "non_retryable"
    assert stored["result"]["run_id"] == "job_biz"


def test_fail_job_requeues_retryable_errors(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    job = _insert_running_job(job_id="job_tmp", attempts=1, max_attempts=3)
    jobs._fail_job(job, "sidecar timeout", retryable=True, error_code="timeout")
    stored = jobs.get_job("job_tmp")
    assert stored["status"] == "queued"
    assert stored["dead_letter"] in (0, False)
    assert stored["result"]["error_class"] == "retryable"
    assert stored["available_at"]


def test_run_audit_job_reuses_stored_report_name_on_retry(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    job = _insert_running_job(job_id="job_resume", attempts=1, max_attempts=3)
    captured: list[str] = []

    def fake_run(**kwargs):
        captured.append(str(kwargs.get("report_name") or ""))
        raise RetryableJobError("sidecar 502", code="workflow_crash")

    monkeypatch.setattr(audit_run, "run_assistant_audit", fake_run)
    asyncio.run(jobs._run_audit_job(job))
    queued = jobs.get_job("job_resume")
    queued["status"] = "running"
    queued["attempts"] = 2
    asyncio.run(jobs._run_audit_job(queued))
    assert captured == [
        "end_to_end_audit_oil_transformer_audit_job_resume.json",
        "end_to_end_audit_oil_transformer_audit_job_resume.json",
    ]
    stored = jobs.get_job("job_resume")
    assert stored["result"]["attempt"] == 2


def test_job_progress_records_resume_counts(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    job = _insert_running_job(job_id="job_obs", attempts=2, max_attempts=3)
    workflow._report_job_progress(
        "job_obs",
        stage="audit_cases",
        case_done=17,
        case_total=24,
        attempt=2,
        resumed=True,
        resumed_case_count=17,
        message="从 17/24 项继续…",
    )
    stored = jobs.get_job("job_obs")
    assert stored["result"]["resumed"] is True
    assert stored["result"]["resumed_case_count"] == 17
    assert stored["result"]["attempt"] == 2
    assert stored["result"]["progress"]["resumed_case_count"] == 17
    assert stored["result"]["progress"]["attempt"] == 2


def test_run_audit_job_keeps_resume_fields_on_success(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    job = _insert_running_job(job_id="job_done", attempts=2, max_attempts=3)

    def fake_run(**_kwargs):
        jobs.merge_job_result(
            "job_done",
            {"resumed": True, "resumed_case_count": 3, "attempt": 2},
        )
        return {
            "report_name": job["result"]["report_name"],
            "summary": {"cases": 5},
        }

    monkeypatch.setattr(audit_run, "run_assistant_audit", fake_run)
    asyncio.run(jobs._run_audit_job(job))
    stored = jobs.get_job("job_done")
    assert stored["status"] == "done"
    assert stored["result"]["resumed"] is True
    assert stored["result"]["resumed_case_count"] == 3
    assert stored["result"]["attempt"] == 2
    assert stored["result"]["progress"]["resumed_case_count"] == 3


def test_run_assistant_audit_keeps_the_same_output_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audit_run.config, "DATA_DIR", tmp_path)
    script = tmp_path / "workflow.py"
    script.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(audit_run, "SCRIPT_PATH", script)
    monkeypatch.setattr(audit_run, "resolve_markdown_path", lambda *_a, **_k: tmp_path / "r.md")
    monkeypatch.setattr(audit_run, "resolve_naming_rule_path", lambda *_a, **_k: tmp_path / "n.md")
    monkeypatch.setattr(audit_run, "resolve_naming_rule_file_id", lambda *_a, **_k: None)
    monkeypatch.setattr("app.storage.repositories.get_content_repository", lambda: None)
    monkeypatch.setattr("app.storage.repositories.get_content_write_repository", lambda: None)

    outputs: list[str] = []

    def fake_subprocess_run(cmd, **_kwargs):
        output = Path(cmd[cmd.index("--output") + 1])
        outputs.append(str(output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"summary": {"cases": 0}}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(audit_run.subprocess, "run", fake_subprocess_run)
    first = audit_run.run_assistant_audit(
        assistant_id="assistant_oil_transformer_audit",
        report_file_id="report",
        job_id="job_stable",
        run_id="job_stable",
        report_name="end_to_end_audit_oil_transformer_audit_job_stable.json",
    )
    second = audit_run.run_assistant_audit(
        assistant_id="assistant_oil_transformer_audit",
        report_file_id="report",
        job_id="job_stable",
        run_id="job_stable",
        report_name="end_to_end_audit_oil_transformer_audit_job_stable.json",
    )
    assert first["report_name"] == second["report_name"]
    assert first["checkpoint_path"] == second["checkpoint_path"]
    assert outputs[0] == outputs[1]
    assert Path(outputs[0]).name == "end_to_end_audit_oil_transformer_audit_job_stable.json"
    assert Path(outputs[0]).with_suffix(".checkpoint.json").name.endswith(".checkpoint.json")


def test_workflow_maps_business_errors_to_non_retryable_exit(monkeypatch) -> None:
    def boom() -> None:
        raise workflow.NonRetryableJobError("报告检测依据中的标准未在当前知识库找到：GB/T 7595")

    monkeypatch.setattr(workflow, "main", boom)
    assert workflow._run_main() == AUDIT_EXIT_NON_RETRYABLE


def test_workflow_maps_valueerror_to_retryable_exit(monkeypatch) -> None:
    def boom() -> None:
        raise ValueError("naming decoder changed the raw model")

    monkeypatch.setattr(workflow, "main", boom)
    assert workflow._run_main() == AUDIT_EXIT_RETRYABLE


def test_workflow_maps_sidecar_crashes_to_retryable_exit(monkeypatch) -> None:
    def boom() -> None:
        raise RuntimeError("agent sidecar 5xx (502)")

    monkeypatch.setattr(workflow, "main", boom)
    assert workflow._run_main() == AUDIT_EXIT_RETRYABLE
