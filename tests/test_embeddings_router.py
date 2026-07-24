"""Embedding build trigger + readiness status endpoint."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers.embeddings import build_embeddings, embeddings_status


def _seed_db(monkeypatch, tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "embeddings-router.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.execute(
        """INSERT INTO files(id, name, path, page_count, metadata, created_at)
           VALUES ('file-1', 'std.pdf', 'files/std.pdf', 1, '{}', '2026-01-01T00:00:00')"""
    )
    for chunk_id, status in (("c-approved", "approved"), ("c-pending", "pending")):
        conn.execute(
            """INSERT INTO chunks(
                   id, file_id, page, bbox, text, text_source, metadata,
                   business_metadata, metadata_llm, source_trace, chunk_logic,
                   relations, ui_state, indexing, status, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chunk_id, "file-1", 1,
                json.dumps({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}),
                "text", "digital", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}",
                status, "2026-01-01T00:00:00", "2026-01-01T00:00:00",
            ),
        )
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)
    return conn


def test_status_counts_approved_and_pending_vectors(monkeypatch, tmp_path: Path) -> None:
    conn = _seed_db(monkeypatch, tmp_path)

    status = embeddings_status()

    assert status["approved_chunks"] == 1
    assert status["pending_chunks"] == 1  # approved chunk has no vector yet
    assert status["embedded_chunks"] == 0
    assert status["latest_job"] is None
    conn.close()


def test_build_endpoint_enqueues_and_reuses_job(monkeypatch, tmp_path: Path) -> None:
    conn = _seed_db(monkeypatch, tmp_path)

    first = build_embeddings()
    second = build_embeddings()

    assert first["type"] == "embed"
    assert first["status"] == "queued"
    assert second["id"] == first["id"]

    status = embeddings_status()
    assert status["latest_job"]["id"] == first["id"]
    conn.close()
