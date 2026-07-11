import json
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.models import ChunkUpdate
from app.routers.chunks import update_chunk


def _chunk_db(monkeypatch, tmp_path: Path, *, status: str = "pending") -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "chunk-status.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.execute(
        """INSERT INTO files(id, name, path, page_count, metadata, created_at)
           VALUES ('file-1', 'test.pdf', 'files/test.pdf', 1, '{}', '2026-01-01T00:00:00')"""
    )
    conn.execute(
        """INSERT INTO chunks(
               id, file_id, page, bbox, text, text_source, metadata,
               business_metadata, metadata_llm, source_trace, chunk_logic,
               relations, ui_state, indexing, status, created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "chunk-1",
            "file-1",
            1,
            json.dumps({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}),
            "original text",
            "digital",
            "{}",
            json.dumps({"content_type": "section", "section": "1"}),
            "{}",
            "{}",
            "{}",
            "{}",
            "{}",
            "{}",
            status,
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)
    return conn


def test_chunk_review_happy_path(monkeypatch, tmp_path: Path) -> None:
    conn = _chunk_db(monkeypatch, tmp_path)

    assert update_chunk("chunk-1", ChunkUpdate(status="reviewed")).status == "reviewed"
    assert update_chunk("chunk-1", ChunkUpdate(status="approved")).status == "approved"
    assert update_chunk("chunk-1", ChunkUpdate(status="approved")).status == "approved"
    conn.close()


def test_chunk_can_be_rejected_after_review(monkeypatch, tmp_path: Path) -> None:
    conn = _chunk_db(monkeypatch, tmp_path)

    update_chunk("chunk-1", ChunkUpdate(status="reviewed"))
    assert update_chunk("chunk-1", ChunkUpdate(status="rejected")).status == "rejected"
    conn.close()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "approved"),
        ("pending", "rejected"),
        ("reviewed", "pending"),
        ("approved", "reviewed"),
        ("rejected", "reviewed"),
    ],
)
def test_invalid_chunk_status_transition_is_rejected(
    monkeypatch, tmp_path: Path, current: str, target: str
) -> None:
    conn = _chunk_db(monkeypatch, tmp_path, status=current)

    with pytest.raises(HTTPException) as exc:
        update_chunk("chunk-1", ChunkUpdate(status=target))

    assert exc.value.status_code == 409
    conn.close()


@pytest.mark.parametrize("terminal_status", ["reviewed", "approved", "rejected"])
def test_content_change_returns_reviewed_chunk_to_pending(
    monkeypatch, tmp_path: Path, terminal_status: str
) -> None:
    conn = _chunk_db(monkeypatch, tmp_path, status=terminal_status)

    updated = update_chunk("chunk-1", ChunkUpdate(text="corrected text"))

    assert updated.status == "pending"
    assert updated.text == "corrected text"
    assert updated.text_source == "manual"
    conn.close()


def test_identical_content_does_not_reset_approval(monkeypatch, tmp_path: Path) -> None:
    conn = _chunk_db(monkeypatch, tmp_path, status="approved")

    updated = update_chunk(
        "chunk-1",
        ChunkUpdate(
            text="original text",
            business_metadata={"content_type": "section", "section": "1"},
        ),
    )

    assert updated.status == "approved"
    assert updated.text_source == "digital"
    conn.close()


def test_content_and_status_cannot_change_together(monkeypatch, tmp_path: Path) -> None:
    conn = _chunk_db(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc:
        update_chunk(
            "chunk-1",
            ChunkUpdate(text="corrected text", status="reviewed"),
        )

    assert exc.value.status_code == 409
    conn.close()
