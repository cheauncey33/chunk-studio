from __future__ import annotations

from pathlib import Path
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


def _version_count(assistant_id: str) -> int:
    row = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM assistant_versions WHERE assistant_id=?",
        (assistant_id,),
    ).fetchone()
    return int(row["n"])


def test_update_active_overwrites_without_inserting_versions(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="单配置库", description="")
    )
    assistant_id = created["assistant_id"]
    first = assistants.get_active_version(assistant_id)
    first_id = first["id"]
    assert _version_count(assistant_id) == 1

    updated = assistants.update_active_version(
        assistant_id,
        assistants.AssistantVersionConfigUpdate.model_validate(
            {
                "model_config": {**first["model_config"], "provider": "deepseek", "temperature": 0.2},
                "node_prompts": first["node_prompts"],
                "rules": first["rules"],
                "retrieval_config": first["retrieval_config"],
                "parameter_schema": first["parameter_schema"],
                "initialization_provenance": first["initialization_provenance"],
            }
        ),
    )
    assert updated["id"] == first_id
    assert updated["version"] == first["version"]
    assert updated["model_config"]["temperature"] == 0.2
    assert _version_count(assistant_id) == 1
    assert first["initialization_provenance"]["source"] == "template_snapshot"
    assert updated["initialization_provenance"]["source"] == "manual_config"
    assert updated["initialization_provenance"]["saved_at"]

    again = assistants.update_active_version(
        assistant_id,
        assistants.AssistantVersionConfigUpdate.model_validate(
            {
                "model_config": {**updated["model_config"], "provider": "deepseek", "temperature": 0.4},
                "node_prompts": updated["node_prompts"],
                "rules": updated["rules"],
                "retrieval_config": updated["retrieval_config"],
                "parameter_schema": updated["parameter_schema"],
                "initialization_provenance": updated["initialization_provenance"],
            }
        ),
    )
    assert again["id"] == first_id
    assert again["model_config"]["temperature"] == 0.4
    assert _version_count(assistant_id) == 1
    assert assistants.get_assistant(assistant_id)["active_version_id"] == first_id

    _close_temp_db(monkeypatch)


def test_update_active_rejects_non_deepseek_provider(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="提供商校验库", description="")
    )
    assistant_id = created["assistant_id"]
    active = assistants.get_active_version(assistant_id)

    with pytest.raises(HTTPException) as exc:
        assistants.update_active_version(
            assistant_id,
            assistants.AssistantVersionConfigUpdate.model_validate(
                {
                    "model_config": {**active["model_config"], "provider": "openai"},
                    "node_prompts": active["node_prompts"],
                    "rules": active["rules"],
                    "retrieval_config": active["retrieval_config"],
                    "parameter_schema": active["parameter_schema"],
                }
            ),
        )
    assert exc.value.status_code == 422
    assert _version_count(assistant_id) == 1

    _close_temp_db(monkeypatch)


def test_migrate_assistant_single_version_keeps_active_deletes_retired(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="迁移清理库", description="")
    )
    assistant_id = created["assistant_id"]
    active = assistants.get_active_version(assistant_id)
    keep_id = active["id"]
    now = "2026-07-25T00:00:00"
    retired_id = f"{assistant_id}_v2_retired"
    db.get_conn().execute(
        """INSERT INTO assistant_versions
           (id,assistant_id,version,name,status,model_config,node_prompts,rules,
            retrieval_config,parameter_schema,category_profile,
            initialization_provenance,created_at,activated_at)
           VALUES (?,?,2,'','retired','{}','{}','{}','{}','{}','{}','{}',?,NULL)""",
        (retired_id, assistant_id, now),
    )
    db.get_conn().commit()
    assert _version_count(assistant_id) == 2

    db._migrate_assistant_single_version()
    db.get_conn().commit()

    rows = db.get_conn().execute(
        "SELECT id, status FROM assistant_versions WHERE assistant_id=?",
        (assistant_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == keep_id
    assert rows[0]["status"] == "active"
    assert assistants.get_assistant(assistant_id)["active_version_id"] == keep_id

    _close_temp_db(monkeypatch)
