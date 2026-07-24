from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import assistants, knowledge_bases


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_create_knowledge_base_auto_binds_assistant(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="电缆库", description="电缆品类")
    )
    assert created["assistant_id"]
    assert db.assistant_id_for_knowledge_base(created["id"]) == created["assistant_id"]
    assert db.knowledge_base_id_for_assistant(created["assistant_id"]) == created["id"]

    assistant = assistants.get_assistant(created["assistant_id"])
    assert assistant["knowledge_bases"] == [{"id": created["id"], "name": "电缆库"}]
    _close_temp_db(monkeypatch)


def test_migrate_preserves_multi_binds_by_cloning_and_disabling_conflicts(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    db.get_conn().execute(
        "DELETE FROM schema_migrations WHERE name='assistant-kb-one-to-one-v2'"
    )
    db.get_conn().execute("DROP INDEX uq_assistant_kb_enabled_assistant")
    db.get_conn().execute("DROP INDEX uq_assistant_kb_enabled_kb")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,manual_rules,few_shot_rules,
                default_naming_file_id,created_at,updated_at)
               VALUES ('kb_extra','额外库','','active',0,'{}','{}','{}','{}',NULL,'now','now')"""
        )
        conn.execute(
            """INSERT INTO assistant_knowledge_bases
               (assistant_id,knowledge_base_id,priority,enabled)
               VALUES ('assistant_oil_transformer_audit','kb_extra',1,1)"""
        )
        # Second assistant also on default KB.
        conn.execute(
            """INSERT INTO audit_assistants
               (id,name,description,status,active_version_id,created_at,updated_at)
               VALUES ('assistant_extra','额外助手','','active',NULL,'now','now')"""
        )
        conn.execute(
            """INSERT INTO assistant_knowledge_bases
               (assistant_id,knowledge_base_id,priority,enabled)
               VALUES ('assistant_extra','kb_uncategorized',5,1)"""
        )

    # Re-run the migration against the deliberately corrupted legacy state.
    details = db._migrate_assistant_kb_one_to_one()
    db.get_conn().commit()

    oil_kbs = db.get_conn().execute(
        """SELECT knowledge_base_id FROM assistant_knowledge_bases
           WHERE assistant_id='assistant_oil_transformer_audit' AND enabled=1"""
    ).fetchall()
    assert len(oil_kbs) == 1

    default_assistants = db.get_conn().execute(
        """SELECT assistant_id FROM assistant_knowledge_bases
           WHERE knowledge_base_id='kb_uncategorized' AND enabled=1"""
    ).fetchall()
    assert len(default_assistants) == 1
    assert default_assistants[0]["assistant_id"] == "assistant_oil_transformer_audit"

    cloned = details["cloned_bindings"]
    assert len(cloned) == 1
    assert cloned[0]["source_assistant_id"] == "assistant_oil_transformer_audit"
    assert cloned[0]["knowledge_base_id"] == "kb_extra"
    clone_id = cloned[0]["clone_assistant_id"]
    clone_versions = db.get_conn().execute(
        "SELECT COUNT(*) FROM assistant_versions WHERE assistant_id=?",
        (clone_id,),
    ).fetchone()[0]
    oil_versions = db.get_conn().execute(
        """SELECT COUNT(*) FROM assistant_versions
           WHERE assistant_id='assistant_oil_transformer_audit'"""
    ).fetchone()[0]
    assert clone_versions == oil_versions

    # Conflicting rows are retained as disabled, not deleted.
    disabled = db.get_conn().execute(
        """SELECT enabled FROM assistant_knowledge_bases
           WHERE assistant_id='assistant_extra'
             AND knowledge_base_id='kb_uncategorized'"""
    ).fetchone()
    assert disabled is not None
    assert disabled["enabled"] == 0
    assert Path(details["backup_path"]).is_file()

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO assistant_knowledge_bases
                   (assistant_id,knowledge_base_id,priority,enabled)
                   VALUES ('assistant_oil_transformer_audit','kb_extra',9,1)"""
            )
    _close_temp_db(monkeypatch)


def test_one_to_one_migration_is_idempotent(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    before = db.get_conn().execute(
        "SELECT details FROM schema_migrations WHERE name='assistant-kb-one-to-one-v2'"
    ).fetchone()
    assert before is not None

    first = json.loads(before["details"])
    second = db._migrate_assistant_kb_one_to_one()
    assert second == first
    assert db.get_conn().execute(
        """SELECT COUNT(*) FROM schema_migrations
           WHERE name='assistant-kb-one-to-one-v2'"""
    ).fetchone()[0] == 1
    _close_temp_db(monkeypatch)


def test_set_knowledge_bases_rejects_multi_and_conflict(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    kb_a = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="库A", description="")
    )
    kb_b = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="库B", description="")
    )
    assistant_a = kb_a["assistant_id"]
    assistant_b = kb_b["assistant_id"]

    with pytest.raises(HTTPException) as multi:
        assistants.set_knowledge_bases(
            assistant_a,
            assistants.KnowledgeBaseSelection(
                knowledge_base_ids=[kb_a["id"], kb_b["id"]]
            ),
        )
    assert multi.value.status_code == 422

    with pytest.raises(HTTPException) as conflict:
        assistants.set_knowledge_bases(
            assistant_a,
            assistants.KnowledgeBaseSelection(knowledge_base_ids=[kb_b["id"]]),
        )
    assert conflict.value.status_code == 409

    # Unbind B then rebind to A is rejected because A already has an assistant.
    assistants.set_knowledge_bases(
        assistant_b,
        assistants.KnowledgeBaseSelection(knowledge_base_ids=[]),
    )
    with pytest.raises(HTTPException) as still_conflict:
        assistants.set_knowledge_bases(
            assistant_b,
            assistants.KnowledgeBaseSelection(knowledge_base_ids=[kb_a["id"]]),
        )
    assert still_conflict.value.status_code == 409
    _close_temp_db(monkeypatch)


def test_delete_knowledge_base_removes_paired_assistant(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="待删库", description="")
    )
    assistant_id = created["assistant_id"]
    assert assistant_id

    knowledge_bases.delete_knowledge_base(created["id"])
    gone = db.get_conn().execute(
        "SELECT id FROM audit_assistants WHERE id=?",
        (assistant_id,),
    ).fetchone()
    assert gone is None
    _close_temp_db(monkeypatch)
