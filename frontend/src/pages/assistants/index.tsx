import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useBlocker, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  FileText,
  Loader2,
  Pencil,
  Plus,
  Send,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
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
import { GENERIC_TEMPLATE_ASSISTANT_ID } from '@/lib/assistants'
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
}: {
  assistant: AuditAssistant
  knowledgeBases: KnowledgeBase[]
  onChanged: () => void
  lockedKnowledgeBaseId?: string
  initialTab?: MainTab
  embedded?: boolean
  backTo?: string
  hideKnowledgePicker?: boolean
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
  const [pendingInitialization, setPendingInitialization] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [messages, setMessages] = useState<
    Array<{
      id: string
      role: 'user' | 'assistant'
      content: string
      citations?: Array<{
        file_name?: string
        page?: number | null
        score?: number | null
        snippet?: string
      }>
    }>
  >([])

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
  const showInitializationPage = Boolean(
    lockedKnowledgeBaseId
    && assistant.id !== GENERIC_TEMPLATE_ASSISTANT_ID
    && (initializing || !hasActiveVersion),
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
    setConfirmAction(null)
    setPendingInitialization(false)
  }, [assistant.id])

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

  const sendChat = async () => {
    const text = chatInput.trim()
    if (!text || chatBusy) return
    if (!kbSelected.size) {
      toast.error('请先在右侧绑定知识库并保存')
      return
    }
    const history = messages.map(item => ({ role: item.role, content: item.content }))
    const userMsg = { id: `u_${Date.now()}`, role: 'user' as const, content: text }
    setMessages(prev => [...prev, userMsg])
    setChatInput('')
    setChatBusy(true)
    try {
      const result = await api.chatAssistant(assistant.id, { message: text, history })
      setMessages(prev => [
        ...prev,
        {
          id: `a_${Date.now()}`,
          role: 'assistant',
          content: result.answer || '（空回复）',
          citations: result.citations,
        },
      ])
    } catch (err) {
      toast.error((err as Error).message)
      setMessages(prev => prev.filter(item => item.id !== userMsg.id))
      setChatInput(text)
    } finally {
      setChatBusy(false)
    }
  }

  const vectorWeight = Number(version?.retrieval_config.vector_weight ?? 0.7)
  const keywordWeight = Math.round((1 - vectorWeight) * 100) / 100
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
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[#e5e7eb] bg-white px-6 py-3">
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
                {mainTab === 'workflow' && embedded ? '审查配置' : assistant.name}
              </h1>
              <p className="text-[14px] text-[#6b7280]">
                {mainTab === 'chat'
                  ? '知识库问答 · 使用已保存的审查配置'
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
        </header>

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
        <div className="mx-auto flex w-full max-w-3xl min-h-0 flex-1 flex-col px-6 py-5">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
              {!messages.length && (
                <div className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-2 px-6 text-center">
                  <Bot className="size-10 text-[#9ca3af]" />
                  <p className="text-[16px] font-medium text-[#111827]">基于知识库提问</p>
                  <p className="max-w-md text-[15px] leading-relaxed text-[#6b7280]">
                    这里是简单的 RAG 问答。改提示词与参数字段请到「审查配置」；完整审查请用「去审查」。
                  </p>
                </div>
              )}
              {messages.map(item => (
                <div
                  key={item.id}
                  className={cn('flex', item.role === 'user' ? 'justify-end' : 'justify-start')}
                >
                  <div
                    className={cn(
                      'max-w-[85%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed',
                      item.role === 'user'
                        ? 'bg-[#111827] text-white'
                        : 'bg-[#f3f4f6] text-[#111827]',
                    )}
                  >
                    <div className="whitespace-pre-wrap">{item.content}</div>
                    {item.role === 'assistant' && item.citations && item.citations.length > 0 && (
                      <div className="mt-3 space-y-1.5 border-t border-black/10 pt-2">
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
              ))}
              {chatBusy && (
                <div className="flex items-center gap-2 text-[15px] text-[#6b7280]">
                  <Loader2 className="size-4 animate-spin" />
                  检索并回答中…
                </div>
              )}
            </div>

            <div className="border-t border-[#e5e7eb] p-4">
              <div className="flex items-end gap-2">
                <Textarea
                  className="min-h-[44px] max-h-32 flex-1 resize-none rounded-xl border-[#e5e7eb] text-[15px]"
                  placeholder="输入问题，例如：绝缘电阻的试验要求是什么？"
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
                使用已保存的模型与知识库配置。改右侧设置后请先点「保存」。
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
                  {showInitializationPage ? '初始化审查配置' : '审查配置'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {showInitializationPage && hasActiveVersion ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="rounded-lg"
                    onClick={() => setInitializing(false)}
                  >
                    返回配置
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
                      重新初始化
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
                    void activeQuery.refetch()
                    onChanged()
                    jumpToReportParameters()
                    toast.success('已更新审查配置')
                  }}
                />
              </div>
            ) : (
            <>
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
                <DialogTitle>重新初始化审查配置？</DialogTitle>
                <DialogDescription className="leading-relaxed text-[#6b7280]">
                  将进入初始化流程，根据样例报告重新生成参数 schema 与抽参品类约束。
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
                  重新初始化
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
                    ? ' 保存后将进入重新初始化。'
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
      <aside className="flex w-[340px] shrink-0 flex-col border-l border-[#e5e7eb] bg-white">
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
                <SettingHint label="相似度阈值" tip="低于该分数的候选会被过滤。数值越高，召回越严。" />
                <SettingSlider
                  value={Number(version.retrieval_config.similarity_threshold ?? 0.2)}
                  min={0}
                  max={1}
                  step={0.01}
                  format={v => v.toFixed(2)}
                  onChange={v => updateRetrieval('similarity_threshold', Math.round(v * 100) / 100)}
                />
              </div>
              <div>
                <SettingHint label="向量相似度权重" tip="语义检索与全文检索的混合比例。向右提高向量权重。" />
                <div className="mb-1.5 flex justify-between text-[15px] text-[#6b7280]">
                  <span>vector {vectorWeight.toFixed(2)}</span>
                  <span>full-text {keywordWeight.toFixed(2)}</span>
                </div>
                <SettingSlider
                  value={vectorWeight}
                  min={0}
                  max={1}
                  step={0.01}
                  format={v => v.toFixed(2)}
                  onChange={v => {
                    const next = Math.round(v * 100) / 100
                    updateRetrieval('vector_weight', next)
                    updateRetrieval('keyword_weight', Math.round((1 - next) * 100) / 100)
                  }}
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
