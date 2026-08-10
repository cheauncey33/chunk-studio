import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useBlocker, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  FileText,
  History,
  Loader2,
  Pencil,
  Plus,
  Send,
  Trash2,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { toast } from 'sonner'
import {
  api,
  type AgentChatResponse,
  type AgentCitation,
  type AgentConversation,
  type BusinessChart,
  type AssistantVersion,
  type AuditAssistant,
  type KnowledgeBase,
  type ManualKnowledgeRules,
  type ParameterSchema,
  type ParameterSchemaField,
} from '@/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input, Label, Textarea } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { SearchableMultiSelect, SearchableSelect } from '@/components/searchable-select'
import { useAssistants, useKnowledgeBase, queryKeys } from '@/hooks/use-knowledge-request'
import {
  GENERIC_TEMPLATE_ASSISTANT_ID,
  needsCategoryInit,
} from '@/lib/assistants'
import { helpText } from '@/lib/help-text'
import { cn } from '@/lib/utils'
import {
  resolveQueryPlannerRoutes,
  type QueryPlannerRoute,
} from '@/lib/query-planner-routes'
import { AssistantInitDraftCard } from '@/pages/assistants/init-draft-card'
import { PromptTemplateEditor } from '@/pages/assistants/prompt-template-editor'
import {
  getStepRulesFromVersionRules,
  setStepRulesOnVersionRules,
  type StepRuleDraft,
} from '@/lib/step-rules'

const RAGFLOW_TEAL = '#13c2c2'
const INLINE_CHART_COLORS = ['#13c2c2', '#ef4444', '#f59e0b', '#6366f1', '#22c55e']

const FLOW_STEPS = [
  { id: 'report_parameters', label: '提取报告参数', kind: 'AI' },
  { id: 'test_items', label: '提取检测项目', kind: 'AI' },
  { id: 'model_decode', label: '解析型号规则', kind: 'AI' },
  { id: 'query_planner', label: '规划检索问题', kind: 'AI' },
  { id: 'candidate_retrieval', label: '查找候选证据', kind: '检索' },
  { id: 'audit_judge', label: '对照标准判定', kind: 'AI' },
  { id: 'result_summary', label: '汇总结果', kind: '汇总' },
] as const

type FlowStepId = (typeof FLOW_STEPS)[number]['id']
type MainTab = 'chat' | 'workflow'
type ManualRuleDraft = NonNullable<ManualKnowledgeRules['rules']>[number]
type AssistantChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: AgentCitation[]
  charts?: BusinessChart[]
}

const AGENT_FLOW_STEPS = {
  understand: '理解问题',
  retrieve: '检索知识库',
  business: '查询业务数据',
  evidence: '整理证据',
  data: '整理数据',
  chart: '生成数据视图',
  answer: '生成回答',
} as const

function appendAgentFlow(current: string[], step: string): string[] {
  if (!step || current.includes(step)) return current
  return [...current, step]
}

function agentFlowStepForTool(name: unknown): string {
  switch (String(name || '')) {
    case 'search_knowledge_base':
      return AGENT_FLOW_STEPS.retrieve
    case 'query_business_data':
      return AGENT_FLOW_STEPS.business
    case 'get_business_schema':
    case 'get_business_overview':
      return AGENT_FLOW_STEPS.data
    default:
      return '调用业务能力'
  }
}

function messagesFromConversation(
  events: Array<{ event_type: string; payload: Record<string, unknown>; sequence: number }>,
): AssistantChatMessage[] {
  const messages: AssistantChatMessage[] = []
  for (const event of events) {
    if (event.event_type === 'user_message') {
      const content = typeof event.payload.content === 'string' ? event.payload.content : ''
      if (content) messages.push({ id: `history-u-${event.sequence}`, role: 'user', content })
      continue
    }
    if (event.event_type !== 'assistant_message') continue
    const raw = event.payload.message
    if (!raw || typeof raw !== 'object') continue
    const message = raw as Record<string, unknown>
    const content = typeof message.content === 'string' ? message.content : ''
    const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : []
    // Tool-call assistant messages are execution traces, not user-facing
    // answers. Keep only the final assistant message after the tool result.
    if (toolCalls.length > 0) continue
    messages.push({
      id: `history-a-${event.sequence}`,
      role: 'assistant',
      content: content || '（空回复）',
      citations: Array.isArray(event.payload.citations)
        ? event.payload.citations as AgentCitation[]
        : undefined,
      charts: Array.isArray(event.payload.charts)
        ? event.payload.charts as BusinessChart[]
        : undefined,
    })
  }
  return messages
}

function uniqueBusinessCharts(charts: BusinessChart[]): BusinessChart[] {
  const seen = new Set<string>()
  return charts.filter(chart => {
    const signature = JSON.stringify(chart)
    if (seen.has(signature)) return false
    seen.add(signature)
    return true
  })
}

function AssistantMarkdown({ text }: { text: string }) {
  return (
    <div className="text-[15px] leading-7 text-[#1f2937]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h3 className="mb-2 mt-4 text-[17px] font-semibold text-[#111827] first:mt-0">{children}</h3>,
          h2: ({ children }) => <h3 className="mb-2 mt-4 text-[17px] font-semibold text-[#111827] first:mt-0">{children}</h3>,
          h3: ({ children }) => <h4 className="mb-2 mt-3 text-[16px] font-semibold text-[#111827] first:mt-0">{children}</h4>,
          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
          li: ({ children }) => <li className="pl-1">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-4 border-[#13c2c2] bg-[#f0fdfa] px-4 py-2 text-[#475569] last:mb-0">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-4 border-[#e5e7eb]" />,
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-xl border border-[#e5e7eb] last:mb-0">
              <table className="min-w-full border-collapse text-[13px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-[#f8fafc]">{children}</thead>,
          th: ({ children }) => <th className="border-b border-[#e5e7eb] px-3 py-2 text-left font-semibold text-[#374151]">{children}</th>,
          td: ({ children }) => <td className="border-b border-[#f1f5f9] px-3 py-2 align-top text-[#4b5563]">{children}</td>,
          code: ({ children, className }) => {
            const isBlock = Boolean(className?.includes('language-'))
            return (
              <code
                className={cn(
                  isBlock
                    ? 'block overflow-x-auto rounded-xl bg-[#0f172a] px-4 py-3 font-mono text-[13px] leading-6 text-[#e2e8f0]'
                    : 'rounded bg-[#e6fffb] px-1.5 py-0.5 font-mono text-[13px] text-[#0f766e]',
                  className,
                )}
              >
                {children}
              </code>
            )
          },
          pre: ({ children }) => <pre className="my-3 overflow-x-auto last:mb-0">{children}</pre>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

function InlineChart({ chart }: { chart: BusinessChart }) {
  if (chart.type === 'metric') {
    return (
      <div className="mt-3 flex items-center justify-between gap-4 rounded-xl border border-[#bae6fd] bg-white px-4 py-3 shadow-sm">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#64748b]">查询指标</div>
          <div className="mt-1 text-xs text-[#94a3b8]">业务分析工具 · 只读结果</div>
        </div>
        <div className="text-3xl font-bold tracking-tight text-[#0f766e]">{String(chart.value ?? '—')}</div>
      </div>
    )
  }
  const denominator = chart.type === 'pie'
    ? chart.denominator
    : chart.data.reduce((total, item) => total + item.value, 0)
  if (chart.type === 'pie') {
    let cursor = 0
    const background = denominator
      ? `conic-gradient(${chart.data.map((item, index) => {
          const start = cursor
          cursor += (item.value / denominator) * 100
          return `${INLINE_CHART_COLORS[index % INLINE_CHART_COLORS.length]} ${start}% ${cursor}%`
        }).join(',')})`
      : '#e5e7eb'
    return (
      <div className="mt-3 flex flex-wrap items-center gap-4 rounded-xl border border-[#e0e7ff] bg-white px-4 py-3 shadow-sm">
        <div className="relative size-24 shrink-0 rounded-full" style={{ background }}>
          <div className="absolute inset-5 grid place-items-center rounded-full bg-white text-xs font-semibold">
            {denominator}
          </div>
        </div>
        <div className="grid min-w-[12rem] gap-1.5 text-xs text-[#6b7280]">
          {chart.data.map((item, index) => (
            <div className="flex items-center gap-1.5" key={item.label}>
              <span className="size-2 rounded-full" style={{ backgroundColor: INLINE_CHART_COLORS[index % INLINE_CHART_COLORS.length] }} />
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </div>
    )
  }
  const maximum = Math.max(1, ...chart.data.map(item => item.value))
  return (
    <div className="mt-3 grid gap-2 rounded-xl border border-[#e0e7ff] bg-white px-4 py-3 shadow-sm">
      {chart.data.map((item, index) => (
        <div className="grid grid-cols-[5rem_1fr_2rem] items-center gap-2 text-xs" key={item.label}>
          <span className="truncate text-[#6b7280]">{item.label}</span>
          <div className="h-2 overflow-hidden rounded-full bg-[#e5e7eb]">
            <div className="h-full rounded-full" style={{ width: `${(item.value / maximum) * 100}%`, backgroundColor: INLINE_CHART_COLORS[index % INLINE_CHART_COLORS.length] }} />
          </div>
          <strong className="text-right">{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

function normalizeManualRuleDrafts(payload: ManualKnowledgeRules | null | undefined): ManualRuleDraft[] {
  const rules = Array.isArray(payload?.rules) ? payload.rules : []
  return rules
    .filter((item): item is ManualRuleDraft => Boolean(item && typeof item === 'object'))
    .map(item => ({ ...item }))
}

function emptyManualRule(): ManualRuleDraft {
  return {
    rule_id: `rule_${Date.now().toString(36)}`,
    rule_text: '',
    domain: 'standard_value_audit',
    rule_type: 'manual',
  }
}

function manualRulesSignature(rules: ManualRuleDraft[]): string {
  return JSON.stringify(rules.map(item => ({
    rule_id: String(item.rule_id || ''),
    rule_text: String(item.rule_text || ''),
    domain: String(item.domain || ''),
    rule_type: String(item.rule_type || ''),
    allowed_use: item.allowed_use ?? null,
    applies_when: item.applies_when ?? null,
  })))
}

function versionDraftSignature(version: AssistantVersion | null, knowledgeBaseIds: Iterable<string>) {
  if (!version) return ''
  return JSON.stringify({
    model_config: version.model_config,
    node_prompts: version.node_prompts,
    rules: version.rules,
    retrieval_config: version.retrieval_config,
    parameter_schema: version.parameter_schema,
    initialization_provenance: version.initialization_provenance,
    knowledge_base_ids: [...knowledgeBaseIds].sort(),
  })
}

const NO_PROMPT_STEPS = new Set<FlowStepId>(['candidate_retrieval', 'result_summary'])
const EDITABLE_STEPS = FLOW_STEPS.filter(step => !NO_PROMPT_STEPS.has(step.id))

const PRODUCT_FLOW_STEPS: Array<{
  id: FlowStepId
  label: string
  summary: string
  kind: 'AI' | '检索' | '汇总'
  /** Open editor when set; omit for steps without editable prompts. */
  editStepId?: FlowStepId
}> = [
  {
    id: 'report_parameters',
    label: '提取报告参数',
    summary: '抽取样品/报告级参数，供后续适用性与检索锚点',
    kind: 'AI',
    editStepId: 'report_parameters',
  },
  {
    id: 'test_items',
    label: '提取检测项目',
    summary: '从汇总表提取检测项目与报告标准要求',
    kind: 'AI',
    editStepId: 'test_items',
  },
  {
    id: 'model_decode',
    label: '解析型号规则',
    summary: '按命名规则解析型号，产出检索用语（可选）',
    kind: 'AI',
    editStepId: 'model_decode',
  },
  {
    id: 'query_planner',
    label: '规划检索问题',
    summary: '为单条标准要求生成多路检索 Query',
    kind: 'AI',
    editStepId: 'query_planner',
  },
  {
    id: 'candidate_retrieval',
    label: '查找候选证据',
    summary: '按规划结果在本库检索标准 Chunk（无独立提示词）',
    kind: '检索',
  },
  {
    id: 'audit_judge',
    label: '对照标准判定',
    summary: '用候选证据与判定约定判定该条要求',
    kind: 'AI',
    editStepId: 'audit_judge',
  },
  {
    id: 'result_summary',
    label: '汇总结果',
    summary: '汇总各条判定与证据说明（无独立提示词）',
    kind: '汇总',
  },
]

const MODEL_OPTIONS = [
  { value: 'deepseek-v4-flash', label: 'deepseek-v4-flash', hint: 'ds', group: 'DeepSeek' },
  { value: 'deepseek-v4-pro', label: 'deepseek-v4-pro', hint: 'ds', group: 'DeepSeek' },
  { value: 'deepseek-chat', label: 'deepseek-chat', hint: 'ds', group: 'DeepSeek' },
  { value: 'deepseek-reasoner', label: 'deepseek-reasoner', hint: 'ds', group: 'DeepSeek' },
]

function ModelMark() {
  return (
    <span className="grid size-5 shrink-0 place-items-center rounded bg-[#dbeafe] text-[10px] font-bold text-[#2563eb]">
      DS
    </span>
  )
}

function SettingHint({ label, tip }: { label: string; tip: string }) {
  return (
    <div className="mb-2 flex items-center gap-1 text-[15px] text-[#374151]">
      <span>{label}</span>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" className="inline-flex text-[#9ca3af] hover:text-[#6b7280]" aria-label={`${label}说明`}>
            <CircleHelp className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-[240px] leading-relaxed">{tip}</TooltipContent>
      </Tooltip>
    </div>
  )
}

function SettingSlider({
  value,
  min,
  max,
  step,
  onChange,
  format = v => String(v),
  parse = v => Number(v),
}: {
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
  format?: (value: number) => string
  parse?: (raw: string) => number
}) {
  const pct = max === min ? 0 : ((value - min) / (max - min)) * 100
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-[#e5e7eb]"
        style={
          {
            '--rag-teal': RAGFLOW_TEAL,
            background: `linear-gradient(to right, ${RAGFLOW_TEAL} ${pct}%, #e5e7eb ${pct}%)`,
          } as CSSProperties
        }
      />
      <input
        type="text"
        inputMode="decimal"
        value={format(value)}
        onChange={e => {
          const next = parse(e.target.value)
          if (!Number.isFinite(next)) return
          onChange(Math.min(max, Math.max(min, next)))
        }}
        className="h-8 w-14 shrink-0 rounded-md border border-[#e5e7eb] bg-white px-2 text-center text-sm text-[#111827] outline-none focus:border-[#13c2c2]"
      />
    </div>
  )
}

/** Legacy /assistants routes redirect into knowledge-base or settings. */
export default function AssistantsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: assistants = [], isLoading } = useAssistants()

  useEffect(() => {
    if (isLoading) return
    if (!id) {
      navigate('/knowledge-bases', { replace: true })
      return
    }
    if (id === GENERIC_TEMPLATE_ASSISTANT_ID) {
      navigate('/settings/assistant-template', { replace: true })
      return
    }
    const selected = assistants.find(item => item.id === id)
    if (!selected) {
      navigate('/knowledge-bases', { replace: true })
      return
    }
    const kbId = selected.knowledge_bases[0]?.id
    navigate(kbId ? `/kb/${kbId}/workflow` : '/knowledge-bases', { replace: true })
  }, [assistants, id, isLoading, navigate])

  return (
    <div className="flex h-full items-center justify-center text-[15px] text-text-secondary">
      正在跳转…
    </div>
  )
}

export function AssistantSettings({
  assistant,
  knowledgeBases,
  onChanged,
  lockedKnowledgeBaseId,
  initialTab = 'chat',
  embedded = false,
  backTo,
  hideKnowledgePicker = false,
  title,
  chatSidebar = 'top',
  chatKnowledgeBaseId = '',
  onChatKnowledgeBaseChange,
}: {
  assistant: AuditAssistant
  knowledgeBases: KnowledgeBase[]
  onChanged: () => void
  lockedKnowledgeBaseId?: string
  initialTab?: MainTab
  embedded?: boolean
  backTo?: string
  hideKnowledgePicker?: boolean
  title?: string
  chatSidebar?: 'top' | 'left'
  chatKnowledgeBaseId?: string
  onChatKnowledgeBaseChange?: (knowledgeBaseId: string) => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [baselineVersion, setBaselineVersion] = useState<AssistantVersion | null>(null)
  const [kbSelected, setKbSelected] = useState(() =>
    lockedKnowledgeBaseId
      ? new Set([lockedKnowledgeBaseId])
      : new Set(assistant.knowledge_bases.map(item => item.id)),
  )
  const [draftManualRules, setDraftManualRules] = useState<ManualRuleDraft[]>([])
  const [baselineManualRules, setBaselineManualRules] = useState<ManualRuleDraft[]>([])
  const [saving, setSaving] = useState(false)
  const [mainTab, setMainTab] = useState<MainTab>(initialTab)
  const [editStepId, setEditStepId] = useState<FlowStepId | null>(null)
  const [queryPlannerPane, setQueryPlannerPane] = useState<'routes' | 'rules' | 'prompt'>('routes')
  const [auditJudgePane, setAuditJudgePane] = useState<'rules' | 'prompt'>('rules')
  const [reportParamsPane, setReportParamsPane] = useState<'fields' | 'rules' | 'prompt'>('fields')
  const [notesOnlyPane, setNotesOnlyPane] = useState<'rules' | 'prompt'>('rules')
  const [confirmAction, setConfirmAction] = useState<'reinitialize' | null>(null)
  const [initializing, setInitializing] = useState(false)
  const [initFlowDismissed, setInitFlowDismissed] = useState(false)
  const [initBannerDismissed, setInitBannerDismissed] = useState(false)
  const [pendingInitialization, setPendingInitialization] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatStatus, setChatStatus] = useState('')
  const [agentFlow, setAgentFlow] = useState<string[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [conversationItems, setConversationItems] = useState<AgentConversation[]>([])
  const [conversationLoading, setConversationLoading] = useState(false)
  const [conversationError, setConversationError] = useState('')
  const [messages, setMessages] = useState<AssistantChatMessage[]>([])

  useEffect(() => {
    setConversationId(null)
    setMessages([])
    setAgentFlow([])
    setConversationItems([])
    setConversationLoading(true)
    setConversationError('')
    let active = true
    void api.listAgentConversations(assistant.id)
      .then(result => {
        if (active) {
          setConversationItems(result.items)
          setConversationError('')
        }
      })
      .catch(() => {
        if (active) {
          setConversationItems([])
          setConversationError('对话历史加载失败')
        }
      })
      .finally(() => {
        if (active) setConversationLoading(false)
      })
    return () => {
      active = false
    }
  }, [assistant.id])

  useEffect(() => {
    setMainTab(initialTab)
  }, [initialTab])

  useEffect(() => {
    if (lockedKnowledgeBaseId) {
      setKbSelected(new Set([lockedKnowledgeBaseId]))
    }
  }, [lockedKnowledgeBaseId, assistant.id])

  const primaryKbId = [...kbSelected][0] || lockedKnowledgeBaseId || ''
  const { data: boundKb } = useKnowledgeBase(primaryKbId || undefined)

  useEffect(() => {
    if (!boundKb) {
      setDraftManualRules([])
      setBaselineManualRules([])
      return
    }
    const next = normalizeManualRuleDrafts(boundKb.manual_rules)
    setDraftManualRules(next)
    setBaselineManualRules(next)
  }, [boundKb?.id, boundKb?.updated_at])

  const activeQuery = useQuery({
    queryKey: queryKeys.assistantVersion(assistant.id),
    queryFn: () => api.getActiveAssistantVersion(assistant.id),
  })
  const hasActiveVersion = Boolean(activeQuery.data || assistant.active_version)
  const categoryInitNeeded = needsCategoryInit(
    assistant.id,
    version?.initialization_provenance,
  )
  const showInitializationPage = Boolean(
    lockedKnowledgeBaseId
    && assistant.id !== GENERIC_TEMPLATE_ASSISTANT_ID
    && (initializing || !hasActiveVersion),
  )
  const showInitBanner = Boolean(
    lockedKnowledgeBaseId
    && categoryInitNeeded
    && !showInitializationPage
    && !initBannerDismissed
    && hasActiveVersion,
  )

  useEffect(() => {
    const active = activeQuery.data || null
    setVersion(active)
    setBaselineVersion(active)
  }, [activeQuery.data])

  useEffect(() => {
    setKbSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
  }, [assistant])

  useEffect(() => {
    setInitializing(false)
    setInitFlowDismissed(false)
    setInitBannerDismissed(false)
    setConfirmAction(null)
    setPendingInitialization(false)
  }, [assistant.id])

  useEffect(() => {
    if (
      !lockedKnowledgeBaseId
      || assistant.id === GENERIC_TEMPLATE_ASSISTANT_ID
      || !hasActiveVersion
      || !version
      || initFlowDismissed
      || !categoryInitNeeded
    ) {
      return
    }
    setInitializing(true)
  }, [
    lockedKnowledgeBaseId,
    assistant.id,
    hasActiveVersion,
    version,
    initFlowDismissed,
    categoryInitNeeded,
  ])

  const isDirty = useMemo(() => {
    const versionDirty = versionDraftSignature(version, kbSelected)
      !== versionDraftSignature(
        baselineVersion,
        assistant.knowledge_bases.map(item => item.id),
      )
    const rulesDirty = manualRulesSignature(draftManualRules)
      !== manualRulesSignature(baselineManualRules)
    return versionDirty || rulesDirty
  }, [
    assistant.knowledge_bases,
    baselineManualRules,
    baselineVersion,
    draftManualRules,
    kbSelected,
    version,
  ])
  const navigationBlocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty && currentLocation.pathname !== nextLocation.pathname,
  )

  useEffect(() => {
    if (!isDirty) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [isDirty])

  const jumpToReportParameters = () => {
    setMainTab('workflow')
    setEditStepId('report_parameters')
  }

  const stepBindings = useMemo(() => {
    const namingName = boundKb?.default_naming_file_name || boundKb?.default_naming_file_id
    const schemaCount = version?.parameter_schema?.fields?.length || 0
    const map: Partial<Record<FlowStepId, Array<{ label: string; value: string }>>> = {
      report_parameters: [
        {
          label: '参数 schema',
          value: schemaCount ? `${schemaCount} 个字段（本步主配置）` : '尚未配置字段',
        },
      ],
      test_items: [
        {
          label: '输入',
          value: '报告 Markdown（检测结果汇总及跨页续表）',
        },
        {
          label: '产出',
          value: '检测项目列表 + 各条报告标准要求 + sample_context',
        },
      ],
      model_decode: [
        {
          label: '命名规则 PDF',
          value: namingName
            ? String(namingName)
            : '未绑定（运行时用评估兜底规则；请到知识库「配置」上传）',
        },
        {
          label: '注入方式',
          value: '运行时作为 naming_rule_markdown 传入，不写在提示词正文里',
        },
        {
          label: '下游合并',
          value: '解码结果与报告参数合并为 sample_profile，供规划检索与审查判定使用',
        },
      ],
      audit_judge: [
        {
          label: '判定约定',
          value: draftManualRules.length
            ? `${draftManualRules.length} 条（本步左侧编辑，写入知识库）`
            : '未配置（可选；本步左侧可添加）',
        },
      ],
      candidate_retrieval: [
        {
          label: '证据范围',
          value: '本库已启用语料文件（标准/规范）',
        },
      ],
    }
    return map
  }, [boundKb, draftManualRules.length, version])

  const currentModel = String(version?.model_config?.model || 'deepseek-v4-flash')
  const modelOptions = useMemo(
    () => MODEL_OPTIONS.map(item => ({ ...item, icon: <ModelMark /> })),
    [],
  )
  const kbOptions = useMemo(
    () =>
      knowledgeBases.map(kb => ({
        value: kb.id,
        label: kb.name,
        hint: `${kb.file_count} 文件`,
      })),
    [knowledgeBases],
  )

  const updateRetrieval = (key: string, value: number | boolean) => {
    if (!version) return
    setVersion({
      ...version,
      retrieval_config: { ...version.retrieval_config, [key]: value },
    })
  }

  const updateModel = (key: string, value: string | number) => {
    if (!version) return
    setVersion({
      ...version,
      model_config: { ...version.model_config, [key]: value },
    })
  }

  const updatePrompt = (content: string) => {
    if (!version || !editStepId) return
    setVersion({
      ...version,
      node_prompts: {
        ...version.node_prompts,
        [editStepId]: { ...(version.node_prompts[editStepId] || {}), content },
      },
    })
  }

  const updateParameterSchema = (next: ParameterSchema) => {
    if (!version) return
    setVersion({ ...version, parameter_schema: next })
  }

  const updateQueryPlannerRoutes = (next: QueryPlannerRoute[]) => {
    if (!version) return
    setVersion({
      ...version,
      retrieval_config: {
        ...version.retrieval_config,
        query_planner_routes: resolveQueryPlannerRoutes(next),
      },
    })
  }

  const updateStepRules = (stepId: string, rules: StepRuleDraft[]) => {
    if (!version) return
    setVersion({
      ...version,
      rules: setStepRulesOnVersionRules(version.rules, stepId, rules),
    })
  }

  const stepRulesFor = (stepId: string) => getStepRulesFromVersionRules(version?.rules, stepId)

  const queryPlannerRoutes = useMemo(
    () => resolveQueryPlannerRoutes(version?.retrieval_config?.query_planner_routes),
    [version?.retrieval_config?.query_planner_routes],
  )

  const saveAll = async (): Promise<boolean> => {
    if (!version) return false
    setSaving(true)
    try {
      if (!hideKnowledgePicker) {
        const kbIds = lockedKnowledgeBaseId
          ? Array.from(new Set([lockedKnowledgeBaseId, ...kbSelected]))
          : [...kbSelected]
        await api.setAssistantKnowledgeBases(assistant.id, kbIds)
      }
      if (primaryKbId) {
        const savedKb = await api.updateKnowledgeBase(primaryKbId, {
          manual_rules: {
            version: Number(boundKb?.manual_rules?.version || 1),
            scope: 'knowledge_base_manual_rules',
            status: String(boundKb?.manual_rules?.status || 'draft'),
            rules: draftManualRules,
          },
        })
        setDraftManualRules(normalizeManualRuleDrafts(savedKb.manual_rules))
        setBaselineManualRules(normalizeManualRuleDrafts(savedKb.manual_rules))
        await queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeBases })
      }
      const saved = await api.updateActiveAssistantVersion(assistant.id, {
        model_config: version.model_config,
        node_prompts: version.node_prompts,
        rules: version.rules,
        retrieval_config: version.retrieval_config,
        parameter_schema: version.parameter_schema,
        initialization_provenance: version.initialization_provenance,
      })
      setVersion(saved)
      setBaselineVersion(saved)
      await activeQuery.refetch()
      onChanged()
      toast.success('已保存')
      return true
    } catch (err) {
      toast.error((err as Error).message)
      return false
    } finally {
      setSaving(false)
    }
  }

  const requestReinitialize = () => {
    if (showInitializationPage) return
    if (isDirty) {
      setPendingInitialization(true)
      return
    }
    setConfirmAction('reinitialize')
  }

  const cancelUnsavedTransition = () => {
    setPendingInitialization(false)
    if (navigationBlocker.state === 'blocked') navigationBlocker.reset()
  }

  const discardUnsavedAndContinue = async () => {
    const shouldInitialize = pendingInitialization
    setPendingInitialization(false)
    if (shouldInitialize) {
      setVersion(baselineVersion)
      setKbSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
      setDraftManualRules(baselineManualRules)
      setConfirmAction('reinitialize')
      return
    }
    if (navigationBlocker.state === 'blocked') navigationBlocker.proceed()
  }

  const saveUnsavedAndContinue = async () => {
    const shouldInitialize = pendingInitialization
    const saved = await saveAll()
    if (!saved) return
    setPendingInitialization(false)
    if (shouldInitialize) {
      setConfirmAction('reinitialize')
      return
    }
    if (navigationBlocker.state === 'blocked') navigationBlocker.proceed()
  }

  const refreshConversations = async () => {
    try {
      const result = await api.listAgentConversations(assistant.id)
      setConversationItems(result.items)
      setConversationError('')
    } catch {
      setConversationError('对话历史加载失败')
    }
  }

  const startNewChat = () => {
    if (chatBusy) return
    setConversationId(null)
    setMessages([])
    setChatStatus('')
    setAgentFlow([])
  }

  const openConversation = async (id: string) => {
    if (chatBusy || id === conversationId) return
    setChatBusy(true)
    try {
      const conversation = await api.getAgentConversation(assistant.id, id)
      setConversationId(id)
      setMessages(messagesFromConversation(conversation.events))
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setChatBusy(false)
    }
  }

  const sendChat = async () => {
    const text = chatInput.trim()
    if (!text || chatBusy) return
    const userMsg = { id: `u_${Date.now()}`, role: 'user' as const, content: text }
    const assistantMsgId = `a_${Date.now()}`
    setMessages(prev => [
      ...prev,
      userMsg,
      { id: assistantMsgId, role: 'assistant', content: '' },
    ])
    setChatInput('')
    setChatBusy(true)
    setAgentFlow([AGENT_FLOW_STEPS.understand])
    setChatStatus('正在理解问题…')
    try {
      const idempotencyKey = crypto.randomUUID()
      for await (const event of api.streamAgentChatAssistant(assistant.id, {
        message: text,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      }, { idempotencyKey })) {
        if (event.event === 'conversation') {
          if (typeof event.data.conversation_id === 'string') {
            setConversationId(event.data.conversation_id)
          }
          continue
        }
        if (event.event === 'agent') {
          const type = String(event.data.type || '')
          if (type === 'token') {
            const token = typeof event.data.content === 'string' ? event.data.content : ''
            if (token) {
              setChatStatus('正在输出回答…')
              setMessages(prev => prev.map(item => (
                item.id === assistantMsgId
                  ? { ...item, content: item.content + token }
                  : item
              )))
            }
          } else if (type === 'tool_call') {
            // Tool names and execution traces are not user-facing content.
            // Clear any pre-tool narration that may have arrived as tokens.
            const flowStep = agentFlowStepForTool(event.data.name)
            setAgentFlow(prev => appendAgentFlow(prev, flowStep))
            setChatStatus(`${flowStep}…`)
            setMessages(prev => prev.map(item => (
              item.id === assistantMsgId ? { ...item, content: '' } : item
            )))
          } else if (type === 'tool_result') {
            const toolResult = event.data.result
            const hasChart = toolResult && typeof toolResult === 'object'
              && 'chart' in toolResult && Boolean((toolResult as Record<string, unknown>).chart)
            const flowStep = hasChart
              ? AGENT_FLOW_STEPS.chart
              : String(event.data.name || '') === 'search_knowledge_base'
                ? AGENT_FLOW_STEPS.evidence
                : AGENT_FLOW_STEPS.data
            setAgentFlow(prev => appendAgentFlow(prev, flowStep))
            setChatStatus(`${flowStep}…`)
          } else if (type === 'turn_started') {
            const turn = Number(event.data.turn || 1)
            if (turn > 1) {
              setAgentFlow(prev => appendAgentFlow(prev, '补充检索'))
              setChatStatus('正在补充和校验依据…')
            } else {
              setChatStatus('正在分析问题意图…')
            }
          } else if (type === 'assistant_message') {
            const message = event.data.message
            if (message && typeof message === 'object') {
              const record = message as Record<string, unknown>
              const toolCalls = Array.isArray(record.tool_calls) ? record.tool_calls : []
              // A tool-call assistant message is an execution trace. The
              // user-facing answer comes from the final assistant message.
              if (toolCalls.length > 0) continue
              setAgentFlow(prev => appendAgentFlow(prev, AGENT_FLOW_STEPS.answer))
              setChatStatus('正在生成回答…')
              const content = record.content
              if (typeof content === 'string' && content) {
                setMessages(prev => prev.map(item => (
                  item.id === assistantMsgId ? { ...item, content } : item
                )))
              }
            }
          }
          continue
        }
        if (event.event === 'final') {
          const result = event.data as unknown as AgentChatResponse
          setAgentFlow(prev => appendAgentFlow(prev, AGENT_FLOW_STEPS.answer))
          setChatStatus('回答即将完成…')
          setConversationId(result.conversation_id)
          setMessages(prev => prev.map(item => (
            item.id === assistantMsgId
              ? {
                  ...item,
                  content: result.answer || '（空回复）',
                  citations: result.citations,
                  charts: result.charts,
                }
              : item
          )))
          await refreshConversations()
          continue
        }
        if (event.event === 'error') {
          throw new Error(String(event.data.error || 'Agent stream failed'))
        }
      }
    } catch (err) {
      toast.error((err as Error).message)
      setMessages(prev => prev.filter(item => item.id !== userMsg.id && item.id !== assistantMsgId))
      setChatInput(text)
      await refreshConversations()
    } finally {
      setChatBusy(false)
      setChatStatus('')
    }
  }

  const manualRulesPayload = useMemo(
    (): ManualKnowledgeRules => ({
      version: Number(boundKb?.manual_rules?.version || 1),
      scope: 'knowledge_base_manual_rules',
      status: String(boundKb?.manual_rules?.status || 'draft'),
      rules: draftManualRules,
    }),
    [boundKb?.manual_rules?.status, boundKb?.manual_rules?.version, draftManualRules],
  )
  const ruleReviewStatus = String(boundKb?.manual_rules?.status || version?.rules?.status || '')

  return (
    <div className={cn('flex h-full overflow-hidden', embedded ? 'bg-transparent' : 'bg-[#f8fafc]')}>
      <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {!(embedded && mainTab === 'chat') && <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[#e5e7eb] bg-white px-6 py-3">
          <div className="flex min-w-0 items-center gap-3">
            {!embedded && (
              <Button
                variant="ghost"
                size="icon"
                className="size-9 shrink-0 rounded-full"
                onClick={() => navigate(backTo || '/knowledge-bases')}
                title="返回"
              >
                <ArrowLeft className="size-4" />
              </Button>
            )}
            <div className="min-w-0">
              <h1 className="truncate text-[24px] font-semibold tracking-tight text-[#111827]">
                {mainTab === 'workflow' && embedded ? '审查配置' : title || assistant.name}
              </h1>
              <p className="text-[14px] text-[#6b7280]">
                {mainTab === 'chat'
                  ? title
                    ? '知识库检索 + 只读业务数据、SQL 与图表工具'
                    : '知识库问答 · 使用已保存的审查配置'
                  : '配置本知识库的报告识别、审查流程与补充约定。'}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {!embedded && (
              <div className="mr-2 hidden items-center rounded-full bg-[#f3f4f6] p-1 sm:flex">
                <button
                  type="button"
                  className={cn(
                    'rounded-full px-3 py-1.5 text-[14px] font-medium transition',
                    mainTab === 'chat' ? 'bg-[#111827] text-white' : 'text-[#4b5563]',
                  )}
                  onClick={() => setMainTab('chat')}
                >
                  问答
                </button>
                <button
                  type="button"
                  className={cn(
                    'rounded-full px-3 py-1.5 text-[14px] font-medium transition',
                    mainTab === 'workflow' ? 'bg-[#111827] text-white' : 'text-[#4b5563]',
                  )}
                  onClick={() => setMainTab('workflow')}
                >
                  审查配置
                </button>
              </div>
            )}
            {!embedded && (
              <Button className="rounded-lg bg-[#111827] text-white hover:bg-[#1f2937]" asChild>
                <Link to="/">去审查</Link>
              </Button>
            )}
          </div>
        </header>}

        {!embedded && (
        <div className="flex gap-2 border-b border-[#e5e7eb] bg-white px-6 py-2 sm:hidden">
          <button
            type="button"
            className={cn(
              'flex-1 rounded-lg py-2 text-[15px] font-medium',
              mainTab === 'chat' ? 'bg-[#111827] text-white' : 'bg-[#f3f4f6] text-[#374151]',
            )}
            onClick={() => setMainTab('chat')}
          >
            问答
          </button>
          <button
            type="button"
            className={cn(
              'flex-1 rounded-lg py-2 text-[15px] font-medium',
              mainTab === 'workflow' ? 'bg-[#111827] text-white' : 'bg-[#f3f4f6] text-[#374151]',
            )}
            onClick={() => setMainTab('workflow')}
          >
            审查配置
          </button>
        </div>
        )}

        {mainTab === 'chat' ? (
        <div className={cn(
          'mx-auto flex w-full max-w-6xl min-h-0 min-w-0 flex-1 gap-4 px-6 py-5',
          chatSidebar === 'left' ? 'flex-col md:flex-row' : 'flex-col',
        )}>
          <aside className={cn(
            'flex min-h-0 min-w-0 shrink-0 flex-col rounded-2xl border border-[#e5e7eb] bg-white p-3 shadow-sm',
            chatSidebar === 'left' ? 'max-h-40 w-full md:max-h-none md:w-64' : 'max-h-40',
          )}>
            {chatSidebar === 'left' && (
              <div className="mb-3 border-b border-[#e5e7eb] px-1 pb-3">
                <Label className="text-xs font-semibold text-[#374151]">问答知识库</Label>
                <SearchableSelect
                  value={chatKnowledgeBaseId}
                  options={kbOptions}
                  placeholder="请选择知识库"
                  searchPlaceholder="搜索知识库…"
                  onChange={onChatKnowledgeBaseChange || (() => undefined)}
                  className="mt-2"
                />
                <p className="mt-2 text-[11px] leading-relaxed text-[#9ca3af]">
                  切换后会使用该库的检索范围和对话历史。
                </p>
              </div>
            )}
            <div className="flex items-center justify-between gap-2 px-1 pb-3">
              <div className="flex items-center gap-2 text-[14px] font-medium text-[#111827]">
                <History className="size-4 text-[#6b7280]" />
                <span>对话历史</span>
                <span className="rounded-full bg-[#f3f4f6] px-1.5 py-0.5 text-[11px] text-[#6b7280]">
                  {conversationItems.length}
                </span>
              </div>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-7 rounded-lg"
                disabled={chatBusy}
                onClick={startNewChat}
                title="新对话"
              >
                <Plus className="size-4" />
              </Button>
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
              {conversationLoading && (
                <div className="px-2 py-3 text-xs text-[#9ca3af]">正在加载历史…</div>
              )}
              {!conversationLoading && conversationError && (
                <div className="px-2 py-3 text-xs text-[#b91c1c]">{conversationError}</div>
              )}
              {!conversationLoading && !conversationError && !conversationItems.length && (
                <div className="px-2 py-3 text-xs leading-relaxed text-[#9ca3af]">
                  暂无历史对话，发送第一条消息后会自动保存。
                </div>
              )}
              {conversationItems.map((item, index) => (
                <button
                  type="button"
                  key={item.id}
                  disabled={chatBusy}
                  onClick={() => void openConversation(item.id)}
                  className={cn(
                    'w-full rounded-xl border px-3 py-2 text-left transition',
                    item.id === conversationId
                      ? 'border-[#111827] bg-[#111827] text-white'
                      : 'border-transparent bg-[#f8fafc] text-[#4b5563] hover:border-[#d1d5db] hover:bg-white',
                  )}
                  title={item.title || `对话 ${index + 1}`}
                >
                  <span className="block truncate text-xs font-medium">
                    {item.title || `对话 ${index + 1}`}
                  </span>
                  <span className="mt-1 block text-[10px] opacity-70">
                    {item.updated_at.slice(0, 10)}
                  </span>
                </button>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              className="mt-3 w-full rounded-xl text-xs"
              disabled={chatBusy}
              onClick={startNewChat}
            >
              <Plus className="size-3.5" />
              新对话
            </Button>
          </aside>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
              {!messages.length && (
                <div className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-2 px-6 text-center">
                  <Bot className="size-10 text-[#9ca3af]" />
                  <p className="text-[16px] font-medium text-[#111827]">
                    {title ? '知识库与业务数据统一问答' : '基于知识库提问'}
                  </p>
                  <p className="max-w-md text-[15px] leading-relaxed text-[#6b7280]">
                    {title
                      ? '问文档事实会检索证据；问统计、SQL 或饼图/柱状图会调用只读业务工具。'
                      : '这里是简单的 RAG 问答。改提示词与参数字段请到「审查配置」；完整审查请用「去审查」。'}
                  </p>
                </div>
              )}
              {messages.map(item => {
                const charts = item.role === 'assistant'
                  ? uniqueBusinessCharts(item.charts ?? [])
                  : []
                return (
                <div
                  key={item.id}
                  className={cn('flex', item.role === 'user' ? 'justify-end' : 'justify-start')}
                >
                  <div
                    className={cn(
                      'max-w-[92%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed',
                      item.role === 'user'
                        ? 'bg-[#111827] text-white'
                        : 'border border-[#dbeafe] bg-[#f7fbff] text-[#111827] shadow-sm',
                    )}
                  >
                    {item.role === 'assistant' && (
                      <div className="mb-3 flex items-center gap-2 border-b border-[#eef2f7] pb-2 text-xs font-semibold text-[#475569]">
                        <span className="grid size-6 place-items-center rounded-lg bg-[#e6fffb] text-[#0f766e]">
                          {charts.length > 0 ? <BarChart3 className="size-3.5" /> : <Bot className="size-3.5" />}
                        </span>
                        <span>{charts.length > 0 ? '业务分析结果' : '知识库回答'}</span>
                        <span className="rounded-full bg-[#f8fafc] px-2 py-0.5 font-normal text-[#94a3b8]">
                          {charts.length > 0 ? '只读查询' : '智能问答'}
                        </span>
                      </div>
                    )}
                    {item.role === 'assistant'
                      ? <AssistantMarkdown text={item.content} />
                      : <div className="whitespace-pre-wrap">{item.content}</div>}
                    {charts.length > 0 && (
                      <section className="mt-4 rounded-2xl border border-[#dbeafe] bg-[#f8fbff] p-3.5">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2 text-sm font-semibold text-[#1e3a8a]">
                            <BarChart3 className="size-4" />
                            数据视图
                          </div>
                          <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-[#64748b]">
                            {charts.length} 项结果
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-[#64748b]">以下内容由业务分析工具返回，可追溯到只读查询结果。</div>
                        <div className="space-y-3">
                          {charts.map((chart, index) => (
                            <InlineChart chart={chart} key={`${item.id}-chart-${index}`} />
                          ))}
                        </div>
                      </section>
                    )}
                    {item.role === 'assistant' && item.citations && item.citations.length > 0 && (
                      <div className="mt-4 space-y-1.5 rounded-xl border border-[#e5e7eb] bg-[#f8fafc] p-3">
                        <div className="text-[13px] font-medium text-[#6b7280]">参考证据</div>
                        {item.citations.slice(0, 4).map((cite, index) => (
                          <div key={`${cite.file_name}-${index}`} className="text-[13px] text-[#6b7280]">
                            [{index + 1}] {cite.file_name || '文件'}
                            {cite.page != null ? ` · p.${cite.page}` : ''}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                )
              })}
              {chatBusy && (
                <div className="hidden flex items-center gap-2 text-[15px] text-[#6b7280]">
                  <Loader2 className="size-4 animate-spin" />
                  检索并回答中…
                </div>
              )}
            </div>

            <div className="border-t border-[#e5e7eb] p-4">
              {chatBusy && agentFlow.length > 0 && (
                <div className="mb-2 flex items-center gap-1 text-[11px] text-[#94a3b8]" aria-live="polite">
                  <Loader2 className="mr-1 size-3 animate-spin text-[#13a2a2]" />
                  <span className="font-medium text-[#64748b]">当前流程：</span>
                  <span className="truncate text-[#0f766e]">{agentFlow.join('  ›  ')}</span>
                  {chatStatus && <span className="ml-1 shrink-0 text-[#64748b]">· {chatStatus}</span>}
                </div>
              )}
              <div className="flex items-end gap-2">
                <Textarea
                  className="min-h-[44px] max-h-32 flex-1 resize-none rounded-xl border-[#e5e7eb] text-[15px]"
                  placeholder={title
                    ? '输入问题，例如：额定容量是什么？或画出审查状态分布饼图'
                    : '输入问题，例如：绝缘电阻的试验要求是什么？'}
                  value={chatInput}
                  disabled={chatBusy}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void sendChat()
                    }
                  }}
                />
                <Button
                  className="h-11 shrink-0 rounded-xl bg-[#111827] px-4 text-white hover:bg-[#1f2937]"
                  disabled={chatBusy || !chatInput.trim()}
                  onClick={() => void sendChat()}
                >
                  {chatBusy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                  发送
                </Button>
              </div>
              <p className="mt-2 text-[13px] text-[#9ca3af]">
                {title
                  ? 'Agent 会根据问题意图选择知识库检索、只读 SQL、schema 或图表工具。'
                  : '使用已保存的模型与知识库配置。改右侧设置后请先点「保存」。'}
              </p>
            </div>
          </div>
        </div>
        ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e7eb] pb-4">
              <div className="flex items-center gap-2 text-[14px] text-[#4b5563]">
                <span
                  className={cn(
                    'size-2 rounded-full',
                    showInitializationPage ? 'bg-[#f59e0b]' : 'bg-[#13c2c2]',
                  )}
                />
                <span className="font-medium text-[#111827]">
                  {showInitializationPage ? '参数提取初始化' : '审查配置'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {showInitializationPage && hasActiveVersion ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="rounded-lg"
                    onClick={() => {
                      setInitializing(false)
                      setInitFlowDismissed(true)
                    }}
                  >
                    稍后再说
                  </Button>
                ) : null}
                {!showInitializationPage && (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="rounded-lg"
                      disabled={saving || !version}
                      onClick={requestReinitialize}
                    >
                      重新生成参数配置
                    </Button>
                    <Button
                      size="sm"
                      className="rounded-lg bg-[#13c2c2] text-white hover:bg-[#0fb3b3]"
                      disabled={saving || !version}
                      onClick={() => void saveAll()}
                      title={helpText.assistants.saveVersion}
                    >
                      {saving ? '保存中…' : '保存'}
                    </Button>
                  </>
                )}
              </div>
            </div>

            {showInitializationPage ? (
              <div className="mx-auto max-w-3xl py-4">
                <AssistantInitDraftCard
                  bare
                  initializationOnly
                  assistantId={assistant.id}
                  activeVersion={assistant.active_version}
                  onJumpToReportParameters={jumpToReportParameters}
                  onApplied={() => {
                    setInitializing(false)
                    setInitFlowDismissed(true)
                    setInitBannerDismissed(true)
                    void activeQuery.refetch()
                    onChanged()
                    jumpToReportParameters()
                    toast.success('已更新审查配置')
                  }}
                />
              </div>
            ) : (
            <>
            {showInitBanner ? (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#f59e0b]/40 bg-[#fffbeb] px-4 py-3">
                <p className="text-[13px] leading-relaxed text-[#92400e]">
                  当前为通用模板副本，建议先初始化报告参数字段后再跑审查。
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="rounded-lg border-[#f59e0b]/50 bg-white"
                    onClick={() => setInitBannerDismissed(true)}
                  >
                    稍后
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    className="rounded-lg bg-[#f59e0b] text-white hover:bg-[#d97706]"
                    onClick={() => {
                      setInitFlowDismissed(false)
                      setInitializing(true)
                    }}
                  >
                    去参数提取初始化
                  </Button>
                </div>
              </div>
            ) : null}
            <div className="min-w-0">
              <div className="min-w-0">
                <section className="border-b border-[#e5e7eb] py-4 first:pt-0">
                  <div className="mb-3">
                    <h2 className="text-[17px] font-semibold text-[#111827]">审查流程</h2>
                    <p className="mt-1 text-[13px] text-[#6b7280]">
                      完整审查流水线共 {PRODUCT_FLOW_STEPS.length} 步；点击 AI 步骤可编辑配置与预览提示词，改完后点右上角「保存」。
                    </p>
                  </div>
                  <ol className="divide-y divide-[#e5e7eb] border-y border-[#e5e7eb]">
                    {PRODUCT_FLOW_STEPS.map((step, index) => {
                      const canEdit = Boolean(step.editStepId)
                      const editTitle =
                        step.editStepId === 'report_parameters'
                          ? '修改字段与本步补充规则'
                          : step.editStepId === 'query_planner'
                            ? '修改改写形式与本步补充规则'
                            : step.editStepId === 'audit_judge'
                              ? '修改判定约定与提示词'
                              : step.editStepId
                                ? '修改本步补充规则'
                                : ''
                      return (
                        <li
                          key={step.id}
                          className={cn(
                            'group grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 py-3 sm:grid-cols-[2rem_minmax(0,1fr)_8rem]',
                            canEdit && version && 'cursor-pointer rounded-lg hover:bg-[#f8fafc]',
                          )}
                          onClick={() => {
                            if (canEdit && version) setEditStepId(step.editStepId || null)
                          }}
                        >
                          <span className="grid size-7 place-items-center rounded-full border border-[#13c2c2] text-[12px] font-medium text-[#0f9f9f]">
                            {index + 1}
                          </span>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-[14px] font-medium text-[#374151]">{step.label}</span>
                              <span className="rounded bg-[#f3f4f6] px-1.5 py-0.5 text-[11px] text-[#6b7280]">
                                {step.kind}
                              </span>
                            </div>
                            <div className="truncate text-[12px] text-[#9ca3af]">{step.summary}</div>
                          </div>
                          <div className="flex items-center justify-end sm:grid sm:grid-cols-[4.5rem_2rem] sm:gap-2">
                            <span className="hidden items-center gap-1 text-[12px] text-[#6b7280] sm:flex">
                              {canEdit ? (
                                <>
                                  <CheckCircle2 className="size-3.5 text-[#13c2c2]" />
                                  可配置
                                </>
                              ) : (
                                <span className="text-[#9ca3af]">系统步骤</span>
                              )}
                            </span>
                            {canEdit ? (
                              <button
                                type="button"
                                className="grid size-7 place-items-center justify-self-end rounded-md text-[#0f9f9f] transition hover:bg-[#ecfdfd] hover:text-[#0b7f7f] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#13c2c2]/30 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={!version}
                                onClick={event => {
                                  event.stopPropagation()
                                  setEditStepId(step.editStepId || null)
                                }}
                                title={editTitle}
                                aria-label={editTitle}
                              >
                                <Pencil className="size-3.5" />
                              </button>
                            ) : (
                              <span aria-hidden className="hidden sm:block" />
                            )}
                          </div>
                        </li>
                      )
                    })}
                  </ol>
                </section>

                <section className="py-4">
                  <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <h2 className="text-[17px] font-semibold text-[#111827]">判定约定</h2>
                      <p className="mt-1 text-[13px] text-[#6b7280]">
                        {draftManualRules.length} 条 · 写入本库；在「对照标准判定」步骤中编辑（知识库「配置」页也可改）
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 text-[#0f9f9f]"
                      onClick={() => setEditStepId('audit_judge')}
                    >
                      编辑规则
                    </Button>
                  </div>
                  <div className="divide-y divide-[#e5e7eb] rounded-lg border border-[#e5e7eb]">
                    {draftManualRules.map((rule, index) => (
                      <details key={String(rule.rule_id || index)} className="group bg-white">
                        <summary className="grid cursor-pointer list-none grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-3 py-3">
                          <ChevronRight className="size-4 text-[#9ca3af] transition group-open:rotate-90" />
                          <span className="truncate text-[14px] text-[#374151]">
                            {String(rule.rule_text || rule.rule_id || `补充约定 ${index + 1}`)}
                          </span>
                          <span className="flex items-center gap-1.5 text-[12px] text-[#a16207]">
                            <span className="size-2 rounded-full bg-[#f59e0b]" />
                            {ruleReviewStatus.includes('pending') ? '待领域复核' : '已配置'}
                          </span>
                        </summary>
                        <div className="border-t border-[#e5e7eb] bg-[#f8fafc] px-10 py-3 text-[13px] leading-relaxed text-[#6b7280]">
                          <p>{String(rule.rule_text || '')}</p>
                          {Array.isArray(rule.applies_when) && (
                            <ul className="mt-2 list-disc space-y-1 pl-5">
                              {rule.applies_when.map(item => <li key={String(item)}>{String(item)}</li>)}
                            </ul>
                          )}
                        </div>
                      </details>
                    ))}
                    {!draftManualRules.length && (
                      <div className="px-3 py-8 text-center text-[13px] text-[#9ca3af]">暂无补充约定</div>
                    )}
                  </div>
                </section>
              </div>

            </div>
            </>
            )}
          </div>

          <Dialog
            open={editStepId !== null}
            onOpenChange={open => {
              if (!open) {
                setEditStepId(null)
                setReportParamsPane('fields')
                setQueryPlannerPane('routes')
                setAuditJudgePane('rules')
                setNotesOnlyPane('rules')
              }
            }}
          >
            <DialogContent
              className="flex h-[min(860px,92vh)] w-[min(1280px,96vw)] max-w-none flex-col gap-0 overflow-hidden p-0"
              aria-describedby={undefined}
            >
              <DialogHeader className="shrink-0 border-b border-[#e5e7eb] px-5 py-4">
                <DialogTitle>
                  {PRODUCT_FLOW_STEPS.find(step => step.id === editStepId)?.label
                    || EDITABLE_STEPS.find(step => step.id === editStepId)?.label
                    || '编辑步骤'}
                </DialogTitle>
                <DialogDescription className="text-[13px] text-[#6b7280]">
                  {editStepId === 'report_parameters'
                    ? '左侧改字段或本步补充规则，右侧为本步预览提示词。关闭后记得点「保存」。'
                    : editStepId === 'query_planner'
                      ? '左侧勾选改写形式或添加本步补充规则，右侧为本步预览提示词。关闭后记得点「保存」。'
                      : editStepId === 'audit_judge'
                        ? '左侧编辑本库判定约定（人工），右侧为本步预览提示词。关闭后记得点「保存」。'
                        : '左侧添加本步补充规则，右侧为本步预览提示词（琥珀色为变量）。关闭后记得点「保存」。'}
                </DialogDescription>
              </DialogHeader>
              {editStepId === 'report_parameters' && version?.parameter_schema ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="flex shrink-0 gap-1 border-b border-[#e5e7eb] px-5 py-2 md:hidden">
                    {([
                      ['fields', '字段'],
                      ['rules', '本步补充规则'],
                      ['prompt', '预览'],
                    ] as const).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={cn(
                          'rounded-md px-3 py-1.5 text-[13px]',
                          reportParamsPane === key
                            ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                            : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                        )}
                        onClick={() => setReportParamsPane(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="grid min-h-0 flex-1 md:grid-cols-2">
                    <div
                      className={cn(
                        'min-h-0 border-[#e5e7eb] px-5 py-4 md:border-r',
                        reportParamsPane === 'prompt' ? 'hidden md:flex md:flex-col' : 'flex flex-col',
                        reportParamsPane === 'rules' ? 'overflow-hidden' : 'overflow-y-auto',
                      )}
                    >
                      <div className="mb-3 hidden shrink-0 gap-1 md:flex">
                        {([
                          ['fields', '字段'],
                          ['rules', '本步补充规则'],
                        ] as const).map(([key, label]) => {
                          const active = reportParamsPane === key
                            || (reportParamsPane === 'prompt' && key === 'fields')
                          return (
                            <button
                              key={key}
                              type="button"
                              className={cn(
                                'rounded-md px-3 py-1.5 text-[13px]',
                                active
                                  ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                                  : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                              )}
                              onClick={() => setReportParamsPane(key)}
                            >
                              {label}
                            </button>
                          )
                        })}
                      </div>
                      {reportParamsPane === 'rules' ? (
                        <ManualRulesEditor
                          rules={stepRulesFor('report_parameters')}
                          onChange={rules => updateStepRules('report_parameters', rules)}
                          hint="人工为本步添加的规则（标识 + 正文），写入助手配置并出现在右侧琥珀色变量区。"
                        />
                      ) : (
                        <ParameterSchemaEditor
                          schema={version.parameter_schema}
                          onChange={updateParameterSchema}
                          splitPane
                        />
                      )}
                    </div>
                    <div
                      className={cn(
                        'min-h-0 overflow-hidden px-5 py-4',
                        reportParamsPane === 'prompt' ? 'flex flex-col' : 'hidden md:flex md:flex-col',
                      )}
                    >
                      <PromptTemplateEditor
                        stepId="report_parameters"
                        value={version.node_prompts.report_parameters?.content || ''}
                        onChange={updatePrompt}
                        disabled={!version}
                        pathHint={version.node_prompts.report_parameters?.path || '内置逻辑'}
                        parameterSchema={version.parameter_schema}
                        manualRules={manualRulesPayload}
                        stepRules={stepRulesFor('report_parameters')}
                        kbName={boundKb?.name || assistant.knowledge_bases.find(kb => kb.id === primaryKbId)?.name}
                        kbDescription={boundKb?.description || ''}
                        fillHeight
                        previewOnly
                      />
                    </div>
                  </div>
                </div>
              ) : editStepId === 'query_planner' && version ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="flex shrink-0 gap-1 border-b border-[#e5e7eb] px-5 py-2 md:hidden">
                    {([
                      ['routes', '改写形式'],
                      ['rules', '本步补充规则'],
                      ['prompt', '预览'],
                    ] as const).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={cn(
                          'rounded-md px-3 py-1.5 text-[13px]',
                          queryPlannerPane === key
                            ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                            : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                        )}
                        onClick={() => setQueryPlannerPane(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="grid min-h-0 flex-1 md:grid-cols-2">
                    <div
                      className={cn(
                        'min-h-0 border-[#e5e7eb] px-5 py-4 md:border-r',
                        queryPlannerPane === 'prompt' ? 'hidden md:flex md:flex-col' : 'flex flex-col',
                        queryPlannerPane === 'rules' ? 'overflow-hidden' : 'overflow-y-auto',
                      )}
                    >
                      <div className="mb-3 hidden shrink-0 gap-1 md:flex">
                        {([
                          ['routes', '改写形式'],
                          ['rules', '本步补充规则'],
                        ] as const).map(([key, label]) => {
                          const active = queryPlannerPane === key
                            || (queryPlannerPane === 'prompt' && key === 'routes')
                          return (
                            <button
                              key={key}
                              type="button"
                              className={cn(
                                'rounded-md px-3 py-1.5 text-[13px]',
                                active
                                  ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                                  : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                              )}
                              onClick={() => setQueryPlannerPane(key)}
                            >
                              {label}
                            </button>
                          )
                        })}
                      </div>
                      {queryPlannerPane === 'rules' ? (
                        <ManualRulesEditor
                          rules={stepRulesFor('query_planner')}
                          onChange={rules => updateStepRules('query_planner', rules)}
                          hint="人工为本步添加的规则（标识 + 正文），写入助手配置并出现在右侧琥珀色变量区。"
                        />
                      ) : (
                        <QueryPlannerRoutesEditor
                          routes={queryPlannerRoutes}
                          onChange={updateQueryPlannerRoutes}
                        />
                      )}
                    </div>
                    <div
                      className={cn(
                        'min-h-0 overflow-hidden px-5 py-4',
                        queryPlannerPane === 'prompt' ? 'flex flex-col' : 'hidden md:flex md:flex-col',
                      )}
                    >
                      <PromptTemplateEditor
                        stepId="query_planner"
                        value={version.node_prompts.query_planner?.content || ''}
                        onChange={updatePrompt}
                        disabled={!version}
                        pathHint={version.node_prompts.query_planner?.path || '内置逻辑'}
                        parameterSchema={version.parameter_schema}
                        manualRules={manualRulesPayload}
                        stepRules={stepRulesFor('query_planner')}
                        kbName={boundKb?.name || assistant.knowledge_bases.find(kb => kb.id === primaryKbId)?.name}
                        kbDescription={boundKb?.description || ''}
                        queryPlannerRoutes={queryPlannerRoutes}
                        fillHeight
                        previewOnly
                      />
                    </div>
                  </div>
                </div>
              ) : editStepId === 'audit_judge' && version ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="flex shrink-0 gap-1 border-b border-[#e5e7eb] px-5 py-2 md:hidden">
                    {([
                      ['rules', '判定约定'],
                      ['prompt', '预览'],
                    ] as const).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={cn(
                          'rounded-md px-3 py-1.5 text-[13px]',
                          auditJudgePane === key
                            ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                            : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                        )}
                        onClick={() => setAuditJudgePane(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="grid min-h-0 flex-1 md:grid-cols-2">
                    <div
                      className={cn(
                        'min-h-0 overflow-y-auto border-[#e5e7eb] px-5 py-4 md:border-r',
                        auditJudgePane === 'prompt' ? 'hidden md:block' : 'block',
                      )}
                    >
                      {primaryKbId ? (
                        <ManualRulesEditor
                          rules={draftManualRules}
                          onChange={setDraftManualRules}
                          kbName={boundKb?.name || ''}
                          hint="判定约定写入绑定知识库；完整条文进入判定输入的 manual_knowledge_rules，右侧预览显示摘要。知识库「配置」页为同一份数据。"
                        />
                      ) : (
                        <p className="text-[13px] leading-relaxed text-[#6b7280]">
                          尚未绑定知识库。判定约定保存在知识库中，请先绑定后再编辑。
                        </p>
                      )}
                    </div>
                    <div
                      className={cn(
                        'min-h-0 overflow-hidden px-5 py-4',
                        auditJudgePane === 'prompt' ? 'flex flex-col' : 'hidden md:flex md:flex-col',
                      )}
                    >
                      <PromptTemplateEditor
                        stepId="audit_judge"
                        value={version.node_prompts.audit_judge?.content || ''}
                        onChange={updatePrompt}
                        disabled={!version}
                        pathHint={version.node_prompts.audit_judge?.path || '内置逻辑'}
                        parameterSchema={version.parameter_schema}
                        manualRules={manualRulesPayload}
                        kbName={boundKb?.name || assistant.knowledge_bases.find(kb => kb.id === primaryKbId)?.name}
                        kbDescription={boundKb?.description || ''}
                        fillHeight
                        previewOnly
                      />
                    </div>
                  </div>
                </div>
              ) : editStepId && version && (editStepId === 'test_items' || editStepId === 'model_decode') ? (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="flex shrink-0 gap-1 border-b border-[#e5e7eb] px-5 py-2 md:hidden">
                    {([
                      ['rules', '本步补充规则'],
                      ['prompt', '预览'],
                    ] as const).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={cn(
                          'rounded-md px-3 py-1.5 text-[13px]',
                          notesOnlyPane === key
                            ? 'bg-[#ecfdfd] font-medium text-[#0f766e]'
                            : 'text-[#6b7280] hover:bg-[#f3f4f6]',
                        )}
                        onClick={() => setNotesOnlyPane(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="grid min-h-0 flex-1 md:grid-cols-2">
                    <div
                      className={cn(
                        'min-h-0 overflow-y-auto border-[#e5e7eb] px-5 py-4 md:border-r',
                        notesOnlyPane === 'prompt' ? 'hidden md:block' : 'block',
                      )}
                    >
                      {(stepBindings[editStepId] || []).length > 0 && (
                        <div className="mb-3 space-y-1.5 rounded-xl border border-[#e5e7eb] bg-[#f8fafc] px-3 py-2.5">
                          <div className="text-[13px] font-medium text-[#374151]">运行时绑定</div>
                          {stepBindings[editStepId]!.map(item => (
                            <div key={item.label} className="text-[13px] leading-relaxed text-[#6b7280]">
                              <span className="font-medium text-[#4b5563]">{item.label}：</span>
                              {item.value}
                            </div>
                          ))}
                        </div>
                      )}
                      <ManualRulesEditor
                        rules={stepRulesFor(editStepId)}
                        onChange={rules => updateStepRules(editStepId, rules)}
                        hint="人工为本步添加的规则（标识 + 正文），写入助手配置并出现在右侧琥珀色变量区。"
                      />
                    </div>
                    <div
                      className={cn(
                        'min-h-0 overflow-hidden px-5 py-4',
                        notesOnlyPane === 'prompt' ? 'flex flex-col' : 'hidden md:flex md:flex-col',
                      )}
                    >
                      <PromptTemplateEditor
                        stepId={editStepId}
                        value={version.node_prompts[editStepId]?.content || ''}
                        onChange={updatePrompt}
                        disabled={!version}
                        pathHint={version.node_prompts[editStepId]?.path || '内置逻辑'}
                        parameterSchema={version.parameter_schema}
                        manualRules={manualRulesPayload}
                        stepRules={stepRulesFor(editStepId)}
                        kbName={boundKb?.name || assistant.knowledge_bases.find(kb => kb.id === primaryKbId)?.name}
                        kbDescription={boundKb?.description || ''}
                        queryPlannerRoutes={queryPlannerRoutes}
                        fillHeight
                        previewOnly
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
                  <p className="text-[13px] text-[#6b7280]">该步骤暂无可编辑配置。</p>
                </div>
              )}
              <DialogFooter className="shrink-0 border-t border-[#e5e7eb] px-5 py-3">
                <Button
                  type="button"
                  className="bg-[#13c2c2] text-white hover:bg-[#0faaaa]"
                  onClick={() => setEditStepId(null)}
                >
                  完成
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Dialog open={confirmAction !== null} onOpenChange={open => !open && setConfirmAction(null)}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>重新生成参数提取配置？</DialogTitle>
                <DialogDescription className="leading-relaxed text-[#6b7280]">
                  将进入参数提取初始化，根据样例报告重新生成参数 schema 与抽参品类约束。
                  确认启用后会覆盖当前审查配置，此操作不可撤销。
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setConfirmAction(null)}>
                  取消
                </Button>
                <Button
                  type="button"
                  className="bg-[#dc2626] text-white hover:bg-[#b91c1c]"
                  onClick={() => {
                    setInitializing(true)
                    setConfirmAction(null)
                  }}
                >
                  重新生成参数配置
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Dialog
            open={pendingInitialization || navigationBlocker.state === 'blocked'}
            onOpenChange={open => !open && cancelUnsavedTransition()}
          >
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>是否保存当前修改？</DialogTitle>
                <DialogDescription className="leading-relaxed text-[#6b7280]">
                  当前配置有未保存的字段、规则或提示词修改。
                  {pendingInitialization
                    ? ' 保存后将进入参数提取初始化。'
                    : ' 保存后将继续离开当前页面。'}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter className="sm:justify-between">
                <Button type="button" variant="outline" onClick={cancelUnsavedTransition}>
                  取消
                </Button>
                <div className="flex flex-col-reverse gap-2 sm:flex-row">
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-[#b42318] hover:bg-[#fef2f2] hover:text-[#b42318]"
                    onClick={() => void discardUnsavedAndContinue()}
                  >
                    放弃修改
                  </Button>
                  <Button
                    type="button"
                    className="bg-[#13c2c2] text-white hover:bg-[#0faaaa]"
                    disabled={saving}
                    onClick={() => void saveUnsavedAndContinue()}
                  >
                    {saving ? '保存中…' : '保存并继续'}
                  </Button>
                </div>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
        )}
      </section>

      {mainTab === 'chat' && (
      <aside className="hidden w-[340px] shrink-0 flex-col border-l border-[#e5e7eb] bg-white lg:flex">
        <div className="border-b border-[#e5e7eb] px-5 py-4">
          <h3 className="text-[18px] font-semibold text-[#111827]">助手设置</h3>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="grid size-12 shrink-0 place-items-center rounded-xl border border-dashed border-[#d1d5db] bg-[#f9fafb] text-[16px] font-semibold text-[#6b7280]">
              {(assistant.name || '助').slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="truncate text-[16px] font-medium text-[#111827]">{assistant.name}</div>
              <p className="line-clamp-2 text-[15px] leading-relaxed text-[#9ca3af]">
                {assistant.description || '内置油变审查流程'}
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-[15px] font-medium text-[#374151]">模型</Label>
            <SearchableSelect
              value={currentModel}
              options={modelOptions}
              placeholder="请选择模型"
              onChange={value => {
                updateModel('model', value)
                updateModel('provider', 'deepseek')
              }}
            />
          </div>

          {!hideKnowledgePicker && !lockedKnowledgeBaseId && (
          <div className="space-y-2">
            <Label className="text-[15px] font-medium text-[#374151]">知识库</Label>
            <SearchableMultiSelect
              values={[...kbSelected]}
              options={kbOptions}
              placeholder="请选择"
              emptyText="没有找到数据。"
              onChange={ids => setKbSelected(new Set(ids))}
            />
            {!kbSelected.size && (
              <p className="text-[13px] leading-relaxed text-[#9ca3af]">
                先绑定知识库。新品类还需在库内上传标准 PDF，并配置命名规则。
              </p>
            )}
            {[...kbSelected].map(kbId => {
              const kb = knowledgeBases.find(item => item.id === kbId)
              if (!kb) return null
              return (
                <div
                  key={kbId}
                  className="rounded-xl border border-[#e5e7eb] bg-[#f9fafb] px-3 py-2.5"
                >
                  <div className="truncate text-[15px] font-medium text-[#111827]">{kb.name}</div>
                  <p className="mt-1 text-[13px] text-[#6b7280]">
                    命名规则 PDF、人工约定、示例 → 在知识库「配置」管理
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" className="h-8 rounded-lg border-[#e5e7eb]" asChild>
                      <Link to={`/kb/${kbId}/settings`}>
                        <FileText className="size-3.5" />
                        库配置
                      </Link>
                    </Button>
                    <Button variant="ghost" size="sm" className="h-8" asChild>
                      <Link to={`/kb/${kbId}/files`}>文件概览</Link>
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
          )}

          {version && (
            <div className="space-y-5">
              <div>
                <SettingHint label="Dense 召回阈值" tip="向量召回阶段的最低分数。0 表示不做 Dense 预过滤；数值越高，进入 RRF 的候选越少。" />
                <SettingSlider
                  value={Number(version.retrieval_config.dense_threshold ?? 0)}
                  min={0}
                  max={1}
                  step={0.01}
                  format={v => v.toFixed(2)}
                  onChange={v => updateRetrieval('dense_threshold', Math.round(v * 100) / 100)}
                />
              </div>
              <div>
                <SettingHint label="Rerank 结果阈值" tip="重排完成后的最低相关性分数。0 表示不做 Rerank 结果过滤；它不参与 RRF 计算。" />
                <SettingSlider
                  value={Number(version.retrieval_config.rerank_threshold ?? 0.2)}
                  min={0}
                  max={1}
                  step={0.01}
                  format={v => v.toFixed(2)}
                  onChange={v => updateRetrieval('rerank_threshold', Math.round(v * 100) / 100)}
                />
              </div>
              <div>
                <SettingHint label="Top N" tip="最终返回给判定步骤的证据条数。" />
                <SettingSlider
                  value={Number(version.retrieval_config.top_k ?? 10)}
                  min={1}
                  max={50}
                  step={1}
                  format={v => String(Math.round(v))}
                  parse={raw => Math.round(Number(raw))}
                  onChange={v => updateRetrieval('top_k', Math.round(v))}
                />
              </div>
              <div>
                <SettingHint label="每路召回" tip="向量 / 全文等各路先各自召回多少条，再合并重排。" />
                <SettingSlider
                  value={Number(version.retrieval_config.route_top_k ?? 30)}
                  min={1}
                  max={100}
                  step={1}
                  format={v => String(Math.round(v))}
                  parse={raw => Math.round(Number(raw))}
                  onChange={v => updateRetrieval('route_top_k', Math.round(v))}
                />
              </div>
              <div>
                <SettingHint label="温度" tip="生成随机性。审查场景建议保持较低温度。" />
                <SettingSlider
                  value={Number(version.model_config.temperature ?? 0)}
                  min={0}
                  max={2}
                  step={0.1}
                  format={v => v.toFixed(1)}
                  onChange={v => updateModel('temperature', Math.round(v * 10) / 10)}
                />
              </div>
            </div>
          )}

        </div>

        <div className="flex gap-2 border-t border-[#e5e7eb] px-5 py-4">
          <Button
            variant="outline"
            className="flex-1 rounded-lg border-[#e5e7eb] bg-white text-[#111827] hover:bg-[#f9fafb]"
            disabled={saving || !version}
            onClick={() => {
              setVersion(activeQuery.data || null)
              setKbSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
              setDraftManualRules(baselineManualRules.map(item => ({ ...item })))
              toast.message('已还原为当前启用版本')
            }}
          >
            取消
          </Button>
          <Button
            className="flex-1 rounded-lg bg-[#111827] text-white hover:bg-[#1f2937]"
            disabled={saving || !version}
            onClick={() => void saveAll()}
            title={helpText.assistants.saveVersion}
          >
            {saving ? '保存中…' : '保存'}
          </Button>
        </div>
      </aside>
      )}
    </div>
  )
}

function emptyField(): ParameterSchemaField {
  return { key: '', label: '', required: false, hint: '' }
}

function ManualRulesEditor({
  rules,
  onChange,
  kbName,
  hint,
}: {
  rules: ManualRuleDraft[]
  onChange: (next: ManualRuleDraft[]) => void
  kbName?: string
  /** Overrides the default KB-scoped help copy. */
  hint?: string
}) {
  const updateRule = (index: number, patch: Partial<ManualRuleDraft>) => {
    onChange(rules.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-[13px] text-[#6b7280]">
          {hint || (
            <>
              可选；写入绑定知识库{kbName ? `「${kbName}」` : ''}。
              规则正文会进入判定输入的 manual_knowledge_rules，右侧仅显示摘要。
            </>
          )}
          {' '}当前 {rules.length} 条。
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 rounded-md"
          onClick={() => onChange([...rules, emptyManualRule()])}
        >
          <Plus className="size-3.5" />
          添加规则
        </Button>
      </div>
      <div className="space-y-3">
        {rules.map((rule, index) => (
          <div
            key={`${String(rule.rule_id || index)}-${index}`}
            className="space-y-2.5 rounded-lg border border-[#e5e7eb] bg-white px-3 py-3"
          >
            <div className="flex items-start justify-between gap-2">
              <label className="min-w-0 flex-1 space-y-1">
                <span className="text-[12px] text-[#6b7280]">规则标识</span>
                <Input
                  className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 font-mono text-[12px]"
                  value={String(rule.rule_id || '')}
                  onChange={e => updateRule(index, { rule_id: e.target.value.trim() })}
                  aria-label={`规则 ${index + 1} 标识`}
                  placeholder="如 total_loss"
                />
              </label>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="mt-6 size-8 text-[#9ca3af] hover:bg-[#fef2f2] hover:text-[#ef4444]"
                onClick={() => onChange(rules.filter((_, i) => i !== index))}
                title="删除规则"
                aria-label={`删除规则 ${index + 1}`}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
            <label className="block space-y-1">
              <span className="text-[12px] text-[#6b7280]">规则正文</span>
              <Textarea
                className="min-h-[5rem] resize-y rounded-md border-[#e5e7eb] bg-white px-2 py-2 text-[13px] leading-relaxed"
                value={String(rule.rule_text || '')}
                onChange={e => updateRule(index, { rule_text: e.target.value })}
                aria-label={`规则 ${index + 1} 正文`}
                placeholder="例如：总损耗 = 空载损耗 + 负载损耗；仅用于派生计算，不提供标准限值。"
              />
            </label>
          </div>
        ))}
        {!rules.length && (
          <div className="rounded-lg border border-dashed border-[#e5e7eb] px-3 py-8 text-center text-[13px] text-[#9ca3af]">
            暂无判定约定；多数判定可仅靠候选证据完成。
          </div>
        )}
      </div>
    </div>
  )
}

function QueryPlannerRoutesEditor({
  routes,
  onChange,
}: {
  routes: QueryPlannerRoute[]
  onChange: (next: QueryPlannerRoute[]) => void
}) {
  const enabledCount = routes.filter(item => item.enabled).length

  const updateRoute = (index: number, patch: Partial<QueryPlannerRoute>) => {
    const next = routes.map((item, i) => (i === index ? { ...item, ...patch } : item))
    onChange(resolveQueryPlannerRoutes(next))
  }

  return (
    <div className="space-y-3">
      <p className="text-[13px] text-[#6b7280]">
        固定 4 路改写；至少启用 1 路。关闭的路不会要求模型生成，也不会参与检索。
        当前已启用 {enabledCount} 路；右侧预览随改动即时更新。
      </p>
      <div className="space-y-3">
        {routes.map((route, index) => (
          <div
            key={route.id}
            className="space-y-2.5 rounded-lg border border-[#e5e7eb] bg-white px-3 py-3"
          >
            <div className="flex items-start justify-between gap-2">
              <label className="inline-flex items-center gap-2 pt-0.5">
                <input
                  type="checkbox"
                  className="size-4 rounded border-[#d1d5db]"
                  checked={route.enabled}
                  disabled={route.enabled && enabledCount <= 1}
                  onChange={e => updateRoute(index, { enabled: e.target.checked })}
                  aria-label={`启用 ${route.label || route.id}`}
                />
                <span className="text-[13px] font-medium text-[#111827]">启用</span>
              </label>
              <span className="rounded bg-[#f3f4f6] px-2 py-0.5 font-mono text-[11px] text-[#6b7280]">
                {route.id}
              </span>
            </div>
            <label className="block space-y-1">
              <span className="text-[12px] text-[#6b7280]">显示名称</span>
              <Input
                className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 text-[13px]"
                value={route.label}
                onChange={e => updateRoute(index, { label: e.target.value })}
                aria-label={`${route.id} 显示名称`}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-[12px] text-[#6b7280]">改写说明</span>
              <Textarea
                className="min-h-[4.5rem] resize-y rounded-md border-[#e5e7eb] bg-white px-2 py-2 text-[13px] leading-relaxed"
                value={route.instruction}
                onChange={e => updateRoute(index, { instruction: e.target.value })}
                aria-label={`${route.id} 改写说明`}
              />
            </label>
          </div>
        ))}
      </div>
    </div>
  )
}

function ParameterSchemaEditor({
  schema,
  onChange,
  splitPane = false,
}: {
  schema: ParameterSchema
  onChange: (next: ParameterSchema) => void
  splitPane?: boolean
}) {
  const updateField = (index: number, patch: Partial<ParameterSchemaField>) => {
    const fields = schema.fields.map((field, i) => (i === index ? { ...field, ...patch } : field))
    onChange({ ...schema, fields })
  }

  const removeField = (index: number) => {
    onChange({ ...schema, fields: schema.fields.filter((_, i) => i !== index) })
  }

  const addField = () => {
    onChange({ ...schema, fields: [...schema.fields, emptyField()] })
  }

  const tableColumns = 'grid-cols-[minmax(8rem,0.8fr)_minmax(10rem,1fr)_4.5rem_minmax(16rem,1.8fr)_2.5rem]'

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] text-[#6b7280]">
          {splitPane
            ? `共 ${schema.fields.length} 项；右侧预览提示词随改动即时更新。`
            : `共 ${schema.fields.length} 项；勾选与字段修改会即时重写预览提示词。`}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex items-center gap-1.5">
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                className="size-4 rounded border-[#d1d5db]"
                checked={Boolean(schema.allow_extra)}
                onChange={e => onChange({ ...schema, allow_extra: e.target.checked })}
              />
              <span className="text-[13px] text-[#374151]">允许额外字段</span>
            </label>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="inline-flex text-[#9ca3af] hover:text-[#6b7280]"
                  aria-label="允许额外字段说明"
                >
                  <CircleHelp className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent className="max-w-[260px] leading-relaxed">
                开启后，抽参时除了列出的字段，还可保留报告里其它未声明但对审查有用的参数；关闭则只提取已声明字段。
              </TooltipContent>
            </Tooltip>
          </div>
          <Button type="button" variant="outline" size="sm" className="h-8 rounded-md" onClick={addField}>
            <Plus className="size-3.5" />
            增加字段
          </Button>
        </div>
      </div>

      {splitPane ? (
        <div className="space-y-3">
          {schema.fields.map((field, index) => (
            <div
              key={`${index}-${field.key}`}
              className="space-y-2.5 rounded-lg border border-[#e5e7eb] bg-white px-3 py-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="grid min-w-0 flex-1 grid-cols-2 gap-2">
                  <label className="min-w-0 space-y-1">
                    <span className="text-[12px] text-[#6b7280]">字段名称</span>
                    <Input
                      className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 text-[13px]"
                      aria-label={`字段 ${index + 1} 名称`}
                      placeholder="如 型号"
                      value={field.label}
                      onChange={e => updateField(index, { label: e.target.value })}
                    />
                  </label>
                  <label className="min-w-0 space-y-1">
                    <span className="text-[12px] text-[#6b7280]">字段标识</span>
                    <Input
                      className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 font-mono text-[12px]"
                      aria-label={`字段 ${index + 1} 标识`}
                      placeholder="如 model"
                      value={field.key}
                      onChange={e => updateField(index, { key: e.target.value.trim() })}
                    />
                  </label>
                </div>
                <div className="flex shrink-0 items-center gap-2 pt-6">
                  <label className="inline-flex items-center gap-1.5 text-[12px] text-[#374151]">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-[#d1d5db]"
                      aria-label={`字段 ${index + 1} 必填`}
                      checked={Boolean(field.required)}
                      onChange={e => updateField(index, { required: e.target.checked })}
                    />
                    必填
                  </label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 text-[#9ca3af] hover:bg-[#fef2f2] hover:text-[#ef4444]"
                    onClick={() => removeField(index)}
                    disabled={schema.fields.length <= 1}
                    title="删除字段"
                    aria-label={`删除字段 ${index + 1}`}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
              <label className="block space-y-1">
                <span className="text-[12px] text-[#6b7280]">提取说明</span>
                <Textarea
                  className="min-h-[4.5rem] resize-y rounded-md border-[#e5e7eb] bg-white px-2 py-2 text-[13px] leading-relaxed"
                  aria-label={`字段 ${index + 1} 提取说明`}
                  placeholder="例如：报告首页样品型号，如 S20-"
                  value={field.hint}
                  onChange={e => updateField(index, { hint: e.target.value })}
                />
              </label>
            </div>
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[#e5e7eb] bg-white">
          <div className="min-w-[820px]">
            <div className={`grid ${tableColumns} items-center gap-3 border-b border-[#e5e7eb] bg-[#f8fafc] px-3 py-2 text-[12px] font-medium text-[#6b7280]`}>
              <span>字段名称</span>
              <span>字段标识</span>
              <span className="text-center">必填</span>
              <span>提取说明</span>
              <span />
            </div>
            <div className="divide-y divide-[#e5e7eb]">
              {schema.fields.map((field, index) => (
                <div
                  key={`${index}-${field.key}`}
                  className={`grid ${tableColumns} items-center gap-3 px-3 py-2.5`}
                >
                  <Input
                    className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 text-[13px]"
                    aria-label={`字段 ${index + 1} 名称`}
                    placeholder="如 型号"
                    value={field.label}
                    onChange={e => updateField(index, { label: e.target.value })}
                  />
                  <Input
                    className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 font-mono text-[12px]"
                    aria-label={`字段 ${index + 1} 标识`}
                    placeholder="如 model"
                    value={field.key}
                    onChange={e => updateField(index, { key: e.target.value.trim() })}
                  />
                  <label className="inline-flex justify-center">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-[#d1d5db]"
                      aria-label={`字段 ${index + 1} 必填`}
                      checked={Boolean(field.required)}
                      onChange={e => updateField(index, { required: e.target.checked })}
                    />
                  </label>
                  <Input
                    className="h-9 rounded-md border-[#e5e7eb] bg-white px-2 text-[13px]"
                    aria-label={`字段 ${index + 1} 提取说明`}
                    placeholder="例如：报告首页样品型号"
                    value={field.hint}
                    onChange={e => updateField(index, { hint: e.target.value })}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 text-[#9ca3af] hover:bg-[#fef2f2] hover:text-[#ef4444]"
                    onClick={() => removeField(index)}
                    disabled={schema.fields.length <= 1}
                    title="删除字段"
                    aria-label={`删除字段 ${index + 1}`}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
