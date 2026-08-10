import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { toast } from 'sonner'
import { api, type KnowledgeBaseFile, type VectorSearchHit } from '@/api'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input, Label, Textarea, Badge } from '@/components/ui/input'
import { EmptyState } from '@/components/empty-state'
import { useKbFiles } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { renderChunkText } from '@/lib/render-chunk-text'
import { cn } from '@/lib/utils'

export default function DatasetRetrievalPage() {
  const { id = '' } = useParams()
  const { data: files = [], isLoading: filesLoading } = useKbFiles(id)
  const [query, setQuery] = useState('变压器的空载损耗限值是什么？')
  const [topK, setTopK] = useState(10)
  const [denseThreshold, setDenseThreshold] = useState(0)
  const [rerankThreshold, setRerankThreshold] = useState(0.2)
  const [routeTopK, setRouteTopK] = useState(30)
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set())
  const [selectionReady, setSelectionReady] = useState(false)
  const [hits, setHits] = useState<VectorSearchHit[]>([])
  const [searched, setSearched] = useState(false)
  const [scopedFileCount, setScopedFileCount] = useState(0)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    setSelectionReady(false)
    setHits([])
    setSearched(false)
  }, [id])

  useEffect(() => {
    if (filesLoading || selectionReady) return
    const defaults = files.filter(file => file.enabled).map(file => file.id)
    setSelectedFileIds(new Set(defaults.length ? defaults : files.map(file => file.id)))
    setSelectionReady(true)
  }, [files, filesLoading, selectionReady])

  const selectedFiles = useMemo(
    () => files.filter(file => selectedFileIds.has(file.id)),
    [files, selectedFileIds],
  )
  const selectedChunkCount = useMemo(
    () => selectedFiles.reduce((sum, file) => sum + (file.chunk_count || 0), 0),
    [selectedFiles],
  )

  const toggleFile = (fileId: string) => {
    setSelectedFileIds(prev => {
      const next = new Set(prev)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return next
    })
  }

  const selectAll = () => setSelectedFileIds(new Set(files.map(file => file.id)))
  const selectEnabled = () =>
    setSelectedFileIds(new Set(files.filter(file => file.enabled).map(file => file.id)))
  const clearSelection = () => setSelectedFileIds(new Set())

  const run = async () => {
    if (!query.trim() || !id) return
    if (!selectedFileIds.size) {
      toast.error('请先勾选至少一个文件作为检索范围')
      return
    }
    setRunning(true)
    try {
      const result = await api.testKnowledgeBaseRetrieval(id, {
        query: query.trim(),
        top_k: topK,
        dense_threshold: denseThreshold,
        rerank_threshold: rerankThreshold,
        route_top_k: routeTopK,
        file_ids: [...selectedFileIds],
      })
      setHits(result.hits)
      setScopedFileCount(result.scoped_file_count)
      setSearched(true)
      if (!result.hits.length) {
        if (!result.scoped_file_count) {
          toast.message('检索范围为空', { description: '当前没有选中文件。' })
        } else if (selectedChunkCount === 0) {
          toast.message('没有可检索内容', {
            description: '选中的文件还没有切片，请先解析并生成切片。',
          })
        }
      }
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="grid min-h-full grid-cols-1 gap-5 pt-2 xl:grid-cols-[minmax(280px,380px)_minmax(0,1fr)]">
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <Explain text={helpText.retrieval.page} title="检索测试">
            <CardTitle>检索测试</CardTitle>
          </Explain>
          <CardDescription>
            在勾选的 {selectedFileIds.size} 个文件（共 {selectedChunkCount} 段）里试检索。不会改正式审查设置。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Explain text={helpText.retrieval.query} title="测试问题">
              <Label>测试问题</Label>
            </Explain>
            <Textarea
              value={query}
              onChange={e => setQuery(e.target.value)}
              rows={4}
              placeholder="用日常说法提问，例如：空载损耗限值是多少？"
            />
          </div>

          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Explain text={helpText.retrieval.scope} title="检索范围">
                <Label>检索范围</Label>
              </Explain>
              <div className="flex flex-wrap gap-1">
                <Button type="button" size="sm" variant="ghost" onClick={selectAll} disabled={!files.length}>
                  全选
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={selectEnabled} disabled={!files.length}>
                  仅启用
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={clearSelection} disabled={!selectedFileIds.size}>
                  清空
                </Button>
              </div>
            </div>
            {filesLoading ? (
              <p className="text-sm text-text-secondary">加载文件列表…</p>
            ) : !files.length ? (
              <p className="rounded-lg border border-dashed border-border-button px-3 py-4 text-sm text-text-secondary">
                这个知识库还没有文件。请先到「文件」页上传并生成切片。
              </p>
            ) : (
              <div className="max-h-56 space-y-1 overflow-auto rounded-lg border border-border-button p-2">
                {files.map(file => (
                  <FileScopeRow
                    key={file.id}
                    file={file}
                    checked={selectedFileIds.has(file.id)}
                    onToggle={() => toggleFile(file.id)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Explain text={helpText.retrieval.topK} title="返回条数">
                <Label>返回条数</Label>
              </Explain>
              <Input type="number" min={1} max={50} value={topK} onChange={e => setTopK(Number(e.target.value))} />
            </div>
            <div className="space-y-2">
              <Explain text="向量召回阶段的最低分数；0 表示不做 Dense 预过滤。" title="Dense 召回阈值">
                <Label>Dense 召回阈值</Label>
              </Explain>
              <Input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={denseThreshold}
                onChange={e => setDenseThreshold(Number(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Explain text="重排完成后的最低相关性分数；0 表示不做 Rerank 结果过滤。" title="Rerank 结果阈值">
                <Label>Rerank 结果阈值</Label>
              </Explain>
              <Input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={rerankThreshold}
                onChange={e => setRerankThreshold(Number(e.target.value))}
              />
            </div>
            <div className="col-span-2 space-y-2">
              <Explain text="每一路检索先捞多少条候选，再合并排序。数字越大候选越多，试检索更慢也可能更全。" title="每路召回数量">
                <Label>每路召回数量</Label>
              </Explain>
              <Input
                type="number"
                min={1}
                max={100}
                value={routeTopK}
                onChange={e => setRouteTopK(Number(e.target.value))}
              />
            </div>
          </div>
          <p className="text-xs text-text-secondary">
            正式审查时的松紧程度，请到「审查助手 → 检索参数」里配置并保存。
          </p>
          <div className="flex gap-2">
            <Explain text={helpText.retrieval.run} title="开始检索">
              <Button onClick={run} disabled={running || !selectedFileIds.size}>
                {running ? '检索中…' : '开始检索'}
              </Button>
            </Explain>
            <Explain text={helpText.retrieval.reset} title="重置">
              <Button
                variant="outline"
                onClick={() => {
                  setHits([])
                  setSearched(false)
                }}
              >
                重置
              </Button>
            </Explain>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <Explain text={helpText.retrieval.result} title="检索结果">
            <CardTitle>检索结果</CardTitle>
          </Explain>
          <CardDescription>
            {searched
              ? `共 ${hits.length} 条 · 范围 ${scopedFileCount} 个文件`
              : '每条都带文件名、页码和原文，方便核对。'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hits.length ? (
            <div className="space-y-3">
              {hits.map((hit, index) => (
                <RetrievalResult key={hit.chunk_id} hit={hit} index={index} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Search />}
              title={searched ? '没有命中结果' : '输入问题开始检索'}
              description={
                searched
                  ? selectedChunkCount === 0
                    ? '选中的文件还没有切片。请先解析并生成切片后再试。'
                    : '可以换个问法、降低相似度门槛，或扩大勾选的文件范围。'
                  : '结果只来自左侧勾选的文件。'
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function FileScopeRow({
  file,
  checked,
  onToggle,
}: {
  file: KnowledgeBaseFile
  checked: boolean
  onToggle: () => void
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-bg-accent',
        checked && 'bg-bg-accent/60',
      )}
    >
      <input
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-[rgb(var(--accent-primary))]"
        checked={checked}
        onChange={onToggle}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-text-primary">{file.name}</span>
        <span className="text-xs text-text-secondary">
          {file.chunk_count} 段
          {!file.enabled ? ' · 未启用' : ''}
          {!file.parse_ready ? ' · 未解析完成' : ''}
        </span>
      </span>
    </label>
  )
}

function RetrievalResult({ hit, index }: { hit: VectorSearchHit; index: number }) {
  const score = hit.rerank_score ?? hit.score
  const scoreLabel = hit.rerank_score != null ? '相似度' : '向量分'
  const scoreHelp =
    hit.rerank_score != null
      ? 'Qwen 重排序给出的相关性分（0–1 左右，越高越相关）'
      : '重排序不可用时回退为向量检索分数'
  const rendered = renderChunkText(hit.text)
  const isTable = String(hit.business_metadata.content_type || '') === 'table' || /<table[\s>]/i.test(hit.text)

  return (
    <article className="rounded-lg border border-border-button p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant="secondary" title={scoreHelp}>
          {scoreLabel} {score.toFixed(3)}
        </Badge>
        <span className="grid size-6 place-items-center rounded-md bg-bg-accent text-xs font-semibold text-accent-primary">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <strong className="block truncate text-sm">{hit.file_name}</strong>
          <small className="text-text-secondary">
            第 {hit.page} 页 · {String(hit.business_metadata.content_type || '正文')}
          </small>
        </div>
      </div>
      <div
        className={cn(
          'legacy-surface rendered-text rounded-md border border-border-button bg-bg-canvas p-3 text-sm',
          isTable && 'fit-width',
        )}
        dangerouslySetInnerHTML={{ __html: rendered || '<p class="muted">（空切片）</p>' }}
      />
    </article>
  )
}
