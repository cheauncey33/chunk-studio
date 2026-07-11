import { useEffect, useMemo, useState } from 'react'
import { api, type Chunk, type CSFile } from './api'
import { getBusinessMetadata, isAutoChunk } from './chunkSchema'

interface Props {
  files: CSFile[]
  onOpenChunk: (chunk: Chunk) => void
  compact?: boolean
}

function sourceLabel(chunk: Chunk): string {
  return isAutoChunk(chunk) ? 'auto' : chunk.text_source
}

type SourceFilter = 'all' | Chunk['text_source']
type JobFilter = 'all' | 'queued' | 'running' | 'done' | 'failed' | 'none'

export function ChunkManager({ files, onOpenChunk, compact = false }: Props) {
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [fileId, setFileId] = useState('all')
  const [source, setSource] = useState<SourceFilter>('all')
  const [job, setJob] = useState<JobFilter>('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Chunk | null>(null)

  const refresh = async () => {
    setLoading(true)
    try {
      setChunks(await api.listChunks(fileId === 'all' ? undefined : fileId))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [fileId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!chunks.some(c => c.ocr_status === 'queued' || c.ocr_status === 'running')) return
    const id = window.setInterval(refresh, 2000)
    return () => window.clearInterval(id)
  }, [chunks]) // eslint-disable-line react-hooks/exhaustive-deps

  const fileNameById = useMemo(
    () => Object.fromEntries(files.map(f => [f.id, f.name])),
    [files],
  )

  const filtered = chunks.filter(chunk => {
    if (source !== 'all' && chunk.text_source !== source) return false
    if (job !== 'all') {
      if (job === 'none' && chunk.ocr_status) return false
      if (job !== 'none' && chunk.ocr_status !== job) return false
    }
    const needle = query.trim().toLowerCase()
    if (!needle) return true
    const haystack = [
      fileNameById[chunk.file_id],
      chunk.text || '',
      chunk.text_source,
      chunk.status,
      JSON.stringify(getBusinessMetadata(chunk)),
    ].join('\n').toLowerCase()
    return haystack.includes(needle)
  })

  const enqueueOcr = async (chunk: Chunk) => {
    setBusyId(chunk.id)
    try {
      await api.ocrChunkSync(chunk.id)
      await refresh()
    } catch (err) {
      alert('MinerU 解析失败: ' + (err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const deleteChunk = async (chunk: Chunk) => {
    setBusyId(chunk.id)
    try {
      await api.deleteChunk(chunk.id)
      setChunks(prev => prev.filter(item => item.id !== chunk.id))
      setDeleteTarget(null)
    } finally {
      setBusyId(null)
    }
  }

  const pendingCount = chunks.filter(c => c.text_source === 'pending').length
  const runningCount = chunks.filter(c => c.ocr_status === 'queued' || c.ocr_status === 'running').length

  return (
    <div className={`chunk-manager ${compact ? 'compact' : ''}`}>
      <section className="manager-panel">
        <div className="section-head">
          <div>
            <h3>切片库</h3>
            {!compact && <p className="muted">集中查看所有已切片内容，筛选待 OCR、失败任务和已校对内容。</p>}
          </div>
          <button onClick={refresh} disabled={loading}>{loading ? '刷新中...' : '刷新'}</button>
        </div>

        <div className="manager-stats">
          <div><strong>{chunks.length}</strong><span>总数</span></div>
          <div><strong>{pendingCount}</strong><span>待 OCR</span></div>
          <div><strong>{runningCount}</strong><span>队列</span></div>
          <div><strong>{filtered.length}</strong><span>结果</span></div>
        </div>

        <div className="manager-filters">
          <select value={fileId} onChange={e => setFileId(e.target.value)}>
            <option value="all">全部文件</option>
            {files.map(file => <option key={file.id} value={file.id}>{file.name}</option>)}
          </select>
          <select value={source} onChange={e => setSource(e.target.value as SourceFilter)}>
            <option value="all">全部来源</option>
            <option value="pending">pending</option>
            <option value="ocr">ocr</option>
            <option value="digital">digital</option>
            <option value="manual">manual</option>
          </select>
          <select value={job} onChange={e => setJob(e.target.value as JobFilter)}>
            <option value="all">全部 OCR 状态</option>
            <option value="queued">queued</option>
            <option value="running">running</option>
            <option value="done">done</option>
            <option value="failed">failed</option>
            <option value="none">无任务</option>
          </select>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索文本、文件名、元数据..."
          />
        </div>

        <div className="manager-list">
          {filtered.length === 0 && <div className="empty-manager">没有匹配的切片</div>}
          {filtered.map(chunk => (
            <article className="manager-item" key={chunk.id}>
              {!compact && (
                <div className="manager-crop">
                  {chunk.crop_url ? <img src={chunk.crop_url} alt="crop" /> : <span>no crop</span>}
                </div>
              )}
              <div className="manager-body">
                <div className="manager-line">
                  <span className="mono">P{chunk.page}</span>
                  <span className={`src ${sourceLabel(chunk)}`}>{sourceLabel(chunk)}</span>
                  <span className={`status ${chunk.status}`}>{chunk.status}</span>
                  {!isAutoChunk(chunk) && chunk.ocr_status && <span className={`job-status ${chunk.ocr_status}`}>ocr:{chunk.ocr_status}</span>}
                </div>
                <div className="manager-file">{fileNameById[chunk.file_id] || chunk.file_id}</div>
                <p>{chunk.text?.trim() || '无文本'}</p>
                {chunk.ocr_error && <div className="manager-error">{chunk.ocr_error}</div>}
              </div>
              <div className="manager-actions">
                <button onClick={() => onOpenChunk(chunk)}>打开</button>
                {!isAutoChunk(chunk) && (
                  <button
                    onClick={() => enqueueOcr(chunk)}
                    disabled={busyId === chunk.id || chunk.ocr_status === 'queued' || chunk.ocr_status === 'running'}
                  >
                    OCR
                  </button>
                )}
                <button className="danger" onClick={() => setDeleteTarget(chunk)} disabled={busyId === chunk.id}>删</button>
              </div>
            </article>
          ))}
        </div>
      </section>
      {deleteTarget && (
        <div className="modal confirm-modal-layer">
          <div className="modal-panel confirm-modal">
            <div className="modal-title">
              <div>
                <h4>删除切片？</h4>
                <p>第 {deleteTarget.page} 页的这个切片会被永久删除。</p>
              </div>
            </div>
            <div className="modal-actions">
              <button onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="danger solid" onClick={() => deleteChunk(deleteTarget)} disabled={busyId === deleteTarget.id}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
