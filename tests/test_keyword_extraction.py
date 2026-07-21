from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, keyword_extraction
from app.evidence_locator import chunk_text_sha256


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
        return {
            "c": {
                "keywords": ["绕组", "热点温升", "绕组"],
                "questions": ["绕组热点温升有什么要求？"],
            }
        }

    first = keyword_extraction.extract_suggestions(extractor=fake_extractor)
    second = keyword_extraction.extract_suggestions(extractor=fake_extractor)

    row = conn.execute("SELECT metadata_llm,status FROM chunks WHERE id='c'").fetchone()
    stored = json.loads(row["metadata_llm"])
    assert first["extracted"] == 1
    assert second["extracted"] == 0
    assert len(calls) == 1
    assert stored["keywords"]["value"] == ["绕组", "热点温升"]
    assert stored["questions"]["value"] == ["绕组热点温升有什么要求？"]
    assert stored["keywords"]["prompt_version"] == keyword_extraction.PROMPT_VERSION
    assert stored["questions"]["source_text_sha256"] == chunk_text_sha256("绕组热点温升")
    assert stored["keywords"]["status"] == "suggested"
    assert row["status"] == "approved"
    conn.close()


def test_normalizes_keyword_suggestions() -> None:
    assert keyword_extraction._normalize_keywords("变压器，热点温升、变压器") == ["变压器", "热点温升"]


def test_normalizes_question_suggestions() -> None:
    assert keyword_extraction._normalize_questions(
        "1. 负载损耗限值是多少？\n2、负载损耗限值是多少？\n温升试验如何进行？"
    ) == ["负载损耗限值是多少？", "温升试验如何进行？"]


def test_metadata_extraction_uses_deepseek_json_client(monkeypatch) -> None:
    captured = {}
    item = {
        "id": "c",
        "metadata": '{"standard_no":"GB/T 1094.1-2013"}',
        "text": "高压侧试验电压为35 kV。",
    }

    def chat_json(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {
            "items": [{
                "id": "c",
                "keywords": ["高压侧", "试验电压", "35 kV"],
                "questions": ["高压侧试验电压是多少？"],
            }]
        }

    monkeypatch.setattr(keyword_extraction.llm, "chat_json", chat_json)

    result = keyword_extraction.extract_with_llm([item])

    assert result["c"]["keywords"] == ["高压侧", "试验电压", "35 kV"]
    assert captured["model"] == "deepseek-v4-flash"
    assert "检索元数据生成器" in captured["messages"][0]["content"]


def test_grounding_validation_rejects_new_numbers_and_standards() -> None:
    item = {
        "id": "c",
        "metadata": '{"standard_no":"GB/T 1094.1-2013"}',
        "text": "高压侧试验电压为35 kV。",
    }

    keyword_extraction._validate_grounded(item, ["GB/T 1094.1-2013", "35 kV试验电压"])
    with pytest.raises(ValueError, match="numeric"):
        keyword_extraction._validate_grounded(item, ["75 kV试验电压"])
    with pytest.raises(ValueError, match="standard"):
        keyword_extraction._validate_grounded(item, ["Q/GDW 12126.4-2024"])


def test_sample_by_content_type_round_robins_files() -> None:
    rows = [
        {
            "id": chunk_id,
            "file_id": file_id,
            "business_metadata": json.dumps({"content_type": content_type}),
        }
        for chunk_id, file_id, content_type in (
            ("t1", "a", "table"),
            ("t2", "a", "table"),
            ("t3", "b", "table"),
            ("s1", "a", "section"),
            ("s2", "a", "section"),
            ("s3", "b", "section"),
            ("i1", "a", "image"),
        )
    ]

    selected = keyword_extraction._sample_by_content_type(rows, 2)

    assert [row["id"] for row in selected] == ["t1", "t3", "s1", "s3"]


def test_old_or_text_stale_suggestions_are_pending() -> None:
    row = {
        "text": "负载损耗",
        "metadata_llm": json.dumps(
            {
                "keywords": {
                    "value": ["负载损耗"],
                    "prompt_version": "old",
                    "source_text_sha256": chunk_text_sha256("负载损耗"),
                },
                "questions": {
                    "value": ["负载损耗是多少？"],
                    "prompt_version": keyword_extraction.PROMPT_VERSION,
                    "source_text_sha256": chunk_text_sha256("负载损耗"),
                },
            },
            ensure_ascii=False,
        ),
    }
    metadata_llm = json.loads(row["metadata_llm"])

    assert not keyword_extraction._suggestions_are_current(row, metadata_llm)
    metadata_llm["keywords"]["prompt_version"] = keyword_extraction.PROMPT_VERSION
    assert keyword_extraction._suggestions_are_current(row, metadata_llm)
    row["text"] = "空载损耗"
    assert not keyword_extraction._suggestions_are_current(row, metadata_llm)
