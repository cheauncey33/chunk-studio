import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Layers } from 'lucide-react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { useKbChunks } from '@/hooks/use-knowledge-request'

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
          title="切片预览"
          description="只读浏览知识库内切片；需要编辑时进入切片工作台。"
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder="按文件名或切片内容筛选"
        />
      </CardHeader>
      <CardContent className="space-y-3 px-0">
        {isLoading && <div className="py-10 text-center text-sm text-text-secondary">加载中…</div>}
        {!isLoading && !filtered.length && (
          <EmptyState
            icon={<Layers />}
            title="暂无切片"
            description="上传并解析文件后，切片会出现在这里。"
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
                <strong className="text-sm">{chunk.file_name} · p.{chunk.page}</strong>
                <Badge variant="secondary">{String(chunk.business_metadata.content_type || 'text')}</Badge>
                <Badge variant="secondary">{chunk.status}</Badge>
              </div>
              <p className="mt-2 text-sm text-text-secondary">
                {chunk.text.replace(/<[^>]+>/g, ' ').slice(0, 260) || '暂无文本'}
              </p>
            </div>
            <Button asChild variant="outline" size="sm">
              <Link to={`/chunk/${chunk.file_id}?kb=${id}&chunk=${chunk.id}&page=${chunk.page}`}>
                在编辑器中打开
              </Link>
            </Button>
          </article>
        ))}
      </CardContent>
    </Card>
  )
}
