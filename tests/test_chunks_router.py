from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import current_user, db
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


def _insert_scoped_chunk(conn: sqlite3.Connection, *, chunk_id: str, workspace_id: str) -> None:
    conn.execute(
        "INSERT INTO files(id, workspace_id, name, path, created_at) VALUES (?, ?, 't.pdf', 'files/t.pdf', 'now')",
        (f"file-{chunk_id}", workspace_id),
    )
    conn.execute(
        """INSERT INTO chunks
           (id, workspace_id, file_id, page, bbox, text, text_source, status, created_at, updated_at)
           VALUES (?, ?, ?, 1, '{"x":0,"y":0,"w":1,"h":1}', 'body', 'digital', 'approved', 'now', 'now')""",
        (chunk_id, workspace_id, f"file-{chunk_id}"),
    )


def test_get_chunk_uses_sidecar_workspace_when_authorized(monkeypatch, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "chunks.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    _insert_scoped_chunk(conn, chunk_id="c-other", workspace_id="ws-other")
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)
    monkeypatch.setattr(chunks, "get_content_repository", lambda: None)
    monkeypatch.setattr(chunks.config, "AGENT_SIDECAR_TOKEN", "secret")
    monkeypatch.setattr(
        current_user,
        "get_current_user",
        lambda: current_user.CurrentUser(
            user_id="u1",
            workspace_id="ws-local",
            roles=frozenset(),
            authenticated=True,
        ),
    )

    found = chunks.get_chunk(
        "c-other",
        workspace_id="ws-other",
        authorization="Bearer secret",
    )
    assert found.id == "c-other"
    assert found.text == "body"

    with pytest.raises(HTTPException) as forged:
        chunks.get_chunk(
            "c-other",
            workspace_id="ws-other",
            authorization="Bearer other",
        )
    assert forged.value.status_code == 404

    with pytest.raises(HTTPException) as unsigned:
        chunks.get_chunk("c-other", workspace_id="ws-other")
    assert unsigned.value.status_code == 404
    conn.close()


def test_workspace_for_chunk_read_ignores_user_override(monkeypatch) -> None:
    monkeypatch.setattr(chunks.config, "AGENT_SIDECAR_TOKEN", "secret")
    monkeypatch.setattr(
        current_user,
        "get_current_user",
        lambda: current_user.CurrentUser(
            user_id="u1",
            workspace_id="ws-local",
            roles=frozenset(),
            authenticated=True,
        ),
    )
    assert chunks._workspace_for_chunk_read("Bearer secret", "ws-other") == "ws-other"
    assert chunks._workspace_for_chunk_read("Bearer other", "ws-other") == "ws-local"
    assert chunks._workspace_for_chunk_read(None, "ws-other") == "ws-local"
