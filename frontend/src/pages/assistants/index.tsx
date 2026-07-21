import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronDown, CircleHelp, Plus, Settings2 } from 'lucide-react'
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
import { Input, Label, Textarea, Badge } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState } from '@/components/empty-state'
import { SearchableMultiSelect, SearchableSelect } from '@/components/searchable-select'
import { useAssistants, useKnowledgeBases, useKbFiles, queryKeys } from '@/hooks/use-knowledge-request'
import { cn, formatDate } from '@/lib/utils'

const RAGFLOW_TEAL = '#13c2c2'

function SettingHint({ label, tip }: { label: string; tip: string }) {
  return (
    <div className="mb-2 flex items-center gap-1 text-sm text-[#374151]">
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
        className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-[#e5e7eb] accent-[var(--rag-teal)] [&::-webkit-slider-thumb]:size-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-[0_0_0_2px_var(--rag-teal)]"
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

const FLOW_NODES = [
  { id: 'report_parameters', label: '提取报告参数', kind: 'AI' },
  { id: 'test_items', label: '提取检测项目', kind: 'AI' },
  { id: 'model_decode', label: '解析型号规则', kind: 'AI' },
  { id: 'query_planner', label: '规划检索问题', kind: 'AI' },
  { id: 'candidate_retrieval', label: '查找候选证据', kind: '检索' },
  { id: 'audit_judge', label: '对照标准判定', kind: 'AI' },
  { id: 'result_summary', label: '汇总结果', kind: '汇总' },
] as const

type FlowNodeId = (typeof FLOW_NODES)[number]['id']

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

export default function AssistantsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: assistants = [], isLoading } = useAssistants()
  const { data: knowledgeBases = [] } = useKnowledgeBases()
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  const selected = assistants.find(item => item.id === id) || assistants[0] || null

  useEffect(() => {
    if (!id && assistants[0]) navigate(`/assistants/${assistants[0].id}`, { replace: true })
  }, [id, assistants, navigate])

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

  return (
    <div className="flex h-full overflow-hidden bg-[#f8fafc]">
      <aside className="flex w-[240px] shrink-0 flex-col border-r border-[#e5e7eb] bg-white">
        <div className="flex items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-[18px] font-semibold text-[#111827]">审查助手</h1>
            <p className="mt-0.5 text-[15px] text-[#6b7280]">选择助手后在右侧改设置</p>
          </div>
          <Button
            size="icon"
            variant="outline"
            className="size-8 rounded-lg border-[#e5e7eb]"
            onClick={() => setCreateOpen(true)}
            title="新建助手"
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {isLoading && <div className="px-3 py-6 text-sm text-[#6b7280]">加载中…</div>}
          {!isLoading && !assistants.length && (
            <EmptyState icon={<Bot />} title="还没有助手" description="先创建一个审查助手。" />
          )}
          {assistants.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => navigate(`/assistants/${item.id}`)}
              className={cn(
                'w-full rounded-xl px-3 py-2.5 text-left transition',
                selected?.id === item.id
                  ? 'bg-[#eff6ff] text-[#1d4ed8]'
                  : 'text-[#374151] hover:bg-[#f9fafb]',
              )}
            >
              <strong className="block truncate text-[16px] font-medium">{item.name}</strong>
              <span className="mt-0.5 block text-[15px] text-[#6b7280]">
                v{item.active_version || '—'} · {item.status === 'active' ? '已启用' : '草稿'}
              </span>
            </button>
          ))}
        </div>
      </aside>

      {selected ? (
        <AssistantWorkspace
          assistant={selected}
          knowledgeBases={knowledgeBases}
          onChanged={() => client.invalidateQueries({ queryKey: queryKeys.assistants })}
        />
      ) : (
        <div className="flex min-w-0 flex-1 items-center justify-center bg-white">
          <EmptyState
            icon={<Bot />}
            title="创建助手后开始配置"
            description="左侧新建，右侧调整模型与知识库。"
            actionLabel="新建助手"
            onAction={() => setCreateOpen(true)}
          />
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建审查助手</DialogTitle>
            <DialogDescription>会复制内置油变审查流程为 v1。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="例如：油浸式变压器审查" />
            </div>
            <div className="space-y-2">
              <Label>说明</Label>
              <Textarea value={description} onChange={e => setDescription(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button disabled={!name.trim() || creating} onClick={create}>
              {creating ? '创建中…' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function AssistantWorkspace({
  assistant,
  knowledgeBases,
  onChanged,
}: {
  assistant: AuditAssistant
  knowledgeBases: KnowledgeBase[]
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const [selectedNode, setSelectedNode] = useState<FlowNodeId>('report_parameters')
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [kbSelected, setKbSelected] = useState(() => new Set(assistant.knowledge_bases.map(item => item.id)))
  const [saving, setSaving] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [trialOpen, setTrialOpen] = useState(false)
  const [reportFileId, setReportFileId] = useState('')
  const [namingFileId, setNamingFileId] = useState('')
  const [trialBusy, setTrialBusy] = useState(false)

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

  const updatePrompt = (content: string) => {
    if (!version) return
    setVersion({
      ...version,
      node_prompts: {
        ...version.node_prompts,
        [selectedNode]: { ...(version.node_prompts[selectedNode] || {}), content },
      },
    })
  }

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

  const currentNode = FLOW_NODES.find(node => node.id === selectedNode)

  return (
    <>
      <section className="flex min-w-0 flex-1 flex-col bg-white">
        <header className="flex items-center justify-between border-b border-[#e5e7eb] px-6 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-[18px] font-semibold text-[#111827]">{assistant.name}</h2>
            <p className="text-[15px] text-[#6b7280]">流程步骤 · 当前 v{assistant.active_version || '—'}</p>
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
          >
            试运行
          </Button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)]">
          <div className="overflow-y-auto border-r border-[#e5e7eb] p-3">
            {FLOW_NODES.map((node, index) => (
              <button
                key={node.id}
                type="button"
                onClick={() => setSelectedNode(node.id)}
                className={cn(
                  'mb-0.5 flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left transition',
                  selectedNode === node.id ? 'bg-[#eff6ff]' : 'hover:bg-[#f9fafb]',
                )}
              >
                <span
                  className={cn(
                    'grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold',
                    selectedNode === node.id
                      ? 'bg-[#3b82f6] text-white'
                      : 'bg-[#f3f4f6] text-[#6b7280]',
                  )}
                >
                  {index + 1}
                </span>
                <span className="min-w-0">
                  <strong
                    className={cn(
                      'block truncate text-sm font-medium',
                      selectedNode === node.id ? 'text-[#1d4ed8]' : 'text-[#111827]',
                    )}
                  >
                    {node.label}
                  </strong>
                  <small className="text-xs text-[#6b7280]">{node.kind}</small>
                </span>
              </button>
            ))}
          </div>

          <div className="flex min-h-0 flex-col overflow-hidden p-5">
            <div className="mb-3 flex items-center gap-2">
              <Badge variant="secondary">{currentNode?.kind}</Badge>
              <span className="text-sm font-medium text-[#111827]">{currentNode?.label}</span>
            </div>
            <Label className="mb-2 text-[#6b7280]">系统提示词</Label>
            <Textarea
              className="min-h-0 flex-1 resize-none rounded-xl border-[#e5e7eb] bg-[#f9fafb] font-mono text-xs leading-relaxed"
              value={version?.node_prompts[selectedNode]?.content || ''}
              onChange={e => updatePrompt(e.target.value)}
              disabled={!version?.node_prompts[selectedNode]}
              placeholder={
                selectedNode === 'candidate_retrieval'
                  ? '这一步是自动检索，不需要提示词。'
                  : '告诉大模型这一步该怎么做'
              }
            />
            <p className="mt-2 truncate text-xs text-[#6b7280]">
              {version?.node_prompts[selectedNode]?.path || '该步不调用大模型'}
            </p>
          </div>
        </div>
      </section>

      <aside className="flex w-[320px] shrink-0 flex-col border-l border-[#e5e7eb] bg-white">
        <div className="border-b border-[#e5e7eb] px-5 py-4">
          <h3 className="text-[18px] font-semibold text-[#111827]">助手设置</h3>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="grid size-12 shrink-0 place-items-center rounded-xl border border-dashed border-[#d1d5db] bg-[#f9fafb] text-sm font-semibold text-[#6b7280]">
              {(assistant.name || '助').slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="truncate text-sm font-medium text-[#111827]">{assistant.name}</div>
              <p className="line-clamp-2 text-xs leading-relaxed text-[#9ca3af]">
                {assistant.description || '请输入描述'}
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-sm font-medium text-[#374151]">模型</Label>
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
            <Label className="text-sm font-medium text-[#374151]">知识库</Label>
            <SearchableMultiSelect
              values={[...kbSelected]}
              options={kbOptions}
              placeholder="请选择"
              emptyText="没有找到数据。"
              onChange={ids => setKbSelected(new Set(ids))}
            />
            {[...kbSelected].slice(0, 3).map(kbId => {
              const kb = knowledgeBases.find(item => item.id === kbId)
              if (!kb) return null
              return (
                <Link
                  key={kbId}
                  to={`/kb/${kbId}/rules`}
                  className="block text-xs text-[#2563eb] hover:underline"
                >
                  「{kb.name}」审查补充 →
                </Link>
              )
            })}
          </div>

          <div className="space-y-4">
            <button
              type="button"
              className="flex w-full items-center justify-between text-sm font-medium text-[#374151]"
              onClick={() => setAdvancedOpen(v => !v)}
            >
              <span className="inline-flex items-center gap-1.5">
                <Settings2 className="size-3.5 text-[#6b7280]" />
                高级设置
              </span>
              <ChevronDown className={cn('size-4 text-[#9ca3af] transition', advancedOpen && 'rotate-180')} />
            </button>
            {advancedOpen && version && (() => {
              const vectorWeight = Number(version.retrieval_config.vector_weight ?? 0.7)
              const keywordWeight = Math.round((1 - vectorWeight) * 100) / 100
              const threshold = Number(version.retrieval_config.similarity_threshold ?? 0.2)
              const topN = Number(version.retrieval_config.top_k ?? 10)
              const routeTopK = Number(version.retrieval_config.route_top_k ?? 30)
              const temperature = Number(version.model_config.temperature ?? 0)
              return (
                <div className="space-y-5">
                  <div>
                    <SettingHint
                      label="相似度阈值"
                      tip="低于该分数的候选会被过滤。数值越高，召回越严。"
                    />
                    <SettingSlider
                      value={threshold}
                      min={0}
                      max={1}
                      step={0.01}
                      format={v => v.toFixed(2)}
                      onChange={v => updateRetrieval('similarity_threshold', Math.round(v * 100) / 100)}
                    />
                  </div>

                  <div>
                    <SettingHint
                      label="向量相似度权重"
                      tip="语义检索与全文检索的混合比例。向右提高向量权重。"
                    />
                    <div className="mb-1.5 flex justify-between text-xs text-[#6b7280]">
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
                      value={topN}
                      min={1}
                      max={50}
                      step={1}
                      format={v => String(Math.round(v))}
                      parse={raw => Math.round(Number(raw))}
                      onChange={v => updateRetrieval('top_k', Math.round(v))}
                    />
                  </div>

                  <div>
                    <SettingHint
                      label="每路召回"
                      tip="向量 / 全文等各路先各自召回多少条，再合并重排。"
                    />
                    <SettingSlider
                      value={routeTopK}
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
                      value={temperature}
                      min={0}
                      max={2}
                      step={0.1}
                      format={v => v.toFixed(1)}
                      onChange={v => updateModel('temperature', Math.round(v * 10) / 10)}
                    />
                  </div>
                </div>
              )
            })()}
          </div>

          {(versionsQuery.data || []).length > 0 && (
            <div className="space-y-2">
              <Label className="text-sm font-medium text-[#374151]">版本</Label>
              <div className="space-y-1 text-xs text-[#6b7280]">
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
              <p className="text-sm text-state-error">请先在右侧选择知识库并保存。</p>
            )}
            <div className="space-y-2">
              <Label>检测报告</Label>
              <select
                className="flex h-10 w-full rounded-md border border-[#e5e7eb] bg-white px-3 text-sm"
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
                className="flex h-10 w-full rounded-md border border-[#e5e7eb] bg-white px-3 text-sm"
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
    </>
  )
}
