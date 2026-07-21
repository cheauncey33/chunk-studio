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
    assert [item["id"] for item in selected] == ["keep"]


def test_runtime_retrieval_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="top_k"):
        workflow._retrieval_runtime_config({"retrieval_config": {"top_k": 0}})
