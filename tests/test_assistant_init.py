from __future__ import annotations

from pathlib import Path
import json
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


def test_generate_and_apply_updates_active_schema_prompt_and_provenance(
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
    assert "category_profile" not in payload
    assert payload["parameter_schema"]["fields"][0]["key"] == "model"
    assert "电缆专用抽参" in payload["report_parameters_prompt"]

    assistant_init.upsert_init_draft(assistant_id, status="ready", payload=payload)
    applied = assistant_init.apply_init_draft(assistant_id)
    assert applied["version"] == int(before["version"])
    assert applied["version_id"] == before["id"]

    after = assistants.get_active_version(assistant_id)
    assert after["id"] == before["id"]
    assert after["version"] == before["version"]
    row_count = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM assistant_versions WHERE assistant_id=?",
        (assistant_id,),
    ).fetchone()
    assert int(row_count["n"]) == 1
    assert after["parameter_schema"]["fields"][1]["key"] == "rated_voltage"
    assert after["node_prompts"]["report_parameters"]["content"].startswith("# 电缆专用抽参")
    assert after["category_profile"] == {}
    assert after["initialization_provenance"]["source"] == "assistant_init_draft"
    assert after["initialization_provenance"]["standard_file_ids"] == ["std1"]
    assert after["initialization_provenance"]["sample_report_file_ids"] == []
    assert after["initialization_provenance"]["generated_at"]
    assert after["initialization_provenance"]["applied_at"]
    for key in ("test_items", "model_decode", "query_planner", "audit_judge"):
        assert after["node_prompts"][key]["content"] == before_nodes[key]["content"]

    draft = assistant_init.get_init_draft(assistant_id)
    assert draft is not None
    assert draft["status"] == "applied"
    _close_temp_db(monkeypatch)


def test_apply_strips_legacy_full_judge_and_planner_notes(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="清理库", description="")
    )
    assistant_id = created["assistant_id"]
    assert assistant_id
    _seed_parsed_standard(tmp_path, kb_id=created["id"], file_id="std_strip")

    legacy_judge = (
        "判断报告填写的标准要求是否被候选标准 Chunk 支持。不得用常识补充标准值。\n"
        "状态：\n"
        "- correct：直接证据支持报告要求；\n"
        "- incorrect：直接证据给出冲突数值；\n"
        "- insufficient_context：缺少参数；\n"
        "- evidence_not_found：没有足够证据。\n"
        '严格输出 JSON：{"status":"","evidence_candidate_keys":[]}\n'
    )
    legacy_planner = (
        Path(__file__).resolve().parents[1]
        / "evaluation/prompts/generic/retrieval_query_planner_generic_v1.md"
    ).read_text(encoding="utf-8")

    before = assistants.get_active_version(assistant_id)
    nodes = dict(before["node_prompts"])
    nodes["audit_judge"] = {
        **(nodes.get("audit_judge") or {}),
        "content": legacy_judge,
    }
    nodes["query_planner"] = {
        **(nodes.get("query_planner") or {}),
        "content": legacy_planner,
    }
    with db.transaction() as conn:
        conn.execute(
            "UPDATE assistant_versions SET node_prompts=? WHERE id=?",
            (json.dumps(nodes, ensure_ascii=False), before["id"]),
        )

    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *_a, **_k: {
            "parameter_schema": {
                "version": 1,
                "allow_extra": False,
                "fields": [{"key": "model", "label": "型号", "required": True}],
            },
        },
    )
    monkeypatch.setattr(llm, "chat_text", lambda *_a, **_k: "")

    payload = assistant_init.generate_init_draft(assistant_id, sample_report_file_ids=[])
    assistant_init.upsert_init_draft(assistant_id, status="ready", payload=payload)
    assistant_init.apply_init_draft(assistant_id)

    after = assistants.get_active_version(assistant_id)
    assert after["node_prompts"]["audit_judge"]["content"] == ""
    assert after["node_prompts"]["query_planner"]["content"] == ""
    assert after["node_prompts"]["report_parameters"]["content"] == ""
    # Routes / version rules are not rewritten by init.
    assert after["retrieval_config"].get("query_planner_routes") == before["retrieval_config"].get(
        "query_planner_routes"
    )
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


def test_backfill_applied_legacy_draft_provenance_only(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    assistant_id = "assistant_oil_transformer_audit"
    assistant_init.upsert_init_draft(
        assistant_id,
        status="applied",
        payload={
            "source_file_ids": {
                "standard": ["std-old"],
                "sample_reports": ["sample-old"],
            },
            "model": "deepseek-v4-flash",
        },
        job_id="job-old",
    )
    with db.transaction() as conn:
        conn.execute(
            """UPDATE assistant_versions
               SET category_profile='{"name":"旧画像"}', initialization_provenance='{}'
               WHERE id='assistant_oil_transformer_audit_v1'"""
        )

    db._backfill_applied_init_profiles()
    active = assistants.get_active_version(assistant_id)
    assert active["category_profile"] == {}
    assert active["initialization_provenance"]["source"] == "assistant_init_draft_backfill"
    assert active["initialization_provenance"]["standard_file_ids"] == ["std-old"]
    assert active["initialization_provenance"]["sample_report_file_ids"] == ["sample-old"]
    assert active["initialization_provenance"]["draft_job_id"] == "job-old"
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

    payload = assistant_init.generate_init_draft(assistant_id, sample_report_file_ids=[])
    assistant_init.upsert_init_draft(assistant_id, status="ready", payload=payload)

    updated = assistants.update_assistant_init_draft(
        assistant_id,
        assistants.AssistantInitDraftUpdate(
            report_parameters_prompt="人工确认后的提示词",
        ),
    )
    assert updated["payload"]["report_parameters_prompt"] == "人工确认后的提示词"
    assert "category_profile" not in (updated["payload"] or {})

    applied = assistants.apply_assistant_init_draft(assistant_id)
    assert applied["assistant"]["id"] == assistant_id
    active = assistants.get_active_version(assistant_id)
    assert active["node_prompts"]["report_parameters"]["content"] == "人工确认后的提示词"
    assert active["category_profile"] == {}
    assert active["initialization_provenance"]["source"] == "assistant_init_draft"
    assert active["initialization_provenance"]["standard_file_ids"] == ["std_api"]

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
