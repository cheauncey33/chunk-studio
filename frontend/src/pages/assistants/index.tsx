import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, ChevronDown, CircleHelp, FileText, Loader2, Play, Plus, Send, Settings, Settings2, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AssistantVersion, type AuditAssistant, type Job, type KnowledgeBase } from '@/api'
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
import { EmptyState } from '@/components/empty-state'
import { ListFilterBar } from '@/components/list-filter-bar'
import { CardContainer, HomeCard } from '@/components/home-card'
import { SearchableMultiSelect, SearchableSelect } from '@/components/searchable-select'
import { useAssistants, useKnowledgeBases, useKbFiles, queryKeys } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

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

export default function AssistantsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: assistants = [], isLoading } = useAssistants()
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  const selected = id ? assistants.find(item => item.id === id) || null : null

  const filtered = assistants.filter(item =>
    `${item.name} ${item.description}`.toLowerCase().includes(query.trim().toLowerCase()),
  )

  const create = async () => {
    if (!name.trim()) return
    setCreating(true)
    try {
      const created = await api.createAssistant({ name: name.trim(), description: description.trim() })
      await client.invalidateQueries({ queryKey: queryKeys.assistants })
      toast.success('助手已创建')
      setCreateOpen(false)
      setName('')
      setDescription('')
      navigate(`/assistants/${created.id}`)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const createDialog = (
    <Dialog open={createOpen} onOpenChange={setCreateOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建审查助手</DialogTitle>
          <DialogDescription>
            会复制内置油变审查流程。新品类请创建后到「工作流」改提示词，并在绑定知识库的「配置」里设置命名规则 PDF。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label title={helpText.assistants.createName}>名称</Label>
            <Input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="例如：油浸式变压器审查"
            />
          </div>
          <div className="space-y-2">
            <Label title={helpText.assistants.createDesc}>说明</Label>
            <Textarea value={description} onChange={e => setDescription(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
          <Button disabled={!name.trim() || creating} onClick={() => void create()}>
            {creating ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )

  if (id) {
    if (isLoading) {
      return <div className="flex h-full items-center justify-center text-[15px] text-text-secondary">加载中…</div>
    }
    if (!selected) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 px-8">
          <EmptyState
            icon={<Bot />}
            title="找不到这个助手"
            description="可能已被删除，或链接不正确。"
            actionLabel="返回助手列表"
            onAction={() => navigate('/assistants')}
          />
        </div>
      )
    }
    return (
      <>
        <AssistantSettings
          assistant={selected}
          knowledgeBases={knowledgeBases}
          onChanged={() => client.invalidateQueries({ queryKey: queryKeys.assistants })}
        />
        {createDialog}
      </>
    )
  }

  return (
    <div className="h-full overflow-auto px-8 py-7">
      <div className="mx-auto w-full max-w-7xl">
        <ListFilterBar
          title="助手"
          titleHelp={helpText.assistants.page}
          description="问答可直接聊知识库；调试审查提示词请进卡片后切到「工作流」。命名规则在各知识库的「配置」。"
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder="搜索助手"
          rightPanel={
            <Button onClick={() => setCreateOpen(true)} title={helpText.assistants.create}>
              <Plus />
              创建助手
            </Button>
          }
        />

        <div className="mt-8">
          {isLoading ? (
            <div className="py-16 text-center text-[15px] text-text-secondary">加载中…</div>
          ) : filtered.length ? (
            <CardContainer>
              {filtered.map(item => (
                <HomeCard
                  key={item.id}
                  title={item.name}
                  description={item.description || '内置审查流程已封装'}
                  onClick={() => navigate(`/assistants/${item.id}`)}
                  actions={
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8"
                      title="设置"
                      onClick={() => navigate(`/assistants/${item.id}`)}
                    >
                      <Settings className="size-3.5" />
                    </Button>
                  }
                  meta={
                    <>
                      <span>v{item.active_version || '—'}</span>
                      <span>{item.knowledge_bases.length} 个知识库</span>
                      <span>{item.status === 'active' ? '已启用' : '草稿'}</span>
                      <span>{formatDate(item.updated_at)}</span>
                    </>
                  }
                />
              ))}
            </CardContainer>
          ) : (
            <EmptyState
              icon={<Bot />}
              title={query ? '没有匹配的助手' : '还没有助手'}
              description={query ? '换个关键词试试。' : '创建一个审查助手，再绑定知识库即可开始。'}
              actionLabel={query ? undefined : '创建助手'}
              onAction={query ? undefined : () => setCreateOpen(true)}
            />
          )}
        </div>
      </div>
      {createDialog}
    </div>
  )
}

function AssistantSettings({
  assistant,
  knowledgeBases,
  onChanged,
}: {
  assistant: AuditAssistant
  knowledgeBases: KnowledgeBase[]
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [kbSelected, setKbSelected] = useState(() => new Set(assistant.knowledge_bases.map(item => item.id)))
  const [saving, setSaving] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [mainTab, setMainTab] = useState<MainTab>('chat')
  const [selectedStep, setSelectedStep] = useState<FlowStepId>('report_parameters')
  const [trialOpen, setTrialOpen] = useState(false)
  const [reportFileId, setReportFileId] = useState('')
  const [namingFileId, setNamingFileId] = useState('')
  const [trialBusy, setTrialBusy] = useState(false)
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

  const primaryKbId = [...kbSelected][0] || ''
  const { data: kbFiles = [] } = useKbFiles(primaryKbId || undefined)
  const readyFiles = useMemo(
    () => kbFiles.filter(file => file.parse_ready || file.parse_status === 'done'),
    [kbFiles],
  )

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
    if (!version) return
    setVersion({
      ...version,
      node_prompts: {
        ...version.node_prompts,
        [selectedStep]: { ...(version.node_prompts[selectedStep] || {}), content },
      },
    })
  }

  const saveAll = async () => {
    if (!version) return
    setSaving(true)
    try {
      await api.setAssistantKnowledgeBases(assistant.id, [...kbSelected])
      await api.createAssistantVersion(assistant.id, {
        model_config: version.model_config,
        node_prompts: version.node_prompts,
        rules: version.rules,
        retrieval_config: version.retrieval_config,
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

  const pollJob = async (jobId: string): Promise<Job> => {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      const job = await api.getJob(jobId)
      if (job.status === 'done' || job.status === 'failed') return job
      await new Promise(resolve => setTimeout(resolve, 2000))
    }
    throw new Error('试运行超时，请稍后在结果详情中查看')
  }

  const startTrial = async () => {
    if (!reportFileId) {
      toast.error('请选择已解析的检测报告')
      return
    }
    setTrialBusy(true)
    try {
      const job = await api.startAssistantRun(assistant.id, {
        report_file_id: reportFileId,
        naming_rule_file_id: namingFileId || null,
      })
      toast.message('审查已开始…')
      const finished = await pollJob(job.id)
      if (finished.status === 'failed') throw new Error(finished.error || '审查失败')
      const reportName = String((finished.result as Record<string, unknown>)?.report_name || '')
      toast.success(reportName ? `完成：${reportName}` : '审查完成')
      setTrialOpen(false)
      navigate(reportName ? `/?report=${encodeURIComponent(reportName)}` : '/')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setTrialBusy(false)
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
    <div className="flex h-full overflow-hidden bg-[#f8fafc]">
      <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[#e5e7eb] bg-white px-8 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 rounded-full"
              onClick={() => navigate('/assistants')}
              title="返回列表"
            >
              <ArrowLeft className="size-4" />
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-[18px] font-semibold text-[#111827]">{assistant.name}</h1>
              <p className="text-[15px] text-[#6b7280]">
                {mainTab === 'chat' ? '知识库问答' : '工作流调试'} · 当前 v{assistant.active_version || '—'}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
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
                工作流
              </button>
            </div>
            <Button
              variant="outline"
              className="rounded-lg border-[#e5e7eb]"
              disabled={!assistant.active_version}
              onClick={() => {
                setReportFileId(readyFiles[0]?.id || '')
                setNamingFileId('')
                setTrialOpen(true)
              }}
              title={helpText.assistants.trial}
            >
              <Play className="size-4" />
              试运行审查
            </Button>
            <Button className="rounded-lg bg-[#111827] text-white hover:bg-[#1f2937]" asChild>
              <Link to="/">去审查</Link>
            </Button>
          </div>
        </header>

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
            工作流
          </button>
        </div>

        {mainTab === 'chat' ? (
        <div className="mx-auto flex w-full max-w-3xl min-h-0 flex-1 flex-col px-6 py-5">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-sm">
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
              {!messages.length && (
                <div className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-2 px-6 text-center">
                  <Bot className="size-10 text-[#9ca3af]" />
                  <p className="text-[16px] font-medium text-[#111827]">基于知识库提问</p>
                  <p className="max-w-md text-[15px] leading-relaxed text-[#6b7280]">
                    这里是简单的 RAG 问答。调试审查提示词请切到「工作流」；完整审查请用「去审查」。
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
        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="overflow-y-auto border-b border-[#e5e7eb] bg-white p-3 lg:border-b-0 lg:border-r">
            <p className="mb-2 px-2 text-[13px] text-[#6b7280]">
              新建助手会复制油变模板。新品类请在此改提示词，并到知识库配置命名规则。
            </p>
            {FLOW_STEPS.map((step, index) => (
              <button
                key={step.id}
                type="button"
                onClick={() => setSelectedStep(step.id)}
                className={cn(
                  'mb-0.5 flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left transition',
                  selectedStep === step.id ? 'bg-[#eff6ff]' : 'hover:bg-[#f9fafb]',
                )}
              >
                <span
                  className={cn(
                    'grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold',
                    selectedStep === step.id
                      ? 'bg-[#3b82f6] text-white'
                      : 'bg-[#f3f4f6] text-[#6b7280]',
                  )}
                >
                  {index + 1}
                </span>
                <span className="min-w-0">
                  <strong
                    className={cn(
                      'block truncate text-[15px] font-medium',
                      selectedStep === step.id ? 'text-[#1d4ed8]' : 'text-[#111827]',
                    )}
                  >
                    {step.label}
                  </strong>
                  <small className="text-[13px] text-[#6b7280]">{step.kind}</small>
                </span>
              </button>
            ))}
          </div>
          <div className="flex min-h-0 flex-col overflow-hidden bg-white p-5">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Workflow className="size-4 text-[#6b7280]" />
              <span className="text-[16px] font-medium text-[#111827]">
                {FLOW_STEPS.find(step => step.id === selectedStep)?.label}
              </span>
              <span className="rounded-md bg-[#f3f4f6] px-2 py-0.5 text-[13px] text-[#6b7280]">
                {FLOW_STEPS.find(step => step.id === selectedStep)?.kind}
              </span>
            </div>
            <Label className="mb-2 text-[15px] text-[#6b7280]">系统提示词</Label>
            <Textarea
              className="min-h-0 flex-1 resize-none rounded-xl border-[#e5e7eb] bg-[#f9fafb] font-mono text-[13px] leading-relaxed"
              value={version?.node_prompts[selectedStep]?.content || ''}
              onChange={e => updatePrompt(e.target.value)}
              disabled={!version || selectedStep === 'candidate_retrieval' || selectedStep === 'result_summary'}
              placeholder={
                selectedStep === 'candidate_retrieval' || selectedStep === 'result_summary'
                  ? '该步不调用大模型，无需提示词。'
                  : '告诉大模型这一步该怎么做。改完后点右侧「保存」。'
              }
            />
            <p className="mt-2 truncate text-[13px] text-[#6b7280]">
              {version?.node_prompts[selectedStep]?.path || '该步不调用大模型 / 内置逻辑'}
            </p>
          </div>
        </div>
        )}
      </section>

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

          <div className="space-y-4">
            <button
              type="button"
              className="flex w-full items-center justify-between text-[15px] font-medium text-[#374151]"
              onClick={() => setAdvancedOpen(v => !v)}
            >
              <span className="inline-flex items-center gap-1.5">
                <Settings2 className="size-3.5 text-[#6b7280]" />
                高级设置
              </span>
              <ChevronDown className={cn('size-4 text-[#9ca3af] transition', advancedOpen && 'rotate-180')} />
            </button>
            {advancedOpen && version && (
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

          {(versionsQuery.data || []).length > 0 && (
            <div className="space-y-2">
              <Label className="text-[15px] font-medium text-[#374151]">版本</Label>
              <div className="space-y-1 text-[15px] text-[#6b7280]">
                {(versionsQuery.data || []).slice(0, 4).map(item => (
                  <div key={item.id} className="flex justify-between gap-2">
                    <span>v{item.version}</span>
                    <span>{item.status === 'active' ? '当前' : formatDate(item.created_at)}</span>
                  </div>
                ))}
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

      <Dialog open={trialOpen} onOpenChange={open => !trialBusy && setTrialOpen(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>试运行</DialogTitle>
            <DialogDescription>选择一份已解析的检测报告。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {!primaryKbId && (
              <p className="text-[15px] text-state-error">请先选择知识库并保存。</p>
            )}
            <div className="space-y-2">
              <Label>检测报告</Label>
              <select
                className="flex h-11 w-full rounded-lg border border-[#e5e7eb] bg-white px-3 text-[15px]"
                value={reportFileId}
                onChange={e => setReportFileId(e.target.value)}
                disabled={!readyFiles.length || trialBusy}
              >
                <option value="">选择 PDF</option>
                {readyFiles.map(file => (
                  <option key={file.id} value={file.id}>{file.name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label>命名规则（可选）</Label>
              <select
                className="flex h-11 w-full rounded-lg border border-[#e5e7eb] bg-white px-3 text-[15px]"
                value={namingFileId}
                onChange={e => setNamingFileId(e.target.value)}
                disabled={!readyFiles.length || trialBusy}
              >
                <option value="">用知识库默认</option>
                {readyFiles.map(file => (
                  <option key={file.id} value={file.id}>{file.name}</option>
                ))}
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={trialBusy} onClick={() => setTrialOpen(false)}>取消</Button>
            <Button disabled={trialBusy || !reportFileId} onClick={() => void startTrial()}>
              {trialBusy ? '运行中…' : '开始'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
