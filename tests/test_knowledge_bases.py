from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import sys

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import assistants, files


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_seed_assigns_existing_files_without_overwriting_file_metadata(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f1','cable.pdf','files/cable.pdf','{"doc_type":"standard"}','now')"""
        )

    db._seed_knowledge_base_and_assistant()
    db.get_conn().commit()
    db._seed_knowledge_base_and_assistant()
    db.get_conn().commit()

    relation_count = db.get_conn().execute(
        """SELECT COUNT(*) AS n FROM knowledge_base_files
           WHERE knowledge_base_id='kb_uncategorized' AND file_id='f1'"""
    ).fetchone()["n"]
    metadata = db.get_conn().execute(
        "SELECT metadata FROM files WHERE id='f1'"
    ).fetchone()["metadata"]
    version_count = db.get_conn().execute(
        """SELECT COUNT(*) AS n FROM assistant_versions
           WHERE assistant_id='assistant_oil_transformer_audit'"""
    ).fetchone()["n"]

    assert relation_count == 1
    assert metadata == '{"doc_type":"standard"}'
    assert version_count == 1
    _close_temp_db(monkeypatch)


def test_default_assistant_is_deepseek_only_and_versioned(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)

    row = db.get_conn().execute(
        """SELECT a.active_version_id, v.version, v.status, v.model_config,
                  v.node_prompts, v.rules, v.retrieval_config
           FROM audit_assistants a
           JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.id='assistant_oil_transformer_audit'"""
    ).fetchone()

    assert row["active_version_id"] == "assistant_oil_transformer_audit_v1"
    assert row["version"] == 1
    assert row["status"] == "active"
    assert '"provider": "deepseek"' in row["model_config"]
    assert "report_parameters" in row["node_prompts"]
    assert row["rules"]
    assert "top_k" in row["retrieval_config"]
    _close_temp_db(monkeypatch)


def test_created_assistant_has_an_editable_active_v1(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)

    created = assistants.create_assistant(
        assistants.AssistantCreate(name="新审查助手", description="test")
    )

    assert created["status"] == "active"
    assert created["active_version"] == 1
    assert created["active_version_id"].endswith("_v1")
    version = assistants.get_active_version(created["id"])
    assert version["status"] == "active"
    assert version["model_config"]["provider"] == "deepseek"
    assert version["node_prompts"]["report_parameters"]["content"]
    _close_temp_db(monkeypatch)


def test_upload_to_named_knowledge_base_does_not_keep_default_membership(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_target','目标库','','active',0,'{}','{}','now','now')"""
        )
    monkeypatch.setattr(files.config, "FILES_DIR", tmp_path / "files")
    files.config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(files.pdf, "page_count", lambda path: 1)
    monkeypatch.setattr(files.jobs, "enqueue_parse_file", lambda file_id: {})

    uploaded = UploadFile(filename="standard.pdf", file=BytesIO(b"%PDF-1.4\n%%EOF"))
    result = asyncio.run(
        files.upload(uploaded, metadata="{}", knowledge_base_id="kb_target")
    )
    memberships = db.get_conn().execute(
        """SELECT knowledge_base_id FROM knowledge_base_files
           WHERE file_id=? ORDER BY knowledge_base_id""",
        (result["id"],),
    ).fetchall()

    assert [row["knowledge_base_id"] for row in memberships] == ["kb_target"]
    _close_temp_db(monkeypatch)
