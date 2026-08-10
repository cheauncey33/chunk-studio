from __future__ import annotations

from pathlib import Path
import sys


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
