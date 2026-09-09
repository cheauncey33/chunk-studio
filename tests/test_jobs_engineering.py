from __future__ import annotations

from pathlib import Path
import asyncio
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, jobs


def _init_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_chunk_stage_is_a_deduplicated_job(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id, workspace_id, name, path, metadata, created_at)
               VALUES ('file-1', ?, 'source.pdf', 'files/source.pdf', '{}', 'now')""",
            (db.config.DEFAULT_WORKSPACE_ID,),
        )

    first = jobs.enqueue_chunk_file("file-1", "parse-1")
    second = jobs.enqueue_chunk_file("file-1", "parse-1")

    assert first["id"] == second["id"]
    assert first["type"] == "chunk"
    assert first["target_type"] == "file"
    assert first["result"]["parse_id"] == "parse-1"


def test_worker_retries_when_queue_dependency_is_temporarily_unavailable(monkeypatch) -> None:
    claim_calls = 0
    sleeps: list[float] = []

    def fake_claim(_job_types):
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            raise RuntimeError("database temporarily unavailable")
        raise asyncio.CancelledError

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(jobs, "_claim_next_job", fake_claim)
    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(jobs.worker_loop({"chunk"}))

    assert claim_calls == 2
    assert sleeps == [jobs.JOB_DEPENDENCY_BACKOFF_SECONDS]


def test_worker_survives_failure_persistence_dependency_error(monkeypatch) -> None:
    sleeps: list[float] = []

    def fake_fail(_job, _error, **_kwargs):
        raise RuntimeError("database unavailable")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(jobs, "_fail_job", fake_fail)
    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    asyncio.run(jobs._record_job_failure({"id": "job-1"}, "handler failed"))

    assert sleeps == [jobs.JOB_DEPENDENCY_BACKOFF_SECONDS]


def test_audit_jobs_use_extended_timeout(monkeypatch) -> None:
    monkeypatch.setattr(jobs.config, "AUDIT_JOB_TIMEOUT_SECONDS", 14400)
    assert jobs._timeout_for_job({"type": "audit"}) == 14400 + jobs.AUDIT_JOB_TIMEOUT_GRACE_SECONDS
    assert jobs._timeout_for_job({"type": "chunk"}) == jobs.JOB_TIMEOUT_SECONDS
    assert jobs._timeout_for_job({"type": "parse"}) == jobs.JOB_TIMEOUT_SECONDS
