"""Night Batch Phase 2: report-level claim budget and audit worker pool."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import audit_batches, audit_claim, audit_run, config, db, jobs
from app.storage.repositories import PostgresJobRepository


ASSISTANT_ID = "assistant_oil_transformer_audit"
PAST_LOCAL = "2000-01-01T00:00:00+08:00"


def _init_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def _seed_reports(tmp_path: Path, report_ids: list[str], *, standard_id: str = "std") -> None:
    parse_path = tmp_path / "parses"
    parse_path.mkdir(parents=True, exist_ok=True)
    (parse_path / f"{standard_id}.md").write_text("# std", encoding="utf-8")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES (?, ?, ?, '{"doc_type":"standard"}', ?)""",
            (standard_id, f"{standard_id}.pdf", f"files/{standard_id}.pdf", jobs.now_iso()),
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
               VALUES ('kb_uncategorized',?,'source','standard',1,?)""",
            (standard_id, jobs.now_iso()),
        )
        conn.execute(
            """INSERT INTO document_parses
               (id,file_id,provider,status,markdown_path,result,error,created_at,updated_at)
               VALUES (?,?, 'mineru','done',?, '{}','', ?, ?)""",
            (
                f"p-{standard_id}",
                standard_id,
                f"parses/{standard_id}.md",
                jobs.now_iso(),
                jobs.now_iso(),
            ),
        )
        for report_id in report_ids:
            conn.execute(
                """INSERT INTO files(id,name,path,metadata,created_at)
                   VALUES (?, ?, ?, '{"doc_role":"report"}', ?)""",
                (report_id, f"{report_id}.pdf", f"files/{report_id}.pdf", jobs.now_iso()),
            )
            conn.execute(
                """INSERT INTO document_parses
                   (id,file_id,provider,status,markdown_path,result,error,created_at,updated_at)
                   VALUES (?,?, 'mineru','done',?, '{}','', ?, ?)""",
                (
                    f"p-{report_id}",
                    report_id,
                    f"parses/{report_id}.md",
                    jobs.now_iso(),
                    jobs.now_iso(),
                ),
            )
            (parse_path / f"{report_id}.md").write_text(f"# {report_id}", encoding="utf-8")


def _set_job_status(job_id: str, status: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def test_batch_claim_allowed_enforces_per_batch_and_global_caps(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUDIT_BATCH_GLOBAL_SLOTS", 3)
    assert audit_claim.batch_claim_allowed(
        is_batch=False, running_for_batch=99, batch_max=1, running_batch_global=99
    )
    assert audit_claim.batch_claim_allowed(
        is_batch=True, running_for_batch=2, batch_max=3, running_batch_global=2
    )
    assert not audit_claim.batch_claim_allowed(
        is_batch=True, running_for_batch=3, batch_max=3, running_batch_global=2
    )
    assert not audit_claim.batch_claim_allowed(
        is_batch=True, running_for_batch=0, batch_max=3, running_batch_global=3
    )


def test_batch_max_concurrency_is_enforced_at_claim(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUDIT_BATCH_GLOBAL_SLOTS", 3)
    reports = ["r1", "r2", "r3", "r4", "r5"]
    _seed_reports(tmp_path, reports)
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=reports,
        scheduled_at=PAST_LOCAL,
        max_concurrency=2,
    )
    first = jobs._claim_next_job({"audit"})
    second = jobs._claim_next_job({"audit"})
    assert first is not None and second is not None
    assert {first["id"], second["id"]} <= {job["id"] for job in created["jobs"]}
    assert jobs._claim_next_job({"audit"}) is None
    _set_job_status(first["id"], "done")
    third = jobs._claim_next_job({"audit"})
    assert third is not None
    assert third["id"] not in {first["id"], second["id"]}
    _close_temp_db(monkeypatch)


def test_global_night_batch_slots_cap_jobs_across_batches(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUDIT_BATCH_GLOBAL_SLOTS", 3)
    _seed_reports(tmp_path, ["a1", "a2", "a3", "b1", "b2"])
    audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["a1", "a2", "a3"],
        scheduled_at=PAST_LOCAL,
        max_concurrency=3,
    )
    audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["b1", "b2"],
        scheduled_at=PAST_LOCAL,
        max_concurrency=3,
    )
    claimed = [jobs._claim_next_job({"audit"}) for _ in range(3)]
    assert all(item is not None for item in claimed)
    assert jobs._claim_next_job({"audit"}) is None
    _close_temp_db(monkeypatch)


def test_interactive_audit_claims_the_reserved_slot(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUDIT_BATCH_GLOBAL_SLOTS", 3)
    _seed_reports(tmp_path, ["n1", "n2", "n3", "n4", "live"])
    audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["n1", "n2", "n3", "n4"],
        scheduled_at=PAST_LOCAL,
        max_concurrency=3,
    )
    for _ in range(3):
        assert jobs._claim_next_job({"audit"}) is not None
    assert jobs._claim_next_job({"audit"}) is None
    interactive = jobs.enqueue_assistant_audit(ASSISTANT_ID, report_file_id="live")
    claimed = jobs._claim_next_job({"audit"})
    assert claimed is not None
    assert claimed["id"] == interactive["id"]
    assert claimed["priority"] == 5
    _close_temp_db(monkeypatch)


def test_audit_worker_pool_runs_jobs_concurrently(monkeypatch) -> None:
    monkeypatch.setattr(jobs.config, "AUDIT_WORKER_CONCURRENCY", 3)
    monkeypatch.setattr(jobs, "JOB_POLL_SECONDS", 0.01)
    running = 0
    max_running = 0
    claimed = 0

    def fake_claim(_job_types):
        nonlocal claimed
        if claimed >= 3:
            return None
        claimed += 1
        return {
            "id": f"job-{claimed}",
            "type": "audit",
            "target_type": "assistant",
            "workspace_id": "workspace-1",
        }

    async def fake_dispatch(_job):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.05)
        running -= 1

    monkeypatch.setattr(jobs, "_claim_next_job", fake_claim)
    monkeypatch.setattr(jobs, "_dispatch_job", fake_dispatch)

    async def run_and_stop() -> None:
        task = asyncio.create_task(jobs.worker_loop({"audit"}))
        for _ in range(80):
            if max_running >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_stop())
    assert max_running == 3


def test_run_assistant_audit_forwards_judge_concurrency(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(audit_run.config, "DATA_DIR", tmp_path)
    script = tmp_path / "workflow.py"
    script.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(audit_run, "SCRIPT_PATH", script)
    monkeypatch.setattr(audit_run, "resolve_markdown_path", lambda *_a, **_k: tmp_path / "r.md")
    monkeypatch.setattr(audit_run, "resolve_naming_rule_path", lambda *_a, **_k: tmp_path / "n.md")
    monkeypatch.setattr(audit_run, "resolve_naming_rule_file_id", lambda *_a, **_k: None)
    monkeypatch.setattr("app.storage.repositories.get_content_repository", lambda: None)
    monkeypatch.setattr("app.storage.repositories.get_content_write_repository", lambda: None)
    captured: list[list[str]] = []

    def fake_subprocess_run(cmd, **_kwargs):
        captured.append(list(cmd))
        output = Path(cmd[cmd.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"summary": {"cases": 0}}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(audit_run.subprocess, "run", fake_subprocess_run)
    audit_run.run_assistant_audit(
        assistant_id=ASSISTANT_ID,
        report_file_id="report",
        job_id="job_case_cap",
        run_id="job_case_cap",
        report_name="end_to_end_audit_oil_transformer_audit_job_case_cap.json",
        judge_concurrency=5,
    )
    assert "--judge-concurrency" in captured[0]
    assert captured[0][captured[0].index("--judge-concurrency") + 1] == "5"

    captured.clear()
    audit_run.run_assistant_audit(
        assistant_id=ASSISTANT_ID,
        report_file_id="report",
        job_id="job_interactive",
        run_id="job_interactive",
        report_name="end_to_end_audit_oil_transformer_audit_job_interactive.json",
    )
    assert "--judge-concurrency" not in captured[0]


def test_batch_audit_job_passes_case_concurrency_cap(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_to_thread(_fn, **kwargs):
        captured.update(kwargs)
        return {"summary": {"cases": 0}}

    monkeypatch.setattr(jobs.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(jobs, "merge_job_result", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs, "get_job", lambda *_a, **_k: {"result": {}})
    monkeypatch.setattr(jobs, "_mark_job_done", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs, "_usage_summary_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs.config, "AUDIT_BATCH_CASE_CONCURRENCY", 5)

    batch_job = {
        "id": "job-batch",
        "type": "audit",
        "target_type": "assistant",
        "target_id": ASSISTANT_ID,
        "attempts": 1,
        "result": {
            "report_file_id": "r1",
            "batch_id": "batch-1",
            "run_id": "job-batch",
            "report_name": "end_to_end_audit_oil_transformer_audit_job-batch.json",
        },
    }
    asyncio.run(jobs._run_audit_job(batch_job))
    assert captured["judge_concurrency"] == 5

    captured.clear()
    interactive = {
        "id": "job-live",
        "type": "audit",
        "target_type": "assistant",
        "target_id": ASSISTANT_ID,
        "attempts": 1,
        "result": {
            "report_file_id": "r2",
            "run_id": "job-live",
            "report_name": "end_to_end_audit_oil_transformer_audit_job-live.json",
        },
    }
    asyncio.run(jobs._run_audit_job(interactive))
    assert captured["judge_concurrency"] is None


def test_postgres_audit_claim_uses_advisory_lock_and_scan_limit(monkeypatch) -> None:
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def __init__(self):
            self.sql: list[str] = []
            self.params: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            self.sql.append(statement)
            self.params.append(params)
            if "pg_advisory_xact_lock" in statement:
                return Result([])
            if "SELECT * FROM jobs" in statement:
                return Result(
                    [
                        {
                            "id": "job-1",
                            "type": "audit",
                            "status": "queued",
                            "result": {},
                            "attempts": 0,
                            "max_attempts": 2,
                        }
                    ]
                )
            return Result(
                [
                    {
                        "id": "job-1",
                        "type": "audit",
                        "status": "running",
                        "result": {},
                        "attempts": 1,
                        "max_attempts": 2,
                    }
                ]
            )

    connection = Connection()
    monkeypatch.setattr(
        PostgresJobRepository,
        "_connect",
        lambda _self: connection,
    )
    claimed = PostgresJobRepository("postgresql://test").claim_pending(
        "worker-1", job_types={"audit"}
    )
    assert claimed[0]["status"] == "running"
    assert "pg_advisory_xact_lock" in connection.sql[0]
    assert "FOR UPDATE SKIP LOCKED" in connection.sql[1]
    assert connection.params[1][-1] == audit_claim.AUDIT_CLAIM_SCAN_LIMIT
    assert "locked_until" in connection.sql[2]
