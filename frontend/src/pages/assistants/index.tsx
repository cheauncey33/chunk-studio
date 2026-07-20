import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronRight, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AssistantVersion, type AuditAssistant } from '@/api'
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
import { useAssistants, useKnowledgeBases, queryKeys } from '@/hooks/use-knowledge-request'
import { cn, formatDate } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

const FLOW_NODES = [
  { id: 'report_parameters', label: '报告参数提取', kind: 'DeepSeek' },
  { id: 'test_items', label: '检测项目提取', kind: 'DeepSeek' },
  { id: 'model_decode', label: '型号规则解析', kind: 'DeepSeek' },
  { id: 'query_planner', label: '检索 Query 规划', kind: 'DeepSeek' },
  { id: 'candidate_retrieval', label: '候选证据检索', kind: '规则检索' },
  { id: 'audit_judge', label: '标准值审查', kind: 'DeepSeek' },
  { id: 'result_summary', label: '结果汇总', kind: '确定性汇总' },
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
            <h1 className="text-base font-semibold">审查助手</h1>
            <p className="text-xs text-text-secondary">配置知识库、流程与检索</p>
          </div>
          <Button size="icon" variant="outline" onClick={() => setCreateOpen(true)} title="新建助手">
            <Plus />
          </Button>
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
                DeepSeek · v{item.active_version || '—'} · {item.status === 'active' ? '已启用' : '草稿'}
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
            <DialogDescription>创建后可绑定知识库并保存首个流程版本。</DialogDescription>
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

function AssistantEditor({
  assistant,
  knowledgeBases,
  onChanged,
}: {
  assistant: AuditAssistant
  knowledgeBases: ReturnType<typeof useKnowledgeBases>['data']
  onChanged: () => void
}) {
  const [tab, setTab] = useState('workflow')
  const [selectedNode, setSelectedNode] = useState<FlowNodeId>('report_parameters')
  const [version, setVersion] = useState<AssistantVersion | null>(null)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

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
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button variant="outline" disabled>
                  试运行
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>试运行能力尚未接入</TooltipContent>
          </Tooltip>
          <Button disabled={!version || saving} onClick={saveVersion}>
            {saving ? '保存中…' : '保存为新版本'}
          </Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="basic">基础设置</TabsTrigger>
          <TabsTrigger value="knowledge">知识库</TabsTrigger>
          <TabsTrigger value="workflow">流程与提示词</TabsTrigger>
          <TabsTrigger value="retrieval">检索参数</TabsTrigger>
          <TabsTrigger value="versions">版本记录</TabsTrigger>
        </TabsList>

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
                  <CardTitle className="text-base">流程节点</CardTitle>
                  <CardDescription>共 {FLOW_NODES.length} 步，纵向展开避免拥挤。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-1">
                  {FLOW_NODES.map((node, index) => (
                    <button
                      key={node.id}
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
                      <Input disabled value={selectedNode === 'candidate_retrieval' ? '确定性检索' : 'DeepSeek'} />
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
                    <CardTitle className="text-base">系统提示词</CardTitle>
                    <CardDescription>{version.node_prompts[selectedNode]?.path || '该节点不调用模型'}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Textarea
                      className="min-h-[240px] font-mono text-xs"
                      value={version.node_prompts[selectedNode]?.content || ''}
                      onChange={e => updatePrompt(e.target.value)}
                      disabled={!version.node_prompts[selectedNode]}
                      placeholder={selectedNode === 'candidate_retrieval' ? '确定性检索节点不使用系统提示词。' : '输入该节点的系统提示词'}
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
        <CardTitle>关联知识库</CardTitle>
        <CardDescription>助手只会从选中的知识库中检索证据，可多选。</CardDescription>
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
              <small className="text-text-secondary">{kb.file_count} 个文件 · {kb.chunk_count} 个切片</small>
            </span>
          </label>
        ))}
        <Button disabled={saving} onClick={save}>{saving ? '保存中…' : '保存知识库范围'}</Button>
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
        <CardTitle>检索参数</CardTitle>
        <CardDescription>
          这些参数随助手版本保存。点击右上角“保存为新版本”后生效；当前 v{version.version} 不会被覆盖。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label>最终返回数量</Label>
          <Input type="number" value={Number(config.top_k || 10)} onChange={e => update('top_k', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Label>每路召回数量</Label>
          <Input type="number" value={Number(config.route_top_k || 30)} onChange={e => update('route_top_k', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Label>相似度阈值</Label>
          <Input type="number" step={0.05} value={Number(config.similarity_threshold || 0.2)} onChange={e => update('similarity_threshold', Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Label>向量权重</Label>
          <Input type="number" step={0.05} value={Number(config.vector_weight || 0.7)} onChange={e => update('vector_weight', Number(e.target.value))} />
        </div>
        <p className="sm:col-span-2 text-sm text-text-secondary">
          也可在 <Link className="text-accent-primary" to="/settings">全局设置</Link> 中调整默认检索开关。
        </p>
      </CardContent>
    </Card>
  )
}
