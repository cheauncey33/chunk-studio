from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import assistant_init, db, jobs, llm
from app.parameter_schema import resolve_parameter_schema
from app.routers import assistants, knowledge_bases


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def _seed_parsed_standard(tmp_path: Path, *, kb_id: str, file_id: str = "std1") -> None:
    parses = tmp_path / "parses"
    parses.mkdir(parents=True, exist_ok=True)
    md = parses / f"{file_id}.md"
    md.write_text("# 电缆标准\n额定电压应标注。\n型号示例 YJV。\n", encoding="utf-8")
    rel = f"parses/{file_id}.md"
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES (?,?,?,?, 'now')""",
            (file_id, "cable-standard.pdf", f"files/{file_id}.pdf", '{"doc_role":"standard"}'),
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
               VALUES (?,?,'source','standard',1,'now')""",
            (kb_id, file_id),
        )
        conn.execute(
            """INSERT INTO document_parses
               (id,file_id,provider,status,markdown_path,result,error,created_at,updated_at)
               VALUES (?,?, 'mineru','done',?,'{}','', 'now','now')""",
            (f"parse_{file_id}", file_id, rel),
        )


def test_generate_and_apply_only_changes_schema_and_report_parameters(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="电缆初始化库", description="")
    )
    assistant_id = created["assistant_id"]
    assert assistant_id
    _seed_parsed_standard(tmp_path, kb_id=created["id"])

    before = assistants.get_active_version(assistant_id)
    before_nodes = dict(before["node_prompts"])

    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *_args, **_kwargs: {
            "category_profile": {
                "name": "电力电缆",
                "equipment_type": "电缆",
                "focus": "额定电压与型号",
                "notes": "",
            },
            "parameter_schema": {
                "version": 1,
                "allow_extra": True,
                "fields": [
                    {"key": "model", "label": "型号", "required": True, "hint": ""},
                    {"key": "rated_voltage", "label": "额定电压", "required": True, "hint": ""},
                ],
            },
        },
    )
    monkeypatch.setattr(
        llm,
        "chat_text",
        lambda *_args, **_kwargs: "# 电缆专用抽参\n只提取型号与额定电压。",
    )

    payload = assistant_init.generate_init_draft(
        assistant_id,
        sample_report_file_ids=[],
    )
    assert payload["parameter_schema"]["fields"][0]["key"] == "model"
    assert "电缆专用抽参" in payload["report_parameters_prompt"]

    assistant_init.upsert_init_draft(assistant_id, status="ready", payload=payload)
    applied = assistant_init.apply_init_draft(assistant_id)
    assert applied["version"] == int(before["version"]) + 1

    after = assistants.get_active_version(assistant_id)
    assert after["parameter_schema"]["fields"][1]["key"] == "rated_voltage"
    assert after["node_prompts"]["report_parameters"]["content"].startswith("# 电缆专用抽参")
    for key in ("test_items", "model_decode", "query_planner", "audit_judge"):
        assert after["node_prompts"][key]["content"] == before_nodes[key]["content"]

    draft = assistant_init.get_init_draft(assistant_id)
    assert draft is not None
    assert draft["status"] == "applied"
    _close_temp_db(monkeypatch)


def test_enqueue_init_requires_corpus_or_sample(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="空库", description="")
    )
    with pytest.raises(ValueError, match="标准语料或样例"):
        jobs.enqueue_assistant_init(created["assistant_id"])
    _close_temp_db(monkeypatch)


def test_apply_rejects_non_ready_draft(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="草案库", description="")
    )
    assistant_id = created["assistant_id"]
    assistant_init.upsert_init_draft(
        assistant_id,
        status="generating",
        payload={"report_parameters_prompt": "x", "parameter_schema": {}},
    )
    with pytest.raises(ValueError, match="ready"):
        assistant_init.apply_init_draft(assistant_id)
    _close_temp_db(monkeypatch)


def test_init_api_apply_flow(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="API库", description="")
    )
    assistant_id = created["assistant_id"]
    _seed_parsed_standard(tmp_path, kb_id=created["id"], file_id="std_api")

    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *_a, **_k: {
            "category_profile": {"name": "X", "equipment_type": "Y", "focus": "Z", "notes": ""},
            "parameter_schema": resolve_parameter_schema(
                {
                    "version": 1,
                    "allow_extra": True,
                    "fields": [{"key": "model", "label": "型号", "required": True}],
                }
            ),
        },
    )
    monkeypatch.setattr(llm, "chat_text", lambda *_a, **_k: "特化提示词正文")

    # Simulate worker without claiming through queue.
    payload = assistant_init.generate_init_draft(assistant_id, sample_report_file_ids=[])
    assistant_init.upsert_init_draft(assistant_id, status="ready", payload=payload)

    updated = assistants.update_assistant_init_draft(
        assistant_id,
        assistants.AssistantInitDraftUpdate(
            category_profile={"name": "改名", "equipment_type": "Y", "focus": "Z", "notes": ""},
            report_parameters_prompt="人工确认后的提示词",
        ),
    )
    assert updated["payload"]["category_profile"]["name"] == "改名"

    applied = assistants.apply_assistant_init_draft(assistant_id)
    assert applied["assistant"]["id"] == assistant_id
    active = assistants.get_active_version(assistant_id)
    assert active["node_prompts"]["report_parameters"]["content"] == "人工确认后的提示词"

    with pytest.raises(HTTPException) as exc:
        assistants.apply_assistant_init_draft(assistant_id)
    assert exc.value.status_code == 400
    _close_temp_db(monkeypatch)


def test_resolve_schema_from_induction_payload() -> None:
    resolved = resolve_parameter_schema(
        {
            "version": 1,
            "allow_extra": True,
            "fields": [
                {"key": "rated_voltage", "label": "额定电压", "required": True},
            ],
        }
    )
    assert resolved["fields"][0]["key"] == "model"
    assert resolved["fields"][1]["key"] == "rated_voltage"
