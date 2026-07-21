import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  FileText,
  Pencil,
  Play,
  RefreshCw,
  MoreHorizontal,
  Sparkles,
  Layers,
  Scissors,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type KnowledgeBaseFile } from '@/api'
import {
  ChunkRuleFields,
  DEFAULT_CHUNK_RULES,
  normalizeChunkRules,
  type ChunkRuleConfig,
} from '@/components/chunk-rule-fields'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ListFilterBar } from '@/components/list-filter-bar'
import { EmptyState } from '@/components/empty-state'
import { Badge } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { queryKeys, useKbFiles, useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn, formatDate } from '@/lib/utils'

const PARSE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '解析中',
  done: '已解析',
  failed: '失败',
}

const COLS =
  'grid-cols-[36px_minmax(0,1.5fr)_72px_100px_100px_64px_120px_148px]'

export default function DatasetFilesPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const { data: files = [], isLoading } = useKbFiles(id)
  const [query, setQuery] = useState('')
  const [uploading, setUploading] = useState(false)
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [chunkRules, setChunkRules] = useState<ChunkRuleConfig>(DEFAULT_CHUNK_RULES)
  const [overrideChunk, setOverrideChunk] = useState(false)
  const [reparseTarget, setReparseTarget] = useState<KnowledgeBaseFile | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)

  const kbDefaults = useMemo(
    () => normalizeChunkRules(knowledgeBase?.parser_config || {}),
    [knowledgeBase],
  )

  useEffect(() => {
    if (!overrideChunk) setChunkRules(kbDefaults)
  }, [kbDefaults, overrideChunk])

  const filtered = useMemo(
    () => files.filter(file => file.name.toLowerCase().includes(query.toLowerCase())),
    [files, query],
  )

  const allFilteredSelected =
    filtered.length > 0 && filtered.every(file => selected.has(file.id))

  const markBusy = (ids: string[], on: boolean) => {
    setBusyIds(prev => {
      const next = new Set(prev)
      for (const fileId of ids) {
        if (on) next.add(fileId)
        else next.delete(fileId)
      }
      return next
    })
  }

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.kbFiles(id) }),
      client.invalidateQueries({ queryKey: queryKeys.knowledgeBases }),
      client.invalidateQueries({ queryKey: queryKeys.knowledgeBase(id) }),
    ])
  }

  const pickUpload = (file?: File) => {
    if (!file) return
    setPendingFile(file)
    setOverrideChunk(false)
    setChunkRules(kbDefaults)
    setUploadOpen(true)
    if (uploadRef.current) uploadRef.current.value = ''
  }

  const confirmUpload = async () => {
    if (!pendingFile || !id) return
    setUploading(true)
    try {
      const metadata = overrideChunk ? { chunk_config: chunkRules } : {}
      await api.uploadFile(pendingFile, metadata, id)
      await refresh()
      toast.success(
        chunkRules.auto_chunk_after_parse
          ? '已上传：解析完成后将按切片规则自动生成内容段'
          : '已上传：仅解析，不会自动切片',
      )
      setUploadOpen(false)
      setPendingFile(null)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const reparse = async (file: KnowledgeBaseFile, deleteChunks: boolean) => {
    markBusy([file.id], true)
    try {
      await api.parseFile(file.id, { delete_chunks: deleteChunks, force: true })
      await refresh()
      toast.success(
        deleteChunks
          ? `已删除旧切片并重新解析（完成后按规则重建）`
          : '已提交重新解析（保留现有切片，仅更新解析结果）',
      )
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      markBusy([file.id], false)
      setReparseTarget(null)
    }
  }

  const requestReparse = (file: KnowledgeBaseFile) => {
    if ((file.chunk_count || 0) > 0) {
      setReparseTarget(file)
      return
    }
    void reparse(file, false)
  }

  const generateChunks = async (file: KnowledgeBaseFile) => {
    markBusy([file.id], true)
    try {
      const summary = await api.autoChunkFile(file.id, { skip_existing: false })
      await refresh()
      toast.success(
        `已生成 ${summary.total} 段（章节 ${summary.sections} / 表 ${summary.tables} / 图 ${summary.images}）`,
      )
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      markBusy([file.id], false)
    }
  }

  const setEnabled = async (file: KnowledgeBaseFile, enabled: boolean) => {
    markBusy([file.id], true)
    try {
      await api.updateKnowledgeBaseFile(id, file.id, { enabled })
      await refresh()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      markBusy([file.id], false)
    }
  }

  const toggleSelect = (fileId: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (allFilteredSelected) {
      setSelected(prev => {
        const next = new Set(prev)
        for (const file of filtered) next.delete(file.id)
        return next
      })
      return
    }
    setSelected(prev => {
      const next = new Set(prev)
      for (const file of filtered) next.add(file.id)
      return next
    })
  }

  const bulk = async (action: 'parse' | 'enable' | 'disable' | 'chunk') => {
    const ids = [...selected]
    if (!ids.length) return
    if (action === 'parse') {
      const withChunks = filtered.filter(file => selected.has(file.id) && (file.chunk_count || 0) > 0)
      const totalChunks = withChunks.reduce((sum, file) => sum + (file.chunk_count || 0), 0)
      let deleteChunks = false
      if (totalChunks > 0) {
        const ok = window.confirm(
          `选中文件共有 ${totalChunks} 个切片。\n\n确定：删除这些切片后重新解析并重建\n取消：中止本次批量解析`,
        )
        if (!ok) return
        deleteChunks = true
      }
      setBulkBusy(true)
      markBusy(ids, true)
      try {
        await Promise.all(ids.map(fileId => api.parseFile(fileId, { delete_chunks: deleteChunks, force: true })))
        toast.success(
          deleteChunks
            ? `已删除旧切片并提交 ${ids.length} 个文件重新解析`
            : `已提交 ${ids.length} 个文件解析`,
        )
        setSelected(new Set())
        await refresh()
      } catch (err) {
        toast.error((err as Error).message)
      } finally {
        markBusy(ids, false)
        setBulkBusy(false)
      }
      return
    }
    setBulkBusy(true)
    markBusy(ids, true)
    try {
      if (action === 'chunk') {
        const results = await Promise.all(
          ids.map(fileId => api.autoChunkFile(fileId, { skip_existing: false })),
        )
        const total = results.reduce((sum, item) => sum + (item.total || 0), 0)
        toast.success(`已为 ${ids.length} 个文件生成共 ${total} 段`)
      } else {
        const enabled = action === 'enable'
        await Promise.all(
          ids.map(fileId => api.updateKnowledgeBaseFile(id, fileId, { enabled })),
        )
        toast.success(enabled ? `已启用 ${ids.length} 个文件` : `已停用 ${ids.length} 个文件`)
      }
      setSelected(new Set())
      await refresh()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      markBusy(ids, false)
      setBulkBusy(false)
    }
  }

  return (
    <div className="min-h-full pt-2">
      <ListFilterBar
        title="文件"
        titleHelp={helpText.files.page}
        description="上传后先解析 PDF，再按知识库默认切片规则生成内容段；上传时可临时改切片参数。"
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
              onChange={e => pickUpload(e.target.files?.[0])}
            />
            <Button disabled={uploading} onClick={() => uploadRef.current?.click()}>
              <Plus />
              {uploading ? '上传中…' : '添加文件'}
            </Button>
          </>
        }
      />

      <Dialog
        open={uploadOpen}
        onOpenChange={open => {
          if (uploading) return
          setUploadOpen(open)
          if (!open) setPendingFile(null)
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>上传并配置切片</DialogTitle>
            <DialogDescription>
              默认使用本知识库「配置 → 默认切片规则」。勾选下方可仅对本文件覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <p className="truncate text-sm">
              文件：<strong>{pendingFile?.name || '—'}</strong>
            </p>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={overrideChunk}
                onChange={e => {
                  setOverrideChunk(e.target.checked)
                  if (!e.target.checked) setChunkRules(kbDefaults)
                }}
              />
              覆盖本知识库默认切片规则
            </label>
            <ChunkRuleFields
              compact
              value={chunkRules}
              onChange={next => {
                setOverrideChunk(true)
                setChunkRules(next)
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={uploading} onClick={() => setUploadOpen(false)}>
              取消
            </Button>
            <Button disabled={uploading || !pendingFile} onClick={confirmUpload}>
              {uploading ? '上传中…' : '开始上传'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(reparseTarget)}
        onOpenChange={open => {
          if (!open) setReparseTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重新解析</DialogTitle>
            <DialogDescription>
              「{reparseTarget?.name}」当前有 {reparseTarget?.chunk_count || 0} 个切片。
              RAGFlow 同类流程会先确认是否删除原切片；删除后重新解析并按规则重建更干净。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={() => setReparseTarget(null)}>
              取消
            </Button>
            <Button
              variant="outline"
              disabled={!reparseTarget || busyIds.has(reparseTarget.id)}
              onClick={() => reparseTarget && reparse(reparseTarget, false)}
            >
              保留切片仅解析
            </Button>
            <Button
              disabled={!reparseTarget || busyIds.has(reparseTarget.id)}
              onClick={() => reparseTarget && reparse(reparseTarget, true)}
            >
              删除并重新解析
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {selected.size > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border-button bg-bg-base px-3 py-2 text-sm">
          <span className="text-text-secondary">已选 {selected.size} 项</span>
          <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => bulk('parse')}>
            <Play />
            批量解析
          </Button>
          <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => bulk('chunk')}>
            <Scissors />
            批量切片
          </Button>
          <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => bulk('enable')}>
            启用
          </Button>
          <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => bulk('disable')}>
            停用
          </Button>
          <Button size="sm" variant="ghost" disabled={bulkBusy} onClick={() => setSelected(new Set())}>
            取消选择
          </Button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-border-button bg-bg-base">
        <div
          className={cn(
            'grid gap-3 border-b border-border-button px-4 py-2.5 text-xs font-medium text-text-secondary',
            COLS,
          )}
        >
          <label className="flex items-center justify-center">
            <input
              type="checkbox"
              className="size-3.5 accent-[rgb(var(--accent-primary))]"
              checked={allFilteredSelected}
              onChange={toggleSelectAll}
              disabled={!filtered.length}
              aria-label="全选"
            />
          </label>
          <span>文件名</span>
          <span>页数</span>
          <span title="总段数 / 已批准">内容就绪</span>
          <span>解析</span>
          <span>启用</span>
          <span>上传时间</span>
          <span className="text-right">操作</span>
        </div>

        {isLoading && (
          <div className="px-4 py-10 text-center text-sm text-text-secondary">加载中…</div>
        )}
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
          const parseVariant =
            parseStatus === 'done'
              ? 'success'
              : parseStatus === 'failed'
                ? 'error'
                : parseStatus === 'running' || parseStatus === 'queued'
                  ? 'warning'
                  : 'secondary'
          const busy = busyIds.has(file.id)
          const needsParse = parseStatus !== 'done' && parseStatus !== 'running' && parseStatus !== 'queued'
          const needsChunk = parseStatus === 'done' && (file.chunk_count || 0) === 0

          return (
            <div
              key={file.id}
              className={cn(
                'group grid items-center gap-3 border-b border-border-button px-4 py-2.5 text-sm last:border-b-0',
                COLS,
                !file.enabled && 'opacity-60',
              )}
            >
              <label className="flex items-center justify-center">
                <input
                  type="checkbox"
                  className="size-3.5 accent-[rgb(var(--accent-primary))]"
                  checked={selected.has(file.id)}
                  onChange={() => toggleSelect(file.id)}
                  aria-label={`选择 ${file.name}`}
                />
              </label>

              <button
                type="button"
                className="flex min-w-0 items-center gap-2 text-left hover:text-accent-primary"
                onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-md bg-bg-accent text-accent-primary">
                  <FileText className="size-4" />
                </span>
                <span className="min-w-0">
                  <strong className="block truncate font-medium">{file.name}</strong>
                  {file.parse_error && (
                    <span className="block truncate text-xs text-state-error" title={file.parse_error}>
                      {file.parse_error}
                    </span>
                  )}
                  {needsChunk && (
                    <span className="block text-xs text-state-warning">已解析但未切片</span>
                  )}
                </span>
              </button>

              <span className="tabular-nums text-text-secondary">{file.page_count || '—'}</span>

              <span className="tabular-nums text-text-secondary">
                {file.chunk_count}
                <span className="text-text-secondary/70"> / </span>
                <span className={cn((file.approved_count || 0) > 0 && 'text-state-success')}>
                  {file.approved_count ?? 0}
                </span>
              </span>

              <Badge variant={parseVariant}>{parseLabel}</Badge>

              <Switch
                checked={file.enabled}
                disabled={busy}
                onCheckedChange={checked => setEnabled(file, checked)}
                aria-label={file.enabled ? '停用检索' : '启用检索'}
                title={file.enabled ? '参与检索' : '已停用'}
              />

              <span className="text-xs text-text-secondary">{formatDate(file.created_at)}</span>

              <div className="flex items-center justify-end gap-0.5">
                {needsChunk ? (
                  <Button
                    variant="default"
                    size="sm"
                    disabled={busy}
                    onClick={() => generateChunks(file)}
                    title="按库规则生成切片"
                  >
                    {busy ? <RefreshCw className="animate-spin" /> : <Scissors />}
                  </Button>
                ) : (
                  <Button
                    variant={needsParse ? 'default' : 'ghost'}
                    size="sm"
                    disabled={busy}
                    onClick={() => requestReparse(file)}
                    title={needsParse ? '开始解析' : '重新解析'}
                  >
                    {busy ? (
                      <RefreshCw className="animate-spin" />
                    ) : needsParse ? (
                      <Play />
                    ) : (
                      <RefreshCw />
                    )}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}
                  title="干预切片"
                >
                  <Pencil />
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" title="更多">
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => navigate(`/chunk/${file.id}?kb=${id}`)}>
                      <Pencil className="size-3.5" />
                      打开切片工作台
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={busy || parseStatus !== 'done'}
                      onClick={() => generateChunks(file)}
                    >
                      <Scissors className="size-3.5" />
                      按规则生成切片
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => navigate(`/kb/${id}/chunks`)}>
                      <Layers className="size-3.5" />
                      浏览库内片段
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => navigate(`/kb/${id}/metadata`)}>
                      <Sparkles className="size-3.5" />
                      AI 建议审核
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem disabled={busy} onClick={() => requestReparse(file)}>
                      <RefreshCw className="size-3.5" />
                      重新解析
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={busy}
                      onClick={() => setEnabled(file, !file.enabled)}
                    >
                      {file.enabled ? '停用检索' : '启用检索'}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          )
        })}
      </div>

      <p className="mt-3 text-xs text-text-secondary">
        共 {filtered.length} 个文件。停用或不批准的内容不会进入检索。
      </p>
    </div>
  )
}
