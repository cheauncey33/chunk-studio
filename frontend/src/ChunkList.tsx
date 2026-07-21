import { useMemo } from 'react'
import { Trash2 } from 'lucide-react'
import type { Chunk } from '@/api'
import { chunkKind as schemaChunkKind, getBusinessMetadata, getRelations, isAutoChunk } from '@/chunkSchema'
import { Explain } from '@/components/explain'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { helpText } from '@/lib/help-text'
import { cn } from '@/lib/utils'

export type ChunkKind = 'manual' | 'table' | 'image' | 'section'

interface Props {
  chunks: Chunk[]
  selectedId: string | null
  activeKind: ChunkKind
  onKindChange: (kind: ChunkKind) => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

const FILTERS: Array<{ key: ChunkKind; label: string }> = [
  { key: 'manual', label: '手动' },
  { key: 'table', label: '表格' },
  { key: 'image', label: '图片' },
  { key: 'section', label: '标题' },
]

const STATUS_VARIANT: Record<string, 'secondary' | 'success' | 'warning' | 'error'> = {
  pending: 'warning',
  reviewed: 'secondary',
  approved: 'success',
  rejected: 'error',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  reviewed: '已复核',
  approved: '已批准',
  rejected: '已驳回',
}

export function ChunkList({ chunks, selectedId, activeKind, onKindChange, onSelect, onDelete }: Props) {
  const counts = useMemo(() => {
    return chunks.reduce<Record<ChunkKind, number>>((acc, chunk) => {
      acc[chunkKind(chunk)] += 1
      return acc
    }, { manual: 0, table: 0, image: 0, section: 0 })
  }, [chunks])
  const visibleChunks = chunks.filter(chunk => chunkKind(chunk) === activeKind)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 px-0.5">
        <Explain text={helpText.chunkStudio.list} title="内容列表">
          <h3 className="text-sm font-semibold text-text-primary">
            内容片段 <span className="font-normal text-text-secondary">{visibleChunks.length}/{chunks.length}</span>
          </h3>
        </Explain>
      </div>

      <Explain text={helpText.chunkStudio.filterKind} title="按类型筛选" className="w-full">
        <div className="inline-flex rounded-lg bg-bg-card p-1">
          {FILTERS.map(item => (
            <button
              key={item.key}
              type="button"
              className={cn(
                'rounded-md px-2.5 py-1.5 text-xs font-medium transition',
                activeKind === item.key
                  ? 'bg-bg-base text-text-primary shadow-sm'
                  : 'text-text-secondary hover:text-text-primary',
              )}
              onClick={() => onKindChange(item.key)}
            >
              {item.label}
              <span className="ml-1 tabular-nums text-text-secondary">{counts[item.key]}</span>
            </button>
          ))}
        </div>
      </Explain>

      {chunks.length === 0 && (
        <p className="px-1 py-6 text-center text-sm text-text-secondary">在 PDF 上拖出方框，创建第一段内容</p>
      )}
      {chunks.length > 0 && visibleChunks.length === 0 && (
        <p className="px-1 py-6 text-center text-sm text-text-secondary">当前类型还没有内容</p>
      )}

      <div className="space-y-2">
        {visibleChunks.map(chunk => {
          const selected = chunk.id === selectedId
          const source = sourceLabel(chunk)
          const ocr = !isAutoChunk(chunk) && chunk.ocr_status ? chunk.ocr_status : null
          return (
            <div
              key={chunk.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(chunk.id)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(chunk.id)
                }
              }}
              className={cn(
                'group cursor-pointer rounded-xl border px-3 py-2.5 text-left transition',
                selected
                  ? 'border-accent-primary bg-bg-accent shadow-[inset_3px_0_0_rgb(var(--accent-primary))]'
                  : 'border-border-button bg-bg-base hover:border-accent-primary/40 hover:bg-bg-accent/50',
              )}
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-semibold tabular-nums text-text-secondary">第{chunk.page}页</span>
                    <Badge variant="secondary">{chunkTypeLabel(chunk)}</Badge>
                    <Badge variant={STATUS_VARIANT[chunk.status] || 'secondary'}>{STATUS_LABEL[chunk.status] || chunk.status}</Badge>
                  </div>
                  <p className="line-clamp-2 text-sm leading-snug text-text-primary">
                    {chunkSummary(chunk)}
                  </p>
                  <div className="flex flex-wrap gap-2 text-[11px] text-text-secondary">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-default">来源 {source}</span>
                      </TooltipTrigger>
                      <TooltipContent>{isAutoChunk(chunk) ? '系统自动切出的段落' : `文字来源：${source}`}</TooltipContent>
                    </Tooltip>
                    {ocr && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="cursor-default">识别 {ocr}</span>
                        </TooltipTrigger>
                        <TooltipContent>{chunk.ocr_error || `文字识别状态：${ocr}`}</TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </div>
                <Explain text={helpText.chunkStudio.deleteChunk} title="删除">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0 opacity-0 transition group-hover:opacity-100"
                    title="删除"
                    onClick={e => {
                      e.stopPropagation()
                      onDelete(chunk.id)
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </Explain>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function chunkKind(chunk: Chunk): ChunkKind {
  return schemaChunkKind(chunk)
}

function chunkTypeLabel(chunk: Chunk): string {
  const kind = chunkKind(chunk)
  if (kind === 'section') return '标题'
  if (kind === 'table') return '表格'
  if (kind === 'image') return '图片'
  return '手动'
}

function sourceLabel(chunk: Chunk): string {
  if (isAutoChunk(chunk)) return '自动'
  if (chunk.text_source === 'digital') return 'PDF 文字'
  if (chunk.text_source === 'manual') return '人工'
  if (chunk.text_source === 'pending') return '待识别'
  return chunk.text_source
}

function chunkSummary(chunk: Chunk): string {
  const meta = getBusinessMetadata(chunk)
  const relationRefs = relationReferences(chunk)
  if (meta.content_type === 'section') {
    const section = String(meta.section || '')
    const title = String(meta.section_title || '')
    const childRange = summarizeSectionRefs(relationChildren(chunk))
    const figs = summarizeRefs(relationRefs.figure, '图')
    const tables = summarizeRefs(relationRefs.table, '表')
    const parts = [
      section && `${section} ${title}`.trim(),
      childRange && `包含 ${childRange}`,
      figs && `图片 ${figs}`,
      tables && `表 ${tables}`,
    ]
    return parts.filter(Boolean).join(' · ') || chunk.text?.slice(0, 80) || '无文本'
  }
  if (meta.content_type === 'table') {
    const no = meta.table_no ? `表 ${meta.table_no}` : '表格'
    const title = meta.table_title ? String(meta.table_title) : ''
    return `${no} ${title}`.trim() || chunk.text?.slice(0, 80) || '无文本'
  }
  if (meta.content_type === 'image') {
    const no = meta.figure_no ? `图 ${meta.figure_no}` : '图片'
    const title = meta.figure_title ? String(meta.figure_title) : ''
    return `${no} ${title}`.trim() || chunk.text?.slice(0, 80) || '无文本'
  }
  return chunk.text?.slice(0, 80) || '无文本'
}

function relationChildren(chunk: Chunk): string[] {
  const children = getRelations(chunk).children
  if (!Array.isArray(children)) return []
  return children
    .map(child => {
      if (!child || typeof child !== 'object') return ''
      const record = child as Record<string, unknown>
      return typeof record.section === 'string' ? record.section : ''
    })
    .filter(Boolean)
}

function relationReferences(chunk: Chunk): Record<'table' | 'figure', string[]> {
  const refs = getRelations(chunk).references
  const out: Record<'table' | 'figure', string[]> = { table: [], figure: [] }
  if (!Array.isArray(refs)) return out
  refs.forEach(ref => {
    if (!ref || typeof ref !== 'object') return
    const record = ref as Record<string, unknown>
    const kind = record.kind === 'figure' ? 'figure' : record.kind === 'table' ? 'table' : null
    const no = record.no ?? record.target_no
    if (kind && no != null) out[kind].push(String(no))
  })
  return out
}

function summarizeRefs(value: unknown, prefix = ''): string {
  const list = Array.isArray(value) ? value.map(String).filter(Boolean) : []
  if (!list.length) return ''
  const numbered = list.map(v => Number(v)).filter(n => Number.isFinite(n))
  if (numbered.length === list.length && numbered.length > 1) {
    const sorted = [...numbered].sort((a, b) => a - b)
    const consecutive = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1)
    if (consecutive) return `${prefix}${sorted[0]}-${prefix}${sorted[sorted.length - 1]}`
  }
  return list.map(v => `${prefix}${v}`).join('、')
}

function summarizeSectionRefs(value: unknown): string {
  const list = Array.isArray(value) ? value.map(String).filter(Boolean) : []
  if (!list.length) return ''
  if (list.length > 1) {
    const first = list[0]
    const last = list[list.length - 1]
    const firstParent = first.split('.').slice(0, -1).join('.')
    const lastParent = last.split('.').slice(0, -1).join('.')
    const firstNo = Number(first.split('.').at(-1))
    const lastNo = Number(last.split('.').at(-1))
    const consecutive = list.every((item, idx) => {
      const parts = item.split('.')
      return parts.slice(0, -1).join('.') === firstParent && Number(parts.at(-1)) === firstNo + idx
    })
    if (firstParent && firstParent === lastParent && Number.isFinite(firstNo) && Number.isFinite(lastNo) && consecutive) {
      return `${first}-${last}`
    }
  }
  return list.join('、')
}
