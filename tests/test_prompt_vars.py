from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.audit_judge_notes import looks_like_full_audit_judge_prompt
from app.prompt_vars import (
    AUDIT_PIPELINE_SCENARIO,
    EMPTY_VALUE,
    NODE_STEP_TASKS,
    build_prompt_var_context,
    build_runtime_prompt_segments,
    compose_runtime_prompt,
    expand_prompt_template,
    format_audit_judge_brief,
    format_extraction_brief,
    format_manual_rules,
    format_node_framework_preamble,
    format_parameter_schema,
    format_query_planner_brief,
)
from app.query_planner_routes import (
    default_query_planner_routes,
    enabled_query_planner_route_ids,
    looks_like_full_query_planner_prompt,
    resolve_query_planner_routes,
)


def test_format_parameter_schema_lists_fields() -> None:
    text = format_parameter_schema(
        {
            "version": 1,
            "allow_extra": True,
            "fields": [
                {"key": "model", "label": "型号", "required": True, "hint": "铭牌"},
                {"key": "rated_voltage", "label": "额定电压", "required": False},
            ],
        }
    )
    assert "允许额外字段：是" in text
    assert "- model（型号，审查关键）：铭牌" in text
    assert "- rated_voltage（额定电压，常规）" in text


def test_format_manual_rules_and_empty() -> None:
    assert format_manual_rules({}) == EMPTY_VALUE
    text = format_manual_rules(
        {
            "scope": "knowledge_base_manual_rules",
            "rules": [
                {"rule_id": "total_loss", "rule_text": "P总 = P0 + Pk"},
                {"rule_id": "skip", "rule_text": "  "},
            ],
        }
    )
    assert text == "- total_loss: P总 = P0 + Pk"


def test_expand_replaces_known_keeps_unknown() -> None:
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        manual_rules={"rules": [{"rule_id": "r1", "rule_text": "规则正文"}]},
        kb_name="油浸式变压器",
        kb_description="",
    )
    template = "库：{{kb_name}}\n未知：{{not_a_real_var}}"
    out = expand_prompt_template(template, context)
    assert "库：油浸式变压器" in out
    assert "{{not_a_real_var}}" in out


def test_report_parameters_rich_brief_fields_once() -> None:
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": True,
            "fields": [
                {"key": "model", "label": "型号", "required": True, "hint": "首页型号"},
                {"key": "rated_capacity", "label": "额定容量", "required": False},
            ],
        },
        kb_name="电缆",
        kb_description="打到",
    )
    out = compose_runtime_prompt("", step_id="report_parameters", context=context)
    assert out.startswith("任务场景：")
    assert AUDIT_PIPELINE_SCENARIO in out
    assert "本步任务：" in out
    assert "提取报告参数" in out
    assert out.count("- model（") == 1
    assert '"key": "model"' in out
    assert '"key": "rated_capacity"' in out
    assert "字段key" not in out
    assert "提取规则：" in out
    assert "保留原始完整表达" in out
    assert "不得作为报告级参数" in out
    assert "知识库：电缆" in out
    assert "首页型号" in out
    assert "## 当前参数字段" not in out


def test_category_notes_fold_into_single_brief() -> None:
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
    )
    notes = "- 冷却方式未记载时不得从型号臆测。"
    out = compose_runtime_prompt(notes, step_id="report_parameters", context=context)
    assert "品类约束与易混淆：" in out
    assert "不得从型号臆测" in out
    assert out.count("- model（") == 1
    assert not out.startswith("补充说明：")
    # Still one contiguous document.
    assert "## " not in out


def test_audit_judge_brief_single_block_with_rules_summary() -> None:
    context = build_prompt_var_context(
        manual_rules={"rules": [{"rule_id": "r1", "rule_text": "总损耗 = 空载 + 负载"}]},
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
    )
    out = compose_runtime_prompt("", step_id="audit_judge", context=context)
    assert out.startswith("任务场景：")
    assert AUDIT_PIPELINE_SCENARIO in out
    assert "本步任务：" in out
    assert "对照标准判定" in out
    assert "`r1`" in out
    assert "总损耗 = 空载 + 负载" in out
    assert "## 补充规则" not in out
    assert "## 当前参数字段" not in out
    assert "必须出现在输出的 parameters" not in out
    assert "- model（" not in out
    assert '"status"' in out
    assert "insufficient_context" in out


def test_audit_judge_compose_drops_nested_full_judge_notes() -> None:
    full = Path(__file__).resolve().parents[1] / (
        "evaluation/prompts/generic/standard_value_audit_judge_generic_v1.md"
    )
    notes = full.read_text(encoding="utf-8")
    assert looks_like_full_audit_judge_prompt(notes)
    context = build_prompt_var_context(kb_name="库A")
    out = compose_runtime_prompt(notes, step_id="audit_judge", context=context)
    assert "品类约束：" not in out
    assert out.count("supported：") == 1
    brief = format_audit_judge_brief(kb_name="库A", notes=notes)
    assert "品类约束：" not in brief


def test_segments_for_report_parameters() -> None:
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
    )
    segments = build_runtime_prompt_segments(
        "注意分接。",
        step_id="report_parameters",
        context=context,
    )
    assert len(segments) == 1
    assert segments[0]["kind"] == "injected"
    assert segments[0]["key"] == "extraction_brief"
    assert "注意分接" in segments[0]["text"]


def test_format_extraction_brief_with_notes() -> None:
    text = format_extraction_brief(
        {
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
        notes="- 易混淆说明",
    )
    assert "品类约束与易混淆：" in text
    assert "- 易混淆说明" in text
    assert text.index("品类约束与易混淆：") < text.index("\n字段：\n")


def test_step_rules_fold_into_briefs_and_notes_only_steps() -> None:
    assistant_rules = {
        "step_rules": {
            "report_parameters": {
                "rules": [{"rule_id": "cool", "rule_text": "冷却方式未记载时不得臆测"}],
            },
            "query_planner": {
                "rules": [{"rule_id": "tbl", "rule_text": "优先区分能效表与性能表"}],
            },
            "test_items": {
                "rules": [{"rule_id": "cont", "rule_text": "跨页续表继承项目名"}],
            },
            "model_decode": {
                "rules": [{"rule_id": "unk", "rule_text": "无法确认放入 unresolved"}],
            },
        }
    }
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
        query_planner_routes=default_query_planner_routes(),
        assistant_rules=assistant_rules,
    )
    extraction = compose_runtime_prompt("", step_id="report_parameters", context=context)
    assert "补充规则（人工配置）：" in extraction
    assert "- cool: 冷却方式未记载时不得臆测" in extraction

    planner = compose_runtime_prompt("", step_id="query_planner", context=context)
    assert "- tbl: 优先区分能效表与性能表" in planner

    test_items = compose_runtime_prompt("legacy notes", step_id="test_items", context=context)
    assert "补充规则（人工配置）：" in test_items
    assert "- cont: 跨页续表继承项目名" in test_items
    assert "legacy notes" not in test_items

    decode = compose_runtime_prompt("", step_id="model_decode", context=context)
    assert "- unk: 无法确认放入 unresolved" in decode


def test_query_planner_brief_only_enabled_routes() -> None:
    routes = default_query_planner_routes()
    for item in routes:
        item["enabled"] = item["id"] in {"semantic", "keyword"}
    text = format_query_planner_brief(
        routes=routes,
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
    )
    assert "`semantic`" in text
    assert "`keyword`" in text
    assert "table_target" not in text
    assert "section_target" not in text
    assert '"semantic"' in text
    assert '"keyword"' in text
    assert "样品/报告参数字段" not in text
    assert "- model（" not in text
    assert "sample_context" in text
    assert "每条启用改写形式只输出一条 Query" in text
    assert text.startswith("任务场景：")
    assert AUDIT_PIPELINE_SCENARIO in text
    assert "本步任务：" in text
    assert "规划检索问题" in text
    assert "若无型号解码则忽略解码相关约束" in text


def test_ai_nodes_share_pipeline_scenario_preamble() -> None:
    context = build_prompt_var_context(kb_name="库A")
    for step_id in (
        "report_parameters",
        "test_items",
        "model_decode",
        "query_planner",
        "audit_judge",
    ):
        out = compose_runtime_prompt("节点细则正文。", step_id=step_id, context=context)
        assert out.startswith("任务场景："), step_id
        assert AUDIT_PIPELINE_SCENARIO in out, step_id
        assert "本步任务：" in out, step_id
        assert NODE_STEP_TASKS[step_id] in out, step_id
        assert "对照知识库中的标准证据" in out, step_id
    preamble = format_node_framework_preamble("test_items")
    assert preamble.startswith("任务场景：")
    assert "提取检测项目" in preamble
    decode = format_node_framework_preamble("model_decode")
    assert "解析型号规则" in decode


def test_query_planner_compose_drops_nested_full_planner_notes() -> None:
    full = Path(__file__).resolve().parents[1] / (
        "evaluation/prompts/retrieval_query_planner_v1.md"
    )
    notes = full.read_text(encoding="utf-8")
    assert looks_like_full_query_planner_prompt(notes)
    context = build_prompt_var_context(kb_name="库A")
    out = compose_runtime_prompt(notes, step_id="query_planner", context=context)
    assert "品类约束：" not in out
    assert "是否正确的标准规则" not in out
    assert out.count("生成以下检索表达") == 1


def test_query_planner_compose_drops_disabled_route_instruction() -> None:
    routes = default_query_planner_routes()
    for item in routes:
        if item["id"] == "table_target":
            item["enabled"] = False
            item["instruction"] = "UNIQUE_TABLE_INSTRUCTION_MARKER"
        else:
            item["enabled"] = True
    context = build_prompt_var_context(
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [{"key": "model", "label": "型号", "required": True}],
        },
        kb_name="库A",
        query_planner_routes=routes,
    )
    out = compose_runtime_prompt("", step_id="query_planner", context=context)
    assert "UNIQUE_TABLE_INSTRUCTION_MARKER" not in out
    assert "`semantic`" in out
    assert "table_target" not in out


def test_looks_like_full_audit_judge_prompt_legacy_statuses() -> None:
    sample = (
        "判断报告填写的标准要求是否被候选标准 Chunk 支持。不得用常识补充标准值。\n"
        "状态：\n"
        "- correct：直接证据支持报告要求；\n"
        "- incorrect：直接证据给出冲突数值；\n"
        "- insufficient_context：缺少参数；\n"
        "- evidence_not_found：没有足够证据。\n"
        '严格输出 JSON：{"status":"","evidence_candidate_keys":[]}\n'
    )
    assert looks_like_full_audit_judge_prompt(sample)
    assert not looks_like_full_audit_judge_prompt(
        "产品专用表匹配样品后勿再要求额外适用性证明。"
    )


def test_looks_like_full_query_planner_prompt() -> None:
    full = Path(__file__).resolve().parents[1] / (
        "evaluation/prompts/generic/retrieval_query_planner_generic_v1.md"
    )
    assert looks_like_full_query_planner_prompt(full.read_text(encoding="utf-8"))
    assert not looks_like_full_query_planner_prompt(
        "table_target 优先区分：能效限值表 / 产品性能参数表。"
    )
    assert not looks_like_full_query_planner_prompt("")


def test_resolve_query_planner_routes_keeps_one_enabled() -> None:
    routes = resolve_query_planner_routes(
        [
            {"id": "semantic", "enabled": False},
            {"id": "keyword", "enabled": False},
            {"id": "table_target", "enabled": False},
            {"id": "section_target", "enabled": False},
        ]
    )
    assert any(item["enabled"] for item in routes)
    assert enabled_query_planner_route_ids(routes) == ["semantic"]
