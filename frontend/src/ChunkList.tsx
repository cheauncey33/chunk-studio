import { useMemo } from 'react'
import type { Chunk } from './api'

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

export function ChunkList({ chunks, selectedId, activeKind, onKindChange, onSelect, onDelete }: Props) {
  const counts = useMemo(() => {
    return chunks.reduce<Record<ChunkKind, number>>((acc, chunk) => {
      acc[chunkKind(chunk)] += 1
      return acc
    }, { manual: 0, table: 0, image: 0, section: 0 })
  }, [chunks])
  const visibleChunks = chunks.filter(chunk => chunkKind(chunk) === activeKind)

  return (
    <div className="chunk-list">
      <h3>当前文件切片 ({visibleChunks.length}/{chunks.length})</h3>
      <div className="chunk-type-tabs">
        {FILTERS.map(item => (
          <button
            key={item.key}
            className={activeKind === item.key ? 'on' : ''}
            onClick={() => onKindChange(item.key)}
          >
            <span>{item.label}</span>
            <b>{counts[item.key]}</b>
          </button>
        ))}
      </div>
      {chunks.length === 0 && <div className="muted">在页面上拖拽框选，创建第一个切片</div>}
      {chunks.length > 0 && visibleChunks.length === 0 && (
        <div className="muted">当前类型还没有切片</div>
      )}
      {visibleChunks.map(chunk => (
        <div
          key={chunk.id}
          className={`chunk-item ${chunk.id === selectedId ? 'selected' : ''}`}
          onClick={() => onSelect(chunk.id)}
        >
          <div className="ci-head">
            <span>P{chunk.page}</span>
            <span className={`type-pill ${chunkKind(chunk)}`}>
              {chunkTypeLabel(chunk)}
            </span>
            <span className={`src ${sourceLabel(chunk)}`}>{sourceLabel(chunk)}</span>
            {!isAutoChunk(chunk) && chunk.ocr_status && (
              <span className={`job-status ${chunk.ocr_status}`}>ocr:{chunk.ocr_status}</span>
            )}
            <button
              className="chunk-list-delete"
              title="删除切片"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(chunk.id)
              }}
            >
              ×
            </button>
          </div>
          <div className="ci-text">{chunkSummary(chunk)}</div>
        </div>
      ))}
    </div>
  )
}

export function chunkKind(chunk: Chunk): ChunkKind {
  const type = String(chunk.metadata?.content_type || '')
  if (type === 'section') return 'section'
  if (type === 'table') return 'table'
  if (type === 'image') return 'image'
  return 'manual'
}

function chunkTypeLabel(chunk: Chunk): string {
  const kind = chunkKind(chunk)
  if (kind === 'section') return '标题'
  if (kind === 'table') return '表格'
  if (kind === 'image') return '图片'
  return '手动'
}

function isAutoChunk(chunk: Chunk): boolean {
  return Boolean(chunk.metadata?.auto_source)
}

function sourceLabel(chunk: Chunk): string {
  return isAutoChunk(chunk) ? 'auto' : chunk.text_source
}

function chunkSummary(chunk: Chunk): string {
  const meta = chunk.metadata || {}
  if (meta.content_type === 'section') {
    const section = String(meta.section || '')
    const title = String(meta.section_title || '')
    const childRange = summarizeSectionRefs(meta.child_sections)
    const figs = summarizeRefs(meta.figure_ref, '图')
    const tables = summarizeRefs(meta.table_ref, '表')
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
    const title = meta.table_header ? String(meta.table_header) : ''
    return `${no} ${title}`.trim() || chunk.text?.slice(0, 80) || '无文本'
  }
  if (meta.content_type === 'image') {
    const no = meta.figure_no ? `图 ${meta.figure_no}` : '图片'
    const title = meta.figure_header ? String(meta.figure_header) : ''
    return `${no} ${title}`.trim() || chunk.text?.slice(0, 80) || '无文本'
  }
  return chunk.text?.slice(0, 80) || '无文本'
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
