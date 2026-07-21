import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { toast } from 'sonner'
import { api, type VectorSearchHit } from '@/api'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input, Label, Textarea, Badge } from '@/components/ui/input'
import { EmptyState } from '@/components/empty-state'
import { useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'

export default function DatasetRetrievalPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const initial = knowledgeBase?.retrieval_config || {}
  const [query, setQuery] = useState('变压器的空载损耗限值是什么？')
  const [topK, setTopK] = useState(Number(initial.top_k || 10))
  const [threshold, setThreshold] = useState(Number(initial.similarity_threshold || 0.2))
  const [routeTopK, setRouteTopK] = useState(Number(initial.route_top_k || 30))
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
        route_top_k: routeTopK,
      })
      setHits(result.hits)
      setDebug({
        retrieval_mode: result.retrieval_mode,
        query_routes: result.query_routes,
        candidate_count: result.candidate_count,
        degraded: result.degraded,
        scoped_file_count: result.scoped_file_count,
        retrieval_params: result.retrieval_params,
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
          <Explain text={helpText.retrieval.page} title="试检索">
            <CardTitle>试检索参数</CardTitle>
          </Explain>
          <CardDescription>
            只在本知识库的 {knowledgeBase?.file_count ?? 0} 个文件里找答案。这里是试跑，不会改正式审查设置。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Explain text={helpText.retrieval.query} title="测试问题">
              <Label>测试问题</Label>
            </Explain>
            <Textarea value={query} onChange={e => setQuery(e.target.value)} rows={4} placeholder="用日常说法提问，例如：空载损耗限值是多少？" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Explain text={helpText.retrieval.topK} title="返回条数">
                <Label>返回条数</Label>
              </Explain>
              <Input type="number" min={1} max={50} value={topK} onChange={e => setTopK(Number(e.target.value))} />
            </div>
            <div className="space-y-2">
              <Explain text={helpText.retrieval.threshold} title="相似度门槛">
                <Label>相似度门槛</Label>
              </Explain>
              <Input type="number" min={-1} max={1} step={0.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} />
            </div>
            <div className="space-y-2 col-span-2">
              <Explain text="每一路检索先捞多少条候选，再合并排序。数字越大候选越多，试检索更慢也可能更全。" title="每路召回数量">
                <Label>每路召回数量</Label>
              </Explain>
              <Input type="number" min={1} max={100} value={routeTopK} onChange={e => setRouteTopK(Number(e.target.value))} />
            </div>
          </div>
          <p className="text-xs text-text-secondary">
            正式审查时的松紧程度，请到「审查助手 → 检索参数」里配置并保存为新版本。
          </p>
          <div className="flex gap-2">
            <Explain text={helpText.retrieval.run} title="开始检索">
              <Button onClick={run} disabled={running}>{running ? '检索中…' : '开始检索'}</Button>
            </Explain>
            <Explain text={helpText.retrieval.reset} title="重置">
              <Button variant="outline" onClick={() => { setHits([]); setDebug(null) }}>重置</Button>
            </Explain>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border-button bg-bg-base">
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div>
            <Explain text={helpText.retrieval.result} title="检索结果">
              <CardTitle>检索结果</CardTitle>
            </Explain>
            <CardDescription>每条都带文件名、页码和原文，方便核对。</CardDescription>
          </div>
          <div className="inline-flex rounded-md bg-bg-card p-1">
            <Explain text={helpText.retrieval.result} title="结果">
              <button
                className={`rounded px-3 py-1 text-sm ${mode === 'result' ? 'bg-bg-base shadow-sm' : 'text-text-secondary'}`}
                onClick={() => setMode('result')}
              >
                结果
              </button>
            </Explain>
            <Explain text={helpText.retrieval.debug} title="调试">
              <button
                className={`rounded px-3 py-1 text-sm ${mode === 'debug' ? 'bg-bg-base shadow-sm' : 'text-text-secondary'}`}
                onClick={() => setMode('debug')}
              >
                调试
              </button>
            </Explain>
          </div>
        </CardHeader>
        <CardContent>
          {mode === 'debug' ? (
            <pre className="overflow-auto rounded-lg bg-bg-canvas p-3 text-xs">
              {JSON.stringify(debug || { message: '先点「开始检索」，这里会出现技术细节' }, null, 2)}
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
              description="结果只来自当前知识库，不会串到其他库。"
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
            第 {hit.page} 页 · {String(hit.business_metadata.content_type || '正文')}
          </small>
          <p className="mt-1 line-clamp-3 text-sm text-text-secondary">
            {hit.text.replace(/<[^>]+>/g, ' ').slice(0, 280)}
          </p>
        </span>
        <Badge variant="secondary">相关度 {score.toFixed(3)}</Badge>
      </button>
      {open && (
        <pre className="border-t border-border-button bg-bg-canvas p-3 text-xs">
          {JSON.stringify({ business_metadata: hit.business_metadata, source_trace: hit.source_trace, route_ranks: hit.route_ranks }, null, 2)}
        </pre>
      )}
    </article>
  )
}
