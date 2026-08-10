from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import assistant_router, audit_run, db, llm
from app.routers import knowledge_bases


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,workspace_id,name,path,metadata,created_at)
               VALUES ('report1', ?, 'report.md', 'files/report.md', '{}', 'now')""",
            (db.config.DEFAULT_WORKSPACE_ID,),
        )


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_list_routable_candidates_excludes_template(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    created = knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="路由库", description="用于路由")
    )
    candidates = assistant_router.list_routable_candidates()
    ids = {item["assistant_id"] for item in candidates}
    assert "assistant_audit_template" not in ids
    assert "assistant_oil_transformer_audit" in ids
    assert created["assistant_id"] in ids
    oil = next(
        item
        for item in candidates
        if item["assistant_id"] == "assistant_oil_transformer_audit"
    )
    assert "category_profile" not in oil
    assert oil["knowledge_base_name"]
    _close_temp_db(monkeypatch)


def test_route_singleton_skips_llm(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    # Leave only the oil assistant as routable by archiving any extras created.
    with db.transaction() as conn:
        conn.execute(
            """UPDATE audit_assistants
               SET status='archived'
               WHERE id NOT IN ('assistant_oil_transformer_audit','assistant_audit_template')"""
        )
        conn.execute(
            """DELETE FROM assistant_knowledge_bases
               WHERE assistant_id!='assistant_oil_transformer_audit'"""
        )

    markdown = tmp_path / "report.md"
    markdown.write_text("油浸式变压器出厂试验报告", encoding="utf-8")
    monkeypatch.setattr(
        audit_run,
        "resolve_markdown_path",
        lambda _file_id: markdown,
    )
    called = {"n": 0}

    def _boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("LLM should not be called for singleton routing")

    monkeypatch.setattr(llm, "chat_json", _boom)

    result = assistant_router.route_report_to_assistant(report_file_id="report1")
    assert result["routed_by"] == "singleton"
    assert result["assistant_id"] == "assistant_oil_transformer_audit"
    assert called["n"] == 0
    _close_temp_db(monkeypatch)


def test_route_falls_back_on_invalid_llm_choice(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    knowledge_bases.create_knowledge_base(
        knowledge_bases.KnowledgeBaseCreate(name="另一库", description="")
    )
    db.set_setting("audit.default_assistant_id", "assistant_oil_transformer_audit")

    markdown = tmp_path / "report.md"
    markdown.write_text("未知品类报告", encoding="utf-8")
    monkeypatch.setattr(
        audit_run,
        "resolve_markdown_path",
        lambda _file_id: markdown,
    )
    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *_args, **_kwargs: {
            "assistant_id": "assistant_does_not_exist",
            "confidence": 0.9,
            "reason": "瞎选",
        },
    )

    result = assistant_router.route_report_to_assistant(report_file_id="report1")
    assert result["fallback_used"] is True
    assert result["routed_by"] == "fallback_invalid"
    assert result["assistant_id"] == "assistant_oil_transformer_audit"
    _close_temp_db(monkeypatch)
