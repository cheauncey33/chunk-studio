import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronRight, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AssistantVersion, type AuditAssistant, type Job } from '@/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input, Label, Textarea, Badge } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EmptyState } from '@/components/empty-state'
import { Explain } from '@/components/explain'
import { useHelpMode } from '@/components/help-mode'
import { useAssistants, useKnowledgeBases, useKbFiles, queryKeys } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

const FLOW_NODES = [
  { id: 'report_parameters', label: '提取报告参数', kind: 'AI', help: '从报告里抽出型号、电压等基本信息。' },
  { id: 'test_items', label: '提取检测项目', kind: 'AI', help: '列出这份报告要审的检测项。' },
  { id: 'model_decode', label: '解析型号规则', kind: 'AI', help: '把型号拆成可对照标准的规则。' },
  { id: 'query_planner', label: '规划检索问题', kind: 'AI', help: '为每个检测项想好该去标准里搜什么。' },
  { id: 'candidate_retrieval', label: '查找候选证据', kind: '自动检索', help: '按规划好的问题，在绑定的知识库里找相关段落。' },
  { id: 'audit_judge', label: '对照标准判定', kind: 'AI', help: '用找到的证据，判断报告结果是否符合标准。' },
  { id: 'result_summary', label: '汇总结果', kind: '自动汇总', help: '把各步判定整理成一份可读的结果。' },
] as const

type FlowNodeId = (typeof FLOW_NODES)[number]['id']

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
    <div className="grid h-full grid-cols-[280px_1fr] overflow-hidden">
      <aside className="flex flex-col border-r border-border-button bg-bg-base">
        <div className="flex items-center justify-between px-4 py-4">
          <div>
            <Explain text={helpText.assistants.page} title="审查助手">
              <h1 className="text-base font-semibold">审查助手</h1>
            </Explain>
            <p className="text-xs text-text-secondary">配置知识库、流程与检索松紧</p>
          </div>
          <Explain text={helpText.assistants.create} title="新建助手">
            <Button size="icon" variant="outline" onClick={() => setCreateOpen(true)} title="新建助手">
              <Plus />
            </Button>
          </Explain>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-4">
          {isLoading && <div className="px-3 py-6 text-sm text-text-secondary">加载中…</div>}
          {!isLoading && !assistants.length && (
            <EmptyState icon={<Bot />} title="还没有审查助手" description="创建一个助手开始配置。" />
          )}
          {assistants.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => navigate(`/assistants/${item.id}`)}
              className={cn(
                'mb-1 w-full rounded-lg px-3 py-2.5 text-left transition hover:bg-bg-card',
                selected?.id === item.id && 'bg-bg-card',
              )}
            >
              <strong className="block truncate text-sm">{item.name}</strong>
              <span className="text-xs text-text-secondary">
                v{item.active_version || '—'} · {item.status === 'active' ? '已启用' : '草稿'}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <div className="min-w-0 overflow-auto p-5">
        {selected ? (
          <AssistantEditor
            assistant={selected}
            knowledgeBases={knowledgeBases}
            onChanged={() => client.invalidateQueries({ queryKey: queryKeys.assistants })}
          />
        ) : (
          <EmptyState
            icon={<Bot />}
            title="创建助手后，在这里配置"
            description="选择知识库、提示词和检索参数。"
            actionLabel="新建助手"
            onAction={() => setCreateOpen(true)}
          />
        )}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建审查助手</DialogTitle>
            <DialogDescription>创建时会复制内置流程为 v1，之后可绑定知识库并继续调整。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Explain text={helpText.assistants.createName} title="名称">
                <Label>名称</Label>
              </Explain>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="例如：油浸式变压器审查" />
            </div>
            <div className="space-y-2">
              <Explain text={helpText.assistants.createDesc} title="说明">
                <Label>说明</Label>
              </Explain>
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

function AssistantEditor({
  assistant,
  knowledgeBases,
  onChanged,
}: {
  assistant: AuditAssistant
  knowledgeBases: ReturnType<typeof useKnowledgeBases>['data']
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const [tab, setTab] = useState('workflow')
  const [selectedNode, setSelectedNode] = useState<FlowNodeId>('report_parameters')
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [trialOpen, setTrialOpen] = useState(false)
  const [reportFileId, setReportFileId] = useState('')
  const [namingFileId, setNamingFileId] = useState('')
  const [trialBusy, setTrialBusy] = useState(false)
  const { enabled: helpEnabled } = useHelpMode()

  const boundKbIds = useMemo(
    () => assistant.knowledge_bases.map(item => item.id),
    [assistant.knowledge_bases],
  )
  const primaryKbId = boundKbIds[0] || ''
  const { data: kbFiles = [] } = useKbFiles(primaryKbId || undefined)
  const readyFiles = useMemo(
    () => kbFiles.filter(file => file.parse_ready),
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

  const saveVersion = async () => {
    if (!version) return
    setSaving(true)
    try {
      await api.createAssistantVersion(assistant.id, {
        model_config: version.model_config,
        node_prompts: version.node_prompts,
        rules: version.rules,
        retrieval_config: version.retrieval_config,
      })
      await Promise.all([activeQuery.refetch(), versionsQuery.refetch()])
      onChanged()
      toast.success('已保存为新版本')
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
    throw new Error('试运行超时，请稍后在运行记录中查看')
  }

  const startTrial = async () => {
    if (!reportFileId) {
      toast.error('请选择已解析完成的报告 PDF')
      return
    }
    setTrialBusy(true)
    try {
      const job = await api.startAssistantRun(assistant.id, {
        report_file_id: reportFileId,
        naming_rule_file_id: namingFileId || null,
      })
      toast.message('试运行已提交，正在执行…')
      const finished = await pollJob(job.id)
      if (finished.status === 'failed') {
        throw new Error(finished.error || '试运行失败')
      }
      const reportName = String((finished.result as Record<string, unknown>)?.report_name || '')
      toast.success(reportName ? `试运行完成：${reportName}` : '试运行完成')
      setTrialOpen(false)
      navigate('/runs')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setTrialBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold">{assistant.name}</h1>
            <Badge variant={assistant.status === 'active' ? 'success' : 'secondary'}>
              {assistant.status === 'active' ? '已启用' : '草稿'}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-text-secondary">当前版本 v{assistant.active_version || '—'}</p>
        </div>
        <div className="flex gap-2">
          <Explain text={helpText.assistants.trial} title="试运行">
            <Button
              variant="outline"
              disabled={!assistant.active_version}
              onClick={() => {
                setReportFileId(readyFiles[0]?.id || '')
                setNamingFileId('')
                setTrialOpen(true)
              }}
            >
              试运行
            </Button>
          </Explain>
          <Explain text={helpText.assistants.saveVersion} title="保存为新版本">
            <Button disabled={!version || saving} onClick={saveVersion}>
              {saving ? '保存中…' : '保存为新版本'}
            </Button>
          </Explain>
        </div>
      </div>

      <Dialog open={trialOpen} onOpenChange={open => !trialBusy && setTrialOpen(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>试运行审查</DialogTitle>
            <DialogDescription>
              选择已解析完成的报告 PDF。未选手动命名规则时，将使用系统默认规则文件。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {!primaryKbId && (
              <p className="text-sm text-state-error">请先在「知识库」页签绑定至少一个知识库。</p>
            )}
            {primaryKbId && !readyFiles.length && (
              <p className="text-sm text-state-warning">
                绑定知识库里还没有「已解析」的文件。请先到知识库上传 PDF 并等解析完成。
              </p>
            )}
            <div className="space-y-2">
              <Label>报告文件</Label>
              <select
                className="flex h-10 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm"
                value={reportFileId}
                onChange={e => setReportFileId(e.target.value)}
                disabled={!readyFiles.length || trialBusy}
              >
                <option value="">选择已解析 PDF</option>
                {readyFiles.map(file => (
                  <option key={file.id} value={file.id}>{file.name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label>命名规则文件（可选）</Label>
              <select
                className="flex h-10 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm"
                value={namingFileId}
                onChange={e => setNamingFileId(e.target.value)}
                disabled={!readyFiles.length || trialBusy}
              >
                <option value="">使用系统默认</option>
                {readyFiles.map(file => (
                  <option key={file.id} value={file.id}>{file.name}</option>
                ))}
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={trialBusy} onClick={() => setTrialOpen(false)}>取消</Button>
            <Button disabled={trialBusy || !reportFileId} onClick={startTrial}>
              {trialBusy ? '运行中…' : '开始试运行'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="basic" title={helpText.assistants.tabBasic}>基础设置</TabsTrigger>
          <TabsTrigger value="knowledge" title={helpText.assistants.tabKnowledge}>知识库</TabsTrigger>
          <TabsTrigger value="workflow" title={helpText.assistants.tabWorkflow}>流程与提示词</TabsTrigger>
          <TabsTrigger value="retrieval" title={helpText.assistants.tabRetrieval}>检索参数</TabsTrigger>
          <TabsTrigger value="versions" title={helpText.assistants.tabVersions}>版本记录</TabsTrigger>
        </TabsList>
        {helpEnabled && (
          <p className="mt-2 text-xs text-text-secondary">
            说明模式：把鼠标移到页签上可看简要提示；各页签内的字段旁还有「?」。
          </p>
        )}

        <TabsContent value="basic">
          <Card>
            <CardHeader>
              <CardTitle>基础设置</CardTitle>
              <CardDescription>当前仅支持 DeepSeek。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>助手名称</Label>
                <Input value={assistant.name} disabled />
              </div>
              <div className="space-y-2">
                <Label>说明</Label>
                <Textarea value={assistant.description} disabled />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="knowledge">
          <AssistantKnowledge assistant={assistant} knowledgeBases={knowledgeBases || []} onChanged={onChanged} />
        </TabsContent>

        <TabsContent value="workflow">
          {version ? (
            <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
              <Card>
                <CardHeader>
                  <Explain text={helpText.assistants.nodeList} title="流程步骤">
                    <CardTitle className="text-base">流程步骤</CardTitle>
                  </Explain>
                  <CardDescription>共 {FLOW_NODES.length} 步。点某一步，右侧改它的提示词。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-1">
                  {FLOW_NODES.map((node, index) => (
                    <Explain key={node.id} text={node.help} title={node.label} className="w-full">
                      <button
                        type="button"
                        onClick={() => setSelectedNode(node.id)}
                        className={cn(
                          'flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-bg-card',
                          selectedNode === node.id && 'bg-bg-card',
                        )}
                      >
                        <span className="grid size-6 shrink-0 place-items-center rounded-full bg-bg-accent text-xs font-semibold text-accent-primary">
                          {index + 1}
                        </span>
                        <span>
                          <strong className="block text-sm">{node.label}</strong>
                          <small className="text-text-secondary">{node.kind}</small>
                        </span>
                      </button>
                    </Explain>
                  ))}
                </CardContent>
              </Card>

              <div className="space-y-4">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">节点设置</CardTitle>
                  </CardHeader>
                  <CardContent className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label>执行类型</Label>
                      <Input disabled value={FLOW_NODES.find(n => n.id === selectedNode)?.kind || ''} />
                    </div>
                    <div className="space-y-2">
                      <Label>执行模型</Label>
                      <Input disabled value={selectedNode === 'candidate_retrieval' ? '自动检索' : '大模型'} />
                    </div>
                    <div className="space-y-2">
                      <Label>温度</Label>
                      <Input disabled value={String(version.model_config.temperature ?? 0)} />
                    </div>
                    <div className="space-y-2">
                      <Label>输出格式</Label>
                      <Input disabled value={String(version.model_config.response_format || 'json_object')} />
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <Explain text={helpText.assistants.prompt} title="系统提示词">
                      <CardTitle className="text-base">系统提示词</CardTitle>
                    </Explain>
                    <CardDescription>{version.node_prompts[selectedNode]?.path || '该步不调用大模型'}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Textarea
                      className="min-h-[240px] font-mono text-xs"
                      value={version.node_prompts[selectedNode]?.content || ''}
                      onChange={e => updatePrompt(e.target.value)}
                      disabled={!version.node_prompts[selectedNode]}
                      placeholder={selectedNode === 'candidate_retrieval' ? '这一步是自动检索，不需要提示词。' : '告诉大模型这一步该怎么做'}
                    />
                  </CardContent>
                </Card>

                {(['输入模板', '输出结构', '额外规则'] as const).map(label => (
                  <button
                    key={label}
                    type="button"
                    className="flex w-full items-center justify-between rounded-xl border border-border-button bg-bg-base px-4 py-3 text-left"
                    onClick={() => setExpanded(expanded === label ? null : label)}
                  >
                    <span>
                      <strong className="text-sm">{label}</strong>
                      <span className="ml-3 text-xs text-text-secondary">
                        {label === '额外规则'
                          ? `${Array.isArray(version.rules.rules) ? version.rules.rules.length : 0} 条版本化规则`
                          : '运行记录中展示每次实际输入/输出'}
                      </span>
                    </span>
                    {expanded === label ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState title="该助手还没有活动版本" description="创建首个版本后可配置流程。" />
          )}
        </TabsContent>

        <TabsContent value="retrieval">
          {version ? (
            <AssistantRetrieval version={version} onChange={setVersion} />
          ) : (
            <EmptyState title="暂无活动版本" />
          )}
        </TabsContent>

        <TabsContent value="versions">
          <Card>
            <CardHeader>
              <CardTitle>版本记录</CardTitle>
              <CardDescription>活动版本可回看，保存配置会产生新版本。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {(versionsQuery.data || []).map(item => (
                <div key={item.id} className="flex items-center justify-between rounded-lg border border-border-button px-3 py-2">
                  <span>
                    <strong>v{item.version}</strong>
                    <small className="ml-2 text-text-secondary">{formatDate(item.created_at)}</small>
                  </span>
                  <Badge variant={item.status === 'active' ? 'success' : 'secondary'}>
                    {item.status === 'active' ? '当前启用' : item.status === 'draft' ? '草稿' : '已退役'}
                  </Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function AssistantKnowledge({
  assistant,
  knowledgeBases,
  onChanged,
}: {
  assistant: AuditAssistant
  knowledgeBases: { id: string; name: string; file_count: number; chunk_count: number }[]
  onChanged: () => void
}) {
  const [selected, setSelected] = useState(() => new Set(assistant.knowledge_bases.map(item => item.id)))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setSelected(new Set(assistant.knowledge_bases.map(item => item.id)))
  }, [assistant])

  const save = async () => {
    setSaving(true)
    try {
      await api.setAssistantKnowledgeBases(assistant.id, [...selected])
      onChanged()
      toast.success('知识库范围已保存')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <Explain text={helpText.assistants.tabKnowledge} title="关联知识库">
          <CardTitle>关联知识库</CardTitle>
        </Explain>
        <CardDescription>助手只会从勾选的知识库里找证据，可多选。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {knowledgeBases.map(kb => (
          <label key={kb.id} className="flex items-start gap-3 rounded-lg border border-border-button px-3 py-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={selected.has(kb.id)}
              onChange={e => {
                setSelected(current => {
                  const next = new Set(current)
                  if (e.target.checked) next.add(kb.id)
                  else next.delete(kb.id)
                  return next
                })
              }}
            />
            <span>
              <strong className="block text-sm">{kb.name}</strong>
              <small className="text-text-secondary">{kb.file_count} 个文件 · {kb.chunk_count} 段内容</small>
            </span>
          </label>
        ))}
        <Explain text={helpText.assistants.bindKb} title="保存知识库范围">
          <Button disabled={saving} onClick={save}>{saving ? '保存中…' : '保存知识库范围'}</Button>
        </Explain>
      </CardContent>
    </Card>
  )
}

function AssistantRetrieval({
  version,
  onChange,
}: {
  version: AssistantVersion
  onChange: (version: AssistantVersion) => void
}) {
  const config = version.retrieval_config
  const update = (key: string, value: number) =>
    onChange({ ...version, retrieval_config: { ...config, [key]: value } })

  return (
    <Card>
      <CardHeader>
        <Explain text={helpText.assistants.tabRetrieval} title="检索参数">
          <CardTitle>检索参数</CardTitle>
        </Explain>
        <CardDescription>
          只影响当前助手：改完后要点右上角「保存为新版本」才生效。不同助手互不影响。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Explain text={helpText.retrieval.topK} title="最终返回数量">
            <Label>最终返回数量</Label>
          </Explain>
          <Input type="number" value={Number(config.top_k || 10)} onChange={e => update('top_k', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Explain text="每一路检索先多捞一些候选，再合并成最终结果。数字越大候选越多。" title="每路召回数量">
            <Label>每路召回数量</Label>
          </Explain>
          <Input type="number" value={Number(config.route_top_k || 30)} onChange={e => update('route_top_k', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Explain text={helpText.retrieval.threshold} title="相似度门槛">
            <Label>相似度门槛</Label>
          </Explain>
          <Input type="number" step={0.05} value={Number(config.similarity_threshold || 0.2)} onChange={e => update('similarity_threshold', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Explain text="语义权重已保存，但当前融合排序尚未使用该权重（预留配置）。" title="语义权重">
            <Label>语义权重</Label>
          </Explain>
          <Input type="number" step={0.05} value={Number(config.vector_weight || 0.7)} onChange={e => update('vector_weight', Number(e.target.value))} />
        </div>
        <p className="sm:col-span-2 text-sm text-text-secondary">
          系统级「是否开启双路检索」在「设置」里；这里只调本助手的松紧。语义权重目前仅保存，不参与融合。
        </p>
      </CardContent>
    </Card>
  )
}
