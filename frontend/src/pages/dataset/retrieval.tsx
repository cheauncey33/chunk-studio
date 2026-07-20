import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { toast } from 'sonner'
import { api, type VectorSearchHit } from '@/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input, Label, Textarea, Badge } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { EmptyState } from '@/components/empty-state'
import { useKnowledgeBase } from '@/hooks/use-knowledge-request'

export default function DatasetRetrievalPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const initial = knowledgeBase?.retrieval_config || {}
  const [query, setQuery] = useState('变压器的空载损耗限值是什么？')
  const [topK, setTopK] = useState(Number(initial.top_k || 10))
  const [threshold, setThreshold] = useState(Number(initial.similarity_threshold || 0.2))
  const [keywordWeight, setKeywordWeight] = useState(Number(initial.keyword_weight || 0.3))
  const [contentType, setContentType] = useState('all')
  const [hits, setHits] = useState<VectorSearchHit[]>([])
  const [debug, setDebug] = useState<Record<string, unknown> | null>(null)
  const [mode, setMode] = useState<'result' | 'debug'>('result')
  const [running, setRunning] = useState(false)

  const run = async () => {
    if (!query.trim() || !id) return
    setRunning(true)
    try {
      const result = await api.testKnowledgeBaseRetrieval(id, {
        query: query.trim(),
        top_k: topK,
        similarity_threshold: threshold,
        keyword_weight: keywordWeight,
        content_type: contentType === 'all' ? undefined : contentType,
      })
      setHits(result.hits)
      setDebug({
        retrieval_mode: result.retrieval_mode,
        query_routes: result.query_routes,
        candidate_count: result.candidate_count,
        degraded: result.degraded,
        scoped_file_count: result.scoped_file_count,
        keyword_weight: keywordWeight,
        content_type: contentType,
      })
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="grid min-h-full grid-cols-1 gap-5 pt-2 xl:grid-cols-2">
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle>检索参数</CardTitle>
          <CardDescription>
            仅在当前知识库的 {knowledgeBase?.file_count ?? 0} 个文件中检索
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>测试问题</Label>
            <Textarea value={query} onChange={e => setQuery(e.target.value)} rows={4} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>返回数量</Label>
              <Input type="number" min={1} max={50} value={topK} onChange={e => setTopK(Number(e.target.value))} />
            </div>
            <div className="space-y-2">
              <Label>相似度阈值</Label>
              <Input type="number" min={-1} max={1} step={0.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} />
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <Label>关键词 / 向量权重</Label>
              <span className="text-text-secondary">
                {keywordWeight.toFixed(2)} / {(1 - keywordWeight).toFixed(2)}
              </span>
            </div>
            <input
              className="w-full accent-[rgb(var(--accent-primary))]"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={keywordWeight}
              onChange={e => setKeywordWeight(Number(e.target.value))}
            />
          </div>
          <div className="space-y-2">
            <Label>内容类型</Label>
            <Select value={contentType} onValueChange={setContentType}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="table">表格</SelectItem>
                <SelectItem value="section">章节</SelectItem>
                <SelectItem value="image">图片</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-2">
            <Button onClick={run} disabled={running}>{running ? '检索中…' : '开始检索'}</Button>
            <Button variant="outline" onClick={() => { setHits([]); setDebug(null) }}>重置</Button>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border-button bg-bg-base">
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div>
            <CardTitle>检索结果</CardTitle>
            <CardDescription>结果保留文件、页码和切片证据。</CardDescription>
          </div>
          <div className="inline-flex rounded-md bg-bg-card p-1">
            <button
              className={`rounded px-3 py-1 text-sm ${mode === 'result' ? 'bg-bg-base shadow-sm' : 'text-text-secondary'}`}
              onClick={() => setMode('result')}
            >
              结果
            </button>
            <button
              className={`rounded px-3 py-1 text-sm ${mode === 'debug' ? 'bg-bg-base shadow-sm' : 'text-text-secondary'}`}
              onClick={() => setMode('debug')}
            >
              调试
            </button>
          </div>
        </CardHeader>
        <CardContent>
          {mode === 'debug' ? (
            <pre className="overflow-auto rounded-lg bg-bg-canvas p-3 text-xs">
              {JSON.stringify(debug || { message: '运行一次检索后显示路由与降级信息' }, null, 2)}
            </pre>
          ) : hits.length ? (
            <div className="space-y-3">
              {hits.map((hit, index) => (
                <RetrievalResult key={hit.chunk_id} hit={hit} index={index} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Search />}
              title="输入问题开始检索"
              description="这里不会跨越当前知识库取证。"
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RetrievalResult({ hit, index }: { hit: VectorSearchHit; index: number }) {
  const [open, setOpen] = useState(false)
  const score = hit.rerank_score ?? hit.score
  return (
    <article className="rounded-lg border border-border-button">
      <button type="button" className="flex w-full gap-3 p-3 text-left" onClick={() => setOpen(v => !v)}>
        <span className="grid size-7 shrink-0 place-items-center rounded-md bg-bg-accent text-xs font-semibold text-accent-primary">
          {index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <strong className="block truncate text-sm">{hit.file_name}</strong>
          <small className="text-text-secondary">
            p.{hit.page} · {String(hit.business_metadata.content_type || 'text')}
          </small>
          <p className="mt-1 line-clamp-3 text-sm text-text-secondary">
            {hit.text.replace(/<[^>]+>/g, ' ').slice(0, 280)}
          </p>
        </span>
        <Badge variant="secondary">融合 {score.toFixed(3)}</Badge>
      </button>
      {open && (
        <pre className="border-t border-border-button bg-bg-canvas p-3 text-xs">
          {JSON.stringify({ business_metadata: hit.business_metadata, source_trace: hit.source_trace, route_ranks: hit.route_ranks }, null, 2)}
        </pre>
      )}
    </article>
  )
}
