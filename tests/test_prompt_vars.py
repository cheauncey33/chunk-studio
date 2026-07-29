from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.audit_judge_notes import looks_like_full_audit_judge_prompt
from app.model_decode_notes import looks_like_full_model_decode_prompt
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
from app.test_items_notes import looks_like_full_test_items_prompt


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
    assert "## 判定约定" not in out
    assert "## 当前参数字段" not in out
    assert "必须出现在输出的 parameters" not in out
    assert "- model（" not in out
    assert '"status"' in out
    assert "insufficient_context" in out
    assert "判定流程（必须按顺序执行；完成前不得给出最终 status）：" in out
    assert "技术规范书 > 企/行标 > 国标" in out
    assert "standard_priority" in out
    assert "不得仅因“等于限值”判定 mismatch。" in out
    assert "禁止自我修正或元评论" in out
    assert "文字/条款与公式细则：" in out
    assert "sample_profile" in out
    assert "missing_context_fields 不得列入 sample_profile 中已给出的信息" in out
    assert "sample_context：" not in out


def test_audit_judge_brief_requires_numeric_limit_checklist() -> None:
    brief = format_audit_judge_brief(kb_name="库A")
    assert "数值限值强制核对" in brief
    assert "比较符/方向" in brief
    assert "松紧比较" in brief
    assert "标准 ≤0.370、报告 ≤0.500 → mismatch" in brief
    assert "标准 ≤55、报告 ≥55" in brief
    assert "不得跳过核对直接凭语感判定 supported" in brief


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
    assert "本步补充规则（人工配置）：" in extraction
    assert "- cool: 冷却方式未记载时不得臆测" in extraction

    planner = compose_runtime_prompt("", step_id="query_planner", context=context)
    assert "- tbl: 优先区分能效表与性能表" in planner

    test_items = compose_runtime_prompt("legacy notes", step_id="test_items", context=context)
    assert "本步补充规则（人工配置）：" in test_items
    assert "- cont: 跨页续表继承项目名" in test_items
    assert "legacy notes" not in test_items

    decode = compose_runtime_prompt("", step_id="model_decode", context=context)
    assert "- unk: 无法确认放入 unresolved" in decode


def test_test_items_model_decode_ignore_legacy_notes_without_step_rules() -> None:
    context = build_prompt_var_context(kb_name="库A")
    # Drop structured keys to simulate older callers; notes must still be ignored.
    context.pop("step_rules_test_items", None)
    context.pop("step_rules_model_decode", None)
    legacy = "# 通用检测报告项目提取 v1\n你是检测报告结构化提取器。"
    out = compose_runtime_prompt(legacy, step_id="test_items", context=context)
    assert "任务场景：" in out
    assert "本步补充规则（人工配置）：" in out
    assert legacy not in out
    decode = compose_runtime_prompt(
        "只依据报告原始型号解析型号。严格输出 JSON：",
        step_id="model_decode",
        context=context,
    )
    assert "只依据报告原始型号解析型号" not in decode


def test_test_items_and_model_decode_prompts_include_json_for_deepseek() -> None:
    """DeepSeek json_object requires the word 'json' somewhere in the prompt."""
    context = build_prompt_var_context(
        kb_name="库A",
        parameter_schema={
            "version": 1,
            "allow_extra": False,
            "fields": [
                {"key": "model", "label": "型号", "required": True},
                {"key": "core_structure", "label": "铁芯结构", "required": False},
            ],
        },
    )
    for step_id in ("test_items", "model_decode"):
        out = compose_runtime_prompt("", step_id=step_id, context=context)
        assert "json" in out.lower(), step_id
        assert "严格输出 JSON" in out, step_id
        if step_id == "test_items":
            assert '"items"' in out
        else:
            assert '"decoded_features"' in out
            assert '"schema_fills"' in out
            assert "empty_schema_fields" in out
            assert "当前参数字段" in out
            assert "core_structure" in out


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
    assert "sample_profile" in text
    assert "每条启用改写形式只输出一条 Query" in text
    assert text.startswith("任务场景：")
    assert AUDIT_PIPELINE_SCENARIO in text
    assert "本步任务：" in text
    assert "规划检索问题" in text
    assert "若 from_model_decode 为空则忽略解码相关约束" in text


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


def test_looks_like_full_test_items_and_model_decode_prompts() -> None:
    root = Path(__file__).resolve().parents[1]
    test_items = (
        root / "evaluation/prompts/generic/report_test_item_extraction_generic_v1.md"
    ).read_text(encoding="utf-8")
    model_decode = (
        root / "evaluation/prompts/generic/model_naming_decode_generic_v1.md"
    ).read_text(encoding="utf-8")
    assert looks_like_full_test_items_prompt(test_items)
    assert looks_like_full_model_decode_prompt(model_decode)
    assert not looks_like_full_test_items_prompt("跨页续表继承最近项目名。")
    assert not looks_like_full_model_decode_prompt("无法确认的片段放入 unresolved。")


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
