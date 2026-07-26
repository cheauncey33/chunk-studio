import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, Minus, Plus, RotateCcw } from 'lucide-react'
import { api } from '@/api'
import { Button } from '@/components/ui/button'

const ZOOM_MIN = 0.75
const ZOOM_MAX = 3
const ZOOM_STEP = 0.25

export function ReportPagePreview({
  fileId,
  page,
  pageCount,
  locating,
  onPageChange,
}: {
  fileId: string
  page: number
  pageCount: number
  locating?: boolean
  onPageChange: (page: number) => void
}) {
  const [imgError, setImgError] = useState(false)
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    setZoom(1)
  }, [fileId, page])

  const safePage = Math.min(Math.max(page, 1), Math.max(pageCount, 1))
  const dpi = 360
  const src = `${api.pageImageUrl(fileId, safePage, dpi)}&t=${safePage}`

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f8fafc]">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border-button bg-white px-3 py-2">
        <div className="flex items-center gap-1">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={safePage <= 1}
            onClick={() => onPageChange(safePage - 1)}
          >
            <ChevronLeft className="size-3.5" />
          </Button>
          <span className="min-w-[5.5rem] text-center text-[13px] text-[#374151]">
            {pageCount > 0 ? `${safePage} / ${pageCount}` : '—'}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={pageCount > 0 ? safePage >= pageCount : true}
            onClick={() => onPageChange(safePage + 1)}
          >
            <ChevronRight className="size-3.5" />
          </Button>
        </div>

        <div className="flex items-center gap-1">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={zoom <= ZOOM_MIN}
            onClick={() => setZoom(value => Math.max(ZOOM_MIN, Number((value - ZOOM_STEP).toFixed(2))))}
            title="缩小"
          >
            <Minus className="size-3.5" />
          </Button>
          <span className="min-w-[3.25rem] text-center text-[12px] tabular-nums text-[#374151]">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={zoom >= ZOOM_MAX}
            onClick={() => setZoom(value => Math.min(ZOOM_MAX, Number((value + ZOOM_STEP).toFixed(2))))}
            title="放大"
          >
            <Plus className="size-3.5" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={zoom === 1}
            onClick={() => setZoom(1)}
            title="重置缩放"
          >
            <RotateCcw className="size-3.5" />
          </Button>
        </div>

        <span className="text-[12px] text-text-secondary">
          {locating ? '定位中…' : '点击左侧卡片跳到对应页'}
        </span>
      </div>

      <div className="relative min-h-0 flex-1 overflow-auto p-3">
        {locating && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/50">
            <Loader2 className="size-5 animate-spin text-text-secondary" />
          </div>
        )}
        {imgError ? (
          <div className="flex h-full items-center justify-center text-[14px] text-text-secondary">
            页面预览加载失败。
          </div>
        ) : (
          <div className="flex min-h-full min-w-full items-start justify-center">
            <img
              key={src}
              src={src}
              alt={`报告第 ${safePage} 页`}
              className="origin-top shadow-sm"
              style={{
                width: `${zoom * 100}%`,
                maxWidth: 'none',
                height: 'auto',
              }}
              onLoad={() => setImgError(false)}
              onError={() => setImgError(true)}
            />
          </div>
        )}
      </div>
    </div>
  )
}
