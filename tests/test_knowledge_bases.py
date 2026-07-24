from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
import sys

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import assistants, files, knowledge_bases


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
    retrieval_config = json.loads(row["retrieval_config"])
    assert retrieval_config["aggregate_continuation_tables"] is False
    assert retrieval_config["expand_references"] is False
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
    assert "generic/" in (version["node_prompts"]["report_parameters"].get("path") or "")
    assert version["parameter_schema"]["allow_extra"] is True
    assert version["parameter_schema"]["fields"][0]["key"] == "model"
    _close_temp_db(monkeypatch)


def test_generic_template_assistant_is_seeded(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)

    row = db.get_conn().execute(
        """SELECT a.name, v.node_prompts, v.parameter_schema
           FROM audit_assistants a
           JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.id='assistant_audit_template'"""
    ).fetchone()
    oil = db.get_conn().execute(
        """SELECT v.parameter_schema, v.node_prompts
           FROM assistant_versions v
           WHERE v.id='assistant_oil_transformer_audit_v1'"""
    ).fetchone()

    assert row["name"] == "通用审查模板"
    prompts = json.loads(row["node_prompts"])
    schema = json.loads(row["parameter_schema"])
    assert "generic/" in prompts["report_parameters"]["path"]
    assert schema["allow_extra"] is True
    oil_schema = json.loads(oil["parameter_schema"])
    oil_prompts = json.loads(oil["node_prompts"])
    assert oil_schema["allow_extra"] is False
    assert len(oil_schema["fields"]) == 7
    assert "generic/" not in oil_prompts["report_parameters"]["path"]
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


def test_patch_knowledge_base_file_toggles_enabled(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_test','测试库','','active',0,'{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f_toggle','toggle.pdf','files/toggle.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_test','f_toggle','source',1,'now')"""
        )

    disabled = knowledge_bases.update_knowledge_base_file(
        "kb_test",
        "f_toggle",
        knowledge_bases.KnowledgeBaseFileUpdate(enabled=False),
    )
    assert disabled["id"] == "f_toggle"
    assert disabled["enabled"] is False
    assert disabled["role"] == "source"

    enabled = knowledge_bases.update_knowledge_base_file(
        "kb_test",
        "f_toggle",
        knowledge_bases.KnowledgeBaseFileUpdate(enabled=True),
    )
    assert enabled["enabled"] is True
    _close_temp_db(monkeypatch)


def test_patch_knowledge_base_corpus_rules(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f_name','naming.pdf','files/naming.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_uncategorized','f_name','source',1,'now')"""
        )

    updated = knowledge_bases.update_knowledge_base(
        "kb_uncategorized",
        knowledge_bases.KnowledgeBaseUpdate(
            manual_rules={
                "version": 1,
                "scope": "knowledge_base_manual_rules",
                "status": "test",
                "rules": [
                    {
                        "rule_id": "r1",
                        "rule_text": "demo",
                    }
                ],
            },
            few_shot_rules={
                "version": 1,
                "items": [{"id": "fs1", "title": "样例", "input": "i", "output": "o"}],
            },
            parser_config={"chunk_method": "general"},
            default_naming_file_id="f_name",
        ),
    )
    assert updated["manual_rules"]["rules"][0]["rule_id"] == "r1"
    assert updated["few_shot_rules"]["items"][0]["id"] == "fs1"
    assert updated["parser_config"]["chunk_method"] == "general"
    assert updated["default_naming_file_id"] == "f_name"
    assert updated["default_naming_file_name"] == "naming.pdf"
    membership = db.get_conn().execute(
        """SELECT COUNT(*) AS n FROM knowledge_base_files
           WHERE file_id='f_name'"""
    ).fetchone()["n"]
    assert membership == 0

    cleared = knowledge_bases.update_knowledge_base(
        "kb_uncategorized",
        knowledge_bases.KnowledgeBaseUpdate(clear_default_naming_file=True),
    )
    assert cleared["default_naming_file_id"] is None
    _close_temp_db(monkeypatch)


def test_merge_manual_rules_priority_and_fallback() -> None:
    merged = db.merge_manual_rules(
        [
            {
                "manual_rules": json.dumps(
                    {
                        "version": 1,
                        "scope": "knowledge_base_manual_rules",
                        "rules": [{"rule_id": "shared", "rule_text": "from-a"}],
                    },
                    ensure_ascii=False,
                )
            },
            {
                "manual_rules": json.dumps(
                    {
                        "version": 1,
                        "scope": "knowledge_base_manual_rules",
                        "rules": [{"rule_id": "shared", "rule_text": "from-b"}],
                    },
                    ensure_ascii=False,
                )
            },
        ]
    )
    by_id = {rule["rule_id"]: rule for rule in merged["rules"]}
    assert by_id["shared"]["rule_text"] == "from-b"

    empty = db.merge_manual_rules(
        [],
        fallback={
            "scope": "knowledge_base_manual_rules",
            "rules": [{"rule_id": "fallback", "rule_text": "ok"}],
        },
    )
    assert empty["rules"][0]["rule_id"] == "fallback"


def test_assistant_default_naming_file_id_uses_bound_kb(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at) VALUES
               ('f_low','low.pdf','files/low.pdf','{}','now'),
               ('f_high','high.pdf','files/high.pdf','{}','now')"""
        )
        conn.execute(
            """UPDATE knowledge_bases SET default_naming_file_id='f_high'
               WHERE id='kb_uncategorized'"""
        )

    assert db.assistant_default_naming_file_id("assistant_oil_transformer_audit") == "f_high"
    from app import audit_run

    assert (
        audit_run.resolve_naming_rule_file_id(
            "assistant_oil_transformer_audit",
            "f_low",
        )
        == "f_low"
    )
    assert (
        audit_run.resolve_naming_rule_file_id("assistant_oil_transformer_audit", None)
        == "f_high"
    )
    _close_temp_db(monkeypatch)


def test_seed_backfills_manual_rules_onto_default_kb(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    row = db.get_conn().execute(
        "SELECT manual_rules FROM knowledge_bases WHERE id='kb_uncategorized'"
    ).fetchone()
    payload = json.loads(row["manual_rules"] or "{}")
    assert payload.get("scope") == "knowledge_base_manual_rules"
    assert any(
        rule.get("rule_id") == "transformer_total_loss_sum_v1"
        for rule in payload.get("rules") or []
    )
    _close_temp_db(monkeypatch)


def test_retrieval_test_accepts_explicit_file_ids(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_ret','检索库','','active',0,'{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f_a','a.pdf','files/a.pdf','{}','now'),
                      ('f_b','b.pdf','files/b.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_ret','f_a','source',1,'now'),
                      ('kb_ret','f_b','source',0,'now')"""
        )

    captured: dict = {}

    def fake_hybrid_search(query, **kwargs):
        captured["query"] = query
        captured["file_ids"] = kwargs.get("file_ids")
        return {
            "hits": [],
            "retrieval_mode": "hybrid",
            "query_routes": [],
            "candidate_count": 0,
            "degraded": False,
        }

    monkeypatch.setattr(knowledge_bases.retrieval, "hybrid_search", fake_hybrid_search)

    defaulted = knowledge_bases.retrieval_test(
        "kb_ret",
        knowledge_bases.RetrievalTestRequest(query="空载损耗"),
    )
    assert defaulted["scoped_file_ids"] == ["f_a"]
    assert captured["file_ids"] == ["f_a"]

    scoped = knowledge_bases.retrieval_test(
        "kb_ret",
        knowledge_bases.RetrievalTestRequest(query="空载损耗", file_ids=["f_b", "f_a", "f_a"]),
    )
    assert scoped["scoped_file_ids"] == ["f_b", "f_a"]
    assert captured["file_ids"] == ["f_b", "f_a"]

    from fastapi import HTTPException

    try:
        knowledge_bases.retrieval_test(
            "kb_ret",
            knowledge_bases.RetrievalTestRequest(query="空载损耗", file_ids=["missing"]),
        )
        raise AssertionError("expected unknown file_ids to fail")
    except HTTPException as exc:
        assert exc.status_code == 400

    _close_temp_db(monkeypatch)


def test_delete_knowledge_base_removes_exclusive_files(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    from app import config as app_config
    from fastapi import HTTPException

    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = files_dir / "only.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    shared_path = files_dir / "shared.pdf"
    shared_path.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "FILES_DIR", files_dir)

    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_del','待删库','','active',0,'{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f_only','only.pdf','files/only.pdf','{}','now'),
                      ('f_shared','shared.pdf','files/shared.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO chunks(id,file_id,page,bbox,text,status,created_at,updated_at)
               VALUES ('c1','f_only',1,'{}','a','pending','now','now'),
                      ('c2','f_shared',1,'{}','b','pending','now','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_del','f_only','source',1,'now'),
                      ('kb_del','f_shared','source',1,'now'),
                      ('kb_uncategorized','f_shared','source',1,'now')"""
        )

    result = knowledge_bases.delete_knowledge_base("kb_del")
    assert result["ok"] is True
    assert result["deleted_file_count"] == 1
    assert result["deleted_chunk_count"] == 1

    remaining = {
        row["id"]
        for row in db.get_conn().execute("SELECT id FROM files").fetchall()
    }
    assert "f_only" not in remaining
    assert "f_shared" in remaining
    assert not pdf_path.exists()
    assert shared_path.exists()

    try:
        knowledge_bases.delete_knowledge_base("kb_uncategorized")
        raise AssertionError("expected default KB delete to fail")
    except HTTPException as exc:
        assert exc.status_code == 400

    _close_temp_db(monkeypatch)


def test_report_upload_does_not_join_knowledge_base(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(files.config, "FILES_DIR", tmp_path / "files")
    files.config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(files.pdf, "page_count", lambda path: 1)
    monkeypatch.setattr(files.jobs, "enqueue_parse_file", lambda file_id: {})

    uploaded = UploadFile(filename="HBJC-report.pdf", file=BytesIO(b"%PDF-1.4\n%%EOF"))
    result = asyncio.run(
        files.upload(
            uploaded,
            metadata='{"doc_role":"report","doc_type":"report"}',
            knowledge_base_id=None,
        )
    )
    membership = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM knowledge_base_files WHERE file_id=?",
        (result["id"],),
    ).fetchone()["n"]
    assert membership == 0
    assert result["metadata"]["doc_role"] == "report"
    _close_temp_db(monkeypatch)


def test_naming_upload_sets_kb_attribute_without_corpus_membership(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_named','命名库','','active',0,'{}','{}','now','now')"""
        )
    monkeypatch.setattr(files.config, "FILES_DIR", tmp_path / "files")
    files.config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(files.pdf, "page_count", lambda path: 1)
    monkeypatch.setattr(files.jobs, "enqueue_parse_file", lambda file_id: {})

    uploaded = UploadFile(filename="naming.pdf", file=BytesIO(b"%PDF-1.4\n%%EOF"))
    result = asyncio.run(
        files.upload(
            uploaded,
            metadata='{"doc_role":"naming"}',
            knowledge_base_id="kb_named",
        )
    )
    kb = knowledge_bases.get_knowledge_base("kb_named")
    assert kb["default_naming_file_id"] == result["id"]
    assert kb["default_naming_file_name"] == "naming.pdf"
    membership = db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM knowledge_base_files WHERE file_id=?",
        (result["id"],),
    ).fetchone()["n"]
    assert membership == 0
    listed = knowledge_bases.list_knowledge_base_files("kb_named")
    assert listed == []
    _close_temp_db(monkeypatch)


def test_patch_knowledge_base_file_corpus_kind(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_kind','分类库','','active',0,'{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('f_kind','spec.pdf','files/spec.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
               VALUES ('kb_kind','f_kind','source','standard',1,'now')"""
        )

    updated = knowledge_bases.update_knowledge_base_file(
        "kb_kind",
        "f_kind",
        knowledge_bases.KnowledgeBaseFileUpdate(corpus_kind="spec"),
    )
    assert updated["corpus_kind"] == "spec"
    listed = knowledge_bases.list_knowledge_base_files("kb_kind")
    assert listed[0]["corpus_kind"] == "spec"
    _close_temp_db(monkeypatch)


def test_seed_skips_report_and_naming_files(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at) VALUES
               ('f_report','report.pdf','files/report.pdf',
                '{"doc_role":"report"}','now'),
               ('f_naming','naming.pdf','files/naming.pdf',
                '{"doc_role":"naming"}','now'),
               ('f_std','GB.pdf','files/GB.pdf','{"doc_type":"standard"}','now')"""
        )
        conn.execute("DELETE FROM knowledge_base_files")

    db._seed_knowledge_base_and_assistant()
    db.get_conn().commit()

    attached = {
        row["file_id"]
        for row in db.get_conn().execute(
            "SELECT file_id FROM knowledge_base_files WHERE knowledge_base_id='kb_uncategorized'"
        ).fetchall()
    }
    assert "f_std" in attached
    assert "f_report" not in attached
    assert "f_naming" not in attached
    _close_temp_db(monkeypatch)
