import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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
  Check,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type CorpusKind, type KnowledgeBaseFile } from '@/api'
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
  'grid-cols-[36px_minmax(0,1.4fr)_88px_72px_100px_100px_64px_120px_148px]'

const CORPUS_LABEL: Record<CorpusKind, string> = {
  standard: '标准文件',
  spec: '规范书',
}

function corpusKindOf(file: KnowledgeBaseFile): CorpusKind {
  return file.corpus_kind === 'spec' ? 'spec' : 'standard'
}

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
  const [uploadCorpusKind, setUploadCorpusKind] = useState<CorpusKind>('standard')
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
  const standardFiles = useMemo(
    () => filtered.filter(file => corpusKindOf(file) === 'standard'),
    [filtered],
  )
  const specFiles = useMemo(
    () => filtered.filter(file => corpusKindOf(file) === 'spec'),
    [filtered],
  )

  const stats = useMemo(() => {
    const fileCount = files.length
    const chunkCount = files.reduce((sum, file) => sum + (file.chunk_count || 0), 0)
    const approvedCount = files.reduce((sum, file) => sum + (file.approved_count || 0), 0)
    const parseReadyCount = files.filter(file => file.parse_ready).length
    const enabledCount = files.filter(file => file.enabled).length
    return { fileCount, chunkCount, approvedCount, parseReadyCount, enabledCount }
  }, [files])

  const checklist = [
    {
      done: stats.fileCount > 0,
      label: '已上传文件',
      hint: '还没有任何文件，先上传 PDF。',
    },
    {
      done: stats.fileCount > 0 && stats.parseReadyCount === stats.fileCount,
      label: '文件已全部解析完成',
      hint: `${stats.parseReadyCount} / ${stats.fileCount} 个文件已解析。`,
    },
    {
      done: stats.approvedCount > 0,
      label: '有已批准的内容片段',
      hint: '只有已批准的片段才会进入检索，去切片工作台审核。',
    },
    {
      done: stats.enabledCount > 0,
      label: '至少一个文件已启用参与检索',
      hint: '停用的文件不会出现在检索结果里。',
    },
  ]

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
    setUploadCorpusKind('standard')
    setOverrideChunk(false)
    setChunkRules(kbDefaults)
    setUploadOpen(true)
    if (uploadRef.current) uploadRef.current.value = ''
  }

  const confirmUpload = async () => {
    if (!pendingFile || !id) return
    setUploading(true)
    try {
      const metadata: Record<string, unknown> = {
        corpus_kind: uploadCorpusKind,
        doc_role: uploadCorpusKind,
        doc_type: uploadCorpusKind,
        ...(overrideChunk ? { chunk_config: chunkRules } : {}),
      }
      await api.uploadFile(pendingFile, metadata, id)
      await refresh()
      toast.success(
        chunkRules.auto_chunk_after_parse
          ? `已上传为${CORPUS_LABEL[uploadCorpusKind]}：解析完成后将按切片规则自动生成内容段`
          : `已上传为${CORPUS_LABEL[uploadCorpusKind]}：仅解析，不会自动切片`,
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

  const setCorpusKind = async (file: KnowledgeBaseFile, corpusKind: CorpusKind) => {
    if (corpusKindOf(file) === corpusKind) return
    markBusy([file.id], true)
    try {
      await api.updateKnowledgeBaseFile(id, file.id, { corpus_kind: corpusKind })
      await refresh()
      toast.success(`已改为${CORPUS_LABEL[corpusKind]}`)
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
        title="文件概览"
        titleHelp={helpText.files.page}
        description="本库只收标准与规范书语料。检测报告请在审查工作台上传；命名规则 PDF 在「配置」页管理。"
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

      <div className="mb-4 grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <section className="rounded-xl border border-border-button bg-bg-base">
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-border-button sm:grid-cols-4">
            <StatCell label="文件" value={stats.fileCount} />
            <StatCell label="内容片段" value={stats.chunkCount} />
            <StatCell
              label="解析就绪"
              value={`${stats.parseReadyCount}/${stats.fileCount || 0}`}
            />
            <StatCell label="已批准" value={stats.approvedCount} />
          </dl>
        </section>
        <section className="rounded-xl border border-border-button bg-bg-base px-4 py-3">
          <h2 className="mb-2 text-[13px] font-medium text-text-secondary">准备情况</h2>
          <ul className="space-y-1.5">
            {checklist.map(item => (
              <li key={item.label} className="flex items-start gap-2">
                <span
                  className={cn(
                    'mt-0.5 grid size-4 shrink-0 place-items-center rounded-full',
                    item.done
                      ? 'bg-state-success/15 text-state-success'
                      : 'bg-bg-card text-text-secondary',
                  )}
                >
                  {item.done ? <Check className="size-2.5" /> : <X className="size-2.5" />}
                </span>
                <div className="min-w-0">
                  <div className="text-[13px] leading-snug text-text-primary">{item.label}</div>
                  {!item.done && (
                    <div className="text-[12px] leading-snug text-text-secondary">{item.hint}</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

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
            <div className="space-y-2">
              <label className="text-sm font-medium">语料类型</label>
              <div className="flex flex-wrap gap-4 text-sm">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="upload-corpus-kind"
                    checked={uploadCorpusKind === 'standard'}
                    onChange={() => setUploadCorpusKind('standard')}
                  />
                  标准文件
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="upload-corpus-kind"
                    checked={uploadCorpusKind === 'spec'}
                    onChange={() => setUploadCorpusKind('spec')}
                  />
                  规范书
                </label>
              </div>
            </div>
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

      <div className="space-y-5">
        {isLoading && (
          <div className="rounded-xl border border-border-button bg-bg-base px-4 py-10 text-center text-sm text-text-secondary">
            加载中…
          </div>
        )}
        {!isLoading && !filtered.length && (
          <div className="overflow-hidden rounded-xl border border-border-button bg-bg-base">
            <EmptyState
              icon={<FileText />}
              title="暂无语料文件"
              description="上传标准或规范书 PDF。检测报告请到审查工作台；命名规则在「配置」。"
              actionLabel="上传 PDF"
              onAction={() => uploadRef.current?.click()}
            />
          </div>
        )}

        {!isLoading && filtered.length > 0 && (
          <>
            <CorpusSection
              title="标准文件"
              hint="国家标准、行业标准等审查证据"
              files={standardFiles}
              emptyText="暂无标准文件"
              allSelected={
                standardFiles.length > 0 && standardFiles.every(file => selected.has(file.id))
              }
              onToggleAll={() => {
                setSelected(prev => {
                  const next = new Set(prev)
                  const allOn = standardFiles.every(file => next.has(file.id))
                  for (const file of standardFiles) {
                    if (allOn) next.delete(file.id)
                    else next.add(file.id)
                  }
                  return next
                })
              }}
              renderRow={file => renderFileRow(file)}
            />
            <CorpusSection
              title="规范书"
              hint="技术规范书等（通常为定制规范）"
              files={specFiles}
              emptyText="暂无规范书"
              allSelected={specFiles.length > 0 && specFiles.every(file => selected.has(file.id))}
              onToggleAll={() => {
                setSelected(prev => {
                  const next = new Set(prev)
                  const allOn = specFiles.every(file => next.has(file.id))
                  for (const file of specFiles) {
                    if (allOn) next.delete(file.id)
                    else next.add(file.id)
                  }
                  return next
                })
              }}
              renderRow={file => renderFileRow(file)}
            />
          </>
        )}
      </div>

      <p className="mt-3 text-xs text-text-secondary">
        共 {filtered.length} 个语料文件（标准 {standardFiles.length} / 规范书 {specFiles.length}）。
        停用或不批准的内容不会进入检索。
      </p>
    </div>
  )

  function renderFileRow(file: KnowledgeBaseFile) {
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
    const kind = corpusKindOf(file)

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

        <select
          className="h-8 rounded-md border border-border-button bg-bg-base px-1.5 text-xs"
          value={kind}
          disabled={busy}
          onChange={e => setCorpusKind(file, e.target.value as CorpusKind)}
          aria-label="语料类型"
        >
          <option value="standard">标准</option>
          <option value="spec">规范书</option>
        </select>

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
              <DropdownMenuItem
                disabled={busy}
                onClick={() => setCorpusKind(file, kind === 'standard' ? 'spec' : 'standard')}
              >
                改为{kind === 'standard' ? '规范书' : '标准文件'}
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
              <DropdownMenuItem disabled={busy} onClick={() => setEnabled(file, !file.enabled)}>
                {file.enabled ? '停用检索' : '启用检索'}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    )
  }
}

function CorpusSection({
  title,
  hint,
  files,
  emptyText,
  allSelected,
  onToggleAll,
  renderRow,
}: {
  title: string
  hint: string
  files: KnowledgeBaseFile[]
  emptyText: string
  allSelected: boolean
  onToggleAll: () => void
  renderRow: (file: KnowledgeBaseFile) => ReactNode
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border-button bg-bg-base">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-button px-4 py-3">
        <div>
          <h2 className="text-[15px] font-semibold text-text-primary">{title}</h2>
          <p className="text-[12px] text-text-secondary">{hint}</p>
        </div>
        <span className="text-[12px] tabular-nums text-text-secondary">{files.length} 个</span>
      </header>
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
            checked={allSelected}
            onChange={onToggleAll}
            disabled={!files.length}
            aria-label={`全选${title}`}
          />
        </label>
        <span>文件名</span>
        <span>类型</span>
        <span>页数</span>
        <span title="总段数 / 已批准">内容就绪</span>
        <span>解析</span>
        <span>启用</span>
        <span>上传时间</span>
        <span className="text-right">操作</span>
      </div>
      {!files.length ? (
        <div className="px-4 py-8 text-center text-sm text-text-secondary">{emptyText}</div>
      ) : (
        files.map(file => renderRow(file))
      )}
    </section>
  )
}

function StatCell({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-bg-base px-4 py-3">
      <div className="text-[12px] text-text-secondary">{label}</div>
      <div className="mt-0.5 text-[17px] font-semibold tabular-nums text-text-primary">{value}</div>
    </div>
  )
}
