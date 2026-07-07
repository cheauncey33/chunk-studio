import { useRef, useState, useCallback, useEffect, useLayoutEffect } from 'react'
import { api, type Chunk, type BBox } from './api'

interface Props {
  fileId: string
  page: number
  chunks: Chunk[]
  selectedChunkId: string | null
  onChunkCreated: (c: Chunk) => void
  onSelectChunk: (id: string | null) => void
}

interface DragRect { x: number; y: number; w: number; h: number }

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
export function PageViewer({ fileId, page, chunks, selectedChunkId, onChunkCreated, onSelectChunk }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ left: number; top: number; w: number; h: number } | null>(null)
  const [drag, setDrag] = useState<DragRect | null>(null)
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
    try {
      const c = await api.createChunk({ file_id: fileId, page, bbox })
      onChunkCreated(c)
    } catch (err) {
      alert('建 chunk 失败: ' + (err as Error).message)
    }
  }

  const onMouseLeave = () => {
    if (startRef.current) { startRef.current = null; setDrag(null) }
  }

  const pct = (v: number) => `${v * 100}%`

  return (
    <div className="page-viewer" ref={containerRef}>
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
            cursor: 'crosshair',
          }}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseLeave}
        >
          {chunks.filter(c => c.page === page).map(c => {
            const sel = c.id === selectedChunkId
            return (
              <div
                key={c.id}
                className={`chunk-rect ${sel ? 'selected' : ''}`}
                style={{
                  position: 'absolute',
                  left: pct(c.bbox.x), top: pct(c.bbox.y),
                  width: pct(c.bbox.w), height: pct(c.bbox.h),
                }}
                onMouseDown={(e) => { e.stopPropagation(); onSelectChunk(c.id) }}
                title={c.text?.slice(0, 60) || '(无文本)'}
              >
                <span className="chunk-rect-badge">{c.page}</span>
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
