import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Layers } from 'lucide-react'
import { Explain } from '@/components/explain'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { useKbChunks } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  reviewed: '已复核',
  approved: '已批准',
  rejected: '已驳回',
}

export default function DatasetChunksPage() {
  const { id = '' } = useParams()
  const { data: chunks = [], isLoading } = useKbChunks(id, 200)
  const [query, setQuery] = useState('')

  const filtered = useMemo(
    () => chunks.filter(chunk => `${chunk.file_name} ${chunk.text}`.toLowerCase().includes(query.toLowerCase())),
    [chunks, query],
  )

  return (
    <Card className="min-h-full border-0 bg-transparent shadow-none">
      <CardHeader className="px-0 pt-2">
        <ListFilterBar
          title="内容片段"
          titleHelp={helpText.chunksPreview.page}
          description="只读浏览本库里已切好的段落。要改字或改信息，请点「在编辑器中打开」。"
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder="按文件名或内容筛选"
          searchHelp="按文件名或段落文字快速找到某一段。"
        />
      </CardHeader>
      <CardContent className="space-y-3 px-0">
        {isLoading && <div className="py-10 text-center text-sm text-text-secondary">加载中…</div>}
        {!isLoading && !filtered.length && (
          <EmptyState
            icon={<Layers />}
            title="暂无内容片段"
            description="上传并解析文件后，内容会出现在这里。"
          />
        )}
        {filtered.map(chunk => (
          <article
            key={chunk.id}
            className="flex items-start gap-3 rounded-xl border border-border-button bg-bg-base p-4"
          >
            <span
              className={`mt-1 size-2.5 shrink-0 rounded-full ${
                chunk.status === 'approved'
                  ? 'bg-state-success'
                  : chunk.status === 'rejected'
                    ? 'bg-state-error'
                    : 'bg-state-warning'
              }`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <strong className="text-sm">{chunk.file_name} · 第 {chunk.page} 页</strong>
                <Badge variant="secondary">{String(chunk.business_metadata.content_type || '正文')}</Badge>
                <Badge variant="secondary">{STATUS_LABEL[chunk.status] || chunk.status}</Badge>
              </div>
              <p className="mt-2 text-sm text-text-secondary">
                {chunk.text.replace(/<[^>]+>/g, ' ').slice(0, 260) || '暂无文本'}
              </p>
            </div>
            <Explain text={helpText.chunksPreview.openEditor} title="在编辑器中打开">
              <Button asChild variant="outline" size="sm">
                <Link to={`/chunk/${chunk.file_id}?kb=${id}&chunk=${chunk.id}&page=${chunk.page}`}>
                  在编辑器中打开
                </Link>
              </Button>
            </Explain>
          </article>
        ))}
      </CardContent>
    </Card>
  )
}
