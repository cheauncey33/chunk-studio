"""Live audit progress is merged into running job.result."""

from __future__ import annotations

import json

from app import db, jobs


def test_merge_job_result_shallow_merges_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "progress.db")
    db._conn = None
    db.init_db()

    jid = "job_progress_1"
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at, started_at)
               VALUES (?, 'audit', 'assistant', 'a1', 'running', 5, 0, 1, '', ?, ?, ?)""",
            (
                jid,
                json.dumps({"assistant_id": "a1", "report_file_id": "f1"}, ensure_ascii=False),
                jobs.now_iso(),
                jobs.now_iso(),
            ),
        )

    jobs.merge_job_result(
        jid,
        {
            "progress": {
                "stage": "test_items",
                "stage_label": "提取检测项目",
                "percent": 12,
            }
        },
    )
    jobs.merge_job_result(
        jid,
        {
            "progress": {
                "stage": "audit_cases",
                "case_done": 3,
                "case_total": 10,
                "percent": 40,
            }
        },
    )

    job = jobs.get_job(jid)
    assert job["status"] == "running"
    assert job["result"]["assistant_id"] == "a1"
    progress = job["result"]["progress"]
    assert progress["stage"] == "audit_cases"
    assert progress["stage_label"] == "提取检测项目"
    assert progress["case_done"] == 3
    assert progress["case_total"] == 10
    assert progress["percent"] == 40


def test_merge_job_result_skips_terminal_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "progress_done.db")
    db._conn = None
    db.init_db()

    jid = "job_progress_done"
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_type, target_id, status, priority, attempts,
                max_attempts, error, result, created_at, finished_at)
               VALUES (?, 'audit', 'assistant', 'a1', 'done', 5, 1, 1, '', '{}', ?, ?)""",
            (jid, jobs.now_iso(), jobs.now_iso()),
        )

    assert jobs.merge_job_result(jid, {"progress": {"percent": 50}}) is None
    assert jobs.get_job(jid)["result"] == {}
