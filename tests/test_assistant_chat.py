from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import assistants


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_assistant_chat_uses_rag_then_plain_text_llm(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','std.pdf','files/std.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files(knowledge_base_id,file_id,enabled,created_at)
               VALUES ('kb_uncategorized','f1',1,'now')"""
        )
        # Seed may already bind default assistant ↔ default KB.

    captured = {}

    def fake_search(query, **kwargs):
        captured["query"] = query
        captured["file_ids"] = kwargs.get("file_ids")
        return {
            "hits": [
                {
                    "chunk_id": "c1",
                    "file_id": "f1",
                    "file_name": "std.pdf",
                    "page": 3,
                    "text": "绝缘电阻试验要求不低于 1000 MΩ。",
                    "score": 0.9,
                }
            ],
            "degraded": [],
        }

    def fake_chat_text(messages, **kwargs):
        captured["messages"] = messages
        captured["model"] = kwargs.get("model")
        return "绝缘电阻应不低于 1000 MΩ。"

    monkeypatch.setattr(assistants.retrieval, "hybrid_search", fake_search)
    monkeypatch.setattr(assistants.llm, "chat_text", fake_chat_text)

    result = assistants.assistant_chat(
        "assistant_oil_transformer_audit",
        assistants.AssistantChatRequest(message="绝缘电阻要求是什么？", history=[]),
    )

    assert result["answer"] == "绝缘电阻应不低于 1000 MΩ。"
    assert result["citations"][0]["file_name"] == "std.pdf"
    assert captured["query"] == "绝缘电阻要求是什么？"
    assert captured["file_ids"] == ["f1"]
    assert "检索证据" in captured["messages"][0]["content"]
    assert captured["messages"][-1]["role"] == "user"

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_citations_dedupe_same_file_page() -> None:
    hits = [
        {
            "chunk_id": "c1",
            "file_id": "f1",
            "file_name": "GBT 1094.7.pdf",
            "page": 12,
            "text": "a",
            "score": 0.5,
        },
        {
            "chunk_id": "c2",
            "file_id": "f1",
            "file_name": "GBT 1094.7.pdf",
            "page": 12,
            "text": "b",
            "score": 0.9,
        },
        {
            "chunk_id": "c3",
            "file_id": "f1",
            "file_name": "GBT 1094.7.pdf",
            "page": 12,
            "text": "c",
            "score": 0.7,
        },
        {
            "chunk_id": "c4",
            "file_id": "f2",
            "file_name": "GB 1094.1.pdf",
            "page": 14,
            "text": "d",
            "score": 0.8,
        },
    ]
    citations = assistants._citations_from_hits(hits)
    assert len(citations) == 2
    assert citations[0]["chunk_id"] == "c2"
    assert citations[0]["page"] == 12
    assert citations[1]["file_name"] == "GB 1094.1.pdf"


def test_assistant_chat_requires_bound_files(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    from fastapi import HTTPException

    try:
        assistants.assistant_chat(
            "assistant_oil_transformer_audit",
            assistants.AssistantChatRequest(message="hello", history=[]),
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "知识库" in str(exc.detail)

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
