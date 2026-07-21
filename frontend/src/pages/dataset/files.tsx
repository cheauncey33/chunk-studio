import { useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Plus, FileText, Pencil, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { api, type KnowledgeBaseFile } from '@/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Explain } from '@/components/explain'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { Badge } from '@/components/ui/input'
import { queryKeys, useKbFiles } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

const PARSE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '解析中',
  done: '已解析',
  failed: '失败',
}

export default function DatasetFilesPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: files = [], isLoading } = useKbFiles(id)
  const [query, setQuery] = useState('')
  const [uploading, setUploading] = useState(false)
  const [reparsingId, setReparsingId] = useState<string | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(
    () => files.filter(file => file.name.toLowerCase().includes(query.toLowerCase())),
    [files, query],
  )

  const upload = async (file?: File) => {
    if (!file || !id) return
    setUploading(true)
    try {
      await api.uploadFile(file, {}, id)
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

  const reparse = async (file: KnowledgeBaseFile) => {
    setReparsingId(file.id)
    try {
      await api.parseFile(file.id)
      await client.invalidateQueries({ queryKey: queryKeys.kbFiles(id) })
      toast.success('已重新提交解析')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setReparsingId(null)
    }
  }

  return (
    <Card className="min-h-full border-0 bg-transparent shadow-none">
      <CardHeader className="px-0 pt-2">
        <ListFilterBar
          title="文件"
          titleHelp={helpText.files.page}
          description="上传 PDF 后系统会自动解析；点文件名或「编辑内容」进入对照原文的工作台。"
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder="搜索文件"
          searchHelp={helpText.files.search}
          rightPanel={
            <>
              <input
                ref={uploadRef}
                hidden
                type="file"
                accept="application/pdf"
                onChange={e => upload(e.target.files?.[0])}
              />
              <Explain text={helpText.files.add} title="添加文件">
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
              </Explain>
            </>
          }
        />
      </CardHeader>
      <CardContent className="px-0">
        <div className="overflow-hidden rounded-xl border border-border-button bg-bg-base">
          <div className="grid grid-cols-[minmax(0,1.6fr)_64px_100px_110px_90px_140px_140px] gap-3 border-b border-border-button px-4 py-3 text-xs font-medium text-text-secondary">
            <span>文件名</span>
            <span>页数</span>
            <Explain text="总段数 / 已批准段数。只有已批准的内容才会进入检索。" title="内容就绪">
              <span>内容就绪</span>
            </Explain>
            <Explain text="PDF 全文解析进度。试运行审查需要「已解析」。" title="解析状态">
              <span>解析状态</span>
            </Explain>
            <Explain text={helpText.files.role} title="用途"><span>用途</span></Explain>
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
          {filtered.map(file => {
            const parseStatus = file.parse_status || null
            const parseLabel = parseStatus ? (PARSE_LABEL[parseStatus] || parseStatus) : '未解析'
            const parseVariant = parseStatus === 'done'
              ? 'success'
              : parseStatus === 'failed'
                ? 'error'
                : parseStatus === 'running' || parseStatus === 'queued'
                  ? 'warning'
                  : 'secondary'
            return (
              <div
                key={file.id}
                className="group grid grid-cols-[minmax(0,1.6fr)_64px_100px_110px_90px_140px_140px] items-center gap-3 border-b border-border-button px-4 py-3 text-sm last:border-b-0"
              >
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-2 text-left hover:text-accent-primary"
                  onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-md bg-bg-accent text-accent-primary">
                    <FileText className="size-4" />
                  </span>
                  <span className="min-w-0">
                    <strong className="block truncate">{file.name}</strong>
                    {file.parse_error && (
                      <span className="block truncate text-xs text-state-error" title={file.parse_error}>
                        {file.parse_error}
                      </span>
                    )}
                  </span>
                </button>
                <span>{file.page_count || '—'}</span>
                <span className="tabular-nums text-text-secondary">
                  {file.chunk_count}
                  <span className="text-text-secondary/70"> / </span>
                  <span className={cn((file.approved_count || 0) > 0 && 'text-state-success')}>
                    {file.approved_count ?? 0} 已批准
                  </span>
                </span>
                <Badge variant={parseVariant}>{parseLabel}</Badge>
                <span>
                  <Badge variant="secondary">{file.role === 'source' ? '业务资料' : '参考资料'}</Badge>
                </span>
                <span className="text-text-secondary">{formatDate(file.created_at)}</span>
                <div className="flex justify-end gap-1 opacity-0 transition group-hover:opacity-100">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={reparsingId === file.id}
                    onClick={() => reparse(file)}
                    title="重新解析"
                  >
                    <RefreshCw className={cn(reparsingId === file.id && 'animate-spin')} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
                  >
                    <Pencil />
                    编辑
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
        <p className={cn('mt-3 text-xs text-text-secondary')}>
          共 {filtered.length} 个文件。未批准的内容段不会进入检索。
        </p>
      </CardContent>
    </Card>
  )
}
