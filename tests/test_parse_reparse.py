from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, jobs


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db.config, "CROPS_DIR", tmp_path / "crops")
    monkeypatch.setattr(db, "_conn", None)
    (tmp_path / "crops").mkdir(parents=True, exist_ok=True)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_enqueue_parse_can_delete_existing_chunks(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','a.pdf','files/a.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO chunks
               (id,file_id,page,bbox,text,status,created_at,updated_at)
               VALUES ('c1','f1',1,'{}','hello','approved','now','now'),
                      ('c2','f1',2,'{}','world','pending','now','now')"""
        )

    job = jobs.enqueue_parse_file("f1", force=True, delete_chunks=True)
    remaining = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE file_id='f1'"
    ).fetchone()["n"]
    assert remaining == 0
    assert job["result"]["delete_chunks"] is True
    assert job["result"]["deleted_chunk_count"] == 2
    _close_temp_db(monkeypatch)


def test_enqueue_parse_keeps_chunks_by_default(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','a.pdf','files/a.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO chunks
               (id,file_id,page,bbox,text,status,created_at,updated_at)
               VALUES ('c1','f1',1,'{}','hello','approved','now','now')"""
        )

    jobs.enqueue_parse_file("f1", force=True, delete_chunks=False)
    remaining = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE file_id='f1'"
    ).fetchone()["n"]
    assert remaining == 1
    _close_temp_db(monkeypatch)
