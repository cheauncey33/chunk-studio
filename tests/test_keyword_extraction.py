from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, keyword_extraction


def test_keyword_extraction_is_incremental_and_keeps_approval(monkeypatch, tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "keywords.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.execute("INSERT INTO files(id,name,path,created_at) VALUES ('f','test.pdf','files/f.pdf','now')")
    conn.execute(
        """INSERT INTO chunks
           (id,file_id,page,bbox,text,business_metadata,status,created_at,updated_at)
           VALUES ('c','f',1,'{}','绕组热点温升','{"standard_no":"GB/T 1094.7-2024"}',
                   'approved','now','now')"""
    )
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)
    calls = []

    def fake_extractor(items, *, model):
        calls.append(items)
        return {"c": ["绕组", "热点温升", "绕组"]}

    first = keyword_extraction.extract_keywords(extractor=fake_extractor)
    second = keyword_extraction.extract_keywords(extractor=fake_extractor)

    row = conn.execute("SELECT metadata_llm,status FROM chunks WHERE id='c'").fetchone()
    stored = json.loads(row["metadata_llm"])["keywords"]
    assert first["extracted"] == 1
    assert second["extracted"] == 0
    assert len(calls) == 1
    assert stored["value"] == ["绕组", "热点温升"]
    assert row["status"] == "approved"
    conn.close()


def test_normalizes_keyword_suggestions() -> None:
    assert keyword_extraction._normalize_keywords("变压器，热点温升、变压器") == ["变压器", "热点温升"]
