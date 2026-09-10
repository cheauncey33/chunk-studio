from __future__ import annotations

import json
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


def test_extract_detection_basis_standard_nos_only_reads_basis_block() -> None:
    markdown = """
<table><tr><td>检测依据</td><td colspan="3">
1.GB/T 1094.1-2013 电力变压器
2.Q∕GDW 12126.4-2024 配电变压器
3.JB／T 501-2021 电力变压器试验导则
</td></tr></table>

正文另有 GB/T 10228-2023，但不属于检测依据。
"""

    assert workflow._extract_detection_basis_standard_nos(markdown) == [
        "GB/T 1094.1-2013",
        "Q/GDW 12126.4-2024",
        "JB/T 501-2021",
    ]


def test_detection_basis_filters_bound_files_by_chunk_standard_no(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    with db.transaction() as conn:
        for file_id in ("oil", "dry"):
            conn.execute(
                """INSERT INTO files(id,name,path,metadata,created_at)
                   VALUES (?,?,?,'{}','now')""",
                (file_id, f"{file_id}.pdf", f"files/{file_id}.pdf"),
            )
        conn.execute(
            """INSERT INTO chunks
               (id,file_id,page,bbox,text,business_metadata,status,created_at,updated_at)
               VALUES ('c-oil','oil',1,'{}','油浸式标准',
                       '{"standard_no":"GB/T 6451-2023"}','approved','now','now'),
                      ('c-dry','dry',1,'{}','干式标准',
                       '{"standard_no":"GB/T 10228-2023"}','approved','now','now')"""
        )

    scoped = workflow._filter_evidence_file_ids_by_detection_basis(
        ["oil", "dry"],
        ["GB/T 6451-2023"],
    )

    assert scoped == ["oil"]
    with pytest.raises(workflow.NonRetryableJobError, match="未在当前知识库找到"):
        workflow._filter_evidence_file_ids_by_detection_basis(
            ["oil", "dry"],
            ["GB/T 1094.3-2017"],
        )
    _close_temp_db(monkeypatch)


def test_checkpoint_resume_skips_completed_cases_and_only_runs_pending() -> None:
    units = [
        {
            "case_id": f"c{index}",
            "test_item": {"item_no": str(index), "project_name": f"item {index}"},
            "requirement": {"requirement_text": f"req {index}"},
        }
        for index in range(1, 6)
    ]
    checkpoint = {
        "cases": [
            {"case_id": "c1", "judgment": {"status": "supported"}},
            {"case_id": "c2", "judgment": {"status": "mismatch"}},
            {"case_id": "c3", "judgment": {"status": "insufficient_context"}},
        ]
    }
    state = workflow.checkpoint_resume_state(checkpoint, units)
    executed: list[str] = []
    for _index, unit in state["pending"]:
        executed.append(unit["case_id"])
    assert executed == ["c4", "c5"]
    assert [item["case_id"] for item in state["results"]] == ["c1", "c2", "c3"]
    assert state["resumed"] is True
    assert state["resumed_case_count"] == 3


def test_checkpoint_resume_fresh_run_executes_every_case() -> None:
    units = [{"case_id": "c1"}, {"case_id": "c2"}]
    state = workflow.checkpoint_resume_state({"cases": []}, units)
    assert [unit["case_id"] for _index, unit in state["pending"]] == ["c1", "c2"]
    assert state["resumed"] is False
    assert state["resumed_case_count"] == 0


def test_select_units_by_case_ids_keeps_order_and_rejects_unknown() -> None:
    units = [{"case_id": "c1"}, {"case_id": "c2"}, {"case_id": "c3"}]
    assert workflow._select_units_by_case_ids(units, None) == units
    assert [item["case_id"] for item in workflow._select_units_by_case_ids(units, ["c3", "c1"])] == [
        "c1",
        "c3",
    ]
    try:
        workflow._select_units_by_case_ids(units, ["c9"])
    except ValueError as exc:
        assert "c9" in str(exc)
    else:
        raise AssertionError("expected unknown --case-id to fail")


def test_write_checkpoint_atomic_leaves_a_complete_json_file(tmp_path: Path) -> None:
    path = tmp_path / "run.checkpoint.json"
    workflow.write_checkpoint_atomic(
        path,
        {"version": 1, "cases": [{"case_id": "c1"}]},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cases"][0]["case_id"] == "c1"
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_write_checkpoint_atomic_keeps_previous_file_if_replace_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "run.checkpoint.json"
    workflow.write_checkpoint_atomic(
        path,
        {"version": 1, "cases": [{"case_id": "c1"}]},
    )

    def fail_replace(_src, _dst):
        raise OSError("killed before replace")

    monkeypatch.setattr(workflow.os, "replace", fail_replace)
    with pytest.raises(OSError, match="killed before replace"):
        workflow.write_checkpoint_atomic(
            path,
            {"version": 1, "cases": [{"case_id": "c2"}]},
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cases"][0]["case_id"] == "c1"


def test_runtime_retrieval_config_and_selection_apply_version_values() -> None:
    profile = {
        "retrieval_config": {
            "top_k": 1,
            "route_top_k": 7,
            "candidate_count_per_type": 5,
            "rrf_k": 40,
            "similarity_threshold": 0.5,
            "aggregate_continuation_tables": True,
            "expand_references": True,
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
    assert config["final_table"] == 8
    assert config["final_section"] == 6
    assert config["final_per_type"] == 8
    assert config["special_route_reserve"] == 3
    assert config["aggregate_continuation_tables"] is True
    assert config["expand_references"] is True
    assert [item["id"] for item in selected] == ["keep"]

    defaults = workflow._retrieval_runtime_config({"retrieval_config": {}})
    assert defaults["final_table"] == 8
    assert defaults["final_section"] == 6
    assert defaults["aggregate_continuation_tables"] is False
    assert defaults["expand_references"] is False

    legacy = workflow._retrieval_runtime_config(
        {"retrieval_config": {"final_per_type": 15}}
    )
    assert legacy["final_table"] == 15
    assert legacy["final_section"] == 15


def test_load_assistant_version_includes_category_provenance(
    monkeypatch, tmp_path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    profile = workflow._load_assistant_version("assistant_oil_transformer_audit")
    assert profile["category_profile"] == {}
    assert profile["initialization_provenance"]["source"] == "built_in_seed"
    keys = {field["key"] for field in profile["parameter_schema"]["fields"]}
    assert {
        "product_type",
        "insulation_medium",
        "equipment_highest_voltage_um",
        "rated_frequency",
        "regulation_method",
        "core_material",
        "core_structure",
        "tank_structure",
        "sealing_type",
    } <= keys
    _close_temp_db(monkeypatch)


def test_oil_schema_backfill_preserves_existing_fields_and_appends_new_ones(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    legacy = {
        "version": 1,
        "allow_extra": False,
        "fields": [
            {
                "key": "model",
                "label": "自定义型号",
                "required": True,
                "hint": "保留人工设置",
            }
        ],
    }
    with db.transaction() as conn:
        conn.execute(
            """UPDATE assistant_versions SET parameter_schema=?
               WHERE id='assistant_oil_transformer_audit_v1'""",
            (json.dumps(legacy, ensure_ascii=False),),
        )

    db._backfill_oil_parameter_schema_fields()
    profile = workflow._load_assistant_version("assistant_oil_transformer_audit")
    fields = profile["parameter_schema"]["fields"]
    assert fields[0]["label"] == "自定义型号"
    assert fields[0]["hint"] == "保留人工设置"
    assert "rated_frequency" in {field["key"] for field in fields}
    assert "tank_structure" in {field["key"] for field in fields}
    _close_temp_db(monkeypatch)


def test_extract_parameters_uses_schema_and_open_list(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
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
    # Schema is injected via system prompt; user payload is report markdown only.
    assert captured["payload"] == {"report_markdown": "# report"}
    assert out == {
        "model": "ABC-1",
        "rated_voltage": "10 kV",
        "extra_field": "x",
    }


def test_extract_parameters_accepts_legacy_flat_oil_output(monkeypatch) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
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
    assert all("gold_case" not in unit for unit in units)
    assert units[0]["test_item"]["item_no"] == "1"
    assert units[1]["requirement"]["requirement_text"] == "≤ 5000 W"
    # Ids are deterministic across reruns so checkpoint resume matches.
    assert units[0]["case_id"] == workflow._build_full_audit_units(extracted)[0]["case_id"]
    assert units[0]["case_id"].startswith("item_")
    assert units[0]["case_id"] != units[1]["case_id"]


def test_full_audit_units_dedupe_whitespace_and_repeat_phase() -> None:
    extracted = {
        "items": [
            {
                "item_no": "3",
                "phase": "initial",
                "project_name": "电压比测量和联结组标号检定",
                "requirements": [
                    {
                        "requirement_text": "其他分接电压比偏差:±1%",
                        "unit": "",
                    },
                    {
                        "requirement_text": "其他分接电压比偏差: ±1%",
                        "unit": "",
                    },
                ],
            },
            {
                "item_no": "15.3",
                "phase": "repeat_routine",
                "project_name": "电压比测量和联结组标号检定",
                "requirements": [
                    {
                        "requirement_text": "其他分接电压比偏差:±1%",
                        "unit": "",
                    },
                    {
                        "requirement_text": "主分接电压比偏差:±0.5%",
                        "unit": "",
                    },
                ],
            },
        ]
    }

    units = workflow._build_full_audit_units(extracted)

    # Same project+requirement (whitespace-normalized) audited once; prefer initial.
    assert len(units) == 2
    texts = [
        unit["requirement"]["requirement_text"].replace(" ", "")
        for unit in units
    ]
    assert texts.count("其他分接电压比偏差:±1%") == 1
    assert "主分接电压比偏差:±0.5%" in texts
    other = next(
        unit
        for unit in units
        if "其他分接" in unit["requirement"]["requirement_text"]
    )
    assert other["test_item"]["phase"] == "initial"


def test_requirement_dedupe_normalizes_numeric_and_unit_noise() -> None:
    assert workflow._normalize_requirement_dedupe_text(
        "介质损耗因数tanδ(90°C):≤1",
        "%",
    ) == workflow._normalize_requirement_dedupe_text(
        "介质损耗因数tanδ(90°C)(%):≤1.0",
        "%",
    )
    # Distinct limits must not collapse.
    assert workflow._normalize_requirement_dedupe_text(
        "偏差:±1%",
        "",
    ) != workflow._normalize_requirement_dedupe_text(
        "偏差:±0.5%",
        "",
    )


def test_full_audit_units_dedupe_insulation_oil_tand_variants() -> None:
    extracted = {
        "items": [
            {
                "item_no": "8",
                "phase": "initial",
                "project_name": "绝缘液试验",
                "requirements": [
                    {
                        "requirement_text": "介质损耗因数tanδ(90°C):≤1",
                        "unit": "%",
                    }
                ],
            },
            {
                "item_no": "15.8",
                "phase": "repeat_routine",
                "project_name": "绝缘液试验",
                "requirements": [
                    {
                        "requirement_text": "介质损耗因数tanδ(90°C)(%):≤1.0",
                        "unit": "%",
                    }
                ],
            },
        ]
    }
    units = workflow._build_full_audit_units(extracted)
    assert len(units) == 1
    assert units[0]["test_item"]["phase"] == "initial"
    assert units[0]["requirement"]["requirement_text"] == "介质损耗因数tanδ(90°C):≤1"


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


def test_standard_priority_classifies_common_standard_nos() -> None:
    assert workflow._standard_priority("Q/GDW 12126.4-2024")["label"] == "企/行标"
    assert workflow._standard_priority("JB/T 501-2021")["label"] == "企/行标"
    assert workflow._standard_priority("GB/T 1094.1-2013")["label"] == "国标"
    assert workflow._standard_priority("GB 20052-2024")["rank"] == 3
    assert (
        workflow._standard_priority(
            "",
            title_blob="某某产品招标技术规范",
        )["label"]
        == "技术规范书"
    )
    enterprise = workflow._standard_priority("Q/GDW 12126.4-2024")["rank"]
    national = workflow._standard_priority("GB/T 1094.1-2013")["rank"]
    assert enterprise < national


def test_compact_candidate_attaches_standard_priority() -> None:
    compact = workflow._compact_candidate(
        {
            "candidate_key": "c02",
            "content_type": "table",
            "business_metadata": {
                "standard_no": "Q/GDW 12126.4-2024",
                "table_no": "30",
                "table_title": "油浸式配电变压器例行试验",
            },
            "text": "判定标准",
        }
    )
    assert compact["standard_priority"]["label"] == "企/行标"
    assert compact["standard_priority"]["rank"] == 2
    assert compact["business_metadata"]["standard_no"] == "Q/GDW 12126.4-2024"


def test_retrieved_pool_for_agent_keeps_section_text_but_strips_table_content() -> None:
    pool = workflow._retrieved_pool_for_agent(
        [
            {
                "chunk_id": "chk-1",
                "candidate_key": "c01",
                "content_type": "table",
                "business_metadata": {
                    "standard_no": "GB/T 1094.1-2013",
                    "table_no": "1",
                    "table_title": "性能参数",
                    "table_columns": ["额定容量", "空载损耗"],
                },
                "table_row_binding": {
                    "state": "matched",
                    "headers": ["额定容量", "空载损耗"],
                    "column_values": {"额定容量": "400", "空载损耗": "0.370"},
                },
                "text": "<table>很大</table>",
            },
            {
                "chunk_id": "chk-2",
                "candidate_key": "c02",
                "content_type": "section",
                "business_metadata": {
                    "standard_no": "GB/T 1094.3-2017",
                    "section": "5.3",
                    "section_title": "绝缘水平",
                },
                "text": "雷电冲击电压应不小于 75 kV，见表3。",
            },
            {
                "candidate_key": "c03",
                "content_type": "section",
                "business_metadata": {},
                "text": "无 id 的片段不交给 agent",
            },
        ]
    )
    assert pool == [
        {
            "chunk_id": "chk-1",
            "candidate_key": "c01",
            "content_type": "table",
            "standard_no": "GB/T 1094.1-2013",
            "table_no": "1",
            "table_title": "性能参数",
            "table_columns": ["额定容量", "空载损耗"],
            "section": None,
            "section_title": None,
            "headers": ["额定容量", "空载损耗"],
            "bind_state": "matched",
        },
        {
            "chunk_id": "chk-2",
            "candidate_key": "c02",
            "content_type": "section",
            "standard_no": "GB/T 1094.3-2017",
            "table_no": None,
            "table_title": None,
            "table_columns": None,
            "section": "5.3",
            "section_title": "绝缘水平",
            "headers": None,
            "bind_state": None,
            "text": "雷电冲击电压应不小于 75 kV，见表3。",
        },
    ]
    assert "text" not in pool[0]
    assert "column_values" not in pool[0]
    assert "<table>" not in str(pool[0])


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


def _sample_profile_fixture() -> dict:
    return {
        "from_report": {
            "model": "S20-M.RL-400/10-NX2",
            "rated_capacity": "400 kVA",
            "rated_voltage": "10/0.4 kV",
        },
        "from_model_decode": {
            "raw_model": "S20-M.RL-400/10-NX2",
            "features": [{"segment": "RL", "meaning": "立体卷铁芯"}],
            "feature_meanings": {"RL": "立体卷铁芯"},
            "retrieval_terms": ["三相油浸式密封式立体卷铁芯变压器"],
            "unresolved_segments": [],
        },
    }


def test_consistency_flags_reason_status_fight() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "mismatch",
            "reason": "报告要求与标准限值一致，应判定为supported。但之前误判为mismatch，现更正。",
            "missing_context_fields": [],
        },
        _sample_profile_fixture(),
    )
    assert any("claims status" in issue for issue in issues)
    assert any("self-correction" in issue for issue in issues)


def test_consistency_flags_agreement_under_insufficient_context() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "insufficient_context",
            "reason": "表19限值40与报告要求≤40一致，与报告要求一致。",
            "missing_context_fields": ["铁心材质"],
        },
        _sample_profile_fixture(),
    )
    assert any("agreement" in issue for issue in issues)


def test_consistency_ignores_negated_support_phrase_under_insufficient_context() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "insufficient_context",
            "reason": "缺少偏差限值证据，无法核对该要求是否被标准支持。",
            "missing_context_fields": ["偏差限值"],
        },
        _sample_profile_fixture(),
    )

    assert not any("agreement" in issue for issue in issues)


def test_consistency_does_not_encode_one_aggregate_sentence_shape() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "标准规定试验应为9次，即每相各3次；报告仅写试验次数3次，因此支持。",
            "missing_context_fields": [],
        },
        _sample_profile_fixture(),
    )

    assert not any("aggregate and subgroup" in issue for issue in issues)


def test_consistency_flags_structured_exact_value_conflict() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "The report and standard are supported.",
            "missing_context_fields": [],
            "comparison": {
                "kind": "exact",
                "report_value": "1.5",
                "report_unit": "Ur",
                "standard_value": "1.155",
                "standard_unit": "Ur",
                "relation": "equal",
                "conclusion": "supports",
            },
        },
        _sample_profile_fixture(),
    )

    assert any("deterministic relation 'different'" in issue for issue in issues)


def test_consistency_preserves_opposite_comparator_direction_at_equal_value() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "mismatch",
            "reason": "报告为≥55，标准为≤55，方向冲突。",
            "missing_context_fields": [],
            "comparison": {
                "kind": "lower_bound",
                "report_value": "55",
                "report_unit": "K",
                "report_operator": "ge",
                "standard_value": "55",
                "standard_unit": "K",
                "standard_operator": "le",
                "relation": "different",
                "conclusion": "conflicts",
            },
        },
        _sample_profile_fixture(),
    )

    assert not any("deterministic relation" in issue for issue in issues)


def test_consistency_allows_alphanumeric_category_as_text() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "mismatch",
            "reason": "AX12 与 BY34 不同。",
            "missing_context_fields": [],
            "comparison": {
                "kind": "text",
                "report_value": "AX12",
                "standard_value": "BY34",
                "relation": "different",
                "conclusion": "conflicts",
            },
        },
        _sample_profile_fixture(),
    )

    assert issues == []


def test_mismatch_tolerance_can_fail_closed_without_inventing_nominal_base() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "mismatch",
            "reason": "报告允许偏差1.0%，标准为0.5%，报告更宽。",
            "missing_context_fields": [],
            "comparison": {
                "kind": "tolerance",
                "report_value": "1.0",
                "report_unit": "%",
                "standard_value": "0.5",
                "standard_unit": "%",
                "relation": "looser",
                "conclusion": "conflicts",
            },
        },
        _sample_profile_fixture(),
    )

    assert not any("requires separate" in issue for issue in issues)


def test_consistency_flags_looser_structured_tolerance() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "The nominal value is the same.",
            "missing_context_fields": [],
            "comparison": {
                "kind": "tolerance",
                "report_value": "4.0",
                "report_unit": "%",
                "standard_value": "4.0",
                "standard_unit": "%",
                "report_tolerance": "20%",
                "standard_tolerance": "10%",
                "relation": "equal",
                "conclusion": "supports",
            },
        },
        _sample_profile_fixture(),
    )

    assert any("deterministic relation 'looser'" in issue for issue in issues)


def test_consistency_rejects_ungrounded_supported_nominal_value() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "The generic waveform tolerance is supported.",
            "missing_context_fields": [],
            "comparison": {
                "kind": "tolerance",
                "report_value": "60",
                "report_unit": "kV",
                "standard_value": "60",
                "standard_unit": "kV",
                "report_tolerance": "3%",
                "standard_tolerance": "3%",
                "relation": "equal",
                "conclusion": "supports",
            },
            "evidence": [{
                "content_type": "section",
                "text": "The waveform tolerance is ±3%; the duration is 60 s.",
            }],
        },
        _sample_profile_fixture(),
    )

    assert any("not grounded in selected evidence" in issue for issue in issues)


def test_consistency_rejects_method_only_numeric_evidence() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "方法条款给出了允许偏差。",
            "missing_context_fields": [],
            "comparison": {
                "kind": "tolerance",
                "report_value": "60",
                "report_unit": "kV",
                "standard_value": "60",
                "standard_unit": "kV",
                "report_tolerance": "3%",
                "standard_tolerance": "3%",
                "relation": "equal",
                "conclusion": "supports",
            },
            "evidence": [{
                "content_type": "section",
                "evidence_roles": ["method_rule", "tolerance_rule"],
                "text": "试验电压值的偏差为±3%。",
            }],
        },
        _sample_profile_fixture(),
    )

    assert any("only by method/tolerance/applicability" in issue for issue in issues)


def test_consistency_accepts_nominal_and_tolerance_role_chain() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "绑定表行给出60kV，方法条款给出±3%。",
            "missing_context_fields": [],
            "comparison": {
                "kind": "tolerance",
                "report_value": "60",
                "report_unit": "kV",
                "standard_value": "60",
                "standard_unit": "kV",
                "report_tolerance": "3%",
                "standard_tolerance": "3%",
                "relation": "equal",
                "conclusion": "supports",
            },
            "evidence": [
                {
                    "content_type": "table",
                    "evidence_roles": ["nominal_rule"],
                    "text": "Um 12 kV | 雷电全波冲击 60 kV",
                },
                {
                    "content_type": "section",
                    "evidence_roles": ["method_rule", "tolerance_rule"],
                    "text": "试验电压值的偏差为±3%。",
                },
            ],
        },
        _sample_profile_fixture(),
    )

    assert not any("evidence" in issue for issue in issues)


def test_consistency_preserves_unresolved_applicability_request() -> None:
    profile = _sample_profile_fixture()
    profile["deterministic_applicability"] = {"state": "parameters_only"}
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "insufficient_context",
            "reason": "缺少绝缘类型。",
            "missing_context_fields": ["绝缘类型", "用户特殊要求"],
        },
        profile,
    )

    assert not any("not required by deterministic applicability" in issue for issue in issues)


def test_consistency_rejects_numeric_values_hidden_as_text_kind() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "insufficient_context",
            "reason": "A numeric comparison was deferred.",
            "missing_context_fields": [],
            "comparison": {
                "kind": "text",
                "report_value": "1.5Ur",
                "standard_value": "2Ur/sqrt(3)",
                "relation": "different",
                "conclusion": "unknown",
            },
        },
        _sample_profile_fixture(),
    )

    assert issues == ["numeric comparison cannot use kind 'text'"]


def test_consistency_flags_scope_count_even_when_values_match() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "supported",
            "reason": "The count is supported.",
            "missing_context_fields": [],
            "comparison": {
                "kind": "scope_count",
                "report_value": "3",
                "standard_value": "3",
                "report_scope": "total",
                "standard_scope": "per phase",
                "relation": "equal",
                "conclusion": "supports",
            },
        },
        _sample_profile_fixture(),
    )

    assert any("deterministic relation 'different'" in issue for issue in issues)


def test_consistency_flags_missing_fields_already_in_profile() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "insufficient_context",
            "reason": "缺少额定电压，无法比较。",
            "missing_context_fields": ["额定电压Ur", "未知专用字段XYZ"],
        },
        _sample_profile_fixture(),
    )
    assert any("额定电压Ur" in issue for issue in issues)
    assert "未知专用字段XYZ" not in ";".join(issues)


def test_consistency_keeps_clean_judgment() -> None:
    issues = workflow._collect_judgment_consistency_issues(
        {
            "status": "mismatch",
            "reason": "报告要求±1%，标准为±0.5%，两者冲突。",
            "missing_context_fields": [],
        },
        _sample_profile_fixture(),
    )
    assert issues == []


def test_run_audit_judge_does_not_call_llm_on_consistency_shaped_input(monkeypatch) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        raise AssertionError("the LLM judge is not part of the pipeline")

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    judgment, trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "sample_profile": _sample_profile_fixture(),
            "candidates": [],
        },
        judge_model="deepseek-chat",
        candidates=[_candidate("c01")],
        sample_profile=_sample_profile_fixture(),
    )

    assert judgment["status"] == "insufficient_context"
    assert trace["judge_source"] == "unbound"


def test_run_audit_judge_applies_retrieved_deterministic_conflict_without_rejudge(
    monkeypatch,
) -> None:
    candidate = _candidate("c01")
    candidate["evidence_roles"] = ["nominal_rule"]
    calls = []

    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        calls.append(payload)
        return {
            "status": "insufficient_context",
            "reason": "模型未完成比较。",
            "evidence_candidate_keys": [],
            "missing_context_fields": ["绝缘类型"],
        }

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    judgment, _trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "reported_requirement": {"text": "持续时间：25", "unit": None},
            "test_item": {"project_name": "持续时间"},
            "deterministic_comparisons": [{
                "source": "generic_bound_table_claim",
                "candidate_key": "c01",
                "kind": "exact",
                "report_value": "25",
                "standard_value": "30",
                "relation": "different",
                "conclusion": "conflicts",
                "table_row_binding": {"state": "matched"},
                "trace": {
                    "report_claim": {"property": {"source_text": "持续时间"}},
                    "evidence_claim": {
                        "property": {"source_text": "持续时间"},
                        "value": {
                            "kind": "quantity",
                            "raw": "30",
                            "normalized": "30",
                            "numbers": [30.0],
                            "unit": None,
                            "operator": "eq",
                        },
                    },
                },
            }],
            "table_claim_execution": {
                "mode": "programmatic_table",
                "reason_code": "unique_bound_comparable",
                "status": "mismatch",
                "nodes": [],
            },
        },
        judge_model="deepseek-v4-flash",
        candidates=[candidate],
        sample_profile=_sample_profile_fixture(),
    )

    assert len(calls) == 0
    assert judgment["status"] == "mismatch"
    assert judgment["kind"] == "numeric_looser"
    assert judgment["caliber"]["rules"] == ["C-00", "kind_priority"]
    assert judgment["evidence_candidate_keys"] == ["c01"]
    assert judgment["deterministic_judge"]["applied"] is True
    assert _trace["judge_source"] == "programmatic_caliber"
    assert _trace["table_claim_path"]["mode"] == "programmatic_table"
    assert judgment["authority"] == "programmatic_caliber"
    assert judgment["authority_closed"] is True
    assert judgment["bind_state"] == "unique"


def test_run_audit_judge_applies_derived_sum_without_llm(monkeypatch) -> None:
    calls = []

    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        calls.append(payload)
        raise AssertionError("derived sum should not call the judge model")

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    judgment, trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "reported_requirement": {"text": "总损耗：3.985", "unit": "kW"},
            "test_item": {"project_name": "总损耗"},
            "deterministic_comparisons": [{
                "source": "generic_derived_sum",
                "candidate_key": "c01",
                "evidence_candidate_keys": ["c01", "c02"],
                "kind": "upper_bound",
                "report_value": 3.985,
                "standard_value": 3.985,
                "tightness": "equal",
                "relation": "supports",
                "conclusion": "supports",
                "status": "supported",
                "target_column": "空载损耗P0(kW) + 负载损耗Pk(kW)",
                "unit_normalize": {"base": "kw", "left_unit": "kw", "right_unit": "kw"},
                "trace": {
                    "report_claim": {"property": {"source_text": "总损耗"}},
                    "evidence_claim": {
                        "property": {"source_text": "总损耗"},
                        "value": {
                            "kind": "quantity",
                            "raw": "3.985",
                            "normalized": "3.985",
                            "numbers": [3.985],
                            "unit": "kW",
                            "operator": "eq",
                        },
                    },
                },
            }],
            "table_claim_execution": {
                "mode": "programmatic_formula",
                "reason_code": "derived_sum_comparable",
                "status": "supported",
                "nodes": [],
            },
        },
        judge_model="deepseek-v4-flash",
        candidates=[_candidate("c01"), _candidate("c02")],
        sample_profile=_sample_profile_fixture(),
    )

    assert calls == []
    assert judgment["status"] == "supported"
    assert judgment["evidence_candidate_keys"] == ["c01", "c02"]
    assert judgment["kind"] == "formula_aggregate"
    assert judgment["deterministic_judge"]["mode"] == "programmatic_caliber"
    assert "P0" in judgment["reason"] or "加和" in judgment["reason"]
    assert trace["judge_source"] == "programmatic_caliber"
    assert trace["table_claim_path"]["mode"] == "programmatic_formula"
    assert judgment["authority"] == "programmatic_caliber"
    assert judgment["authority_closed"] is True
    assert judgment["bind_state"] == "unique"


def test_run_audit_judge_stays_open_when_no_table_claim(monkeypatch) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        raise AssertionError("the LLM judge is not part of the pipeline")

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    judgment, trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "reported_requirement": {"text": "冲击电压: 75 kV"},
            "test_item": {"project_name": "雷电冲击"},
            "deterministic_comparisons": [],
        },
        judge_model="deepseek-v4-flash",
        candidates=[_candidate("c01")],
        sample_profile=_sample_profile_fixture(),
    )

    assert judgment["status"] == "insufficient_context"
    assert trace["judge_source"] == "unbound"
    assert judgment["deterministic_judge"]["applied"] is False
    assert judgment["authority_closed"] is False


def test_run_audit_judge_keeps_agent_verdict_without_caliber_rejudge(monkeypatch) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        raise AssertionError("agent evidence is not an LLM judge call")

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)

    def fetch_agent_evidence():
        return {
            "ok": True,
            "result": {
                "verdict": "match",
                "kind": "exact",
                "standard_value": "75 kV",
                "standard_no": "GB/T 1094.3-2017",
                "reasoning": "表2 冲击电压 75 kV",
                "evidence": [{"chunk_id": "c01", "source": "GB/T 1094.3-2017", "location": "表2"}],
            },
        }

    judgment, trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "reported_requirement": {"text": "冲击电压: 75 kV", "unit": "kV"},
            "test_item": {"project_name": "雷电冲击"},
            "deterministic_comparisons": [],
        },
        judge_model="deepseek-v4-flash",
        candidates=[_candidate("c01")],
        sample_profile=_sample_profile_fixture(),
        fetch_agent_evidence=fetch_agent_evidence,
    )

    assert judgment["status"] == "supported"
    assert judgment["verdict"] == "match"
    assert judgment["kind"] == "exact"
    assert trace["judge_source"] == "agent"
    assert trace["table_claim_path"]["caliber"]["deferred_to_agent"] is True
    assert "agent_audit" in trace


def test_run_audit_judge_rules_qualitative_clause_out_of_scope_without_llm(
    monkeypatch,
) -> None:
    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        raise AssertionError("a qualitative clause must not reach the judge model")

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    judgment, trace = workflow._run_audit_judge_with_consistency(
        judge_prompt="judge",
        judge_input={
            "reported_requirement": {"text": "油箱及所有附件应无渗漏油现象", "unit": None},
            "test_item": {"project_name": "外观检查"},
            "deterministic_comparisons": [],
            "table_claim_execution": {"nodes": [], "attempts": []},
        },
        judge_model="deepseek-v4-flash",
        candidates=[_candidate("c01")],
        sample_profile=_sample_profile_fixture(),
    )

    assert judgment["status"] == "not_audited"
    assert judgment["verdict"] == "out_of_scope"
    assert judgment["caliber"]["rules"] == ["C-05"]
    assert judgment["evidence_candidate_keys"] == []
    assert trace["judge_source"] == "programmatic_caliber"
    assert trace["table_claim_path"]["caliber_prefilter"] is True
    assert judgment["authority"] == "programmatic_caliber"
    assert judgment["authority_closed"] is True


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
            "final_table": 8,
            "final_section": 6,
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
        final_table=8,
        final_section=6,
        special_route_reserve=3,
        rrf_k=40,
        similarity_threshold=0.3,
        aggregate_continuation_tables=False,
        expand_references=True,
        case_id="c09",
    )

    assert captured["query"] == "空载损耗限值"
    assert captured["kwargs"]["file_ids"] == ["standard"]
    assert captured["kwargs"]["query_routes"] == routes
    assert captured["kwargs"]["top_k"] == max(5, 8 + 6)
    assert captured["kwargs"]["route_top_k"] == 11
    assert captured["kwargs"]["candidates_per_type"] == 9
    assert captured["kwargs"]["final_table"] == 8
    assert captured["kwargs"]["final_section"] == 6
    assert captured["kwargs"]["special_route_reserve"] == 3
    assert captured["kwargs"]["rrf_k"] == 40
    assert captured["kwargs"]["similarity_threshold"] == 0.3
    assert captured["kwargs"]["aggregate_continuation_tables"] is False
    assert captured["kwargs"]["expand_references"] is True
    assert captured["kwargs"]["usage_context"].case_id == "c09"
    assert [item["chunk_id"] for item in candidates] == ["t1", "s1"]
    assert candidates[0]["content_type"] == "table"
    assert candidates[0]["route_scores"] == {"hybrid": 0.91}
    assert debug["retrieval_mode"] == "dual_rerank"
    assert debug["routes_injected"] is True
    assert debug["final_table"] == 8
    assert debug["final_section"] == 6


def test_workflow_has_no_fixed_applicability_lookup_hook() -> None:
    assert not hasattr(workflow, "_inject_applicability_candidates")


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
    # The queued runtime payload contains only inputs needed for full-report audit.
    assert set((job.get("result") or {})) == {
        "assistant_id",
        "report_file_id",
        "naming_rule_file_id",
        "run_id",
        "report_name",
        "report_path",
        "checkpoint_path",
    }
    assert job["max_attempts"] == jobs.AUDIT_JOB_MAX_ATTEMPTS
    assert job["result"]["run_id"] == job["id"]
    assert job["result"]["report_name"].endswith(f"{job['id']}.json")
    assert job["result"]["checkpoint_path"].endswith(".checkpoint.json")
    _close_temp_db(monkeypatch)


def test_collect_enabled_planner_queries_filters_routes() -> None:
    routes = [
        {"id": "semantic", "enabled": True, "label": "语义改写", "instruction": "s"},
        {"id": "keyword", "enabled": False, "label": "关键词", "instruction": "k"},
        {"id": "table_target", "enabled": True, "label": "表格定向", "instruction": "t"},
        {"id": "section_target", "enabled": True, "label": "章节定向", "instruction": "sec"},
    ]
    planned = {
        "semantic": "语义查询",
        "keyword": "关键词不应收录",
        "table_target": "",
        "section_target": "  章节查询  ",
        "extra": "忽略",
    }
    queries = workflow._collect_enabled_planner_queries(
        planned,
        query_planner_routes=routes,
        production_query="生产兜底",
    )
    assert queries == {
        "production": "生产兜底",
        "semantic": "语义查询",
        "section_target": "章节查询",
    }
    assert "keyword" not in queries
    assert "table_target" not in queries


def test_build_sample_profile_merges_report_and_decode() -> None:
    profile = workflow._build_sample_profile(
        {
            "model": "S20-M.RL-400/10-NX2",
            "rated_capacity": "400 kVA",
            "core_structure": "",
            "sealing_type": "",
        },
        {
            "raw_model": "S20-M.RL-400/10-NX2",
            "decoded_features": [
                {"segment": "RL", "meaning": "立体卷铁芯"},
                {"segment": "M", "meaning": "密封式"},
            ],
            "retrieval_terms": ["三相油浸式密封式立体卷铁芯变压器"],
            "unresolved_segments": [],
            "schema_fills": {
                "core_structure": "立体卷铁芯",
                "sealing_type": "密封式",
                "rated_capacity": "999 kVA",  # must not overwrite non-empty extract
                "not_a_schema_key": "x",
            },
        },
    )
    assert profile["from_report"]["rated_capacity"] == "400 kVA"
    assert profile["from_report"]["core_structure"] == "立体卷铁芯"
    assert profile["from_report"]["sealing_type"] == "密封式"
    assert "not_a_schema_key" not in profile["from_report"]
    assert profile["from_model_decode"]["feature_meanings"]["RL"] == "立体卷铁芯"
    assert profile["from_model_decode"]["feature_meanings"]["M"] == "密封式"
    assert profile["from_model_decode"]["retrieval_terms"] == [
        "三相油浸式密封式立体卷铁芯变压器"
    ]
    assert profile["from_model_decode"]["features"][0]["segment"] == "RL"
    assert set(profile["from_model_decode"]["filled_keys"]) == {
        "core_structure",
        "sealing_type",
    }


def test_decode_model_passes_empty_schema_fields_and_filters_fills(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_model(prompt: str, payload: dict, *, model: str, **_kwargs):
        captured["payload"] = payload
        return {
            "raw_model": "S20-M.RL-400/10-NX2",
            "decoded_features": [
                {"segment": "RL", "meaning": "立体卷铁芯", "evidence_quote": "RL"},
            ],
            "retrieval_terms": [],
            "unresolved_segments": [],
            "schema_fills": {
                "core_structure": "立体卷铁芯",
                "rated_capacity": "should_drop_already_filled",
                "unknown_key": "nope",
            },
        }

    monkeypatch.setattr(workflow, "_call_model", fake_call_model)
    out = workflow._decode_model(
        {
            "model": "S20-M.RL-400/10-NX2",
            "rated_capacity": "400 kVA",
            "core_structure": "",
        },
        "RL 立体卷铁芯",
        prompt="decode",
        model="deepseek-chat",
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [
                {"key": "model", "label": "型号", "required": True, "hint": ""},
                {"key": "rated_capacity", "label": "额定容量", "required": False, "hint": ""},
                {"key": "core_structure", "label": "铁芯结构", "required": False, "hint": ""},
            ],
        },
    )
    empty_keys = {
        item["key"] for item in captured["payload"]["empty_schema_fields"]  # type: ignore[index]
    }
    assert empty_keys == {"core_structure"}
    assert out["schema_fills"] == {"core_structure": "立体卷铁芯"}


def test_resolve_judge_concurrency_priority_and_bounds(monkeypatch) -> None:
    monkeypatch.delenv("AUDIT_JUDGE_CONCURRENCY", raising=False)
    assert workflow._resolve_judge_concurrency(None, {}) == workflow.DEFAULT_JUDGE_CONCURRENCY
    assert workflow._resolve_judge_concurrency(None, {"judge_concurrency": 12}) == 12
    assert workflow._resolve_judge_concurrency(3, {"judge_concurrency": 12}) == 3
    monkeypatch.setenv("AUDIT_JUDGE_CONCURRENCY", "6")
    assert workflow._resolve_judge_concurrency(None, {"judge_concurrency": 12}) == 6
    assert workflow._resolve_judge_concurrency(2, {"judge_concurrency": 12}) == 2
    assert (
        workflow._resolve_judge_concurrency(999, {})
        == workflow.MAX_JUDGE_CONCURRENCY
    )
    with pytest.raises(workflow.NonRetryableJobError, match=">= 1"):
        workflow._resolve_judge_concurrency(0, {})


def test_apply_judge_concurrency_cap_never_raises_a_lower_value() -> None:
    assert workflow._apply_judge_concurrency_cap(8, 5) == 5
    assert workflow._apply_judge_concurrency_cap(3, 5) == 3
    assert workflow._apply_judge_concurrency_cap(1, 5) == 1
    assert workflow._apply_judge_concurrency_cap(8, None) == 8
    with pytest.raises(workflow.NonRetryableJobError, match="judge_concurrency_cap"):
        workflow._apply_judge_concurrency_cap(8, 0)


def test_agent_verdict_maps_to_production_status() -> None:
    assert workflow.AGENT_VERDICT_TO_STATUS == {
        "match": "supported",
        "mismatch": "mismatch",
        "unevaluable": "insufficient_context",
        "out_of_scope": "not_audited",
    }


def _agent_unit() -> dict:
    return {
        "case_id": "item_1",
        "test_item": {
            "item_no": "5",
            "project_name": "空载损耗和空载电流测量",
            "phase": "initial",
        },
        "requirement": {"requirement_text": "空载损耗P0(kW):≤0.370", "unit": "kW"},
    }


def test_agent_sidecar_payload_binds_current_report() -> None:
    payload = workflow._agent_sidecar_payload(
        case_id="item_1",
        sample_context={"model": "S20"},
        test_item={"item_no": "5"},
        reported_requirement={"text": "空载损耗P0(kW):≤0.370"},
        evidence_file_ids=["std-1"],
        report_file_id="report-file",
        production_query="空载损耗",
        retrieved_candidates=[{"chunk_id": "c1"}],
    )
    assert payload["report_file_id"] == "report-file"
    assert payload["file_scope"] == ["std-1"]
    assert payload["retrieved_candidates"][0]["chunk_id"] == "c1"


def test_audit_one_case_agent_writes_compatible_judgment(monkeypatch) -> None:
    def fake_call(_url, payload, token="", timeout_seconds=480.0):
        assert payload["case_id"] == "item_1"
        assert payload["file_scope"] == ["std-1"]
        assert payload["report_file_id"] == "report-file"
        del token, timeout_seconds
        return {
            "ok": True,
            "parse_mode": "strict",
            "result": {
                "verdict": "match",
                "kind": "exact",
                "reasoning": "与表列限值一致",
                "standard_no": "GB/T 1094.1",
                "standard_value": "0.370",
                "reported_value": "0.370",
                "evidence": [
                    {
                        "source": "GB/T 1094.1",
                        "location": "表 6",
                        "text": "空载损耗 ≤0.370 kW",
                        "chunk_id": "c1",
                    }
                ],
            },
            "stats": {"tool_calls": 2, "turns": 3},
            "status": "supported",
            "trace_file": "traces/item_1.jsonl",
        }

    monkeypatch.setattr(workflow, "_call_agent_sidecar", fake_call)
    monkeypatch.setattr(workflow, "_load_chunk_for_evidence", lambda _cid: None)
    entry = workflow._audit_one_case_agent(
        _agent_unit(),
        sample_profile={"from_report": {"model": "S20-M.RL-400/10-NX2"}},
        evidence_file_ids=["std-1"],
        sidecar_url="http://127.0.0.1:8787",
        retries=0,
        report_file_id="report-file",
    )
    assert entry["judgment"]["status"] == "supported"
    assert entry["judgment"]["kind"] == "exact"
    assert entry["judgment"]["judge_source"] == "agent"
    assert entry["status_layer"]["authority"] == "model"
    assert entry["workflow_trace"]["agent_audit"]["output"]["ok"] is True
    evidence = entry["judgment"]["evidence"]
    assert evidence and evidence[0]["candidate_key"] == "a01"
    assert evidence[0]["locator"]["standard_no"] == "GB/T 1094.1"
    assert "≤0.370" in evidence[0]["text"]
    assert entry["judgment"]["evidence_candidate_keys"] == ["a01"]


def test_normalize_agent_evidence_hydrates_chunk(monkeypatch) -> None:
    monkeypatch.setattr(
        workflow,
        "_load_chunk_for_evidence",
        lambda _cid: {
            "id": "a" * 32,
            "page": 10,
            "text": "<table><tr><td>400</td><td>0.370</td></tr></table>",
            "business_metadata": {
                "standard_no": "Q/GDW 12126.4-2024",
                "content_type": "table",
                "table_no": "6",
                "table_title": "性能参数",
            },
            "source_trace": {"page_start": 10, "page_end": 10},
        },
    )
    items = workflow.normalize_agent_evidence(
        [f"Q/GDW 表6 p10 chunk_id={'a' * 32} 空载损耗 0.370"],
        standard_no="Q/GDW 12126.4-2024",
    )
    assert len(items) == 1
    assert items[0]["locator"]["table_no"] == "6"
    assert items[0]["locator"]["page_start"] == 10
    assert "<table>" in items[0]["text"]


def test_normalize_agent_evidence_keeps_string_excerpt_only_with_chunk_id(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_load_chunk_for_evidence", lambda _cid: None)
    items = workflow.normalize_agent_evidence(
        ["GB/T 6451-2023 第4.3.2条 表4 p12：线电阻不平衡率不应大于 2%"],
        standard_no="GB/T 6451-2023",
    )
    assert items == []


def test_normalize_agent_evidence_does_not_fill_citation_from_table_locator(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_load_chunk_for_evidence", lambda _cid: None)
    items = workflow.normalize_agent_evidence(
        [{
            "source": "Q/GDW 12126.4-2024",
            "location": "表30",
            "text": "其他分接: 匝数比设计值的±0.5 %",
        }],
        standard_no="Q/GDW 12126.4-2024",
    )
    assert items == []


def test_normalize_agent_evidence_drops_placeholder_chunk_id(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_load_chunk_for_evidence", lambda _cid: None)
    items = workflow.normalize_agent_evidence(
        [{
            "chunk_id": "7c549...placeholder",
            "source": "Q/GDW 12126.4-2024",
            "location": "表30",
            "text": "其他分接: 匝数比设计值的±0.5 %",
        }],
        standard_no="Q/GDW 12126.4-2024",
    )
    assert items == []


def test_judgment_from_agent_keeps_verdict_on_protocol_error_without_locator_fill(
    monkeypatch,
) -> None:
    monkeypatch.setattr(workflow, "_load_chunk_for_evidence", lambda _cid: None)
    judgment = workflow._judgment_from_agent_result(
        {
            "result": {
                "verdict": "mismatch",
                "kind": "looser",
                "reasoning": "表30 限值更严，但未给出已读 chunk_id",
                "standard_no": "Q/GDW 12126.4-2024",
                "evidence": [{
                    "source": "Q/GDW 12126.4-2024",
                    "location": "表30",
                    "text": "其他分接: 匝数比设计值的±0.5 %",
                }],
            },
            "stats": {"protocol_error": True},
        },
        reason_code="agent_retrieved",
    )
    assert judgment is not None
    assert judgment["verdict"] == "mismatch"
    assert judgment["status"] == "mismatch"
    assert judgment["kind"] == "looser"
    assert judgment["protocol_error"] is True
    assert judgment["evidence"] == []
    assert judgment["evidence_candidate_keys"] == []


def test_audit_one_case_agent_degrades_on_sidecar_failure(monkeypatch) -> None:
    def fake_call(*_args, **_kwargs):
        raise RuntimeError("sidecar 5xx")

    monkeypatch.setattr(workflow, "_call_agent_sidecar", fake_call)
    entry = workflow._audit_one_case_agent(
        _agent_unit(),
        sample_profile={"from_report": {"model": "S20"}},
        evidence_file_ids=[],
        sidecar_url="http://127.0.0.1:8787",
        retries=0,
    )
    assert entry["judgment"]["status"] == "insufficient_context"
    assert entry["judgment"]["judge_source"] == "agent_error"
    assert "sidecar 5xx" in entry["judgment"]["reason"]
