import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type Chunk, type CSFile } from '@/api'
import { PageViewer } from '@/PageViewer'
import { ChunkList, type ChunkKind } from '@/ChunkList'
import { ChunkEditor } from '@/ChunkEditor'
import { Explain, HelpModeBanner } from '@/components/explain'
import { useHelpMode } from '@/components/help-mode'
import { Button } from '@/components/ui/button'
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb'
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { queryKeys, useFields, useKnowledgeBase } from '@/hooks/use-knowledge-request'
import { helpText } from '@/lib/help-text'
import { cn } from '@/lib/utils'

export default function ChunkPage() {
  const { docId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const { enabled: helpEnabled, toggle: toggleHelp } = useHelpMode()
  const kbId = searchParams.get('kb') || ''
  const { data: kb } = useKnowledgeBase(kbId || undefined)
  const { data: fields = [] } = useFields()

  const pageFromUrl = Number(searchParams.get('page') || 1)
  const chunkFromUrl = searchParams.get('chunk')

  const [page, setPage] = useState(pageFromUrl > 0 ? pageFromUrl : 1)
  const [pageDraft, setPageDraft] = useState(String(page))
  const [selectedId, setSelectedId] = useState<string | null>(chunkFromUrl)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [chunkViewKind, setChunkViewKind] = useState<ChunkKind>('manual')
  const [autoOcr, setAutoOcr] = useState(false)
  const [bulkQueuing, setBulkQueuing] = useState(false)
  const [rightW, setRightW] = useState(420)
  const [rightOpen, setRightOpen] = useState(true)
  const [deleteChunkIds, setDeleteChunkIds] = useState<string[] | null>(null)

  const fileQuery = useQuery({
    queryKey: ['file', docId],
    queryFn: async () => {
      const files = await api.listFiles()
      const file = files.find(item => item.id === docId)
      if (!file) throw new Error('文件不存在')
      return file
    },
    enabled: Boolean(docId),
  })

  const chunksQuery = useQuery({
    queryKey: queryKeys.chunks(docId),
    queryFn: () => api.listChunks(docId),
    enabled: Boolean(docId),
  })

  const currentFile: CSFile | undefined = fileQuery.data
  const chunks = useMemo(() => chunksQuery.data || [], [chunksQuery.data])
  const editingChunk = useMemo(
    () => chunks.find(item => item.id === editingId) || null,
    [chunks, editingId],
  )

  useEffect(() => {
    api.getSettings().then(payload => {
      setAutoOcr(payload.settings['ocr.auto_on_create'] === 'true')
    }).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (chunkFromUrl) setSelectedId(chunkFromUrl)
  }, [chunkFromUrl])

  useEffect(() => {
    if (pageFromUrl > 0) {
      setPage(pageFromUrl)
      setPageDraft(String(pageFromUrl))
    }
  }, [pageFromUrl])

  useEffect(() => {
    setPageDraft(String(page))
    const next = new URLSearchParams(searchParams)
    next.set('page', String(page))
    if (selectedId) next.set('chunk', selectedId)
    else next.delete('chunk')
    if (kbId) next.set('kb', kbId)
    setSearchParams(next, { replace: true })
  }, [page, selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshChunks = useCallback(async () => {
    await client.invalidateQueries({ queryKey: queryKeys.chunks(docId) })
  }, [client, docId])

  const selectChunk = useCallback((id: string | null) => {
    if (!id) {
      setSelectedId(null)
      return
    }
    const chunk = chunks.find(item => item.id === id)
    if (chunk) setPage(chunk.page)
    setSelectedId(id)
  }, [chunks])

  const openChunkEditor = useCallback((id: string) => {
    selectChunk(id)
    setRightOpen(true)
    setEditingId(id)
  }, [selectChunk])

  const onChunkCreated = useCallback(async (chunk: Chunk) => {
    await refreshChunks()
    setSelectedId(chunk.id)
    setPage(chunk.page)
    setRightOpen(true)
    setEditingId(chunk.id)
  }, [refreshChunks])

  const onChunkSaved = useCallback(async (chunk: Chunk) => {
    await refreshChunks()
    setSelectedId(chunk.id)
    toast.success('切片已保存')
  }, [refreshChunks])

  const onChunkUpdated = useCallback(async (chunk: Chunk) => {
    await client.setQueryData(queryKeys.chunks(docId), (prev: Chunk[] | undefined) => {
      if (!prev) return prev
      return prev.map(item => (item.id === chunk.id ? chunk : item))
    })
    await refreshChunks()
  }, [client, docId, refreshChunks])

  const requestChunkDelete = (ids: string | string[]) => {
    setDeleteChunkIds(Array.isArray(ids) ? ids : [ids])
  }

  const confirmDelete = async () => {
    if (!deleteChunkIds?.length) return
    try {
      for (const id of deleteChunkIds) {
        await api.deleteChunk(id)
      }
      if (selectedId && deleteChunkIds.includes(selectedId)) setSelectedId(null)
      if (editingId && deleteChunkIds.includes(editingId)) setEditingId(null)
      const count = deleteChunkIds.length
      setDeleteChunkIds(null)
      await refreshChunks()
      toast.success(count > 1 ? `已删除 ${count} 段` : '切片已删除')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const enqueuePendingOcr = async () => {
    if (!currentFile) return
    setBulkQueuing(true)
    try {
      const result = await api.ocrBulk(currentFile.id, { pending_only: true })
      toast.success(`已提交 ${result.queued} 个 OCR 任务`)
      await refreshChunks()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setBulkQueuing(false)
    }
  }

  const goPrev = () => setPage(p => Math.max(1, p - 1))
  const goNext = () => setPage(p => Math.min(currentFile?.page_count || 1, p + 1))

  const commitPageDraft = () => {
    const max = currentFile?.page_count || 1
    const next = Math.max(1, Math.min(max, Number(pageDraft) || 1))
    setPage(next)
    setPageDraft(String(next))
  }

  const startResizeRight = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startRight = rightW
    const onMove = (ev: MouseEvent) => {
      setRightW(Math.max(320, Math.min(640, startRight - (ev.clientX - startX))))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    if (!currentFile) return
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) return
      const max = currentFile.page_count
      if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) {
        e.preventDefault()
        setPage(p => Math.max(1, p - 1))
      } else if (['ArrowRight', 'ArrowDown', 'PageDown'].includes(e.key)) {
        e.preventDefault()
        setPage(p => Math.min(max, p + 1))
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [currentFile])

  const backTo = kbId ? `/kb/${kbId}/files` : '/'

  return (
    <div className="flex h-screen flex-col bg-bg-canvas text-text-primary">
      <HelpModeBanner />
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border-button bg-bg-base px-4">
        <Explain text={helpText.chunkStudio.back} title="返回">
          <Button variant="ghost" size="icon" onClick={() => navigate(backTo)} title="返回">
            <ArrowLeft />
          </Button>
        </Explain>
        <Breadcrumb className="min-w-0 flex-1">
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link to="/knowledge-bases">知识库</Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            {kb && (
              <>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbLink asChild>
                    <Link to={`/kb/${kb.id}/files`}>{kb.name}</Link>
                  </BreadcrumbLink>
                </BreadcrumbItem>
              </>
            )}
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage className="truncate max-w-[280px]">
                {currentFile?.name || '内容工作台'}
              </BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>

        <div className="flex items-center gap-2">
          <Explain text={helpText.nav.helpMode} title="说明模式">
            <Button
              variant={helpEnabled ? 'default' : 'ghost'}
              size="icon"
              onClick={toggleHelp}
              title="说明模式"
            >
              <CircleHelp />
            </Button>
          </Explain>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon"><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setAutoOcr(v => !v)}>
                {autoOcr ? '关闭' : '开启'}新建后自动识别文字
              </DropdownMenuItem>
              <DropdownMenuItem disabled={bulkQueuing} onClick={enqueuePendingOcr}>
                {bulkQueuing ? '提交中…' : '识别待处理段落'}
              </DropdownMenuItem>
              {kbId && (
                <DropdownMenuItem onClick={() => navigate(`/kb/${kbId}/metadata`)}>
                  AI 建议审核
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {(fileQuery.isError || (!fileQuery.isLoading && !currentFile)) && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <p className="text-text-secondary">找不到该文件</p>
            <Button className="mt-3" variant="outline" onClick={() => navigate(backTo)}>返回</Button>
          </div>
        </div>
      )}

      {currentFile && (
        <div className="relative flex min-h-0 flex-1">
          <main className="relative min-w-0 flex-1 bg-bg-canvas p-3">
            <div className="legacy-surface chunk-studio-viewer relative h-full overflow-hidden rounded-xl border border-border-button bg-bg-base">
              <PageViewer
                fileId={currentFile.id}
                page={page}
                chunks={chunks}
                selectedChunkId={selectedId}
                visibleKind={chunkViewKind}
                autoParseOnCreate={autoOcr}
                onChunkCreated={onChunkCreated}
                onSelectChunk={selectChunk}
                onDeleteChunk={requestChunkDelete}
              />
              <div
                className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center"
                title={helpText.chunkStudio.pageNav}
              >
                <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-border-button bg-bg-base/95 px-2 py-1.5 shadow-md backdrop-blur-sm">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8 rounded-full"
                      onClick={goPrev}
                      disabled={page <= 1}
                      title="上一页"
                    >
                      <ChevronLeft />
                    </Button>
                    <div className="flex items-center gap-1 px-1 text-sm tabular-nums">
                      <input
                        className="h-7 w-10 rounded-md border border-border-button bg-bg-input px-1 text-center"
                        value={pageDraft}
                        aria-label="页码"
                        onChange={e => setPageDraft(e.target.value.replace(/[^\d]/g, ''))}
                        onBlur={commitPageDraft}
                        onKeyDown={e => {
                          if (e.key === 'Enter') e.currentTarget.blur()
                        }}
                      />
                      <span className="text-text-secondary">/</span>
                      <span className="min-w-6 text-text-secondary">{currentFile.page_count}</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8 rounded-full"
                      onClick={goNext}
                      disabled={page >= currentFile.page_count}
                      title="下一页"
                    >
                      <ChevronRight />
                    </Button>
                </div>
              </div>
            </div>
          </main>

          {rightOpen ? (
            <>
              <div
                className="w-1 cursor-col-resize bg-transparent hover:bg-accent-primary/30"
                onMouseDown={startResizeRight}
              />
              <aside
                className="flex shrink-0 flex-col border-l border-border-button bg-bg-base"
                style={{ width: rightW }}
              >
                <div className="flex items-start gap-2 border-b border-border-button px-3 py-3">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-sm font-semibold" title={currentFile.name}>
                      {currentFile.name}
                    </h2>
                    <p className="mt-0.5 text-xs text-text-secondary">
                      {currentFile.page_count} 页 · {chunks.length} 段内容
                    </p>
                  </div>
                  <div className="shrink-0">
                    <Explain text={helpText.chunkStudio.collapseRight} title="收起列表">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setRightOpen(false)}
                        title="收起列表"
                      >
                        <PanelRightClose />
                      </Button>
                    </Explain>
                  </div>
                </div>
                <div className="chunk-studio-list min-h-0 flex-1">
                  <ChunkList
                    chunks={chunks}
                    selectedId={selectedId}
                    activeKind={chunkViewKind}
                    onKindChange={setChunkViewKind}
                    onSelect={selectChunk}
                    onEdit={openChunkEditor}
                    onDelete={requestChunkDelete}
                    onChunkUpdated={chunk => void onChunkUpdated(chunk)}
                  />
                </div>
              </aside>
            </>
          ) : (
            <Explain text={helpText.chunkStudio.expandRight} title="展开列表">
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  'absolute right-0 top-3 z-10 h-9 w-9 rounded-r-none rounded-l-md',
                  'border border-r-0 border-border-button bg-bg-base shadow-sm',
                )}
                onClick={() => setRightOpen(true)}
                title="展开列表"
              >
                <PanelRightOpen />
              </Button>
            </Explain>
          )}
        </div>
      )}

      <Dialog open={Boolean(editingId)} onOpenChange={open => { if (!open) setEditingId(null) }}>
        <DialogContent
          className="flex h-[min(780px,90vh)] w-[min(920px,92vw)] max-w-none flex-col gap-0 overflow-hidden p-0"
          aria-describedby={undefined}
          showCloseButton={false}
        >
          <DialogTitle className="sr-only">修改内容片段</DialogTitle>
          <div className="min-h-0 flex-1 overflow-hidden p-5 pt-5">
            {editingChunk ? (
              <ChunkEditor
                chunk={editingChunk}
                fields={fields}
                onSaved={async chunk => {
                  await onChunkSaved(chunk)
                }}
                onCancel={() => setEditingId(null)}
                onQueued={refreshChunks}
              />
            ) : (
              <p className="py-10 text-center text-sm text-text-secondary">该片段已不存在</p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {deleteChunkIds && deleteChunkIds.length > 0 && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl border border-border-button bg-bg-base p-5 shadow-lg">
            <h3 className="text-base font-semibold">
              {deleteChunkIds.length > 1 ? `删除选中的 ${deleteChunkIds.length} 段？` : '删除这段内容？'}
            </h3>
            <p className="mt-2 text-sm text-text-secondary">删除后不能恢复，请确认。</p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDeleteChunkIds(null)}>取消</Button>
              <Button variant="destructive" onClick={() => void confirmDelete()}>删除</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
