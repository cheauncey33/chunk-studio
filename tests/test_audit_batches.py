"""Night Batch Phase 1: schedule and aggregate existing audit jobs."""
from __future__ import annotations

import inspect
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import audit_batches, current_user, db, jobs, observability
from app.current_user import CurrentUser
from app.routers import audit_batches as batch_router
from app.storage.batch_repository import (
    BATCH_MAX_CONCURRENCY,
    NIGHT_BATCH_PRIORITY,
    get_batch_repository,
)
from app.storage.repositories import postgres_schema_sql
from app.storage.usage_repository import (
    SqliteUsageRepository,
    UsageEvent,
    get_usage_repository,
)


ASSISTANT_ID = "assistant_oil_transformer_audit"


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


def _record_usage(*, job_id: str, request_id: str, total_tokens: int | None, cost_microunits: int | None, usage_source: str = "provider") -> None:
    get_usage_repository().record_usage(
        UsageEvent(
            id=request_id,
            request_id=request_id,
            workspace_id=db.config.DEFAULT_WORKSPACE_ID,
            job_id=job_id,
            run_id=job_id,
            case_id="c01",
            job_attempt=1,
            request_attempt=1,
            stage="audit_agent",
            provider="test",
            model="fake-model",
            status="success",
            input_tokens=None if usage_source == "unknown" else int(total_tokens or 0),
            output_tokens=None if usage_source == "unknown" else 0,
            reasoning_tokens=None if usage_source == "unknown" else 0,
            cache_read_tokens=None if usage_source == "unknown" else 0,
            cache_write_tokens=None if usage_source == "unknown" else 0,
            total_tokens=total_tokens,
            cost_microunits=cost_microunits,
            usage_source=usage_source,
        )
    )


def _set_job_status(job_id: str, status: str, *, attempts: int | None = None) -> None:
    with db.transaction() as conn:
        if attempts is None:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        else:
            conn.execute(
                "UPDATE jobs SET status=?, attempts=? WHERE id=?",
                (status, attempts, job_id),
            )


def test_postgres_schema_includes_audit_batches() -> None:
    schema = "\n".join(postgres_schema_sql())
    assert "CREATE TABLE IF NOT EXISTS audit_batches" in schema
    assert "CREATE TABLE IF NOT EXISTS audit_batch_items" in schema
    assert "UNIQUE (batch_id, report_file_id)" in schema
    assert "UNIQUE (audit_job_id)" in schema
    assert "max_concurrency INTEGER NOT NULL DEFAULT 1" in schema


def test_normalize_job_schedule_time_rejects_timezones() -> None:
    assert jobs.normalize_job_schedule_time("2026-09-09T23:00:00") == "2026-09-09T23:00:00"
    assert jobs.normalize_job_schedule_time("2026-09-09T23:00") == "2026-09-09T23:00:00"
    with pytest.raises(ValueError, match="naive local format"):
        jobs.normalize_job_schedule_time("2026-09-09T23:00:00Z")
    with pytest.raises(ValueError, match="timezone"):
        jobs.normalize_job_schedule_time("2026-09-09T23:00:00+08:00")
    with pytest.raises(ValueError, match="invalid scheduled_at"):
        jobs.normalize_job_schedule_time("tomorrow")


def test_night_batch_creates_one_audit_job_per_report(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1", "r2", "r3"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1", "r2", "r3"],
        scheduled_at="2099-12-31T23:00:00",
    )
    assert created["total"] == 3
    assert created["mode"] == "night"
    assert created["status"] == "scheduled"
    assert created["max_concurrency"] == BATCH_MAX_CONCURRENCY
    assert len(created["jobs"]) == 3
    assert {job["type"] for job in created["jobs"]} == {"audit"}
    items = get_batch_repository().list_items(
        created["id"], workspace_id=db.config.DEFAULT_WORKSPACE_ID
    )
    assert len(items) == 3
    assert [item["report_file_id"] for item in items] == ["r1", "r2", "r3"]
    assert len({item["audit_job_id"] for item in items}) == 3
    for job in created["jobs"]:
        assert job["priority"] == NIGHT_BATCH_PRIORITY
        assert job["available_at"] == "2099-12-31T23:00:00"
        assert job["status"] == "queued"
        assert job["result"]["batch_id"] == created["id"]
        assert job["result"]["batch_item_id"]
        assert job["batch_id"] == created["id"]
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["total"] == 3
    assert detail["scheduled"] == 3
    assert detail["progress"]["finished"] == 0
    _close_temp_db(monkeypatch)


def test_future_batch_jobs_are_not_claimed_until_scheduled_at(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1"],
        scheduled_at="2099-12-31T23:00:00",
    )
    job_id = created["jobs"][0]["id"]
    assert jobs._claim_next_job({"audit"}) is None
    stored = jobs.get_job(job_id)
    assert stored["status"] == "queued"
    assert stored["available_at"] == "2099-12-31T23:00:00"

    monkeypatch.setattr(jobs, "now_iso", lambda: "2099-12-31T23:00:00")
    claimed = jobs._claim_next_job({"audit"})
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    _close_temp_db(monkeypatch)


def test_interactive_audit_is_claimed_before_night_batch(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["night", "live"])
    audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["night"],
        scheduled_at="2000-01-01T00:00:00",
    )
    interactive = jobs.enqueue_assistant_audit(ASSISTANT_ID, report_file_id="live")
    assert interactive["priority"] == 5
    claimed = jobs._claim_next_job({"audit"})
    assert claimed is not None
    assert claimed["id"] == interactive["id"]
    assert claimed["priority"] == 5
    _close_temp_db(monkeypatch)


def test_ten_reports_create_ten_audit_jobs_not_one_batch_job(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    report_ids = [f"r{i:02d}" for i in range(10)]
    _seed_reports(tmp_path, report_ids)
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=report_ids,
        scheduled_at="2099-12-31T23:00:00",
    )
    jobs_rows = db.get_conn().execute(
        "SELECT type FROM jobs WHERE type IN ('audit','batch')"
    ).fetchall()
    assert [row["type"] for row in jobs_rows] == ["audit"] * 10
    assert created["total"] == 10
    assert len(created["jobs"]) == 10
    _close_temp_db(monkeypatch)


def test_retry_keeps_the_same_batch_item_and_audit_job(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1"],
        scheduled_at="2000-01-01T00:00:00",
    )
    job_id = created["jobs"][0]["id"]
    item_id = created["jobs"][0]["result"]["batch_item_id"]
    run_id = created["jobs"][0]["result"]["run_id"]
    _set_job_status(job_id, "running", attempts=1)
    job = jobs.get_job(job_id)
    jobs._fail_job(job, "sidecar timeout", retryable=True, error_code="timeout")
    stored = jobs.get_job(job_id)
    assert stored["status"] == "queued"
    assert stored["id"] == job_id
    assert stored["result"]["run_id"] == run_id
    assert stored["result"]["batch_id"] == created["id"]
    assert stored["result"]["batch_item_id"] == item_id
    items = get_batch_repository().list_items(
        created["id"], workspace_id=db.config.DEFAULT_WORKSPACE_ID
    )
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["audit_job_id"] == job_id
    _close_temp_db(monkeypatch)


def test_batch_completed_partial_failed_and_failed(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["a", "b", "c"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["a", "b", "c"],
        scheduled_at="2000-01-01T00:00:00",
    )
    job_ids = [job["id"] for job in created["jobs"]]
    for job_id in job_ids:
        _set_job_status(job_id, "done")
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["status"] == "completed"
    assert detail["completed"] == 3
    assert detail["failed"] == 0
    assert detail["progress"]["percent"] == 100.0

    _set_job_status(job_ids[-1], "failed")
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["status"] == "partial_failed"
    assert detail["completed"] == 2
    assert detail["failed"] == 1

    for job_id in job_ids:
        _set_job_status(job_id, "failed")
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["status"] == "failed"
    assert detail["failed"] == 3
    _close_temp_db(monkeypatch)


def test_batch_usage_sums_child_jobs_including_retries(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1", "r2", "r3"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1", "r2", "r3"],
        scheduled_at="2099-12-31T23:00:00",
    )
    job_ids = [job["id"] for job in created["jobs"]]
    _record_usage(job_id=job_ids[0], request_id="a1", total_tokens=100, cost_microunits=10)
    _record_usage(job_id=job_ids[1], request_id="b1", total_tokens=200, cost_microunits=20)
    _record_usage(job_id=job_ids[2], request_id="c1", total_tokens=300, cost_microunits=30)
    _record_usage(job_id=job_ids[1], request_id="b2", total_tokens=50, cost_microunits=5)

    def _should_not_list(self, **_kwargs):
        raise AssertionError("batch usage must not list rows")

    monkeypatch.setattr(SqliteUsageRepository, "list_usage_events", _should_not_list)
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["usage"]["total_tokens"] == 650
    assert detail["usage"]["known_cost_microunits"] == 65
    assert detail["usage"]["cost_microunits"] == 65
    assert detail["usage"]["cost_complete"] is True
    listed = audit_batches.list_batches()
    assert listed[0]["total_tokens"] == 650
    assert listed[0]["cost_complete"] is True
    _close_temp_db(monkeypatch)


def test_batch_incomplete_cost_does_not_look_like_a_total(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1", "r2"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1", "r2"],
        scheduled_at="2099-12-31T23:00:00",
    )
    job_ids = [job["id"] for job in created["jobs"]]
    _record_usage(job_id=job_ids[0], request_id="known", total_tokens=100, cost_microunits=9)
    _record_usage(
        job_id=job_ids[1],
        request_id="unknown",
        total_tokens=None,
        cost_microunits=None,
        usage_source="unknown",
    )
    detail = audit_batches.get_batch_detail(created["id"])
    assert detail["usage"]["known_cost_microunits"] == 9
    assert detail["usage"]["cost_microunits"] is None
    assert detail["usage"]["cost_complete"] is False
    assert detail["usage"]["usage_unknown_event_count"] == 1
    _close_temp_db(monkeypatch)


def test_batch_detail_is_workspace_scoped(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1"])
    created = audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1"],
        scheduled_at="2099-12-31T23:00:00",
    )
    token = current_user.set_current_user(
        CurrentUser(user_id="other", workspace_id="ws-b", roles=frozenset({"member"}))
    )
    try:
        with pytest.raises(KeyError, match="batch not found"):
            audit_batches.get_batch_detail(created["id"])
        assert audit_batches.list_batches() == []
        with pytest.raises(HTTPException) as exc:
            batch_router.get_audit_batch(created["id"])
        assert exc.value.status_code == 404
    finally:
        current_user.reset_current_user(token)
    _close_temp_db(monkeypatch)


def test_duplicate_report_ids_are_rejected(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1"])
    with pytest.raises(ValueError, match="duplicate report_file_id"):
        audit_batches.create_night_batch(
            assistant_id=ASSISTANT_ID,
            report_file_ids=["r1", "r1"],
            scheduled_at="2099-12-31T23:00:00",
        )
    assert get_batch_repository().list_batches(workspace_id=db.config.DEFAULT_WORKSPACE_ID) == []
    _close_temp_db(monkeypatch)


def test_active_audit_conflict_rejects_night_batch(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1", "r2"])
    existing = jobs.enqueue_assistant_audit(ASSISTANT_ID, report_file_id="r1")
    with pytest.raises(jobs.ActiveAuditConflict, match="r1 already has an active audit job"):
        audit_batches.create_night_batch(
            assistant_id=ASSISTANT_ID,
            report_file_ids=["r1", "r2"],
            scheduled_at="2099-12-31T23:00:00",
        )
    assert existing["status"] == "queued"
    assert get_batch_repository().list_batches(workspace_id=db.config.DEFAULT_WORKSPACE_ID) == []
    with pytest.raises(HTTPException) as exc:
        batch_router.create_audit_batch(
            batch_router.AuditBatchCreateRequest(
                assistant_id=ASSISTANT_ID,
                report_file_ids=["r1"],
                scheduled_at="2099-12-31T23:00:00",
            )
        )
    assert exc.value.status_code == 409
    _close_temp_db(monkeypatch)


def test_existing_enqueue_assistant_audit_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["report"])
    job = jobs.enqueue_assistant_audit(ASSISTANT_ID, report_file_id="report")
    assert job["status"] == "queued"
    assert job["priority"] == 5
    assert job.get("available_at") in (None, "")
    assert "batch_id" not in (job.get("result") or {})
    assert "batch_item_id" not in (job.get("result") or {})
    assert inspect.signature(jobs.enqueue_assistant_audit).parameters["priority"].default == 5
    assert inspect.signature(jobs.enqueue_assistant_audit).parameters["available_at"].default is None
    _close_temp_db(monkeypatch)


def test_max_concurrency_greater_than_one_is_rejected(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _seed_reports(tmp_path, ["r1"])
    with pytest.raises(ValueError, match="max_concurrency must be 1"):
        audit_batches.create_night_batch(
            assistant_id=ASSISTANT_ID,
            report_file_ids=["r1"],
            scheduled_at="2099-12-31T23:00:00",
            max_concurrency=2,
        )
    _close_temp_db(monkeypatch)


def test_create_batch_increments_low_cardinality_metrics(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    observability.metrics.reset()
    _seed_reports(tmp_path, ["r1", "r2"])
    audit_batches.create_night_batch(
        assistant_id=ASSISTANT_ID,
        report_file_ids=["r1", "r2"],
        scheduled_at="2099-12-31T23:00:00",
    )
    rendered = observability.metrics.render_prometheus()
    assert "audit_batches_created_total" in rendered
    assert "audit_batch_items_total" in rendered
    _close_temp_db(monkeypatch)
