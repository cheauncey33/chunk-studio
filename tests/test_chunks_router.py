from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import chunks


def test_list_chunks_can_filter_llm_suggestions(monkeypatch, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chunks.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.execute(
        "INSERT INTO files(id,name,path,created_at) VALUES ('f','test.pdf','files/f.pdf','now')"
    )
    rows = [
        ("keywords", {"keywords": {"value": ["负载损耗"]}}),
        ("questions", {"questions": {"value": ["负载损耗是多少？"]}}),
        ("empty", {"keywords": {"value": []}, "questions": {"value": []}}),
        ("none", {}),
    ]
    conn.executemany(
        """INSERT INTO chunks
           (id,file_id,page,bbox,text,metadata_llm,status,created_at,updated_at)
           VALUES (?, 'f', 1, '{"x":0,"y":0,"w":1,"h":1}', ?, ?, 'approved', 'now', 'now')""",
        [(chunk_id, chunk_id, json.dumps(metadata, ensure_ascii=False)) for chunk_id, metadata in rows],
    )
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)

    all_chunks = chunks.list_chunks()
    suggested = chunks.list_chunks(has_llm_suggestions=True)

    assert {chunk.id for chunk in all_chunks} == {"keywords", "questions", "empty", "none"}
    assert {chunk.id for chunk in suggested} == {"keywords", "questions"}
    conn.close()
