from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, embeddings


def test_build_embeddings_is_incremental(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO files(id,name,path,created_at) VALUES ('f','GB/T 1-2024.pdf','files/f.pdf','now')"
        )
        conn.execute(
            """INSERT INTO chunks
               (id,file_id,page,bbox,text,business_metadata,status,created_at,updated_at)
               VALUES ('c','f',1,'{}','正文','{"standard_no":"GB/T 1-2024","section":"1"}',
                       'approved','now','now')"""
        )

    calls: list[list[str]] = []

    def fake_embedder(texts, *, model, dimension):
        calls.append(texts)
        return [[0.5] * dimension for _ in texts], 4

    first = embeddings.build_embeddings(dimension=4, embedder=fake_embedder)
    second = embeddings.build_embeddings(dimension=4, embedder=fake_embedder)

    assert first["embedded"] == 1
    assert second["embedded"] == 0
    assert len(calls) == 1
    row = db.get_conn().execute("SELECT dimension, length(embedding) AS size FROM chunk_embeddings").fetchone()
    assert dict(row) == {"dimension": 4, "size": 16}

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
