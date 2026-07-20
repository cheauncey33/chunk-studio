import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type CSFile, type Chunk, type FieldConfig } from './api'
import { PageViewer } from './PageViewer'
import { ChunkList, chunkKind, type ChunkKind } from './ChunkList'
import { ChunkEditor } from './ChunkEditor'
import { FieldConfigPanel } from './FieldConfigPanel'
import { SearchPage } from './SearchPage'
import { SettingsPage } from './SettingsPage'
import { AuditPage } from './AuditPage'
import { MetadataSuggestionsPage } from './MetadataSuggestionsPage'
import './App.css'

type Tab = 'studio' | 'search' | 'metadata' | 'audit' | 'fields' | 'settings'
type ReturnTab = 'search' | 'metadata'
type AutoChunkKind = 'table' | 'section' | 'image'
type CategoryTarget = 'upload' | 'file'
type UploadMeta = {
  standard_no: string
  doc_type: string
  knowledge_categories: string[]
  auto_chunk_types: AutoChunkKind[]
  applicable_date: string
  publish_date: string
  effective_date: string
  notes: string
}

const DEFAULT_KNOWLEDGE_CATEGORIES = ['油浸式变压器', '干式变压器', '配电箱', '开关柜']
const AUTO_CHUNK_OPTIONS: Array<{ key: AutoChunkKind; label: string }> = [
  { key: 'table', label: '表格切片' },
  { key: 'section', label: '章节切片' },
  { key: 'image', label: '图片切片' },
]

const DEFAULT_UPLOAD_META: UploadMeta = {
  standard_no: '',
  doc_type: '标准',
  knowledge_categories: [],
  auto_chunk_types: [],
  applicable_date: '',
  publish_date: '',
  effective_date: '',
  notes: '',
}

function stringMeta(meta: Record<string, unknown>, key: keyof UploadMeta, fallback = ''): string {
  const value = meta[key]
  return typeof value === 'string' ? value : fallback
}

function listMeta(meta: Record<string, unknown>, key: keyof UploadMeta): string[] {
  const value = meta[key]
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

function autoChunkTypesMeta(meta: Record<string, unknown>): AutoChunkKind[] {
  const allowed = new Set<AutoChunkKind>(['table', 'section', 'image'])
  return listMeta(meta, 'auto_chunk_types').filter((item): item is AutoChunkKind => (
    allowed.has(item as AutoChunkKind)
  ))
}

function fileToMetaDraft(file: CSFile): UploadMeta {
  return {
    standard_no: stringMeta(file.metadata, 'standard_no'),
    doc_type: stringMeta(file.metadata, 'doc_type', '标准'),
    knowledge_categories: listMeta(file.metadata, 'knowledge_categories'),
    auto_chunk_types: autoChunkTypesMeta(file.metadata),
    applicable_date: stringMeta(file.metadata, 'applicable_date'),
    publish_date: stringMeta(file.metadata, 'publish_date'),
    effective_date: stringMeta(file.metadata, 'effective_date'),
    notes: stringMeta(file.metadata, 'notes'),
  }
}

function App() {
  const [tab, setTab] = useState<Tab>('studio')
  const [files, setFiles] = useState<CSFile[]>([])
  const [currentFile, setCurrentFile] = useState<CSFile | null>(null)
  const [page, setPage] = useState(1)
  const [pageDraft, setPageDraft] = useState('1')
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [chunkViewKind, setChunkViewKind] = useState<ChunkKind>('manual')
  const [fields, setFields] = useState<FieldConfig[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadMeta, setUploadMeta] = useState<UploadMeta>(DEFAULT_UPLOAD_META)
  const [selectedKnowledgeCategory, setSelectedKnowledgeCategory] = useState('all')
  const [categoryModalOpen, setCategoryModalOpen] = useState(false)
  const [categoryTarget, setCategoryTarget] = useState<CategoryTarget>('upload')
  const [newCategoryName, setNewCategoryName] = useState('')
  const [editingFile, setEditingFile] = useState<CSFile | null>(null)
  const [fileMetaDraft, setFileMetaDraft] = useState<UploadMeta>(DEFAULT_UPLOAD_META)
  const [savingFileMeta, setSavingFileMeta] = useState(false)
  const [manualChunkCreating, setManualChunkCreating] = useState(false)
  const [pendingPulse, setPendingPulse] = useState(false)
  const [pendingModalOpen, setPendingModalOpen] = useState(false)
  const [deleteChunkId, setDeleteChunkId] = useState<string | null>(null)
  const [knowledgeCategories, setKnowledgeCategories] = useState<string[]>(() => {
    try {
      const saved = window.localStorage.getItem('chunkstudio.knowledgeCategories')
      const list = saved ? JSON.parse(saved) : null
      if (Array.isArray(list)) {
        return Array.from(new Set([...DEFAULT_KNOWLEDGE_CATEGORIES, ...list.map(String).filter(Boolean)]))
      }
    } catch {
      // Ignore malformed localStorage and fall back to defaults.
    }
    return DEFAULT_KNOWLEDGE_CATEGORIES
  })
  const [autoOcr, setAutoOcr] = useState(false)
  const [bulkQueuing, setBulkQueuing] = useState(false)
  const [autoGenerating, setAutoGenerating] = useState(false)
  const [edge, setEdge] = useState<'left' | 'right' | null>(null)
  const [leftW, setLeftW] = useState(300)
  const [rightW, setRightW] = useState(480)
  const [rightListH, setRightListH] = useState(320)
  const [pendingLocation, setPendingLocation] = useState<{ fileId: string; chunkId: string; page: number } | null>(null)
  const [returnTab, setReturnTab] = useState<ReturnTab | null>(null)

  const startResize = (side: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startLeft = leftW
    const startRight = rightW
    const onMove = (ev: MouseEvent) => {
      if (side === 'left') {
        setLeftW(Math.max(180, Math.min(480, startLeft + ev.clientX - startX)))
      } else {
        setRightW(Math.max(360, Math.min(1400, startRight - (ev.clientX - startX))))
      }
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

  const startRightListResize = (e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startHeight = rightListH
    const onMove = (ev: MouseEvent) => {
      const maxHeight = Math.max(220, window.innerHeight - 260)
      setRightListH(Math.max(160, Math.min(maxHeight, startHeight + ev.clientY - startY)))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    if (tab !== 'studio' || !currentFile) return
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) return

      const max = currentFile.page_count
      if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) {
        e.preventDefault()
        setPage(p => Math.max(1, p - 1))
      } else if (['ArrowRight', 'ArrowDown', 'PageDown'].includes(e.key)) {
        e.preventDefault()
        setPage(p => Math.min(max, p + 1))
      } else if (e.key === 'Home') {
        e.preventDefault()
        setPage(1)
      } else if (e.key === 'End') {
        e.preventDefault()
        setPage(max)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [tab, currentFile])

  const refreshFiles = useCallback(async () => {
    const fs = await api.listFiles()
    setFiles(fs)
    if (!currentFile && fs.length) setCurrentFile(fs[0])
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshFields = useCallback(async () => {
    setFields(await api.listFields())
  }, [])

  const refreshSettings = useCallback(async () => {
    const settings = await api.getSettings()
    setAutoOcr(settings['ocr.auto_on_create'] === 'true')
  }, [])

  const refreshChunks = useCallback(async () => {
    if (!currentFile) {
      setChunks([])
      return
    }
    setChunks(await api.listChunks(currentFile.id))
  }, [currentFile])

  useEffect(() => {
    refreshFiles()
    refreshFields()
    refreshSettings()
  }, [refreshFiles, refreshFields, refreshSettings])

  useEffect(() => {
    window.localStorage.setItem('chunkstudio.knowledgeCategories', JSON.stringify(knowledgeCategories))
  }, [knowledgeCategories])

  const allKnowledgeCategories = useMemo(() => {
    const fromFiles = files.flatMap(file => listMeta(file.metadata, 'knowledge_categories'))
    return Array.from(new Set([...knowledgeCategories, ...fromFiles])).filter(Boolean)
  }, [files, knowledgeCategories])

  const filteredFiles = useMemo(() => {
    if (selectedKnowledgeCategory === 'all') return files
    return files.filter(file => listMeta(file.metadata, 'knowledge_categories').includes(selectedKnowledgeCategory))
  }, [files, selectedKnowledgeCategory])

  useEffect(() => {
    if (!filteredFiles.length) {
      if (currentFile) setCurrentFile(null)
      return
    }
    if (!currentFile || !filteredFiles.some(file => file.id === currentFile.id)) {
      setCurrentFile(filteredFiles[0])
    }
  }, [currentFile, filteredFiles])

  useEffect(() => { refreshChunks() }, [refreshChunks])

  useEffect(() => {
    setPage(1)
    setPageDraft('1')
    setSelectedId(null)
  }, [currentFile?.id])

  useEffect(() => {
    setPageDraft(String(page))
  }, [page])

  useEffect(() => {
    if (!pendingLocation || currentFile?.id !== pendingLocation.fileId) return
    const target = chunks.find(chunk => chunk.id === pendingLocation.chunkId)
    if (!target) return
    setPage(pendingLocation.page)
    setSelectedId(target.id)
    setChunkViewKind(chunkKind(target))
    setPendingLocation(null)
  }, [chunks, currentFile?.id, pendingLocation])

  useEffect(() => {
    if (!chunks.some(c => c.ocr_status === 'queued' || c.ocr_status === 'running')) return
    const id = window.setInterval(refreshChunks, 2000)
    return () => window.clearInterval(id)
  }, [chunks, refreshChunks])

  const goPrev = useCallback(() => setPage(p => Math.max(1, p - 1)), [])
  const goNext = useCallback(() => {
    setPage(p => Math.min(currentFile?.page_count ?? 1, p + 1))
  }, [currentFile])

  const commitPageDraft = () => {
    if (!currentFile) return
    const parsed = Number(pageDraft)
    if (!Number.isFinite(parsed)) {
      setPageDraft(String(page))
      return
    }
    const next = Math.max(1, Math.min(currentFile.page_count, Math.trunc(parsed)))
    setPage(next)
    setPageDraft(String(next))
  }

  const onStageMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const ratioX = (e.clientX - rect.left) / rect.width
    setEdge(ratioX < 0.2 ? 'left' : ratioX > 0.8 ? 'right' : null)
  }

  const openUploadDialog = () => {
    setUploadFile(null)
    setUploadMeta({
      ...DEFAULT_UPLOAD_META,
      knowledge_categories: selectedKnowledgeCategory === 'all' ? [] : [selectedKnowledgeCategory],
    })
    setUploadDialogOpen(true)
  }

  const closeUploadDialog = () => {
    if (uploading) return
    setUploadDialogOpen(false)
    setUploadFile(null)
    setUploadMeta(DEFAULT_UPLOAD_META)
  }

  const toggleUploadAutoChunkType = (kind: AutoChunkKind) => {
    setUploadMeta(prev => ({
      ...prev,
      auto_chunk_types: prev.auto_chunk_types.includes(kind)
        ? prev.auto_chunk_types.filter(item => item !== kind)
        : [...prev.auto_chunk_types, kind],
    }))
  }

  const toggleFileAutoChunkType = (kind: AutoChunkKind) => {
    setFileMetaDraft(prev => ({
      ...prev,
      auto_chunk_types: prev.auto_chunk_types.includes(kind)
        ? prev.auto_chunk_types.filter(item => item !== kind)
        : [...prev.auto_chunk_types, kind],
    }))
  }

  const runAutoGenerationForFile = async (fileId: string, types: AutoChunkKind[]) => {
    if (!types.length) return
    setAutoGenerating(true)
    try {
      let parseId: string | undefined
      const existingParses = await api.listFileParses(fileId)
      const hasDoneParse = existingParses.some(parse => parse.status === 'done' && parse.raw_zip_path)
      const hasActiveParse = existingParses.some(parse => parse.status === 'queued' || parse.status === 'running')
      if (!hasDoneParse && !hasActiveParse) {
        await api.parseFile(fileId)
      }

      for (let attempt = 0; attempt < 90; attempt += 1) {
        const parses = await api.listFileParses(fileId)
        const done = parses.find(parse => parse.status === 'done' && parse.raw_zip_path)
        const active = parses.find(parse => parse.status === 'queued' || parse.status === 'running')
        const latest = parses[0]
        if (done) {
          parseId = done.id
          break
        }
        if (!active && latest?.status === 'failed') throw new Error(latest.error || 'MinerU 文档解析失败')
        await new Promise(resolve => window.setTimeout(resolve, 2000))
      }
      if (!parseId) throw new Error('等待 MinerU 文档解析超时')

      for (const type of types) {
        if (type === 'table') {
          await api.autoTableChunks(fileId, { parse_id: parseId, dry_run: false, include_caption: true, skip_existing: true })
        } else if (type === 'section') {
          await api.autoSectionChunks(fileId, { parse_id: parseId, dry_run: false, target_level: 2, max_chars: 8192, skip_existing: true })
        } else if (type === 'image') {
          await api.autoImageChunks(fileId, { parse_id: parseId, dry_run: false, include_caption: true, skip_existing: true })
        }
      }
      setChunks(await api.listChunks(fileId))
    } finally {
      setAutoGenerating(false)
    }
  }

  const submitUpload = async () => {
    if (!uploadFile) return
    setUploading(true)
    let created: CSFile
    try {
      const metadata = Object.fromEntries(
        Object.entries(uploadMeta)
          .map(([key, value]) => [key, Array.isArray(value) ? value : value.trim()])
          .filter(([, value]) => Array.isArray(value) ? value.length > 0 : Boolean(value))
      )
      created = await api.uploadFile(uploadFile, metadata)
      await refreshFiles()
      setCurrentFile(created)
      setUploadDialogOpen(false)
    } catch (err) {
      alert('上传失败: ' + (err as Error).message)
      return
    } finally {
      setUploading(false)
    }

    try {
      await runAutoGenerationForFile(created.id, uploadMeta.auto_chunk_types)
    } catch (err) {
      alert('自动生成切片失败: ' + (err as Error).message)
    }
  }

  const toggleFileDraftCategory = (category: string) => {
    setFileMetaDraft(prev => {
      const exists = prev.knowledge_categories.includes(category)
      return {
        ...prev,
        knowledge_categories: exists
          ? prev.knowledge_categories.filter(item => item !== category)
          : [...prev.knowledge_categories, category],
      }
    })
  }

  const openCategoryModal = (target: CategoryTarget) => {
    setCategoryTarget(target)
    setNewCategoryName('')
    setCategoryModalOpen(true)
  }

  const closeCategoryModal = () => {
    setCategoryModalOpen(false)
    setNewCategoryName('')
  }

  const confirmNewCategory = () => {
    const trimmed = newCategoryName.trim()
    if (!trimmed) return
    setKnowledgeCategories(prev => prev.includes(trimmed) ? prev : [...prev, trimmed])
    if (categoryTarget === 'file') {
      setFileMetaDraft(prev => ({
        ...prev,
        knowledge_categories: prev.knowledge_categories.includes(trimmed)
          ? prev.knowledge_categories
          : [...prev.knowledge_categories, trimmed],
      }))
    } else {
      setUploadMeta(prev => ({
        ...prev,
        knowledge_categories: prev.knowledge_categories.includes(trimmed)
          ? prev.knowledge_categories
          : [...prev.knowledge_categories, trimmed],
      }))
    }
    closeCategoryModal()
  }

  const openFileSettings = (file: CSFile) => {
    setEditingFile(file)
    setFileMetaDraft(fileToMetaDraft(file))
  }

  const closeFileSettings = () => {
    setEditingFile(null)
    setFileMetaDraft(DEFAULT_UPLOAD_META)
  }

  const updateFileMetaDraft = <K extends keyof UploadMeta>(key: K, value: UploadMeta[K]) => {
    setFileMetaDraft(prev => ({ ...prev, [key]: value }))
  }

  const saveFileSettings = async () => {
    if (!editingFile) return
    setSavingFileMeta(true)
    let updated: CSFile
    const autoChunkTypes = fileMetaDraft.auto_chunk_types
    try {
      const metadata = Object.fromEntries(
        Object.entries(fileMetaDraft)
          .map(([key, value]) => [key, Array.isArray(value) ? value : value.trim()])
          .filter(([, value]) => Array.isArray(value) ? value.length > 0 : Boolean(value))
      )
      updated = await api.updateFile(editingFile.id, { metadata })
      setFiles(prev => prev.map(file => file.id === updated.id ? updated : file))
      if (currentFile?.id === updated.id) setCurrentFile(updated)
      closeFileSettings()
    } catch (err) {
      alert('保存文件属性失败: ' + (err as Error).message)
      return
    } finally {
      setSavingFileMeta(false)
    }

    try {
      await runAutoGenerationForFile(updated.id, autoChunkTypes)
    } catch (err) {
      alert('自动生成切片失败: ' + (err as Error).message)
    }
  }

  const selectedChunk = chunks.find(c => c.id === selectedId) || null
  const pendingChunks = useMemo(() => chunks.filter(chunk => chunk.text_source === 'pending'), [chunks])
  const deleteCandidate = chunks.find(chunk => chunk.id === deleteChunkId) || null

  const onChunkCreated = (chunk: Chunk) => {
    setChunks(prev => [...prev, chunk])
    setSelectedId(chunk.id)
    if (chunk.text_source === 'pending') {
      setPendingPulse(true)
      window.setTimeout(() => setPendingPulse(false), 650)
    }
  }

  const onChunkSaved = (chunk: Chunk) => {
    setChunks(prev => prev.map(item => item.id === chunk.id ? chunk : item))
  }

  const requestChunkDelete = (id: string) => {
    setDeleteChunkId(id)
  }

  const closeDeleteConfirm = () => {
    setDeleteChunkId(null)
  }

  const onChunkDelete = async (id: string) => {
    await api.deleteChunk(id)
    setChunks(prev => prev.filter(item => item.id !== id))
    if (selectedId === id) setSelectedId(null)
  }

  const confirmChunkDelete = async () => {
    if (!deleteChunkId) return
    await onChunkDelete(deleteChunkId)
    closeDeleteConfirm()
  }

  const toggleAutoOcr = async () => {
    const next = !autoOcr
    setAutoOcr(next)
    try {
      await api.updateSettings({ 'ocr.auto_on_create': String(next) })
    } catch (err) {
      setAutoOcr(!next)
      alert('保存设置失败: ' + (err as Error).message)
    }
  }

  const enqueuePendingOcr = async () => {
    if (!currentFile) return
    setBulkQueuing(true)
    try {
      await api.ocrBulk(currentFile.id, { pending_only: true })
      await refreshChunks()
    } catch (err) {
      alert('加入 OCR 队列失败: ' + (err as Error).message)
    } finally {
      setBulkQueuing(false)
    }
  }

  const pendingCount = chunks.filter(chunk => chunk.text_source === 'pending').length
  const parsedCount = chunks.filter(chunk => chunk.text_source === 'ocr' || chunk.text_source === 'digital').length

  const locateChunk = (
    hit: { file_id: string; chunk_id: string; page: number },
    returnTarget: ReturnTab,
  ) => {
    const file = files.find(item => item.id === hit.file_id)
    if (!file) {
      alert('检索结果对应的文件已不存在。')
      return
    }
    setSelectedKnowledgeCategory('all')
    setPendingLocation({ fileId: hit.file_id, chunkId: hit.chunk_id, page: hit.page })
    setReturnTab(returnTarget)
    setCurrentFile(file)
    setTab('studio')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">CS</div>
          <div>
            <h1>Chunk Studio</h1>
            <span>PDF 知识切片工作台</span>
          </div>
        </div>
        <nav>
          <button className={tab === 'studio' ? 'on' : ''} onClick={() => setTab('studio')}>文档</button>
          <button className={tab === 'search' ? 'on' : ''} onClick={() => setTab('search')}>检索</button>
          <button className={tab === 'metadata' ? 'on' : ''} onClick={() => setTab('metadata')}>LLM 元数据</button>
          <button className={tab === 'audit' ? 'on' : ''} onClick={() => setTab('audit')}>评测</button>
          <button className={tab === 'fields' ? 'on' : ''} onClick={() => setTab('fields')}>字段</button>
          <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>设置</button>
        </nav>
      </header>

      {tab === 'studio' && (
        <div className="three-pane">
          <aside className="sidebar" style={{ width: leftW }}>
            <div className="kb-card">
              <div className="kb-avatar">KB</div>
              <div>
                <h2>标准知识库</h2>
                <p>{files.length} 个文档 · {chunks.length} 个切片</p>
              </div>
            </div>

            <div className="side-section">
              <div className="side-section-head">
                <span>知识库类别</span>
              </div>
              <div className="category-filter">
                <button
                  type="button"
                  className={selectedKnowledgeCategory === 'all' ? 'on' : ''}
                  onClick={() => setSelectedKnowledgeCategory('all')}
                >
                  全部
                </button>
                {allKnowledgeCategories.map(category => (
                  <button
                    type="button"
                    key={category}
                    className={selectedKnowledgeCategory === category ? 'on' : ''}
                    onClick={() => setSelectedKnowledgeCategory(category)}
                  >
                    {category}
                  </button>
                ))}
              </div>
            </div>

            <div className="side-section">
              <div className="side-section-head">
                <span>文件列表</span>
                <button className="mini-upload" type="button" onClick={openUploadDialog}>
                  {uploading ? '上传中' : '上传'}
                </button>
              </div>
              <div className="file-list">
                {filteredFiles.map(file => (
                  <div
                    key={file.id}
                    className={`file-item ${currentFile?.id === file.id ? 'selected' : ''}`}
                    onClick={() => setCurrentFile(file)}
                  >
                    <div className="file-name">{file.name}</div>
                    <button
                      type="button"
                      className="file-settings-btn"
                      title="编辑文件属性"
                      onClick={event => {
                        event.stopPropagation()
                        openFileSettings(file)
                      }}
                    >
                      设置
                    </button>
                  </div>
                ))}
                {files.length === 0 && <div className="muted empty-side">还没有上传文件</div>}
                {files.length > 0 && filteredFiles.length === 0 && (
                  <div className="muted empty-side">当前类别下还没有文件</div>
                )}
              </div>
            </div>
          </aside>
          <div className="pane-splitter" onMouseDown={startResize('left')} />

          <main className="viewer-pane">
            {currentFile ? (
              <>
                <div className="document-header">
                  <div>
                    <h2>{currentFile.name}</h2>
                    <p>{currentFile.page_count} 页 · 当前第 {page} 页 · 拖拽框选建立切片</p>
                  </div>
                  <div className="doc-stats">
                    {returnTab && (
                      <button
                        type="button"
                        className="return-search"
                        onClick={() => {
                          setTab(returnTab)
                          setReturnTab(null)
                        }}
                      >
                        {returnTab === 'search' ? '返回检索结果' : '返回 LLM 元数据'}
                      </button>
                    )}
                    <div className="stat-card"><strong>{chunks.length}</strong><span>切片</span></div>
                    <div className="stat-card"><strong>{parsedCount}</strong><span>已解析</span></div>
                    <button
                      type="button"
                      className={`stat-card pending-stat ${pendingPulse ? 'pulse' : ''}`}
                      onClick={() => setPendingModalOpen(true)}
                    >
                      <strong>{pendingCount}</strong><span>待处理</span>
                    </button>
                  </div>
                </div>

                <div className="page-nav">
                  <div className="pager-group">
                    <button onClick={goPrev} disabled={page <= 1}>上一页</button>
                    <span className="page-jump">
                      <input
                        value={pageDraft}
                        inputMode="numeric"
                        aria-label="当前页码"
                        onChange={e => setPageDraft(e.target.value.replace(/[^\d]/g, ''))}
                        onBlur={commitPageDraft}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            e.currentTarget.blur()
                          } else if (e.key === 'Escape') {
                            setPageDraft(String(page))
                            e.currentTarget.blur()
                          }
                        }}
                      />
                      <b>/ {currentFile.page_count}</b>
                    </span>
                    <button onClick={goNext} disabled={page >= currentFile.page_count}>下一页</button>
                  </div>
                  <div className="tool-group">
                    <label className="toolbar-toggle">
                      <input type="checkbox" checked={autoOcr} onChange={toggleAutoOcr} />
                      新建切片后自动解析
                    </label>
                    <button onClick={enqueuePendingOcr} disabled={bulkQueuing}>
                      {bulkQueuing ? '提交中...' : '解析待处理切片'}
                    </button>
                  </div>
                </div>
                <div className="viewer-stage" onMouseMove={onStageMove} onMouseLeave={() => setEdge(null)}>
                  <PageViewer
                    fileId={currentFile.id}
                    page={page}
                    chunks={chunks}
                    selectedChunkId={selectedId}
                    visibleKind={chunkViewKind}
                    autoParseOnCreate={autoOcr}
                    onChunkCreated={onChunkCreated}
                    onSelectChunk={setSelectedId}
                    onDeleteChunk={requestChunkDelete}
                    onCreatingChange={setManualChunkCreating}
                  />
                  {edge === 'left' && page > 1 && (
                    <button className="edge-nav edge-left" onClick={goPrev} title="上一页">‹</button>
                  )}
                  {edge === 'right' && page < currentFile.page_count && (
                    <button className="edge-nav edge-right" onClick={goNext} title="下一页">›</button>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-viewer">
                <h2>上传一个 PDF 开始切片</h2>
                <p>左侧填写入库信息后上传文档，中央会显示 PDF 预览，右侧用于管理切片与元数据。</p>
              </div>
            )}
          </main>
          <div className="pane-splitter" onMouseDown={startResize('right')} />
          <aside className="right-pane" style={{ width: rightW }}>
            <section className="inspector-section chunk-list-shell" style={{ height: rightListH }}>
              <ChunkList chunks={chunks} selectedId={selectedId} activeKind={chunkViewKind} onKindChange={setChunkViewKind} onSelect={(id) => {
                const chunk = chunks.find(item => item.id === id)
                if (chunk) setPage(chunk.page)
                setSelectedId(id)
              }} onDelete={requestChunkDelete} />
            </section>
            <div className="right-pane-resizer" onMouseDown={startRightListResize}>
              <span />
            </div>
            <section className="inspector-section editor-shell">
              <ChunkEditor
                chunk={selectedChunk}
                fields={fields}
                onSaved={onChunkSaved}
                onDelete={requestChunkDelete}
                onQueued={refreshChunks}
              />
            </section>
          </aside>
        </div>
      )}

      <SearchPage hidden={tab !== 'search'} onLocate={hit => locateChunk(hit, 'search')} />
      {tab === 'metadata' && (
        <MetadataSuggestionsPage
          onLocate={chunk => locateChunk({
            file_id: chunk.file_id,
            chunk_id: chunk.id,
            page: chunk.page,
          }, 'metadata')}
        />
      )}
      {tab === 'audit' && <AuditPage />}
      {tab === 'fields' && <FieldConfigPanel fields={fields} onChanged={refreshFields} />}
      {tab === 'settings' && <SettingsPage />}

      {uploadDialogOpen && (
        <div className="modal">
          <div className="modal-panel upload-modal">
            <div className="modal-title">
              <div>
                <h4>上传 PDF</h4>
                <p>选择上传文件，并设置本文件需要自动生成的切片类型。</p>
              </div>
              <button className="ghost" onClick={closeUploadDialog}>关闭</button>
            </div>
            <label>
              PDF 文件
              <input
                type="file"
                accept="application/pdf"
                onChange={event => setUploadFile(event.target.files?.[0] || null)}
              />
            </label>
            <div className="category-label">自动生成切片</div>
            <div className="category-chips modal-chips">
              {AUTO_CHUNK_OPTIONS.map(option => (
                <button
                  type="button"
                  key={option.key}
                  className={uploadMeta.auto_chunk_types.includes(option.key) ? 'on' : ''}
                  onClick={() => toggleUploadAutoChunkType(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="modal-help">不勾选时，只上传并解析文档，不自动生成表格/章节/图片切片。</p>
            <div className="modal-actions">
              <button onClick={closeUploadDialog}>取消</button>
              <button className="primary" onClick={submitUpload} disabled={!uploadFile || uploading || autoGenerating}>
                {uploading || autoGenerating ? '处理中...' : '上传'}
              </button>
            </div>
          </div>
        </div>
      )}

      {editingFile && (
        <div className="modal">
          <div className="modal-panel file-meta-modal">
            <div className="modal-title">
              <div>
                <h4>文件属性</h4>
                <p>{editingFile.name}</p>
              </div>
              <button className="ghost" onClick={closeFileSettings}>关闭</button>
            </div>
            <div className="form-grid two">
              <label>
                标准号
                <input
                  value={fileMetaDraft.standard_no}
                  onChange={e => updateFileMetaDraft('standard_no', e.target.value)}
                  placeholder="GB/T 6451-2023"
                />
              </label>
              <label>
                文档类型
                <select
                  value={fileMetaDraft.doc_type}
                  onChange={e => updateFileMetaDraft('doc_type', e.target.value)}
                >
                  <option value="标准">标准</option>
                  <option value="技术规范书">技术规范书</option>
                  <option value="合同">合同</option>
                  <option value="其他">其他</option>
                </select>
              </label>
            </div>
            <div className="category-label">知识库所属类别</div>
            <div className="category-chips modal-chips">
              {allKnowledgeCategories.map(category => (
                <button
                  type="button"
                  key={category}
                  className={fileMetaDraft.knowledge_categories.includes(category) ? 'on' : ''}
                  onClick={() => toggleFileDraftCategory(category)}
                >
                  {category}
                </button>
              ))}
              <button
                type="button"
                className="add-category"
                onClick={() => openCategoryModal('file')}
              >
                + 新增类别
              </button>
            </div>
            <div className="category-label">自动生成切片</div>
            <div className="category-chips modal-chips">
              {AUTO_CHUNK_OPTIONS.map(option => (
                <button
                  type="button"
                  key={option.key}
                  className={fileMetaDraft.auto_chunk_types.includes(option.key) ? 'on' : ''}
                  onClick={() => toggleFileAutoChunkType(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <div className="form-grid three">
              <label>
                适用日期
                <input
                  type="date"
                  value={fileMetaDraft.applicable_date}
                  onChange={e => updateFileMetaDraft('applicable_date', e.target.value)}
                />
              </label>
              <label>
                发布日期
                <input
                  type="date"
                  value={fileMetaDraft.publish_date}
                  onChange={e => updateFileMetaDraft('publish_date', e.target.value)}
                />
              </label>
              <label>
                实施日期
                <input
                  type="date"
                  value={fileMetaDraft.effective_date}
                  onChange={e => updateFileMetaDraft('effective_date', e.target.value)}
                />
              </label>
            </div>
            <label>
              备注
              <textarea
                rows={3}
                value={fileMetaDraft.notes}
                onChange={e => updateFileMetaDraft('notes', e.target.value)}
                placeholder="废止关系、适用范围、特殊说明"
              />
            </label>
            <div className="modal-actions">
              <button onClick={closeFileSettings}>取消</button>
              <button className="primary" onClick={saveFileSettings} disabled={savingFileMeta}>
                {savingFileMeta || autoGenerating ? '处理中...' : '保存属性'}
              </button>
            </div>
          </div>
        </div>
      )}

      {categoryModalOpen && (
        <div className="modal">
          <div className="modal-panel category-modal">
            <div className="modal-title">
              <div>
                <h4>新增知识库类别</h4>
                <p>创建后会自动选中当前编辑对象。</p>
              </div>
              <button className="ghost" onClick={closeCategoryModal}>关闭</button>
            </div>
            <label>
              类别名称
              <input
                autoFocus
                value={newCategoryName}
                onChange={e => setNewCategoryName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') confirmNewCategory()
                  if (e.key === 'Escape') closeCategoryModal()
                }}
                placeholder="例如：箱式变电站"
              />
            </label>
            <div className="modal-actions">
              <button onClick={closeCategoryModal}>取消</button>
              <button className="primary" onClick={confirmNewCategory} disabled={!newCategoryName.trim()}>
                新增类别
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingModalOpen && (
        <div className="modal">
          <div className="modal-panel pending-modal">
            <div className="modal-title">
              <div>
                <h4>待处理切片</h4>
                <p>这些切片还没有解析文本，可以在这里删除误加入的待解析图片。</p>
              </div>
              <button className="ghost" onClick={() => setPendingModalOpen(false)}>关闭</button>
            </div>
            <div className="pending-list">
              {pendingChunks.length === 0 && (
                <div className="empty-manager">当前没有待处理切片</div>
              )}
              {pendingChunks.map(chunk => (
                <div className="pending-item" key={chunk.id}>
                  <div className="pending-thumb">
                    {chunk.crop_url ? (
                      <>
                        <img src={chunk.crop_url} alt="待处理切片" />
                        <div className="pending-zoom">
                          <img src={chunk.crop_url} alt="待处理切片放大预览" />
                        </div>
                      </>
                    ) : <span>no crop</span>}
                  </div>
                  <div className="pending-body">
                    <div className="pending-title">第 {chunk.page} 页</div>
                    <p>{chunk.text?.trim() || '待 MinerU 解析'}</p>
                  </div>
                  <button className="danger" onClick={() => requestChunkDelete(chunk.id)}>删除</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {deleteCandidate && (
        <div className="modal confirm-modal-layer">
          <div className="modal-panel confirm-modal">
            <div className="modal-title">
              <div>
                <h4>删除切片？</h4>
                <p>第 {deleteCandidate.page} 页的这个切片会被永久删除。</p>
              </div>
            </div>
            {deleteCandidate.crop_url && (
              <div className="confirm-preview">
                <img src={deleteCandidate.crop_url} alt="待删除切片" />
              </div>
            )}
            <div className="modal-actions">
              <button onClick={closeDeleteConfirm}>取消</button>
              <button className="danger solid" onClick={confirmChunkDelete}>确认删除</button>
            </div>
          </div>
        </div>
      )}

      {autoOcr && manualChunkCreating && (
        <div className="blocking-overlay">
          <div className="blocking-card">
            <span className="spinner large" />
            <h4>MinerU 正在解析刚刚框选的切片</h4>
          </div>
        </div>
      )}

      {autoGenerating && (
        <div className="blocking-overlay">
          <div className="blocking-card">
            <span className="spinner large" />
            <h4>正在根据 MinerU 结果生成切片</h4>
            <p>系统会自动跳过已经存在的相似切片。</p>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
