import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Bot, CircleHelp, FileText, Loader2, Pencil, Plus, Send, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  type AssistantVersion,
  type AuditAssistant,
  type KnowledgeBase,
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
import { useDevMode } from '@/lib/dev-mode'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'
import { AssistantInitDraftCard } from '@/pages/assistants/init-draft-card'

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

const NO_PROMPT_STEPS = new Set<FlowStepId>(['candidate_retrieval', 'result_summary'])
const EDITABLE_STEPS = FLOW_STEPS.filter(step => !NO_PROMPT_STEPS.has(step.id))

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
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [kbSelected, setKbSelected] = useState(() =>
    lockedKnowledgeBaseId
      ? new Set([lockedKnowledgeBaseId])
      : new Set(assistant.knowledge_bases.map(item => item.id)),
  )
  const [saving, setSaving] = useState(false)
  const [activatingVersionId, setActivatingVersionId] = useState<string | null>(null)
  const [mainTab, setMainTab] = useState<MainTab>(initialTab)
  const [editStepId, setEditStepId] = useState<FlowStepId | null>(null)
  const [devMode] = useDevMode()
  const visibleSteps = useMemo(
    () => (devMode ? EDITABLE_STEPS : EDITABLE_STEPS.filter(step => step.id === 'report_parameters')),
    [devMode],
  )
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
    if (!devMode && editStepId && editStepId !== 'report_parameters') {
      setEditStepId(null)
    }
  }, [devMode, editStepId])

  useEffect(() => {
    setMainTab(initialTab)
  }, [initialTab])

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

  const versionsQuery = useQuery({
    queryKey: queryKeys.assistantVersions(assistant.id),
    queryFn: () => api.listAssistantVersions(assistant.id),
  })
  const activeQuery = useQuery({
    queryKey: queryKeys.assistantVersion(assistant.id),
    queryFn: () => api.getActiveAssistantVersion(assistant.id),
  })

  useEffect(() => {
    setVersion(activeQuery.data || null)
  }, [activeQuery.data])

  useEffect(() => {
    setKbSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
  }, [assistant])

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
          label: '补充规则',
          value: '来自知识库 manual_rules / few-shot（若有）',
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
  }, [boundKb, version])

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

  const updateRetrieval = (key: string, value: number) => {
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

  const saveAll = async () => {
    if (!version) return
    setSaving(true)
    try {
      if (!hideKnowledgePicker) {
        const kbIds = lockedKnowledgeBaseId
          ? Array.from(new Set([lockedKnowledgeBaseId, ...kbSelected]))
          : [...kbSelected]
        await api.setAssistantKnowledgeBases(assistant.id, kbIds)
      }
      await api.createAssistantVersion(assistant.id, {
        model_config: version.model_config,
        node_prompts: version.node_prompts,
        rules: version.rules,
        retrieval_config: version.retrieval_config,
        parameter_schema: version.parameter_schema,
      })
      await Promise.all([activeQuery.refetch(), versionsQuery.refetch()])
      onChanged()
      toast.success('已保存')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const selectVersion = async (item: AssistantVersion) => {
    if (item.status === 'active') {
      setVersion(item)
      return
    }
    if (activatingVersionId) return
    setActivatingVersionId(item.id)
    try {
      const active = await api.activateAssistantVersion(assistant.id, item.id)
      setVersion(active)
      await Promise.all([activeQuery.refetch(), versionsQuery.refetch()])
      onChanged()
      toast.success(`已切换并启用 v${active.version}`)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setActivatingVersionId(null)
    }
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
              <h1 className="truncate text-[18px] font-semibold text-[#111827]">{assistant.name}</h1>
              <p className="text-[15px] text-[#6b7280]">
                {mainTab === 'chat' ? '知识库问答' : '审查配置'} · 当前 v{assistant.active_version || '—'}
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
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[16px] font-medium text-[#111827]">审查配置</p>
                <p className="text-[13px] text-[#6b7280]" title={helpText.assistants.tabWorkflow}>
                  {devMode
                    ? '开发者模式：可编辑各步提示词。改完后保存为新版本。'
                    : '日常只需改报告参数字段。其它步骤提示词请在系统设置打开开发者模式。'}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {(versionsQuery.data || []).map(item => {
                  const isActive = item.status === 'active'
                  const busy = activatingVersionId === item.id
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={cn(
                        'rounded-lg px-2.5 py-1 text-[13px] transition disabled:opacity-60',
                        isActive
                          ? 'bg-[#111827] text-white'
                          : 'bg-[#f3f4f6] text-[#4b5563] hover:bg-[#e5e7eb]',
                      )}
                      disabled={busy || !!activatingVersionId}
                      onClick={() => void selectVersion(item)}
                      title={isActive ? '当前启用版本' : '切换并启用此版本'}
                    >
                      v{item.version}
                      {busy ? '…' : isActive ? ' · 当前' : ''}
                    </button>
                  )
                })}
                <Button
                  variant="outline"
                  size="sm"
                  className="rounded-lg border-[#e5e7eb]"
                  disabled={saving || !version}
                  onClick={() => {
                    setVersion(activeQuery.data || null)
                    setKbSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
                    toast.message('已还原为当前启用版本')
                  }}
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  className="rounded-lg bg-[#111827] text-white hover:bg-[#1f2937]"
                  disabled={saving || !version}
                  onClick={() => void saveAll()}
                  title={helpText.assistants.saveVersion}
                >
                  {saving ? '保存中…' : '保存'}
                </Button>
              </div>
            </div>

            {(lockedKnowledgeBaseId && assistant.id !== GENERIC_TEMPLATE_ASSISTANT_ID) || visibleSteps.length > 0 ? (
              <div className="space-y-2 rounded-xl border border-[#e5e7eb] bg-[#f8fafc] p-3">
                {lockedKnowledgeBaseId && assistant.id !== GENERIC_TEMPLATE_ASSISTANT_ID && (
                  <AssistantInitDraftCard
                    bare
                    assistantId={assistant.id}
                    activeVersion={assistant.active_version}
                    onJumpToReportParameters={jumpToReportParameters}
                    onApplied={({ version: versionNo }) => {
                      void activeQuery.refetch()
                      void versionsQuery.refetch()
                      onChanged()
                      jumpToReportParameters()
                      if (versionNo) {
                        toast.success(`审查配置已切换到已启用版本 v${versionNo}`)
                      }
                    }}
                  />
                )}

                {visibleSteps.length > 0 && (
                  <div
                    className={cn(
                      'space-y-1.5',
                      lockedKnowledgeBaseId && assistant.id !== GENERIC_TEMPLATE_ASSISTANT_ID
                        && 'border-t border-[#e5e7eb] pt-2',
                    )}
                  >
                    {visibleSteps.map(step => {
                      const fieldCount = version?.parameter_schema?.fields?.length || 0
                      const summary =
                        step.id === 'report_parameters'
                          ? `${fieldCount} 个字段`
                          : '系统提示词'
                      return (
                        <div
                          key={step.id}
                          className="flex items-center gap-3 rounded-lg border border-[#e5e7eb] bg-white px-3 py-2"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[14px] font-medium text-[#111827]">{step.label}</div>
                            <div className="truncate text-[12px] text-[#9ca3af]">{summary}</div>
                          </div>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="h-8 shrink-0 rounded-lg"
                            disabled={!version}
                            onClick={() => setEditStepId(step.id)}
                          >
                            <Pencil className="size-3.5" />
                            修改
                          </Button>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <Dialog open={editStepId !== null} onOpenChange={open => !open && setEditStepId(null)}>
            <DialogContent
              className="flex h-[min(780px,90vh)] w-[min(820px,92vw)] max-w-none flex-col gap-0 overflow-hidden p-0"
              aria-describedby={undefined}
            >
              <DialogHeader className="shrink-0 border-b border-[#e5e7eb] px-5 py-4">
                <DialogTitle>
                  {EDITABLE_STEPS.find(step => step.id === editStepId)?.label || '编辑步骤'}
                </DialogTitle>
                <DialogDescription className="sr-only">
                  在弹窗中修改本步配置，关闭后记得点「保存」。
                </DialogDescription>
              </DialogHeader>
              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
                {editStepId && (stepBindings[editStepId] || []).length > 0 && (
                  <div className="space-y-1.5 rounded-xl border border-[#e5e7eb] bg-[#f8fafc] px-3 py-2.5">
                    <div className="text-[13px] font-medium text-[#374151]">运行时绑定</div>
                    {stepBindings[editStepId]!.map(item => (
                      <div key={item.label} className="text-[13px] leading-relaxed text-[#6b7280]">
                        <span className="font-medium text-[#4b5563]">{item.label}：</span>
                        {item.value}
                      </div>
                    ))}
                  </div>
                )}
                {editStepId === 'report_parameters' && version?.parameter_schema && (
                  <ParameterSchemaEditor
                    schema={version.parameter_schema}
                    onChange={updateParameterSchema}
                  />
                )}
                {editStepId === 'report_parameters' && !devMode && (
                  <p className="text-[13px] text-[#9ca3af]">
                    日常改上面的字段即可，保存后抽参会按新字段列表执行。系统提示词请在「设置 → 开发者模式」开启后编辑。
                  </p>
                )}
                {editStepId
                  && (devMode || editStepId !== 'report_parameters') && (
                  <div className="space-y-2">
                    <Label className="text-[15px] text-[#6b7280]">系统提示词</Label>
                    <Textarea
                      className="min-h-[16rem] resize-y rounded-xl border-[#e5e7eb] bg-[#f9fafb] font-mono text-[13px] leading-relaxed"
                      value={version?.node_prompts[editStepId]?.content || ''}
                      onChange={e => updatePrompt(e.target.value)}
                      disabled={!version}
                      placeholder="告诉大模型这一步该怎么做。改完后关闭弹窗并点「保存」。"
                    />
                    <p className="truncate text-[13px] text-[#6b7280]">
                      {version?.node_prompts[editStepId]?.path || '内置逻辑'}
                    </p>
                    {editStepId === 'report_parameters' && (
                      <p className="text-[12px] text-[#9ca3af]">
                        提示：改字段列表不会自动改写这段提示词；抽参以字段 schema 为准。
                      </p>
                    )}
                  </div>
                )}
              </div>
              <DialogFooter className="shrink-0 border-t border-[#e5e7eb] px-5 py-3">
                <Button type="button" variant="outline" onClick={() => setEditStepId(null)}>
                  完成
                </Button>
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

          {(versionsQuery.data || []).length > 0 && (
            <div className="space-y-2">
              <Label className="text-[15px] font-medium text-[#374151]" title={helpText.assistants.tabVersions}>
                版本
              </Label>
              <div className="space-y-1">
                {(versionsQuery.data || []).map(item => {
                  const isActive = item.status === 'active'
                  const busy = activatingVersionId === item.id
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={cn(
                        'flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[15px] transition disabled:opacity-60',
                        isActive
                          ? 'bg-[#f3f4f6] text-[#111827]'
                          : 'text-[#6b7280] hover:bg-[#f9fafb]',
                      )}
                      disabled={busy || !!activatingVersionId}
                      onClick={() => void selectVersion(item)}
                      title={isActive ? '当前启用版本' : '切换并启用此版本'}
                    >
                      <span className="font-medium">v{item.version}</span>
                      <span className="shrink-0 text-[13px] text-[#9ca3af]">
                        {busy ? '切换中…' : isActive ? '当前' : formatDate(item.created_at)}
                      </span>
                    </button>
                  )
                })}
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

function ParameterSchemaEditor({
  schema,
  onChange,
}: {
  schema: ParameterSchema
  onChange: (next: ParameterSchema) => void
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

  return (
    <div className="mb-4 shrink-0 space-y-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[15px] font-medium text-[#111827]">报告参数字段</p>
          <p className="text-[13px] text-[#6b7280]">
            告诉模型要从报告里抽出哪些参数。增删改字段后点「保存」即可；不依赖下方系统提示词自动同步。
          </p>
        </div>
        <div className="inline-flex items-center gap-1.5 text-[13px] text-[#374151]">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              className="size-4 rounded border-[#d1d5db]"
              checked={Boolean(schema.allow_extra)}
              onChange={e => onChange({ ...schema, allow_extra: e.target.checked })}
            />
            允许额外字段
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
      </div>
      <div className="space-y-2.5">
        {schema.fields.map((field, index) => (
          <div
            key={`${index}-${field.key}`}
            className="space-y-2 rounded-xl border border-[#e5e7eb] bg-white p-3 shadow-[0_1px_0_rgba(15,23,42,0.03)]"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-medium text-[#9ca3af]">字段 {index + 1}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7 text-[#9ca3af] hover:text-[#ef4444]"
                onClick={() => removeField(index)}
                disabled={schema.fields.length <= 1}
                title="删除字段"
                aria-label="删除字段"
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
            <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
              <div className="space-y-1">
                <div className="flex items-center gap-1">
                  <Label className="text-[12px] text-[#6b7280]">英文标识</Label>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="inline-flex text-[#9ca3af] hover:text-[#6b7280]"
                        aria-label="英文标识说明"
                      >
                        <CircleHelp className="size-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-[240px] leading-relaxed">
                      给程序用的英文名（如 model），写入抽参结果 JSON，建议用小写字母和下划线。
                    </TooltipContent>
                  </Tooltip>
                </div>
                <Input
                  className="h-9 rounded-lg bg-[#f9fafb] text-[13px]"
                  placeholder="如 model"
                  value={field.key}
                  onChange={e => updateField(index, { key: e.target.value.trim() })}
                />
              </div>
              <div className="space-y-1">
                <div className="flex items-center gap-1">
                  <Label className="text-[12px] text-[#6b7280]">中文名称</Label>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="inline-flex text-[#9ca3af] hover:text-[#6b7280]"
                        aria-label="中文名称说明"
                      >
                        <CircleHelp className="size-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-[240px] leading-relaxed">
                      给人看的名字（如「型号」），出现在审查结果和界面展示里。
                    </TooltipContent>
                  </Tooltip>
                </div>
                <Input
                  className="h-9 rounded-lg bg-[#f9fafb] text-[13px]"
                  placeholder="如 型号"
                  value={field.label}
                  onChange={e => updateField(index, { label: e.target.value })}
                />
              </div>
              <label className="inline-flex h-9 items-end gap-1.5 whitespace-nowrap pb-2 text-[12px] text-[#4b5563]">
                <input
                  type="checkbox"
                  className="size-3.5 rounded border-[#d1d5db]"
                  checked={Boolean(field.required)}
                  onChange={e => updateField(index, { required: e.target.checked })}
                />
                必填
              </label>
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-1">
                <Label className="text-[12px] text-[#6b7280]">在报告里怎么找（可选）</Label>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      className="inline-flex text-[#9ca3af] hover:text-[#6b7280]"
                      aria-label="提取提示说明"
                    >
                      <CircleHelp className="size-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-[260px] leading-relaxed">
                    补充说明这个值通常出现在报告哪里、长什么样，帮助模型更准地抽出。
                  </TooltipContent>
                </Tooltip>
              </div>
              <Input
                className="h-8 rounded-lg bg-[#f9fafb] text-[12px]"
                placeholder="例如：报告首页样品型号"
                value={field.hint}
                onChange={e => updateField(index, { hint: e.target.value })}
              />
            </div>
          </div>
        ))}
      </div>
      <Button type="button" variant="outline" size="sm" className="h-8 rounded-lg" onClick={addField}>
        <Plus className="size-3.5" />
        增加字段
      </Button>
    </div>
  )
}
