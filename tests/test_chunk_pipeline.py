from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import chunk_pipeline, db


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_resolve_file_chunk_config_merges_kb_then_file_override(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """UPDATE knowledge_bases
               SET parser_config=?
               WHERE id='kb_uncategorized'""",
            (
                '{"auto_chunk_after_parse": true, "sections": {"enabled": true, "target_level": 3, "max_chars": 4096}}',
            ),
        )
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','a.pdf','files/a.pdf',?, 'now')""",
            (
                '{"chunk_config":{"sections":{"max_chars":2048},"images":{"enabled":false}}}',
            ),
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_uncategorized','f1','source',1,'now')"""
        )

    resolved = chunk_pipeline.resolve_file_chunk_config("f1")
    assert resolved["auto_chunk_after_parse"] is True
    assert resolved["sections"]["target_level"] == 3
    assert resolved["sections"]["max_chars"] == 2048
    assert resolved["images"]["enabled"] is False
    assert resolved["tables"]["enabled"] is True
    _close_temp_db(monkeypatch)


def test_seed_default_parser_config_enables_auto_chunk(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    row = db.get_conn().execute(
        "SELECT parser_config FROM knowledge_bases WHERE id='kb_uncategorized'"
    ).fetchone()
    config = chunk_pipeline.normalize_parser_config(
        __import__("json").loads(row["parser_config"] or "{}")
    )
    assert config["auto_chunk_after_parse"] is True
    assert config["sections"]["target_level"] == 2
    _close_temp_db(monkeypatch)
