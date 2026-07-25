import type { ManualKnowledgeRules, ParameterSchema } from '@/api'
import { looksLikeFullAuditJudgePrompt } from '@/lib/audit-judge-notes'
import {
  looksLikeFullQueryPlannerPrompt,
  resolveQueryPlannerRoutes,
  type QueryPlannerRoute,
} from '@/lib/query-planner-routes'
import { formatStepRulesLines, type StepRuleDraft } from '@/lib/step-rules'

export const EMPTY_PROMPT_VAR = '（未配置）'

/** Shared across all AI workflow nodes (framework only; not a variable). */
export const AUDIT_PIPELINE_SCENARIO =
  '本系统对照知识库中的标准证据，审查检测报告里填写的标准要求是否成立。'
  + '完整流水线为：提取报告参数 → 提取检测项目与标准要求 → 解析型号规则（可选）→ '
  + '规划检索问题 → 查找候选证据 → 对照标准判定 → 汇总结果。'
  + '总原则：只依据报告原文与检索到的标准 Chunk；不得用行业常识补全标准值或适用条件；'
  + '上游步骤产出（样品参数、型号解码等）仅作上下文或检索锚点，不是已被标准证明的事实。'

export const NODE_STEP_TASKS: Record<string, string> = {
  report_parameters:
    '你是流水线中的「提取报告参数」节点。'
    + '根据输入的报告 Markdown 与字段表，只提取样品/报告级参数，供后续适用性判断与检索锚点使用。'
    + '不使用行业常识补全，不编造报告未记载的内容；'
    + '检测结果、实测值与符合性结论不属于本步输出。',
  test_items:
    '你是流水线中的「提取检测项目」节点。'
    + '从报告的检测结果汇总（含跨页续表）中提取实际检测项目及其报告标准要求，'
    + '供后续逐条检索与判定。'
    + '样品上下文可写入自由键值；不得把检测结果/结论当作标准要求，'
    + '也不得把检测项目误放入报告级参数。',
  model_decode:
    '你是流水线中的「解析型号规则」节点。'
    + '仅依据报告原始型号、已提取参数与输入的型号命名规则 Markdown 解析型号特征，产出检索用语。'
    + '解析结果只服务后续检索改写，不是审查证据；不得使用行业常识补全，不得生成标准限值。',
  query_planner:
    '你是流水线中的「规划检索问题」节点。'
    + '针对输入中的单条报告标准要求，结合已提取的样品上下文（sample_context）'
    + '与可选的型号解码结果，生成多路检索 Query，供下一步在标准知识库中查找候选证据。'
    + 'sample_context 与解码结果仅作检索锚点；本步不输出审查判定，也不输出 parameters。',
  audit_judge:
    '你是流水线中的「对照标准判定」节点。'
    + '判断该条报告标准要求是否被候选标准 Chunk 支持。'
    + '只能使用输入中的报告事实、样品上下文、型号解析结果、peer_report_context、'
    + 'manual_knowledge_rules、few_shot_examples 与候选 Chunk；不得用常识补充标准值。'
    + '按四态 status（supported / mismatch / insufficient_context / not_audited）输出结论。',
}

export function formatNodeFrameworkPreamble(stepId: string): string {
  const task = String(NODE_STEP_TASKS[stepId] || '').trim()
  if (!task) return `任务场景：\n${AUDIT_PIPELINE_SCENARIO}`
  return `任务场景：\n${AUDIT_PIPELINE_SCENARIO}\n\n本步任务：\n${task}`
}

export type PromptInjectKey =
  | 'parameter_schema'
  | 'parameter_schema_json'
  | 'manual_rules'
  | 'kb_name'
  | 'kb_description'
  | 'kb_context'
  | 'extraction_brief'
  | 'query_planner_brief'
  | 'audit_judge_brief'

const PLACEHOLDER_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g

const KNOWN_INJECT_KEYS = new Set<string>([
  'parameter_schema',
  'parameter_schema_json',
  'manual_rules',
  'kb_name',
  'kb_description',
  'kb_context',
  'extraction_brief',
  'query_planner_brief',
  'audit_judge_brief',
])

const INJECT_TITLES: Record<PromptInjectKey, string> = {
  parameter_schema: '当前参数字段',
  parameter_schema_json: '参数 schema JSON',
  manual_rules: '判定约定',
  kb_name: '知识库名称',
  kb_description: '知识库说明',
  kb_context: '绑定知识库',
  extraction_brief: '抽参说明（系统生成）',
  query_planner_brief: '检索规划说明（系统生成）',
  audit_judge_brief: '审查判定说明（系统生成）',
}

/** Auto-append order per workflow step — mirrors backend prompt_vars.STEP_AUTO_INJECT. */
export const STEP_AUTO_INJECT: Partial<Record<string, PromptInjectKey[]>> = {
  report_parameters: ['extraction_brief'],
  test_items: ['kb_context'],
  model_decode: ['kb_context'],
  query_planner: ['query_planner_brief'],
  audit_judge: ['audit_judge_brief'],
}

function formatParameterSchemaFieldLines(schema: ParameterSchema | null | undefined): string[] {
  const fields = schema?.fields || []
  const lines: string[] = []
  for (const field of fields) {
    const key = String(field.key || '').trim()
    if (!key) continue
    const label = String(field.label || key).trim() || key
    const importance = field.required ? '审查关键' : '常规'
    const hint = String(field.hint || '').trim()
    let line = `- ${key}（${label}，${importance}）`
    if (hint) line = `${line}：${hint}`
    lines.push(line)
  }
  return lines
}

export function formatParameterSchema(schema: ParameterSchema | null | undefined): string {
  const fields = schema?.fields || []
  if (!fields.length) return EMPTY_PROMPT_VAR
  const allowExtra = Boolean(schema?.allow_extra)
  const fieldLines = formatParameterSchemaFieldLines(schema)
  if (!fieldLines.length) return EMPTY_PROMPT_VAR
  return [
    '下列每个 key 都必须出现在输出的 parameters 中；报告中找不到时 value 与 unit 均为空字符串。',
    '「审查关键」表示该字段对后续审查重要，不是允许省略该 key。',
    `允许额外字段：${allowExtra ? '是（可追加 schema 未声明、但须来自原文的报告级参数）' : '否（只提取下列已声明字段）'}`,
    '字段：',
    ...fieldLines,
  ].join('\n')
}

export function formatParameterSchemaJson(schema: ParameterSchema | null | undefined): string {
  const fields = schema?.fields || []
  if (!fields.length) return EMPTY_PROMPT_VAR
  return JSON.stringify(
    {
      version: schema?.version ?? 1,
      allow_extra: Boolean(schema?.allow_extra),
      fields,
    },
    null,
    2,
  )
}

export function formatManualRules(rules: ManualKnowledgeRules | Record<string, unknown> | null | undefined): string {
  const list = Array.isArray(rules?.rules) ? rules.rules : []
  const lines: string[] = []
  for (const rule of list) {
    if (!rule || typeof rule !== 'object') continue
    const text = String((rule as { rule_text?: unknown }).rule_text || '').trim()
    if (!text) continue
    const ruleId = String((rule as { rule_id?: unknown }).rule_id || '').trim() || 'rule'
    lines.push(`- ${ruleId}: ${text}`)
  }
  return lines.length ? lines.join('\n') : EMPTY_PROMPT_VAR
}

export function formatKbContext(kbName?: string | null, kbDescription?: string | null): string {
  const name = String(kbName || '').trim() || EMPTY_PROMPT_VAR
  const description = String(kbDescription || '').trim() || EMPTY_PROMPT_VAR
  return `名称：${name}\n说明：${description}`
}

export type BriefPreviewPart =
  | { kind: 'framework'; text: string }
  | { kind: 'variable'; label: string; text: string }

function joinBriefParts(parts: BriefPreviewPart[]): string {
  return parts.map(part => part.text).join('\n')
}

function stepRulesVariableParts(stepRules?: StepRuleDraft[] | null): BriefPreviewPart[] {
  const lines = formatStepRulesLines(stepRules || [])
  return [
    { kind: 'framework', text: '' },
    { kind: 'framework', text: '本步补充规则（人工配置）：' },
    {
      kind: 'variable',
      label: '本步补充规则',
      text: lines,
    },
  ]
}

export function buildExtractionBriefParts(input: {
  parameterSchema?: ParameterSchema | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
  stepRules?: StepRuleDraft[] | null
}): BriefPreviewPart[] {
  const schema = input.parameterSchema
  const fields = schema?.fields || []
  const allowExtra = Boolean(schema?.allow_extra)
  const name = String(input.kbName || '').trim() || EMPTY_PROMPT_VAR
  const description = String(input.kbDescription || '').trim() || EMPTY_PROMPT_VAR
  const notesText = String(input.notes || '').trim()

  const fieldLines: string[] = []
  for (const field of fields) {
    const key = String(field.key || '').trim()
    if (!key) continue
    const label = String(field.label || key).trim() || key
    const importance = field.required ? '审查关键' : '常规'
    const hint = String(field.hint || '').trim()
    let line = `- ${key}（${label}，${importance}）`
    if (hint) line = `${line}：${hint}`
    fieldLines.push(line)
  }
  if (!fieldLines.length) fieldLines.push(`- （${EMPTY_PROMPT_VAR}）`)

  const exampleItems: string[] = []
  for (const field of fields) {
    const key = String(field.key || '').trim()
    if (!key) continue
    exampleItems.push(`    {"key": "${key}", "value": "", "unit": ""}`)
  }
  if (!exampleItems.length) {
    exampleItems.push('    {"key": "", "value": "", "unit": ""}')
  }
  const exampleJson = `{\n  "parameters": [\n${exampleItems.join(',\n')}\n  ]\n}`

  const extraLine = allowExtra
    ? '允许额外字段：是；可追加 schema 未声明、但对后续审查有用的报告级参数（仍须来自原文，不得把检测结果当作参数）。'
    : '允许额外字段：否；只提取下列已声明字段，不得自行增补 key。'

  const parts: BriefPreviewPart[] = [
    {
      kind: 'framework',
      text: formatNodeFrameworkPreamble('report_parameters'),
    },
    {
      kind: 'variable',
      label: '知识库',
      text: description !== EMPTY_PROMPT_VAR ? `知识库：${name}。${description}` : `知识库：${name}。`,
    },
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: [
        '提取规则：',
        '1. 只提取报告直接记载的样品/报告级参数；找不到时该字段 value 与 unit 均为空字符串，但 key 仍须出现在输出中。',
        '2. 保留原始完整表达，不拆分、不改写单位与符号（如 10/0.4kV、Dyn11、LI75 AC35）。',
        '3. 短路阻抗、空载/负载损耗、空载电流、温升、绝缘电阻、电压比偏差、耐压试验电压与时长、'
        + '声级、以及任何检测项目的符合/不符合结论，一律不得作为报告级参数。',
        `4. ${extraLine}`,
        '5. 「审查关键」表示该字段对后续审查重要，不是允许省略该 key。',
      ].join('\n'),
    },
  ]
  parts.push(...stepRulesVariableParts(input.stepRules))
  if (notesText) {
    parts.push(
      { kind: 'framework', text: '' },
      {
        kind: 'variable',
        label: '品类约束',
        text: ['品类约束与易混淆：', notesText].join('\n'),
      },
    )
  }
  parts.push(
    { kind: 'framework', text: '' },
    { kind: 'framework', text: '字段：' },
    {
      kind: 'variable',
      label: '参数字段',
      text: fieldLines.join('\n'),
    },
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: [
        '输出：',
        '严格输出 JSON 对象，不要输出 Markdown 代码块。须包含上表全部 key，示例如下：',
      ].join('\n'),
    },
    {
      kind: 'variable',
      label: '输出示例',
      text: exampleJson,
    },
    {
      kind: 'framework',
      text:
        '找不到的字段仍保留对应 key，value/unit 为空字符串；'
        + (allowExtra
          ? '若允许额外字段，可在 parameters 末尾追加，key 用简短英文或拼音 snake_case。'
          : '不得增加上表以外的 key。'),
    },
  )
  return parts
}

export function formatExtractionBrief(input: {
  parameterSchema?: ParameterSchema | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
}): string {
  return joinBriefParts(buildExtractionBriefParts(input))
}

export function mergeNotesIntoExtractionBrief(brief: string, notes: string): string {
  const notesText = (notes || '').trim()
  const base = (brief || '').trimEnd()
  if (!notesText) return base
  if (base.includes('品类约束与易混淆：')) return base
  const marker = '\n字段：\n'
  const block = `\n品类约束与易混淆：\n${notesText}\n`
  if (base.includes(marker)) {
    const [head, ...rest] = base.split(marker)
    return `${head.trimEnd()}\n${block}\n字段：\n${rest.join(marker)}`.trim()
  }
  return `${base}\n${block}`.trim()
}

export function buildQueryPlannerBriefParts(input: {
  queryPlannerRoutes?: QueryPlannerRoute[] | unknown
  parameterSchema?: ParameterSchema | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
  stepRules?: StepRuleDraft[] | null
}): BriefPreviewPart[] {
  const resolved = resolveQueryPlannerRoutes(input.queryPlannerRoutes)
  let enabled = resolved.filter(item => item.enabled)
  if (!enabled.length) enabled = [resolved[0]]

  const name = String(input.kbName || '').trim() || EMPTY_PROMPT_VAR
  const description = String(input.kbDescription || '').trim() || EMPTY_PROMPT_VAR
  let notesText = String(input.notes || '').trim()
  if (looksLikeFullQueryPlannerPrompt(notesText)) notesText = ''

  const routeLines = enabled.map(
    item => `- \`${item.id}\`（${item.label}）：${item.instruction}`,
  )
  const exampleObj: Record<string, string> = {}
  for (const item of enabled) {
    exampleObj[item.id] = item.id === 'table_target' || item.id === 'section_target'
      ? '...或空字符串'
      : '...'
  }
  const exampleJson = JSON.stringify(exampleObj, null, 2)

  const parts: BriefPreviewPart[] = [
    {
      kind: 'framework',
      text: formatNodeFrameworkPreamble('query_planner'),
    },
    {
      kind: 'variable',
      label: '知识库',
      text: description !== EMPTY_PROMPT_VAR ? `知识库：${name}。${description}` : `知识库：${name}。`,
    },
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: '生成以下检索表达（仅包含已启用的改写形式）：',
    },
    {
      kind: 'variable',
      label: '检索改写',
      text: routeLines.join('\n'),
    },
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: [
        '约束：',
        '1. 只能使用输入提供的报告事实、样品上下文和型号解码结果；若无型号解码则忽略解码相关约束。',
        '2. 不得把报告标准值当成已经被标准文件证明的事实；可以保留报告声称值作为检索锚点，'
        + '但表达应是“寻找支持或核验该值的证据”。',
        '3. 不得加入输入中没有出现、且（在有解码时）不能由型号解码结果支持的产品条件。',
        '4. 型号无法解释时保留原始型号，不得凭行业常识展开。',
        '5. 每条启用改写形式只输出一条 Query。',
        '6. 严格输出 JSON 对象，不要输出 Markdown 代码块。',
      ].join('\n'),
    },
  ]
  parts.push(...stepRulesVariableParts(input.stepRules))
  if (notesText) {
    parts.push(
      { kind: 'framework', text: '' },
      {
        kind: 'variable',
        label: '品类约束',
        text: ['品类约束：', notesText].join('\n'),
      },
    )
  }
  parts.push(
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: ['输出：', '严格输出 JSON 对象，须包含下列已启用 key：'].join('\n'),
    },
    {
      kind: 'variable',
      label: '输出示例',
      text: exampleJson,
    },
  )
  return parts
}

export function formatQueryPlannerBrief(input: {
  queryPlannerRoutes?: QueryPlannerRoute[] | unknown
  parameterSchema?: ParameterSchema | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
}): string {
  return joinBriefParts(buildQueryPlannerBriefParts(input))
}

export function mergeNotesIntoQueryPlannerBrief(brief: string, notes: string): string {
  const notesText = (notes || '').trim()
  const base = (brief || '').trimEnd()
  if (!notesText || looksLikeFullQueryPlannerPrompt(notesText)) return base
  if (base.includes('品类约束：')) return base
  const marker = '\n输出：\n'
  const block = `\n品类约束：\n${notesText}\n`
  if (base.includes(marker)) {
    const [head, ...rest] = base.split(marker)
    return `${head.trimEnd()}\n${block}\n输出：\n${rest.join(marker)}`.trim()
  }
  return `${base}\n${block}`.trim()
}

function manualRulesSummaryLines(
  manualRules?: ManualKnowledgeRules | Record<string, unknown> | null,
): string[] {
  const rules = Array.isArray(manualRules?.rules) ? manualRules.rules : []
  const lines: string[] = []
  for (const rule of rules) {
    if (!rule || typeof rule !== 'object') continue
    const ruleId = String((rule as { rule_id?: unknown }).rule_id || '').trim() || 'rule'
    const text = String((rule as { rule_text?: unknown }).rule_text || '')
      .trim()
      .replace(/\n/g, ' ')
    if (!text) continue
    const snippet = text.length <= 72 ? text : `${text.slice(0, 72)}…`
    lines.push(`- \`${ruleId}\`：${snippet}`)
  }
  return lines
}

export function buildAuditJudgeBriefParts(input: {
  manualRules?: ManualKnowledgeRules | Record<string, unknown> | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
}): BriefPreviewPart[] {
  const name = String(input.kbName || '').trim() || EMPTY_PROMPT_VAR
  const description = String(input.kbDescription || '').trim() || EMPTY_PROMPT_VAR
  let notesText = String(input.notes || '').trim()
  if (looksLikeFullAuditJudgePrompt(notesText)) notesText = ''

  const ruleLines = manualRulesSummaryLines(input.manualRules)
  const exampleJson = JSON.stringify(
    {
      status: 'supported|mismatch|insufficient_context|not_audited',
      reason: '',
      evidence_candidate_keys: [],
      missing_context_fields: [],
    },
    null,
    2,
  )

  const parts: BriefPreviewPart[] = [
    {
      kind: 'framework',
      text: formatNodeFrameworkPreamble('audit_judge'),
    },
    {
      kind: 'variable',
      label: '知识库',
      text: description !== EMPTY_PROMPT_VAR ? `知识库：${name}。${description}` : `知识库：${name}。`,
    },
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: [
        '状态：',
        '- supported：直接证据支持报告要求；',
        '- mismatch：直接证据给出冲突数值、公式或适用条件；',
        '- insufficient_context：候选中存在相关条件规则，但报告缺少决定适用性的参数；',
        '- not_audited：候选中没有足够证据，本条无法完成审查。',
        '',
        '证据边界：',
        '1. 表格可能需要结合容量、型号、规格等选择行列；多个 Chunk 可组成证据链。',
        '2. 不要把报告声称的数值反过来当作标准证据。',
        '3. 不得使用“通常”“一般”“常见”“大概率属于”等行业常识确认适用条件；'
        + '标准规则依赖的产品结构若报告未明确给出，输出 insufficient_context。',
        '4. 若候选只给出部分分项值、缺少公式或另一项必需证据，且 manual_knowledge_rules '
        + '也无适用规则，输出 not_audited。',
        '5. 目标要求含具体基准值/限值时，必须在候选 Chunk、peer_report_context 或适用 '
        + 'manual_knowledge_rules 的派生结果中找到数值来源。',
        '',
        '输入字段用法：',
        '- peer_report_context：仅作同报告事实上下文，不是标准证据。',
        '- manual_knowledge_rules：仅用于其 allowed_use 描述的计算/派生/固定项目规则；'
        + '不能提供标准限值来源或候选中不存在的产品结构事实；使用时 reason 须写明 rule_id。',
        '- few_shot_examples：只对齐输出口径与状态选择，不是标准证据。',
        '- sample_context：已提取样品参数，仅作检索/适用性锚点，勿当作已证标准事实。',
        '',
        '判定约定摘要（完整条文见输入 manual_knowledge_rules，此处不重复正文）：',
      ].join('\n'),
    },
    {
      kind: 'variable',
      label: '判定约定',
      text: ruleLines.length ? ruleLines.join('\n') : '- （未配置）',
    },
  ]
  if (notesText) {
    parts.push(
      { kind: 'framework', text: '' },
      {
        kind: 'variable',
        label: '品类约束',
        text: ['品类约束：', notesText].join('\n'),
      },
    )
  }
  parts.push(
    { kind: 'framework', text: '' },
    {
      kind: 'framework',
      text: ['输出：', '严格输出 JSON 对象，不要输出 Markdown 代码块：'].join('\n'),
    },
    {
      kind: 'variable',
      label: '输出示例',
      text: exampleJson,
    },
  )
  return parts
}

export function formatAuditJudgeBrief(input: {
  manualRules?: ManualKnowledgeRules | Record<string, unknown> | null
  kbName?: string | null
  kbDescription?: string | null
  notes?: string | null
}): string {
  return joinBriefParts(buildAuditJudgeBriefParts(input))
}

/** Brief preview for steps without specialized left vars (step rules + kb). */
export function buildNotesOnlyBriefParts(
  stepId: string,
  _notes: string,
  input: {
    kbName?: string | null
    kbDescription?: string | null
    stepRules?: StepRuleDraft[] | null
  },
): BriefPreviewPart[] {
  const name = String(input.kbName || '').trim() || EMPTY_PROMPT_VAR
  const description = String(input.kbDescription || '').trim() || EMPTY_PROMPT_VAR
  return [
    {
      kind: 'framework',
      text: formatNodeFrameworkPreamble(stepId),
    },
    {
      kind: 'variable',
      label: '知识库',
      text: description !== EMPTY_PROMPT_VAR ? `知识库：${name}。${description}` : `知识库：${name}。`,
    },
    ...stepRulesVariableParts(input.stepRules),
  ]
}

/** Structured brief parts for UI preview (framework vs configurable variables). */
export function buildBriefPreviewParts(
  stepId: string,
  notes: string,
  input: {
    parameterSchema?: ParameterSchema | null
    manualRules?: ManualKnowledgeRules | Record<string, unknown> | null
    kbName?: string | null
    kbDescription?: string | null
    queryPlannerRoutes?: QueryPlannerRoute[] | unknown
    stepRules?: StepRuleDraft[] | null
  },
): BriefPreviewPart[] {
  if (stepId === 'report_parameters') {
    return buildExtractionBriefParts({ ...input, notes })
  }
  if (stepId === 'query_planner') {
    return buildQueryPlannerBriefParts({ ...input, notes })
  }
  if (stepId === 'audit_judge') {
    return buildAuditJudgeBriefParts({ ...input, notes })
  }
  if (stepId === 'test_items' || stepId === 'model_decode') {
    return buildNotesOnlyBriefParts(stepId, notes, input)
  }
  return []
}

export function mergeNotesIntoAuditJudgeBrief(brief: string, notes: string): string {
  const notesText = (notes || '').trim()
  const base = (brief || '').trimEnd()
  if (!notesText || looksLikeFullAuditJudgePrompt(notesText)) return base
  if (base.includes('品类约束：')) return base
  const marker = '\n输出：\n'
  const block = `\n品类约束：\n${notesText}\n`
  if (base.includes(marker)) {
    const [head, ...rest] = base.split(marker)
    return `${head.trimEnd()}\n${block}\n输出：\n${rest.join(marker)}`.trim()
  }
  return `${base}\n${block}`.trim()
}

export function buildPromptVarContext(input: {
  parameterSchema?: ParameterSchema | null
  manualRules?: ManualKnowledgeRules | Record<string, unknown> | null
  kbName?: string | null
  kbDescription?: string | null
  queryPlannerRoutes?: QueryPlannerRoute[] | unknown
}): Record<PromptInjectKey, string> {
  return {
    parameter_schema: formatParameterSchema(input.parameterSchema),
    parameter_schema_json: formatParameterSchemaJson(input.parameterSchema),
    manual_rules: formatManualRules(input.manualRules),
    kb_name: String(input.kbName || '').trim() || EMPTY_PROMPT_VAR,
    kb_description: String(input.kbDescription || '').trim() || EMPTY_PROMPT_VAR,
    kb_context: formatKbContext(input.kbName, input.kbDescription),
    extraction_brief: formatExtractionBrief(input),
    query_planner_brief: formatQueryPlannerBrief(input),
    audit_judge_brief: formatAuditJudgeBrief(input),
  }
}

/** Strip known system placeholders so the editor preview keeps injections in highlighted blocks only. */
export function stripKnownPlaceholders(template: string): string {
  return (template || '')
    .replace(PLACEHOLDER_RE, (full, key: string) => (KNOWN_INJECT_KEYS.has(key) ? '' : full))
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export type RuntimePromptSegment =
  | { kind: 'static'; text: string }
  | { kind: 'injected'; key: PromptInjectKey; title: string; text: string }

/**
 * UI preview segments: optional notes + system injection blocks.
 * report_parameters folds notes into one extraction_brief document.
 */
export function buildRuntimePromptSegments(
  template: string,
  stepId: string,
  context: Record<string, string>,
): RuntimePromptSegment[] {
  const notes = stripKnownPlaceholders(template)

  if (stepId === 'report_parameters') {
    return [
      {
        kind: 'injected',
        key: 'extraction_brief',
        title: INJECT_TITLES.extraction_brief,
        text: mergeNotesIntoExtractionBrief(context.extraction_brief || EMPTY_PROMPT_VAR, notes),
      },
    ]
  }

  if (stepId === 'query_planner') {
    return [
      {
        kind: 'injected',
        key: 'query_planner_brief',
        title: INJECT_TITLES.query_planner_brief,
        text: mergeNotesIntoQueryPlannerBrief(
          context.query_planner_brief || EMPTY_PROMPT_VAR,
          notes,
        ),
      },
    ]
  }

  if (stepId === 'audit_judge') {
    return [
      {
        kind: 'injected',
        key: 'audit_judge_brief',
        title: INJECT_TITLES.audit_judge_brief,
        text: mergeNotesIntoAuditJudgeBrief(
          context.audit_judge_brief || EMPTY_PROMPT_VAR,
          notes,
        ),
      },
    ]
  }

  const segments: RuntimePromptSegment[] = []
  if (stepId in NODE_STEP_TASKS) {
    segments.push({ kind: 'static', text: formatNodeFrameworkPreamble(stepId) })
  }
  if (notes) {
    segments.push({ kind: 'static', text: notes })
  }

  const keys = STEP_AUTO_INJECT[stepId] || (['kb_context'] as PromptInjectKey[])
  for (const key of keys) {
    segments.push({
      kind: 'injected',
      key,
      title: INJECT_TITLES[key] || key,
      text: context[key] || EMPTY_PROMPT_VAR,
    })
  }
  return segments
}
