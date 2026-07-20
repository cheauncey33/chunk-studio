import { useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Plus, FileText, Pencil } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { Badge } from '@/components/ui/input'
import { queryKeys, useKbFiles, useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { cn, formatDate } from '@/lib/utils'

export default function DatasetFilesPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: kb } = useKnowledgeBase(id)
  const { data: files = [], isLoading } = useKbFiles(id)
  const [query, setQuery] = useState('')
  const [uploading, setUploading] = useState(false)
  const uploadRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(
    () => files.filter(file => file.name.toLowerCase().includes(query.toLowerCase())),
    [files, query],
  )

  const upload = async (file?: File) => {
    if (!file || !id) return
    setUploading(true)
    try {
      const created = await api.uploadFile(file)
      await api.addFileToKnowledgeBase(id, created.id)
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.kbFiles(id) }),
        client.invalidateQueries({ queryKey: queryKeys.knowledgeBases }),
      ])
      toast.success('文件已上传，正在解析')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setUploading(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  return (
    <Card className="min-h-full border-0 bg-transparent shadow-none">
      <CardHeader className="px-0 pt-2">
        <ListFilterBar
          title={kb?.name || '文件'}
          description="上传 PDF 后自动进入解析队列；点击文件进入切片工作台。"
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder="搜索文件"
          rightPanel={
            <>
              <input
                ref={uploadRef}
                hidden
                type="file"
                accept="application/pdf"
                onChange={e => upload(e.target.files?.[0])}
              />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button disabled={uploading}>
                    <Plus />
                    {uploading ? '上传中…' : '添加文件'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => uploadRef.current?.click()}>
                    上传 PDF
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          }
        />
      </CardHeader>
      <CardContent className="px-0">
        <div className="overflow-hidden rounded-xl border border-border-button bg-bg-base">
          <div className="grid grid-cols-[minmax(0,2fr)_80px_80px_100px_160px_120px] gap-3 border-b border-border-button px-4 py-3 text-xs font-medium text-text-secondary">
            <span>文件名</span>
            <span>页数</span>
            <span>切片</span>
            <span>角色</span>
            <span>上传时间</span>
            <span className="text-right">操作</span>
          </div>
          {isLoading && <div className="px-4 py-10 text-center text-sm text-text-secondary">加载中…</div>}
          {!isLoading && !filtered.length && (
            <EmptyState
              icon={<FileText />}
              title="暂无文件"
              description="上传 PDF 后会出现在这里。"
              actionLabel="上传 PDF"
              onAction={() => uploadRef.current?.click()}
            />
          )}
          {filtered.map(file => (
            <div
              key={file.id}
              className="group grid grid-cols-[minmax(0,2fr)_80px_80px_100px_160px_120px] items-center gap-3 border-b border-border-button px-4 py-3 text-sm last:border-b-0"
            >
              <button
                type="button"
                className="flex min-w-0 items-center gap-2 text-left hover:text-accent-primary"
                onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-md bg-bg-accent text-accent-primary">
                  <FileText className="size-4" />
                </span>
                <strong className="truncate">{file.name}</strong>
              </button>
              <span>{file.page_count || '—'}</span>
              <span>{file.chunk_count}</span>
              <span>
                <Badge variant="secondary">{file.role === 'source' ? '业务资料' : '参考资料'}</Badge>
              </span>
              <span className="text-text-secondary">{formatDate(file.created_at)}</span>
              <div className="flex justify-end opacity-0 transition group-hover:opacity-100">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
                >
                  <Pencil />
                  编辑切片
                </Button>
              </div>
            </div>
          ))}
        </div>
        <p className={cn('mt-3 text-xs text-text-secondary')}>
          共 {filtered.length} 个文件
        </p>
      </CardContent>
    </Card>
  )
}
