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


def test_extract_parameters_uses_schema_and_open_list(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_model(prompt: str, payload: dict, *, model: str):
        captured["payload"] = payload
        captured["model"] = model
        return {
            "parameters": [
                {"key": "model", "value": "ABC-1", "unit": ""},
                {"key": "rated_voltage", "value": "10", "unit": "kV"},
                {"key": "extra_field", "value": "x", "unit": ""},
            ]
        }

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    schema = {
        "version": 1,
        "allow_extra": True,
        "fields": [
            {"key": "model", "label": "型号", "required": True, "hint": ""},
            {"key": "rated_voltage", "label": "额定电压", "required": False, "hint": ""},
        ],
    }
    out = workflow._extract_parameters(
        "# report",
        prompt="extract",
        model="deepseek-v4-flash",
        parameter_schema=schema,
    )
    assert captured["payload"]["parameter_schema"]["fields"][0]["key"] == "model"
    assert out == {
        "model": "ABC-1",
        "rated_voltage": "10 kV",
        "extra_field": "x",
    }


def test_extract_parameters_accepts_legacy_flat_oil_output(monkeypatch) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str):
        return {
            "model": "S20",
            "rated_capacity": "400 kVA",
            "rated_voltage": "10 kV",
            "phase_count": "三相",
            "connection_group": "Dyn11",
            "cooling_method": "ONAN",
            "insulation_level": "",
        }

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    out = workflow._extract_parameters(
        "# report",
        prompt="oil",
        model="deepseek-v4-flash",
        parameter_schema=None,
    )
    assert out["model"] == "S20"
    assert out["rated_capacity"] == "400 kVA"
    assert out["insulation_level"] == ""


def test_full_audit_units_cover_every_requirement_with_stable_ids() -> None:
    extracted = {
        "items": [
            {
                "item_no": "1",
                "phase": "出厂",
                "project_name": "空载损耗",
                "requirements": [
                    {"requirement_text": "≤ 480 W", "unit": "W"},
                    {"requirement_text": "", "unit": ""},
                ],
            },
            {
                "item_no": "2",
                "phase": "出厂",
                "project_name": "负载损耗",
                "requirements": [
                    {"requirement_text": "≤ 5000 W", "unit": "W"},
                ],
            },
        ]
    }

    units = workflow._build_full_audit_units(extracted)

    assert len(units) == 2  # blank requirement rows are skipped
    assert all(unit["gold_case"] is None for unit in units)
    assert units[0]["test_item"]["item_no"] == "1"
    assert units[1]["requirement"]["requirement_text"] == "≤ 5000 W"
    # Ids are deterministic across reruns so checkpoint resume matches.
    assert units[0]["case_id"] == workflow._build_full_audit_units(extracted)[0]["case_id"]
    assert units[0]["case_id"].startswith("item_")
    assert units[0]["case_id"] != units[1]["case_id"]


def test_full_audit_units_disambiguate_duplicate_requirements() -> None:
    item = {
        "item_no": "1",
        "phase": "出厂",
        "project_name": "空载损耗",
        "requirements": [
            {"requirement_text": "≤ 480 W", "unit": "W"},
            {"requirement_text": "≤480 W", "unit": "W"},
        ],
    }

    units = workflow._build_full_audit_units({"items": [item]})

    # Whitespace-insensitive duplicates share the hash but stay one-to-one.
    assert len(units) == 2
    assert len({unit["case_id"] for unit in units}) == 2
    assert units[1]["case_id"] == f"{units[0]['case_id']}_2"


def test_peer_context_rules_read_from_assistant_retrieval_config() -> None:
    configured = workflow._resolve_peer_context_rules({
        "peer_context_rules": [
            {"triggers": ["温升"], "related": ["温升", "顶层油温"]},
            {"triggers": [], "related": ["ignored"]},
            "not-a-rule",
        ],
    })
    assert configured == [(("温升",), ("温升", "顶层油温"))]

    # Explicit empty list disables peer context entirely.
    assert workflow._resolve_peer_context_rules({"peer_context_rules": []}) == []

    # Missing key keeps the built-in defaults for older assistant versions.
    fallback = workflow._resolve_peer_context_rules({})
    assert fallback == workflow.DEFAULT_PEER_CONTEXT_RULES


def test_peer_context_terms_use_injected_rules() -> None:
    rules = [(("总损耗",), ("空载损耗", "负载损耗"))]

    terms = workflow._peer_context_terms("总损耗P总(kW):≤3.985", "损耗测量", rules)
    assert terms == ("空载损耗", "负载损耗")

    assert workflow._peer_context_terms("温升试验", "温升", rules) == ()


def _candidate(key: str, text: str = "证据文本") -> dict:
    return {
        "candidate_key": key,
        "content_type": "table",
        "business_metadata": {
            "standard_no": "GB 20052-2024",
            "table_no": "表1",
            "table_title": "能效限值",
        },
        "source_trace": {"page_start": 3, "page_end": 4},
        "text": text,
    }


def test_judge_validation_maps_legacy_status_and_builds_locators() -> None:
    candidates = [_candidate("c01"), _candidate("c02", text="其他证据")]

    judgment = workflow._validate_judgment(
        {"status": "correct", "reason": "ok", "evidence_candidate_keys": ["c01"]},
        candidates,
    )

    assert judgment["status"] == "supported"
    assert judgment["evidence_candidate_keys"] == ["c01"]
    assert len(judgment["evidence"]) == 1
    locator = judgment["evidence"][0]["locator"]
    assert locator["standard_no"] == "GB 20052-2024"
    assert locator["content_type"] == "table"
    assert locator["page_start"] == 3
    assert locator["page_end"] == 4
    assert locator["table_no"] == "表1"
    from app.evidence_locator import chunk_text_sha256

    assert locator["text_sha256"] == chunk_text_sha256("证据文本")
    assert "legacy status 'correct' mapped to 'supported'" in judgment["validation_issues"]


def test_judge_validation_drops_unknown_keys_and_downgrades_verdict() -> None:
    candidates = [_candidate("c01")]

    judgment = workflow._validate_judgment(
        {"status": "mismatch", "reason": "", "evidence_candidate_keys": ["c99"]},
        candidates,
    )

    assert judgment["status"] == "insufficient_context"
    assert judgment["evidence_candidate_keys"] == []
    assert judgment["evidence"] == []
    issues = judgment["validation_issues"]
    assert any("unknown evidence keys" in issue for issue in issues)
    assert any("downgraded to insufficient_context" in issue for issue in issues)


def test_judge_validation_rejects_invented_status() -> None:
    judgment = workflow._validate_judgment(
        {"status": "definitely_fine", "evidence_candidate_keys": []},
        [_candidate("c01")],
    )

    assert judgment["status"] == "insufficient_context"
    assert any("invalid status" in issue for issue in judgment["validation_issues"])


def test_judge_validation_keeps_clean_contract_output_untouched() -> None:
    candidates = [_candidate("c01")]

    judgment = workflow._validate_judgment(
        {"status": "not_audited", "reason": "无证据", "evidence_candidate_keys": []},
        candidates,
    )

    assert judgment["status"] == "not_audited"
    assert "validation_issues" not in judgment


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


def test_job_priorities_keep_interactive_jobs_above_parse() -> None:
    """Audit / init / embed must outrank slow MinerU parses in the queue."""
    import inspect

    from app import jobs

    def default_priority(func) -> int:
        return inspect.signature(func).parameters["priority"].default

    parse_priority = default_priority(jobs.enqueue_parse_file)
    assert default_priority(jobs.enqueue_assistant_audit) > parse_priority
    assert default_priority(jobs.enqueue_assistant_init) > parse_priority
    assert default_priority(jobs.enqueue_build_embeddings) > parse_priority


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
    # Default runs audit the full report; report_id is an eval-only opt-in.
    assert "report_id" not in (job.get("result") or {})
    _close_temp_db(monkeypatch)
