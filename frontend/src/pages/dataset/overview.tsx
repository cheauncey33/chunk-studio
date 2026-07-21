import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Check, X } from 'lucide-react'
import { Explain } from '@/components/explain'
import { useKbFiles, useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

export default function DatasetOverviewPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const { data: files = [], isLoading } = useKbFiles(id)

  const stats = useMemo(() => {
    const fileCount = files.length
    const chunkCount = files.reduce((sum, file) => sum + (file.chunk_count || 0), 0)
    const approvedCount = files.reduce((sum, file) => sum + (file.approved_count || 0), 0)
    const parseReadyCount = files.filter(file => file.parse_ready).length
    const enabledCount = files.filter(file => file.enabled).length
    return { fileCount, chunkCount, approvedCount, parseReadyCount, enabledCount }
  }, [files])

  const checklist = [
    { done: stats.fileCount > 0, label: '已上传文件', hint: '还没有任何文件，先到「文件」页上传 PDF。' },
    {
      done: stats.fileCount > 0 && stats.parseReadyCount === stats.fileCount,
      label: '文件已全部解析完成',
      hint: `${stats.parseReadyCount} / ${stats.fileCount} 个文件已解析。`,
    },
    { done: stats.approvedCount > 0, label: '有已批准的内容片段', hint: '只有已批准的片段才会进入检索，去切片工作台审核。' },
    { done: stats.enabledCount > 0, label: '至少一个文件已启用参与检索', hint: '停用的文件不会出现在检索结果里。' },
  ]

  return (
    <div className="max-w-3xl space-y-6 pt-2">
      <div>
        <Explain text={helpText.kbNav.overview} title="概览">
          <h1 className="text-lg font-semibold">概览</h1>
        </Explain>
        <p className="mt-1 text-sm text-text-secondary">
          {isLoading ? '加载中…' : `${knowledgeBase?.name || '知识库'} 的当前状态一览`}
        </p>
      </div>

      <section className="rounded-xl border border-border-button bg-bg-base">
        <dl className="divide-y divide-border-button">
          <Row label="文件数" value={stats.fileCount} />
          <Row label="内容片段" value={stats.chunkCount} />
          <Row label="解析就绪" value={`${stats.parseReadyCount} / ${stats.fileCount}`} />
          <Row label="已批准片段" value={stats.approvedCount} />
          <Row label="最近更新" value={knowledgeBase ? formatDate(knowledgeBase.updated_at) : '—'} />
        </dl>
      </section>

      <section className="rounded-xl border border-border-button bg-bg-base">
        <header className="border-b border-border-button px-5 py-3">
          <h2 className="text-sm font-semibold">准备情况</h2>
        </header>
        <ul className="divide-y divide-border-button">
          {checklist.map(item => (
            <li key={item.label} className="flex items-start gap-3 px-5 py-3">
              <span
                className={cn(
                  'mt-0.5 grid size-5 shrink-0 place-items-center rounded-full',
                  item.done ? 'bg-state-success/15 text-state-success' : 'bg-bg-card text-text-secondary',
                )}
              >
                {item.done ? <Check className="size-3.5" /> : <X className="size-3.5" />}
              </span>
              <div>
                <div className="text-sm text-text-primary">{item.label}</div>
                <div className="text-xs text-text-secondary">{item.hint}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between px-5 py-3 text-sm">
      <dt className="text-text-secondary">{label}</dt>
      <dd className="font-medium tabular-nums text-text-primary">{value}</dd>
    </div>
  )
}
