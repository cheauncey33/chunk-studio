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


def test_activate_previous_assistant_version(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="版本库", description="")
    )
    assistant_id = created["assistant_id"]
    active = assistants.get_active_version(assistant_id)
    first_id = active["id"]

    second = assistants.create_version(
        assistant_id,
        assistants.AssistantVersionCreate.model_validate(
            {
                "model_config": {**active["model_config"], "provider": "deepseek"},
                "node_prompts": active["node_prompts"],
                "rules": active["rules"],
                "retrieval_config": active["retrieval_config"],
                "parameter_schema": active["parameter_schema"],
                "activate": True,
            }
        ),
    )
    assert second["id"] != first_id
    assert second["status"] == "active"
    assert assistants.get_assistant(assistant_id)["active_version_id"] == second["id"]

    restored = assistants.activate_version(assistant_id, first_id)
    assert restored["id"] == first_id
    assert restored["status"] == "active"
    assert assistants.get_assistant(assistant_id)["active_version_id"] == first_id

    versions = assistants.list_versions(assistant_id)
    by_id = {item["id"]: item for item in versions}
    assert by_id[first_id]["status"] == "active"
    assert by_id[second["id"]]["status"] == "retired"

    with pytest.raises(HTTPException) as exc:
        assistants.activate_version(assistant_id, "missing_version")
    assert exc.value.status_code == 404

    _close_temp_db(monkeypatch)
