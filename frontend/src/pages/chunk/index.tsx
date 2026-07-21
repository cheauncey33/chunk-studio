import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  MoreHorizontal,
  Save,
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
  const [chunkViewKind, setChunkViewKind] = useState<ChunkKind>('manual')
  const [autoOcr, setAutoOcr] = useState(false)
  const [bulkQueuing, setBulkQueuing] = useState(false)
  const [leftW, setLeftW] = useState(240)
  const [rightW, setRightW] = useState(420)
  const [view, setView] = useState<'preview' | 'list' | 'edit'>('preview')
  const [deleteChunkId, setDeleteChunkId] = useState<string | null>(null)

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
  const selectedChunk = useMemo(
    () => chunks.find(item => item.id === selectedId) || null,
    [chunks, selectedId],
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

  const onChunkCreated = useCallback(async (chunk: Chunk) => {
    await refreshChunks()
    setSelectedId(chunk.id)
    setPage(chunk.page)
    setView('edit')
  }, [refreshChunks])

  const onChunkSaved = useCallback(async (chunk: Chunk) => {
    await refreshChunks()
    setSelectedId(chunk.id)
    toast.success('切片已保存')
  }, [refreshChunks])

  const requestChunkDelete = (id: string) => setDeleteChunkId(id)

  const confirmDelete = async () => {
    if (!deleteChunkId) return
    try {
      await api.deleteChunk(deleteChunkId)
      if (selectedId === deleteChunkId) setSelectedId(null)
      setDeleteChunkId(null)
      await refreshChunks()
      toast.success('切片已删除')
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

  const startResize = (side: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startLeft = leftW
    const startRight = rightW
    const onMove = (ev: MouseEvent) => {
      if (side === 'left') setLeftW(Math.max(180, Math.min(360, startLeft + ev.clientX - startX)))
      else setRightW(Math.max(320, Math.min(640, startRight - (ev.clientX - startX))))
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

        <div className="inline-flex rounded-md bg-bg-card p-1">
          {([
            ['preview', '看原文', helpText.chunkStudio.viewPreview],
            ['list', '看列表', helpText.chunkStudio.viewList],
            ['edit', '编辑', helpText.chunkStudio.viewEdit],
          ] as const).map(([key, label, help]) => (
            <Explain key={key} text={help} title={label}>
              <button
                type="button"
                className={cn(
                  'rounded px-3 py-1.5 text-sm transition',
                  view === key ? 'bg-bg-base text-text-primary shadow-sm' : 'text-text-secondary',
                )}
                onClick={() => setView(key)}
              >
                {label}
              </button>
            </Explain>
          ))}
        </div>

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
          <Explain text={helpText.chunkStudio.viewEdit} title="编辑选中">
            <Button size="sm" disabled={!selectedChunk} onClick={() => setView('edit')}>
              <Save />
              编辑选中
            </Button>
          </Explain>
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
        <div className="flex min-h-0 flex-1">
          <aside className="flex shrink-0 flex-col border-r border-border-button bg-bg-base p-4" style={{ width: leftW }}>
            <h2 className="truncate text-sm font-semibold" title={currentFile.name}>{currentFile.name}</h2>
            <p className="mt-1 text-xs text-text-secondary">
              {currentFile.page_count} 页 · {chunks.length} 段内容
            </p>
            <div className="mt-4 flex items-center gap-2">
              <Explain text={helpText.chunkStudio.pageNav} title="翻页">
                <Button variant="outline" size="icon" onClick={goPrev} disabled={page <= 1}>
                  <ChevronLeft />
                </Button>
              </Explain>
              <div className="flex flex-1 items-center justify-center gap-1 text-sm">
                <input
                  className="w-12 rounded border border-border-button bg-bg-input px-1 py-1 text-center"
                  value={pageDraft}
                  onChange={e => setPageDraft(e.target.value.replace(/[^\d]/g, ''))}
                  onBlur={commitPageDraft}
                  onKeyDown={e => {
                    if (e.key === 'Enter') e.currentTarget.blur()
                  }}
                />
                <span className="text-text-secondary">/ {currentFile.page_count}</span>
              </div>
              <Button variant="outline" size="icon" onClick={goNext} disabled={page >= currentFile.page_count}>
                <ChevronRight />
              </Button>
            </div>
            <Explain text={helpText.chunkStudio.autoOcr} title="新建后自动识别" className="mt-4">
              <label className="flex items-center gap-2 text-xs text-text-secondary">
                <input type="checkbox" checked={autoOcr} onChange={e => setAutoOcr(e.target.checked)} />
                新建后自动识别文字
              </label>
            </Explain>
            <Explain text={helpText.chunkStudio.drawHint} title="如何新建" className="mt-auto">
              <p className="text-xs text-text-secondary">
                在 PDF 上按住拖出方框即可新建；方向键可翻页。
              </p>
            </Explain>
          </aside>

          <div className="w-1 cursor-col-resize bg-transparent hover:bg-accent-primary/30" onMouseDown={startResize('left')} />

          <main className={cn('min-w-0 flex-1 bg-bg-canvas p-3', view === 'list' && 'hidden')}>
            <div className="legacy-surface chunk-studio-viewer h-full overflow-hidden rounded-xl border border-border-button bg-bg-base">
              <PageViewer
                fileId={currentFile.id}
                page={page}
                chunks={chunks}
                selectedChunkId={selectedId}
                visibleKind={chunkViewKind}
                autoParseOnCreate={autoOcr}
                onChunkCreated={onChunkCreated}
                onSelectChunk={(id) => {
                  setSelectedId(id)
                  if (id) setView('edit')
                }}
                onDeleteChunk={requestChunkDelete}
              />
            </div>
          </main>

          <div className="w-1 cursor-col-resize bg-transparent hover:bg-accent-primary/30" onMouseDown={startResize('right')} />

          <aside
            className={cn(
              'flex shrink-0 flex-col border-l border-border-button bg-bg-base',
              view === 'preview' && 'hidden xl:flex',
            )}
            style={{ width: view === 'list' ? '100%' : rightW }}
          >
            <Tabs defaultValue="list" className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-border-button px-3 pt-3">
                <TabsList>
                  <TabsTrigger value="list">内容列表</TabsTrigger>
                  <TabsTrigger value="edit">编辑</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="list" className="mt-0 min-h-0 flex-1 overflow-hidden p-0">
                <ScrollArea className="h-full">
                  <div className="chunk-studio-list p-3">
                    <ChunkList
                      chunks={chunks}
                      selectedId={selectedId}
                      activeKind={chunkViewKind}
                      onKindChange={setChunkViewKind}
                      onSelect={(id) => {
                        const chunk = chunks.find(item => item.id === id)
                        if (chunk) setPage(chunk.page)
                        setSelectedId(id)
                        setView('edit')
                      }}
                      onDelete={requestChunkDelete}
                    />
                  </div>
                </ScrollArea>
              </TabsContent>
              <TabsContent value="edit" className="mt-0 min-h-0 flex-1 overflow-auto p-3">
                <div className="chunk-studio-editor h-full min-h-0 p-3">
                  <ChunkEditor
                    chunk={selectedChunk}
                    fields={fields}
                    onSaved={onChunkSaved}
                    onDelete={requestChunkDelete}
                    onQueued={refreshChunks}
                  />
                </div>
              </TabsContent>
            </Tabs>
          </aside>
        </div>
      )}

      {deleteChunkId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl border border-border-button bg-bg-base p-5 shadow-lg">
            <h3 className="text-base font-semibold">删除这段内容？</h3>
            <p className="mt-2 text-sm text-text-secondary">删除后不能恢复，请确认。</p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDeleteChunkId(null)}>取消</Button>
              <Button variant="destructive" onClick={confirmDelete}>删除</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
