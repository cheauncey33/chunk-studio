import { useRef, useState, useCallback, useEffect, useLayoutEffect } from 'react'
import { api, type Chunk, type BBox } from './api'
import type { ChunkKind } from './ChunkList'

interface Props {
  fileId: string
  page: number
  chunks: Chunk[]
  selectedChunkId: string | null
  visibleKind: ChunkKind
  autoParseOnCreate: boolean
  onChunkCreated: (c: Chunk) => void
  onSelectChunk: (id: string | null) => void
  onDeleteChunk: (id: string) => void
  onCreatingChange?: (creating: boolean) => void
}

interface DragRect { x: number; y: number; w: number; h: number }
interface PageBox { x: number; y: number; w: number; h: number }

/**
 * Renders one PDF page as an <img> with a transparent overlay for box-select.
 *
 * Layout: the <img> is a direct child of .page-viewer (a flex item stretched to
 * the stage's definite height) and sized with height:100%/max-width:100% so the
 * whole page fits with no scrolling. Because the overlay must cover exactly the
 * image's rendered box (not the full stage, which has centering margins), we
 * measure the img's rect with a ResizeObserver and position the overlay to match.
 * Mouse coords ÷ overlay size → 0..1 normalized bbox, so zoom/DPR are irrelevant.
 */
export function PageViewer({
  fileId,
  page,
  chunks,
  selectedChunkId,
  visibleKind,
  autoParseOnCreate,
  onChunkCreated,
  onSelectChunk,
  onDeleteChunk,
  onCreatingChange,
}: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ left: number; top: number; w: number; h: number } | null>(null)
  const [drag, setDrag] = useState<DragRect | null>(null)
  const [creating, setCreating] = useState(false)
  const startRef = useRef<{ x: number; y: number } | null>(null)

  // Position the overlay to exactly cover the rendered <img>.
  const measure = useCallback(() => {
    const img = imgRef.current
    const container = containerRef.current
    if (!img || !container) return
    const ir = img.getBoundingClientRect()
    const cr = container.getBoundingClientRect()
    if (ir.width === 0 || ir.height === 0) return
    setBox({ left: ir.left - cr.left, top: ir.top - cr.top, w: ir.width, h: ir.height })
  }, [])

  // Re-measure on container resize and when the image loads (new page).
  useLayoutEffect(() => {
    measure()
    const img = imgRef.current
    const container = containerRef.current
    if (!img || !container) return
    const ro = new ResizeObserver(measure)
    ro.observe(img)
    ro.observe(container)
    return () => ro.disconnect()
  }, [measure])

  useEffect(() => { measure() }, [page, fileId, measure]) // eslint-disable-line react-hooks/exhaustive-deps

  const toNorm = useCallback((clientX: number, clientY: number): { x: number; y: number } => {
    const img = imgRef.current!
    const r = img.getBoundingClientRect()
    return { x: (clientX - r.left) / r.width, y: (clientY - r.top) / r.height }
  }, [])

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    const p = toNorm(e.clientX, e.clientY)
    startRef.current = p
    setDrag({ x: p.x, y: p.y, w: 0, h: 0 })
    onSelectChunk(null)
  }

  const onMouseMove = (e: React.MouseEvent) => {
    const s = startRef.current
    if (!s) return
    const p = toNorm(e.clientX, e.clientY)
    setDrag({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) })
  }

  const onMouseUp = async () => {
    const s = startRef.current
    startRef.current = null
    const d = drag
    setDrag(null)
    if (!s || !d) return
    if (d.w < 0.01 || d.h < 0.01) return // ignore accidental clicks
    const bbox: BBox = { x: d.x, y: d.y, w: d.w, h: d.h }
    setCreating(true)
    onCreatingChange?.(true)
    try {
      const c = await api.createChunk({ file_id: fileId, page, bbox })
      onChunkCreated(c)
      if (c.ocr_status === 'failed') {
        alert('切片已创建，但 MinerU 解析失败: ' + (c.ocr_error || '未知错误'))
      }
    } catch (err) {
      alert('建 chunk 失败: ' + (err as Error).message)
    } finally {
      setCreating(false)
      onCreatingChange?.(false)
    }
  }

  const onMouseLeave = () => {
    if (startRef.current) { startRef.current = null; setDrag(null) }
  }

  const pct = (v: number) => `${v * 100}%`
  const visibleChunks = chunks
    .map(chunk => ({ chunk, pageBox: pageBoxForChunk(chunk, page, visibleKind) }))
    .filter((item): item is { chunk: Chunk; pageBox: PageBox } => Boolean(item.pageBox))

  return (
    <div className="page-viewer" ref={containerRef}>
      {creating && autoParseOnCreate && (
        <div className="page-viewer-loading">
          <span className="spinner" />
          <span>MinerU 正在解析切片…</span>
        </div>
      )}
      <img
        ref={imgRef}
        src={api.pageImageUrl(fileId, page)}
        alt={`page ${page}`}
        draggable={false}
        onLoad={measure}
      />
      {box && (
        <div
          className="overlay"
          style={{
            position: 'absolute',
            left: box.left, top: box.top, width: box.w, height: box.h,
            cursor: creating ? 'wait' : 'crosshair',
            pointerEvents: creating ? 'none' : undefined,
          }}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseLeave}
        >
          {visibleChunks.map(({ chunk: c, pageBox }) => {
            const sel = c.id === selectedChunkId
            return (
              <div
                key={c.id}
                className={`chunk-rect ${chunkKind(c)} ${sel ? 'selected' : ''}`}
                style={{
                  position: 'absolute',
                  left: pct(pageBox.x), top: pct(pageBox.y),
                  width: pct(pageBox.w), height: pct(pageBox.h),
                }}
                onMouseDown={(e) => { e.stopPropagation(); onSelectChunk(c.id) }}
                title={c.text?.slice(0, 60) || '(无文本)'}
              >
                <span className="chunk-rect-badge">{c.page}</span>
                <button
                  className="chunk-rect-delete"
                  title="删除切片"
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation()
                    onDeleteChunk(c.id)
                  }}
                >
                  ×
                </button>
              </div>
            )
          })}
          {drag && (
            <div
              className="drag-rect"
              style={{
                position: 'absolute',
                left: pct(drag.x), top: pct(drag.y),
                width: pct(drag.w), height: pct(drag.h),
              }}
            />
          )}
        </div>
      )}
    </div>
  )
}

function chunkKind(chunk: Chunk): ChunkKind {
  const type = String(chunk.metadata?.content_type || '')
  if (type === 'section') return 'section'
  if (type === 'table') return 'table'
  if (type === 'image') return 'image'
  return 'manual'
}

function pageBoxForChunk(chunk: Chunk, page: number, visibleKind: ChunkKind): PageBox | null {
  const kind = chunkKind(chunk)
  if (kind !== visibleKind) return null
  if (kind !== 'section') return chunk.page === page ? chunk.bbox : null

  const box = sectionPageBox(chunk, page)
  if (box) return box
  return chunk.page === page ? chunk.bbox : null
}

function sectionPageBox(chunk: Chunk, page: number): PageBox | null {
  const sourceBlocks = chunk.metadata?.source_blocks
  if (!Array.isArray(sourceBlocks)) return null

  const pageIdx = page - 1
  const boxes = sourceBlocks
    .map(block => {
      if (!block || typeof block !== 'object') return null
      const record = block as Record<string, unknown>
      if (Number(record.page_idx) !== pageIdx) return null
      return rawBoxToPageBox(record.bbox)
    })
    .filter((box): box is PageBox => Boolean(box))

  if (!boxes.length) return null
  return unionPageBoxes(boxes)
}

function rawBoxToPageBox(value: unknown): PageBox | null {
  if (!Array.isArray(value) || value.length < 4) return null
  const [x0, y0, x1, y1] = value.map(Number)
  if (![x0, y0, x1, y1].every(Number.isFinite)) return null
  const x = Math.max(0, Math.min(1, x0))
  const y = Math.max(0, Math.min(1, y0))
  const right = Math.max(0, Math.min(1, x1))
  const bottom = Math.max(0, Math.min(1, y1))
  const w = right - x
  const h = bottom - y
  if (w <= 0 || h <= 0) return null
  return { x, y, w, h }
}

function unionPageBoxes(boxes: PageBox[]): PageBox {
  const x0 = Math.min(...boxes.map(box => box.x))
  const y0 = Math.min(...boxes.map(box => box.y))
  const x1 = Math.max(...boxes.map(box => box.x + box.w))
  const y1 = Math.max(...boxes.map(box => box.y + box.h))
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}
