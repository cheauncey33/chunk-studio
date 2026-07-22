from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db
import run_report_audit_workflow as workflow


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _close_temp_db(monkeypatch) -> None:
    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_assistant_evidence_scope_excludes_runtime_inputs_and_other_kbs(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        for file_id in ("report", "naming", "standard", "outsider"):
            conn.execute(
                """INSERT INTO files(id,name,path,metadata,created_at)
                   VALUES (?,?,?,'{}','now')""",
                (file_id, f"{file_id}.pdf", f"files/{file_id}.pdf"),
            )
        conn.executemany(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_uncategorized',?,'source',1,'now')""",
            [("report",), ("naming",), ("standard",)],
        )
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,name,description,status,is_default,parser_config,
                retrieval_config,created_at,updated_at)
               VALUES ('kb_other','其他库','','active',0,'{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_other','outsider','source',1,'now')"""
        )

    file_ids = workflow._assistant_evidence_file_ids(
        "assistant_oil_transformer_audit",
        excluded_file_ids={"report", "naming"},
    )

    assert file_ids == ["standard"]
    _close_temp_db(monkeypatch)


def test_runtime_retrieval_config_and_selection_apply_version_values() -> None:
    profile = {
        "retrieval_config": {
            "top_k": 1,
            "route_top_k": 7,
            "candidate_count_per_type": 5,
            "rrf_k": 40,
            "similarity_threshold": 0.5,
        }
    }
    config = workflow._retrieval_runtime_config(profile)
    candidates = [
        {"route_scores": {"semantic": 0.9}, "rrf_score": 0.01, "id": "keep"},
        {"route_scores": {"semantic": 0.4}, "rrf_score": 0.9, "id": "threshold"},
        {"route_scores": {"semantic": 0.8}, "rrf_score": 0.005, "id": "top-k"},
    ]

    selected = workflow._select_retrieval_candidates(
        candidates,
        top_k=int(config["top_k"]),
        similarity_threshold=float(config["similarity_threshold"]),
    )

    assert config["route_top_k"] == 7
    assert config["final_per_type"] == 15
    assert config["special_route_reserve"] == 3
    assert [item["id"] for item in selected] == ["keep"]


def test_runtime_retrieval_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="top_k"):
        workflow._retrieval_runtime_config({"retrieval_config": {"top_k": 0}})


def test_retrieve_hybrid_candidates_maps_hits_and_passes_scope(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return {
            "retrieval_mode": "dual_rerank",
            "query_routes": {
                "production": query,
                "semantic": "s",
                "keyword": "k",
                "table_target": "表",
            },
            "routes_injected": True,
            "special_route_reserve": 3,
            "final_per_type": 15,
            "candidate_count": 2,
            "degraded": [],
            "hits": [
                {
                    "chunk_id": "t1",
                    "text": "table evidence",
                    "score": 0.4,
                    "rerank_score": 0.91,
                    "rrf_score": 0.03,
                    "business_metadata": {"content_type": "table"},
                },
                {
                    "chunk_id": "s1",
                    "text": "section evidence",
                    "score": 0.5,
                    "rerank_score": 0.7,
                    "rrf_score": 0.02,
                    "business_metadata": {"content_type": "section"},
                },
            ],
        }

    import app.retrieval as retrieval_mod

    monkeypatch.setattr(retrieval_mod, "hybrid_search", fake_hybrid_search)
    routes = {
        "production": "空载损耗限值",
        "semantic": "s",
        "keyword": "k",
        "table_target": "表",
    }
    candidates, debug = workflow._retrieve_hybrid_candidates(
        "空载损耗限值",
        query_routes=routes,
        file_ids=["standard"],
        top_k=5,
        route_top_k=11,
        candidates_per_type=9,
        final_per_type=15,
        special_route_reserve=3,
        rrf_k=40,
        similarity_threshold=0.3,
    )

    assert captured["query"] == "空载损耗限值"
    assert captured["kwargs"]["file_ids"] == ["standard"]
    assert captured["kwargs"]["query_routes"] == routes
    assert captured["kwargs"]["top_k"] == max(5, 15 * 2)
    assert captured["kwargs"]["route_top_k"] == 11
    assert captured["kwargs"]["candidates_per_type"] == 9
    assert captured["kwargs"]["final_per_type"] == 15
    assert captured["kwargs"]["special_route_reserve"] == 3
    assert captured["kwargs"]["rrf_k"] == 40
    assert captured["kwargs"]["similarity_threshold"] == 0.3
    assert [item["chunk_id"] for item in candidates] == ["t1", "s1"]
    assert candidates[0]["content_type"] == "table"
    assert candidates[0]["route_scores"] == {"hybrid": 0.91}
    assert debug["retrieval_mode"] == "dual_rerank"
    assert debug["routes_injected"] is True
    assert debug["final_per_type"] == 15


def test_enqueue_rejects_empty_evidence_after_excluding_report(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    from app import jobs

    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at)
               VALUES ('report','report.pdf','files/report.pdf','{}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES ('kb_uncategorized','report','source',1,'now')"""
        )
        conn.execute(
            """INSERT INTO document_parses
               (id,file_id,provider,status,markdown_path,result,error,created_at,updated_at)
               VALUES ('p1','report','mineru','done','parses/report.md','{}','','now','now')"""
        )

    parse_path = tmp_path / "parses"
    parse_path.mkdir(parents=True, exist_ok=True)
    (parse_path / "report.md").write_text("# report", encoding="utf-8")

    with pytest.raises(ValueError, match="no evidence files after excluding"):
        jobs.enqueue_assistant_audit(
            "assistant_oil_transformer_audit",
            report_file_id="report",
        )
    _close_temp_db(monkeypatch)


def test_enqueue_accepts_report_outside_knowledge_base(monkeypatch, tmp_path) -> None:
    """Reports are audit inputs and no longer need KB membership."""
    _init_temp_db(monkeypatch, tmp_path)
    from app import jobs

    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,metadata,created_at) VALUES
               ('report','report.pdf','files/report.pdf',
                '{"doc_role":"report"}','now'),
               ('std','GB.pdf','files/GB.pdf','{"doc_type":"standard"}','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
               VALUES ('kb_uncategorized','std','source','standard',1,'now')"""
        )
        conn.execute(
            """INSERT INTO document_parses
               (id,file_id,provider,status,markdown_path,result,error,created_at,updated_at)
               VALUES
               ('p_report','report','mineru','done','parses/report.md','{}','','now','now'),
               ('p_std','std','mineru','done','parses/std.md','{}','','now','now')"""
        )

    parse_path = tmp_path / "parses"
    parse_path.mkdir(parents=True, exist_ok=True)
    (parse_path / "report.md").write_text("# report", encoding="utf-8")
    (parse_path / "std.md").write_text("# std", encoding="utf-8")

    job = jobs.enqueue_assistant_audit(
        "assistant_oil_transformer_audit",
        report_file_id="report",
    )
    assert job["status"] == "queued"
    assert job["type"] == "audit"
    _close_temp_db(monkeypatch)
