from __future__ import annotations

from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db
import migrate_embeddings_to_pgvector as migration


def test_embedding_backfill_is_workspace_scoped(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    db_path = tmp_path / "chunkstudio.db"
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO files(id, workspace_id, name, path, created_at) VALUES ('f1', 'local-workspace', 'a.pdf', 'files/a.pdf', 'now')"
        )
        conn.execute(
            "INSERT INTO files(id, workspace_id, name, path, created_at) VALUES ('f2', 'other-workspace', 'b.pdf', 'files/b.pdf', 'now')"
        )
        conn.execute(
            """INSERT INTO chunks(id, workspace_id, file_id, page, bbox, text, status, created_at, updated_at)
               VALUES ('c1', 'local-workspace', 'f1', 1, '{}', 'a', 'approved', 'now', 'now')"""
        )
        conn.execute(
            """INSERT INTO chunks(id, workspace_id, file_id, page, bbox, text, status, created_at, updated_at)
               VALUES ('c2', 'other-workspace', 'f2', 1, '{}', 'b', 'approved', 'now', 'now')"""
        )
        vector = struct.pack("<2f", 0.1, 0.2)
        for chunk_id in ("c1", "c2"):
            conn.execute(
                """INSERT INTO chunk_embeddings
                   (chunk_id, model, dimension, text_sha256, embedding, created_at, updated_at)
                   VALUES (?, 'test-model', 2, ?, ?, 'now', 'now')""",
                (chunk_id, chunk_id, vector),
            )

    rows = migration._rows(
        db_path,
        workspace_id="local-workspace",
        model="test-model",
        dimension=2,
    )

    assert [row["chunk_id"] for row in rows] == ["c1"]
