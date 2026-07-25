"""System-managed prompt context injection and optional {{placeholder}} expand."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from .audit_judge_notes import looks_like_full_audit_judge_prompt
from .parameter_schema import resolve_parameter_schema
from .query_planner_routes import (
    looks_like_full_query_planner_prompt,
    resolve_query_planner_routes,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

EMPTY_VALUE = "（未配置）"

KNOWN_VARS = (
    "parameter_schema",
    "parameter_schema_json",
    "manual_rules",
    "kb_name",
    "kb_description",
    "kb_context",
    "extraction_brief",
    "query_planner_brief",
    "audit_judge_brief",
)

# Auto-append order per workflow step. Users edit static instructions only;
# the runtime system prompt always receives these labeled blocks.
STEP_AUTO_INJECT: dict[str, tuple[str, ...]] = {
    # One block only: role + kb + fields + output (no parallel field catalogs).
    "report_parameters": ("extraction_brief",),
    "test_items": ("kb_context",),
    "model_decode": ("kb_context",),
    "query_planner": ("query_planner_brief",),
    "audit_judge": ("audit_judge_brief",),
}

INJECT_TITLES: dict[str, str] = {
    "parameter_schema": "当前参数字段（系统注入）",
    "parameter_schema_json": "参数 schema JSON（系统注入）",
    "manual_rules": "判定约定（系统注入）",
    "kb_name": "知识库名称（系统注入）",
    "kb_description": "知识库说明（系统注入）",
    "kb_context": "绑定知识库（系统注入）",
    "extraction_brief": "抽参说明（系统生成）",
    "query_planner_brief": "检索规划说明（系统生成）",
    "audit_judge_brief": "审查判定说明（系统生成）",
}

# Injected keys that are already a full prompt section (skip ## title wrapper).
FULL_PROMPT_INJECT_KEYS = frozenset(
    {"extraction_brief", "query_planner_brief", "audit_judge_brief"}
)

# Shared across all AI workflow nodes (framework only; not a variable).
AUDIT_PIPELINE_SCENARIO = (
    "本系统对照知识库中的标准证据，审查检测报告里填写的标准要求是否成立。"
    "完整流水线为：提取报告参数 → 提取检测项目与标准要求 → 解析型号规则（可选）→ "
    "规划检索问题 → 查找候选证据 → 对照标准判定 → 汇总结果。"
    "总原则：只依据报告原文与检索到的标准 Chunk；不得用行业常识补全标准值或适用条件；"
    "上游步骤产出（样品参数、型号解码等）仅作上下文或检索锚点，不是已被标准证明的事实。"
)

NODE_STEP_TASKS: dict[str, str] = {
    "report_parameters": (
        "你是流水线中的「提取报告参数」节点。"
        "根据输入的报告 Markdown 与字段表，只提取样品/报告级参数，供后续适用性判断与检索锚点使用。"
        "不使用行业常识补全，不编造报告未记载的内容；"
        "检测结果、实测值与符合性结论不属于本步输出。"
    ),
    "test_items": (
        "你是流水线中的「提取检测项目」节点。"
        "从报告的检测结果汇总（含跨页续表）中提取实际检测项目及其报告标准要求，"
        "供后续逐条检索与判定。"
        "样品上下文可写入自由键值；不得把检测结果/结论当作标准要求，"
        "也不得把检测项目误放入报告级参数。"
        "本步须以 JSON 对象输出结构化结果。"
    ),
    "model_decode": (
        "你是流水线中的「解析型号规则」节点。"
        "仅依据报告原始型号、已提取参数与输入的型号命名规则 Markdown 解析型号特征，产出检索用语。"
        "解析结果只服务后续检索改写，不是审查证据；不得使用行业常识补全，不得生成标准限值。"
        "本步须以 JSON 对象输出结构化结果。"
    ),
    "query_planner": (
        "你是流水线中的「规划检索问题」节点。"
        "针对输入中的单条报告标准要求，结合已提取的样品上下文（sample_context）"
        "与可选的型号解码结果，生成多路检索 Query，供下一步在标准知识库中查找候选证据。"
        "sample_context 与解码结果仅作检索锚点；本步不输出审查判定，也不输出 parameters。"
    ),
    "audit_judge": (
        "你是流水线中的「对照标准判定」节点。"
        "判断该条报告标准要求是否被候选标准 Chunk 支持。"
        "只能使用输入中的报告事实、样品上下文、型号解析结果、peer_report_context、"
        "manual_knowledge_rules、few_shot_examples 与候选 Chunk；不得用常识补充标准值。"
        "按四态 status（supported / mismatch / insufficient_context / not_audited）输出结论。"
    ),
}


def format_node_framework_preamble(step_id: str) -> str:
    """Shared pipeline scene + this node's task (framework header)."""
    task = NODE_STEP_TASKS.get(step_id, "").strip()
    if not task:
        return f"任务场景：\n{AUDIT_PIPELINE_SCENARIO}"
    return f"任务场景：\n{AUDIT_PIPELINE_SCENARIO}\n\n本步任务：\n{task}"


# Output contracts for notes-only AI steps (framework-owned; DeepSeek json_object
# requires the word "json" to appear in the prompt).
NODE_JSON_OUTPUT_CONTRACTS: dict[str, str] = {
    "test_items": (
        "输出：\n"
        "严格输出 JSON 对象，不要输出 Markdown 代码块：\n"
        "{\n"
        '  "report_id": "输入提供的report_id",\n'
        '  "sample_context": {\n'
        '    "sample_name": "报告原文或空字符串",\n'
        '    "model": "报告原文或空字符串"\n'
        "  },\n"
        '  "items": [\n'
        "    {\n"
        '      "item_no": "报告中的编号",\n'
        '      "project_name": "检测项目原文",\n'
        '      "phase": "initial或repeat_routine",\n'
        '      "requirements": [\n'
        "        {\n"
        '          "requirement_text": "字段名与报告标准值组成的完整原文要求",\n'
        '          "unit": "单位"\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    ),
    "model_decode": (
        "输出：\n"
        "严格输出 JSON 对象，不要输出 Markdown 代码块：\n"
        "{\n"
        '  "raw_model": "",\n'
        '  "decoded_features": [\n'
        '    {"segment": "", "meaning": "", "evidence_quote": ""}\n'
        "  ],\n"
        '  "retrieval_terms": [],\n'
        '  "unresolved_segments": []\n'
        "}"
    ),
}


def format_node_json_output_contract(step_id: str) -> str:
    """Return the JSON output contract for notes-only steps, or empty."""
    return NODE_JSON_OUTPUT_CONTRACTS.get(step_id, "").strip()


def _format_parameter_schema_field_lines(schema: Any) -> list[str]:
    resolved = resolve_parameter_schema(schema)
    fields = resolved.get("fields") or []
    lines: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        label = str(field.get("label") or key).strip() or key
        importance = "审查关键" if field.get("required") else "常规"
        hint = str(field.get("hint") or "").strip()
        line = f"- {key}（{label}，{importance}）"
        if hint:
            line = f"{line}：{hint}"
        lines.append(line)
    return lines


def format_parameter_schema(schema: Any) -> str:
    """Human-readable field list for parameter_schema injection (extraction steps)."""
    resolved = resolve_parameter_schema(schema)
    fields = resolved.get("fields") or []
    if not fields:
        return EMPTY_VALUE
    allow_extra = bool(resolved.get("allow_extra"))
    field_lines = _format_parameter_schema_field_lines(schema)
    if not field_lines:
        return EMPTY_VALUE
    lines: list[str] = [
        "下列每个 key 都必须出现在输出的 parameters 中；报告中找不到时 value 与 unit 均为空字符串。",
        "「审查关键」表示该字段对后续审查重要，不是允许省略该 key。",
        f"允许额外字段：{'是（可追加 schema 未声明、但须来自原文的报告级参数）' if allow_extra else '否（只提取下列已声明字段）'}",
        "字段：",
        *field_lines,
    ]
    return "\n".join(lines)


def format_parameter_schema_json(schema: Any) -> str:
    """Indented JSON for parameter_schema_json."""
    resolved = resolve_parameter_schema(schema)
    if not (resolved.get("fields") or []):
        return EMPTY_VALUE
    return json.dumps(resolved, ensure_ascii=False, indent=2)


def format_manual_rules(rules_payload: Any) -> str:
    """One line per rule for manual_rules injection."""
    if not isinstance(rules_payload, dict):
        return EMPTY_VALUE
    rules = rules_payload.get("rules")
    if not isinstance(rules, list) or not rules:
        return EMPTY_VALUE
    lines: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        text = str(rule.get("rule_text") or "").strip()
        if not text:
            continue
        rule_id = str(rule.get("rule_id") or "").strip() or "rule"
        lines.append(f"- {rule_id}: {text}")
    return "\n".join(lines) if lines else EMPTY_VALUE


def extract_step_rules(version_rules: Any, step_id: str) -> dict[str, Any]:
    """Read human-added rules for one workflow step from assistant_versions.rules."""
    if not isinstance(version_rules, dict):
        return {"rules": []}
    step_rules = version_rules.get("step_rules")
    if not isinstance(step_rules, dict):
        return {"rules": []}
    payload = step_rules.get(step_id)
    if not isinstance(payload, dict):
        return {"rules": []}
    rules = payload.get("rules")
    if not isinstance(rules, list):
        return {"rules": []}
    return {**payload, "rules": rules}


def format_step_rules_section(rules_payload: Any) -> list[str]:
    """Framework label + amber-style variable body lines for per-step human rules."""
    body = format_manual_rules(rules_payload)
    if body == EMPTY_VALUE:
        body = "（未配置）"
    return ["", "本步补充规则（人工配置）：", body]


def format_kb_context(kb_name: str | None, kb_description: str | None) -> str:
    name = str(kb_name or "").strip() or EMPTY_VALUE
    description = str(kb_description or "").strip() or EMPTY_VALUE
    return f"名称：{name}\n说明：{description}"


def format_extraction_brief(
    schema: Any,
    *,
    kb_name: str | None = None,
    kb_description: str | None = None,
    notes: str | None = None,
    step_rules: Any = None,
) -> str:
    """Single report_parameters system prompt body (fields appear once)."""
    resolved = resolve_parameter_schema(schema)
    fields = resolved.get("fields") or []
    allow_extra = bool(resolved.get("allow_extra"))
    name = str(kb_name or "").strip() or EMPTY_VALUE
    description = str(kb_description or "").strip() or EMPTY_VALUE
    notes_text = str(notes or "").strip()

    field_lines: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        label = str(field.get("label") or key).strip() or key
        importance = "审查关键" if field.get("required") else "常规"
        hint = str(field.get("hint") or "").strip()
        line = f"- {key}（{label}，{importance}）"
        if hint:
            line = f"{line}：{hint}"
        field_lines.append(line)
    if not field_lines:
        field_lines.append(f"- （{EMPTY_VALUE}）")

    example_items: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        example_items.append(
            f'    {{"key": "{key}", "value": "", "unit": ""}}'
        )
    if not example_items:
        example_items.append('    {"key": "", "value": "", "unit": ""}')
    example_json = "{\n  \"parameters\": [\n" + ",\n".join(example_items) + "\n  ]\n}"

    extra_line = (
        "允许额外字段：是；可追加 schema 未声明、但对后续审查有用的报告级参数（仍须来自原文，不得把检测结果当作参数）。"
        if allow_extra
        else "允许额外字段：否；只提取下列已声明字段，不得自行增补 key。"
    )

    parts: list[str] = [
        format_node_framework_preamble("report_parameters"),
        f"知识库：{name}" + (f"。{description}" if description != EMPTY_VALUE else "。"),
        "",
        "提取规则：",
        "1. 只提取报告直接记载的样品/报告级参数；找不到时该字段 value 与 unit 均为空字符串，但 key 仍须出现在输出中。",
        "2. 保留原始完整表达，不拆分、不改写单位与符号（如 10/0.4kV、Dyn11、LI75 AC35）。",
        "3. 短路阻抗、空载/负载损耗、空载电流、温升、绝缘电阻、电压比偏差、耐压试验电压与时长、"
        "声级、以及任何检测项目的符合/不符合结论，一律不得作为报告级参数。",
        f"4. {extra_line}",
        "5. 「审查关键」表示该字段对后续审查重要，不是允许省略该 key。",
    ]
    parts.extend(format_step_rules_section(step_rules))
    if notes_text:
        parts.extend(["", "品类约束与易混淆：", notes_text])
    parts.extend(
        [
            "",
            "字段：",
            *field_lines,
            "",
            "输出：",
            "严格输出 JSON 对象，不要输出 Markdown 代码块。须包含上表全部 key，示例如下：",
            example_json,
            "找不到的字段仍保留对应 key，value/unit 为空字符串；"
            + (
                "若允许额外字段，可在 parameters 末尾追加，key 用简短英文或拼音 snake_case。"
                if allow_extra
                else "不得增加上表以外的 key。"
            ),
        ]
    )
    return "\n".join(parts)


def format_query_planner_brief(
    *,
    routes: Any = None,
    parameter_schema: Any = None,
    kb_name: str | None = None,
    kb_description: str | None = None,
    notes: str | None = None,
    step_rules: Any = None,
) -> str:
    """Single query_planner system prompt (enabled routes only)."""
    resolved_routes = resolve_query_planner_routes(routes)
    enabled = [item for item in resolved_routes if item.get("enabled")]
    if not enabled:
        enabled = [resolved_routes[0]]

    name = str(kb_name or "").strip() or EMPTY_VALUE
    description = str(kb_description or "").strip() or EMPTY_VALUE
    # Full legacy Planner prompts must not be folded back as "品类约束".
    notes_text = str(notes or "").strip()
    if looks_like_full_query_planner_prompt(notes_text):
        notes_text = ""
    # parameter_schema is intentionally not listed here: runtime user JSON already
    # carries extracted sample_context values as retrieval anchors.

    route_lines = [
        f"- `{item['id']}`（{item['label']}）：{item['instruction']}"
        for item in enabled
    ]
    example_obj = {item["id"]: "..." for item in enabled}
    # table/section targets may be empty strings
    for item in enabled:
        if item["id"] in {"table_target", "section_target"}:
            example_obj[item["id"]] = "...或空字符串"
    example_json = json.dumps(example_obj, ensure_ascii=False, indent=2)

    parts: list[str] = [
        format_node_framework_preamble("query_planner"),
        f"知识库：{name}" + (f"。{description}" if description != EMPTY_VALUE else "。"),
        "",
        "生成以下检索表达（仅包含已启用的改写形式）：",
        *route_lines,
        "",
        "约束：",
        "1. 只能使用输入提供的报告事实、样品上下文和型号解码结果；若无型号解码则忽略解码相关约束。",
        "2. 不得把报告标准值当成已经被标准文件证明的事实；可以保留报告声称值作为检索锚点，"
        "但表达应是“寻找支持或核验该值的证据”。",
        "3. 不得加入输入中没有出现、且（在有解码时）不能由型号解码结果支持的产品条件。",
        "4. 型号无法解释时保留原始型号，不得凭行业常识展开。",
        "5. 每条启用改写形式只输出一条 Query。",
        "6. 严格输出 JSON 对象，不要输出 Markdown 代码块。",
    ]
    parts.extend(format_step_rules_section(step_rules))
    if notes_text:
        parts.extend(["", "品类约束：", notes_text])
    parts.extend(
        [
            "",
            "输出：",
            "严格输出 JSON 对象，须包含下列已启用 key：",
            example_json,
        ]
    )
    return "\n".join(parts)


def merge_notes_into_query_planner_brief(brief: str, notes: str) -> str:
    """Fold optional notes into the planner brief before the output section."""
    notes_text = (notes or "").strip()
    base = (brief or "").rstrip()
    if not notes_text or looks_like_full_query_planner_prompt(notes_text):
        return base
    if "品类约束：" in base:
        return base
    marker = "\n输出：\n"
    block = f"\n品类约束：\n{notes_text}\n"
    if marker in base:
        head, tail = base.split(marker, 1)
        return f"{head.rstrip()}\n{block}\n输出：\n{tail}".strip()
    return f"{base}\n{block}".strip()


def _manual_rules_summary_lines(manual_rules: Any) -> list[str]:
    if not isinstance(manual_rules, dict):
        return []
    rules = manual_rules.get("rules")
    if not isinstance(rules, list):
        return []
    lines: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or "").strip() or "rule"
        text = str(rule.get("rule_text") or "").strip().replace("\n", " ")
        if not text:
            continue
        snippet = text if len(text) <= 72 else f"{text[:72]}…"
        lines.append(f"- `{rule_id}`：{snippet}")
    return lines


def format_audit_judge_brief(
    *,
    manual_rules: Any = None,
    kb_name: str | None = None,
    kb_description: str | None = None,
    notes: str | None = None,
) -> str:
    """Single audit_judge system prompt (contracts only; rule bodies in user JSON)."""
    name = str(kb_name or "").strip() or EMPTY_VALUE
    description = str(kb_description or "").strip() or EMPTY_VALUE
    notes_text = str(notes or "").strip()
    if looks_like_full_audit_judge_prompt(notes_text):
        notes_text = ""

    rule_lines = _manual_rules_summary_lines(manual_rules)
    example_json = json.dumps(
        {
            "status": "supported|mismatch|insufficient_context|not_audited",
            "reason": "",
            "evidence_candidate_keys": [],
            "missing_context_fields": [],
        },
        ensure_ascii=False,
        indent=2,
    )

    parts: list[str] = [
        format_node_framework_preamble("audit_judge"),
        f"知识库：{name}" + (f"。{description}" if description != EMPTY_VALUE else "。"),
        "",
        "状态：",
        "- supported：直接证据支持报告要求；",
        "- mismatch：直接证据给出冲突数值、公式或适用条件；",
        "- insufficient_context：候选中存在相关条件规则，但报告缺少决定适用性的参数；",
        "- not_audited：候选中没有足够证据，本条无法完成审查。",
        "",
        "证据边界：",
        "1. 表格可能需要结合容量、型号、规格等选择行列；多个 Chunk 可组成证据链。",
        "2. 不要把报告声称的数值反过来当作标准证据。",
        "3. 不得使用“通常”“一般”“常见”“大概率属于”等行业常识确认适用条件；"
        "标准规则依赖的产品结构若报告未明确给出，输出 insufficient_context。",
        "4. 若候选只给出部分分项值、缺少公式或另一项必需证据，且 manual_knowledge_rules "
        "也无适用规则，输出 not_audited。",
        "5. 目标要求含具体基准值/限值时，必须在候选 Chunk、peer_report_context 或适用 "
        "manual_knowledge_rules 的派生结果中找到数值来源。",
        "",
        "输入字段用法：",
        "- peer_report_context：仅作同报告事实上下文，不是标准证据。",
        "- manual_knowledge_rules：仅用于其 allowed_use 描述的计算/派生/固定项目规则；"
        "不能提供标准限值来源或候选中不存在的产品结构事实；使用时 reason 须写明 rule_id。",
        "- few_shot_examples：只对齐输出口径与状态选择，不是标准证据。",
        "- sample_context：已提取样品参数，仅作检索/适用性锚点，勿当作已证标准事实。",
        "",
        "判定约定摘要（完整条文见输入 manual_knowledge_rules，此处不重复正文）：",
    ]
    if rule_lines:
        parts.extend(rule_lines)
    else:
        parts.append("- （未配置）")
    if notes_text:
        parts.extend(["", "品类约束：", notes_text])
    parts.extend(
        [
            "",
            "输出：",
            "严格输出 JSON 对象，不要输出 Markdown 代码块：",
            example_json,
        ]
    )
    return "\n".join(parts)


def merge_notes_into_audit_judge_brief(brief: str, notes: str) -> str:
    """Fold optional notes into the judge brief before the output section."""
    notes_text = (notes or "").strip()
    base = (brief or "").rstrip()
    if not notes_text or looks_like_full_audit_judge_prompt(notes_text):
        return base
    if "品类约束：" in base:
        return base
    marker = "\n输出：\n"
    block = f"\n品类约束：\n{notes_text}\n"
    if marker in base:
        head, tail = base.split(marker, 1)
        return f"{head.rstrip()}\n{block}\n输出：\n{tail}".strip()
    return f"{base}\n{block}".strip()


def build_prompt_var_context(
    *,
    parameter_schema: Any = None,
    manual_rules: Any = None,
    kb_name: str | None = None,
    kb_description: str | None = None,
    query_planner_routes: Any = None,
    assistant_rules: Any = None,
) -> dict[str, str]:
    """Build the fixed context map for expand / auto-inject."""
    name = str(kb_name or "").strip() or EMPTY_VALUE
    description = str(kb_description or "").strip() or EMPTY_VALUE
    report_step_rules = extract_step_rules(assistant_rules, "report_parameters")
    planner_step_rules = extract_step_rules(assistant_rules, "query_planner")
    test_items_step_rules = extract_step_rules(assistant_rules, "test_items")
    model_decode_step_rules = extract_step_rules(assistant_rules, "model_decode")
    return {
        "parameter_schema": format_parameter_schema(parameter_schema),
        "parameter_schema_json": format_parameter_schema_json(parameter_schema),
        "manual_rules": format_manual_rules(manual_rules),
        "kb_name": name,
        "kb_description": description,
        "kb_context": format_kb_context(kb_name, kb_description),
        "step_rules_test_items": format_manual_rules(test_items_step_rules),
        "step_rules_model_decode": format_manual_rules(model_decode_step_rules),
        "extraction_brief": format_extraction_brief(
            parameter_schema,
            kb_name=kb_name,
            kb_description=kb_description,
            step_rules=report_step_rules,
        ),
        "query_planner_brief": format_query_planner_brief(
            routes=query_planner_routes,
            parameter_schema=parameter_schema,
            kb_name=kb_name,
            kb_description=kb_description,
            step_rules=planner_step_rules,
        ),
        "audit_judge_brief": format_audit_judge_brief(
            manual_rules=manual_rules,
            kb_name=kb_name,
            kb_description=kb_description,
        ),
    }


def expand_prompt_template(template: str, context: dict[str, str]) -> str:
    """Replace known ``{{var}}`` placeholders; leave unknown tokens unchanged."""

    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            return match.group(0)
        value = context[key]
        return value if value else EMPTY_VALUE

    return PLACEHOLDER_RE.sub(replacer, template or "")


def list_used_placeholders(template: str) -> list[str]:
    """Return unique placeholder names in template order of first appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for match in PLACEHOLDER_RE.finditer(template or ""):
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def step_auto_inject_keys(step_id: str) -> tuple[str, ...]:
    return STEP_AUTO_INJECT.get(step_id, ("kb_context",))


PromptSegmentKind = Literal["static", "injected"]


def merge_notes_into_extraction_brief(brief: str, notes: str) -> str:
    """Fold optional category notes into the single brief (before the field list)."""
    notes_text = (notes or "").strip()
    base = (brief or "").rstrip()
    if not notes_text:
        return base
    if "品类约束与易混淆：" in base:
        return base
    marker = "\n字段：\n"
    block = f"\n品类约束与易混淆：\n{notes_text}\n"
    if marker in base:
        head, tail = base.split(marker, 1)
        return f"{head.rstrip()}\n{block}\n字段：\n{tail}".strip()
    return f"{base}\n{block}".strip()


def build_runtime_prompt_segments(
    template: str,
    *,
    step_id: str,
    context: dict[str, str],
) -> list[dict[str, str]]:
    """Build runtime system-prompt segments: optional notes + system-managed blocks."""
    used = set(list_used_placeholders(template))
    if used & {"kb_name", "kb_description", "kb_context"}:
        used.add("kb_context")
        used.update({"kb_name", "kb_description"})
    if "extraction_brief" in used or "parameter_schema" in used:
        used.add("extraction_brief")
        used.add("parameter_schema")

    notes = expand_prompt_template(template, context).rstrip()

    # report_parameters / query_planner: one preview document (notes folded in).
    if step_id == "report_parameters":
        brief = merge_notes_into_extraction_brief(
            context.get("extraction_brief") or EMPTY_VALUE,
            notes,
        )
        return [
            {
                "kind": "injected",
                "text": brief,
                "key": "extraction_brief",
                "title": INJECT_TITLES["extraction_brief"],
            }
        ]
    if step_id == "query_planner":
        brief = merge_notes_into_query_planner_brief(
            context.get("query_planner_brief") or EMPTY_VALUE,
            notes,
        )
        return [
            {
                "kind": "injected",
                "text": brief,
                "key": "query_planner_brief",
                "title": INJECT_TITLES["query_planner_brief"],
            }
        ]
    if step_id == "audit_judge":
        brief = merge_notes_into_audit_judge_brief(
            context.get("audit_judge_brief") or EMPTY_VALUE,
            notes,
        )
        return [
            {
                "kind": "injected",
                "text": brief,
                "key": "audit_judge_brief",
                "title": INJECT_TITLES["audit_judge_brief"],
            }
        ]

    segments: list[dict[str, str]] = []
    if step_id in NODE_STEP_TASKS:
        segments.append(
            {
                "kind": "static",
                "text": format_node_framework_preamble(step_id),
                "key": "",
                "title": "",
            }
        )
    # Per-step human rules (rule_id + rule_text), not free-text node_prompts.
    # test_items / model_decode never fall back to legacy node_prompts content.
    step_rules_key = f"step_rules_{step_id}"
    notes_only_steps = frozenset({"test_items", "model_decode"})
    if step_id in notes_only_steps or step_rules_key in context:
        rules_body = str(context.get(step_rules_key) or "").strip() or EMPTY_VALUE
        if rules_body == EMPTY_VALUE:
            rules_body = "（未配置）"
        segments.append(
            {
                "kind": "static",
                "text": f"本步补充规则（人工配置）：\n{rules_body}",
                "key": "",
                "title": "",
            }
        )
    elif notes:
        # Legacy free-text for other steps when no structured step_rules key exists.
        segments.append({"kind": "static", "text": notes, "key": "", "title": ""})

    for key in step_auto_inject_keys(step_id):
        if key in used:
            continue
        body = context.get(key) or EMPTY_VALUE
        title = INJECT_TITLES.get(key, key)
        segments.append(
            {
                "kind": "injected",
                "text": body,
                "key": key,
                "title": title,
            }
        )
    output_contract = format_node_json_output_contract(step_id)
    if output_contract:
        segments.append(
            {
                "kind": "static",
                "text": output_contract,
                "key": "",
                "title": "",
            }
        )
    return segments


def render_runtime_prompt_segments(segments: list[dict[str, str]]) -> str:
    """Join segments into the final system prompt string."""
    parts: list[str] = []
    for segment in segments:
        kind = segment.get("kind")
        text = str(segment.get("text") or "").rstrip()
        key = str(segment.get("key") or "")
        if not text and kind == "static":
            continue
        if kind == "injected":
            if key in FULL_PROMPT_INJECT_KEYS:
                parts.append(text)
            else:
                title = str(segment.get("title") or key or "系统注入")
                parts.append(f"## {title}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def compose_runtime_prompt(
    template: str,
    *,
    step_id: str,
    context: dict[str, str],
) -> str:
    """Expand legacy placeholders and append step-specific system injections."""
    return render_runtime_prompt_segments(
        build_runtime_prompt_segments(template, step_id=step_id, context=context)
    )
