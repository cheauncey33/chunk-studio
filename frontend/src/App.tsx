import { useCallback, useEffect, useState } from 'react'
import { api, type CSFile, type Chunk, type FieldConfig } from './api'
import { PageViewer } from './PageViewer'
import { ChunkList } from './ChunkList'
import { ChunkEditor } from './ChunkEditor'
import { ChunkManager } from './ChunkManager'
import { FieldConfigPanel } from './FieldConfigPanel'
import { SettingsPage } from './SettingsPage'
import './App.css'

type Tab = 'studio' | 'fields' | 'settings'
type SideMode = 'file' | 'library'
type UploadMeta = {
  standard_no: string
  doc_type: string
  applicable_date: string
  publish_date: string
  effective_date: string
  notes: string
}

const DEFAULT_UPLOAD_META: UploadMeta = {
  standard_no: '',
  doc_type: '标准',
  applicable_date: '',
  publish_date: '',
  effective_date: '',
  notes: '',
}

function App() {
  const [tab, setTab] = useState<Tab>('studio')
  const [sideMode, setSideMode] = useState<SideMode>('file')
  const [files, setFiles] = useState<CSFile[]>([])
  const [currentFile, setCurrentFile] = useState<CSFile | null>(null)
  const [page, setPage] = useState(1)
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fields, setFields] = useState<FieldConfig[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadMeta, setUploadMeta] = useState<UploadMeta>(DEFAULT_UPLOAD_META)
  const [autoOcr, setAutoOcr] = useState(false)
  const [bulkQueuing, setBulkQueuing] = useState(false)
  const [edge, setEdge] = useState<'left' | 'right' | null>(null)
  const [leftW, setLeftW] = useState(240)
  const [rightW, setRightW] = useState(720)

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

  useEffect(() => { refreshChunks() }, [refreshChunks])

  useEffect(() => {
    setPage(1)
    setSelectedId(null)
  }, [currentFile?.id])

  useEffect(() => {
    if (!chunks.some(c => c.ocr_status === 'queued' || c.ocr_status === 'running')) return
    const id = window.setInterval(refreshChunks, 2000)
    return () => window.clearInterval(id)
  }, [chunks, refreshChunks])

  const goPrev = useCallback(() => setPage(p => Math.max(1, p - 1)), [])
  const goNext = useCallback(() => {
    setPage(p => Math.min(currentFile?.page_count ?? 1, p + 1))
  }, [currentFile])

  const onStageMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const ratioX = (e.clientX - rect.left) / rect.width
    setEdge(ratioX < 0.2 ? 'left' : ratioX > 0.8 ? 'right' : null)
  }

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const metadata = Object.fromEntries(
        Object.entries(uploadMeta).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value)
      )
      const created = await api.uploadFile(file, metadata)
      await refreshFiles()
      setCurrentFile(created)
    } catch (err) {
      alert('上传失败: ' + (err as Error).message)
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const updateUploadMeta = (key: keyof UploadMeta, value: string) => {
    setUploadMeta(prev => ({ ...prev, [key]: value }))
  }

  const selectedChunk = chunks.find(c => c.id === selectedId) || null

  const onChunkCreated = (chunk: Chunk) => {
    setChunks(prev => [...prev, chunk])
    setSelectedId(chunk.id)
  }

  const onChunkSaved = (chunk: Chunk) => {
    setChunks(prev => prev.map(item => item.id === chunk.id ? chunk : item))
  }

  const onChunkDelete = async (id: string) => {
    await api.deleteChunk(id)
    setChunks(prev => prev.filter(item => item.id !== id))
    if (selectedId === id) setSelectedId(null)
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

  const openManagedChunk = (chunk: Chunk) => {
    const file = files.find(f => f.id === chunk.file_id)
    if (file) setCurrentFile(file)
    setPage(chunk.page)
    setSelectedId(chunk.id)
    setSideMode('file')
    setTab('studio')
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Chunk Studio</h1>
        <nav>
          <button className={tab === 'studio' ? 'on' : ''} onClick={() => setTab('studio')}>工作台</button>
          <button className={tab === 'fields' ? 'on' : ''} onClick={() => setTab('fields')}>字段配置</button>
          <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>设置</button>
        </nav>
      </header>

      {tab === 'studio' && (
        <div className="three-pane">
          <aside className="sidebar" style={{ width: leftW }}>
            <div className="upload-panel">
              <label>
                标准号
                <input
                  value={uploadMeta.standard_no}
                  onChange={e => updateUploadMeta('standard_no', e.target.value)}
                  placeholder="GB/T 6451-2023"
                />
              </label>
              <label>
                文档类型
                <select value={uploadMeta.doc_type} onChange={e => updateUploadMeta('doc_type', e.target.value)}>
                  <option value="标准">标准</option>
                  <option value="规范">规范</option>
                  <option value="报告">报告</option>
                  <option value="合同">合同</option>
                  <option value="手册">手册</option>
                  <option value="其他">其他</option>
                </select>
              </label>
              <label>
                适用日期
                <input
                  type="date"
                  value={uploadMeta.applicable_date}
                  onChange={e => updateUploadMeta('applicable_date', e.target.value)}
                />
              </label>
              <label>
                发布日期
                <input
                  type="date"
                  value={uploadMeta.publish_date}
                  onChange={e => updateUploadMeta('publish_date', e.target.value)}
                />
              </label>
              <label>
                实施日期
                <input
                  type="date"
                  value={uploadMeta.effective_date}
                  onChange={e => updateUploadMeta('effective_date', e.target.value)}
                />
              </label>
              <label>
                备注
                <textarea
                  rows={2}
                  value={uploadMeta.notes}
                  onChange={e => updateUploadMeta('notes', e.target.value)}
                  placeholder="废止关系、适用范围、特殊说明"
                />
              </label>
              <label className="upload-btn">
                {uploading ? '上传中...' : '+ 上传 PDF'}
                <input type="file" accept="application/pdf" onChange={onUpload} hidden />
              </label>
            </div>
            <div className="file-list">
              {files.map(file => (
                <div
                  key={file.id}
                  className={`file-item ${currentFile?.id === file.id ? 'selected' : ''}`}
                  onClick={() => setCurrentFile(file)}
                >
                  <div className="file-name">{file.name}</div>
                  <div className="file-meta">
                    {file.page_count} 页
                    {typeof file.metadata?.standard_no === 'string' && ` · ${file.metadata.standard_no}`}
                  </div>
                </div>
              ))}
              {files.length === 0 && <div className="muted">还没有上传文件</div>}
            </div>
          </aside>
          <div className="pane-splitter" onMouseDown={startResize('left')} />

          <main className="viewer-pane">
            {currentFile ? (
              <>
                <div className="page-nav">
                  <button onClick={goPrev} disabled={page <= 1}>上一页</button>
                  <span>{page} / {currentFile.page_count}</span>
                  <label className="toolbar-toggle">
                    <input type="checkbox" checked={autoOcr} onChange={toggleAutoOcr} />
                    自动 OCR
                  </label>
                  <button onClick={enqueuePendingOcr} disabled={bulkQueuing}>
                    {bulkQueuing ? '加入中...' : 'OCR 待处理'}
                  </button>
                  <button onClick={goNext} disabled={page >= currentFile.page_count}>下一页</button>
                  <span className="hint">拖拽框选建立切片 · 左右方向键翻页 · 悬停页面左右边缘可翻页</span>
                </div>
                <div className="viewer-stage" onMouseMove={onStageMove} onMouseLeave={() => setEdge(null)}>
                  <PageViewer
                    fileId={currentFile.id}
                    page={page}
                    chunks={chunks}
                    selectedChunkId={selectedId}
                    onChunkCreated={onChunkCreated}
                    onSelectChunk={setSelectedId}
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
              <div className="empty-viewer">上传一个 PDF 开始</div>
            )}
          </main>
          <div className="pane-splitter" onMouseDown={startResize('right')} />
          <aside className="right-pane" style={{ width: rightW }}>
            <div className="right-tabs">
              <button className={sideMode === 'file' ? 'on' : ''} onClick={() => setSideMode('file')}>当前文件</button>
              <button className={sideMode === 'library' ? 'on' : ''} onClick={() => setSideMode('library')}>全部切片</button>
            </div>
            {sideMode === 'file' ? (
              <ChunkList chunks={chunks} selectedId={selectedId} onSelect={(id) => {
                const chunk = chunks.find(item => item.id === id)
                if (chunk) setPage(chunk.page)
                setSelectedId(id)
              }} />
            ) : (
              <ChunkManager files={files} onOpenChunk={openManagedChunk} compact />
            )}
            <ChunkEditor
              chunk={selectedChunk}
              fields={fields}
              onSaved={onChunkSaved}
              onDelete={onChunkDelete}
              onQueued={refreshChunks}
            />
          </aside>
        </div>
      )}

      {tab === 'fields' && <FieldConfigPanel fields={fields} onChanged={refreshFields} />}
      {tab === 'settings' && <SettingsPage />}
    </div>
  )
}

export default App
