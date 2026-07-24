import { useEffect, useMemo, useRef, useState } from 'react'
import { FoldVertical, Pencil, Trash2, UnfoldVertical } from 'lucide-react'
import { toast } from 'sonner'
import { api, type Chunk } from '@/api'
import { chunkKind as schemaChunkKind, getBusinessMetadata, getRelations, isAutoChunk } from '@/chunkSchema'
import { Explain } from '@/components/explain'
import { Badge } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
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
  onEdit: (id: string) => void
  onDelete: (ids: string | string[]) => void
  onChunkUpdated: (chunk: Chunk) => void
}

type ListItem =
  | { type: 'single'; chunk: Chunk }
  | {
      type: 'table_group'
      key: string
      tableNo: string
      title: string
      members: Chunk[]
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

export function ChunkList({
  chunks,
  selectedId,
  activeKind,
  onKindChange,
  onSelect,
  onEdit,
  onDelete,
  onChunkUpdated,
}: Props) {
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})
  const [checkedIds, setCheckedIds] = useState<Set<string>>(() => new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const selectAllRef = useRef<HTMLInputElement>(null)
  const counts = useMemo(() => {
    return chunks.reduce<Record<ChunkKind, number>>((acc, chunk) => {
      acc[chunkKind(chunk)] += 1
      return acc
    }, { manual: 0, table: 0, image: 0, section: 0 })
  }, [chunks])
  const visibleChunks = useMemo(
    () => chunks.filter(chunk => chunkKind(chunk) === activeKind),
    [chunks, activeKind],
  )
  const visibleIds = useMemo(() => visibleChunks.map(chunk => chunk.id), [visibleChunks])
  const listItems = useMemo(() => buildListItems(visibleChunks), [visibleChunks])
  const checkedVisibleCount = visibleIds.filter(id => checkedIds.has(id)).length
  const allVisibleChecked = visibleIds.length > 0 && checkedVisibleCount === visibleIds.length
  const someVisibleChecked = checkedVisibleCount > 0 && !allVisibleChecked
  const checkedChunks = useMemo(
    () => visibleChunks.filter(chunk => checkedIds.has(chunk.id)),
    [visibleChunks, checkedIds],
  )

  useEffect(() => {
    setCheckedIds(new Set())
  }, [activeKind])

  useEffect(() => {
    setCheckedIds(prev => {
      const visible = new Set(visibleIds)
      let changed = false
      const next = new Set<string>()
      for (const id of prev) {
        if (visible.has(id)) next.add(id)
        else changed = true
      }
      return changed ? next : prev
    })
  }, [visibleIds])

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleChecked
    }
  }, [someVisibleChecked])

  // Only auto-expand when the selected chunk changes into a group — never fight a manual collapse.
  useEffect(() => {
    if (!selectedId) return
    const group = listItems.find(
      item => item.type === 'table_group' && item.members.some(member => member.id === selectedId),
    )
    if (!group || group.type !== 'table_group') return
    setExpandedGroups(prev => (prev[group.key] ? prev : { ...prev, [group.key]: true }))
  }, [selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleChecked = (id: string, checked: boolean) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const toggleGroupChecked = (members: Chunk[], checked: boolean) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      for (const member of members) {
        if (checked) next.add(member.id)
        else next.delete(member.id)
      }
      return next
    })
  }

  const toggleSelectAllVisible = (checked: boolean) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      for (const id of visibleIds) {
        if (checked) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }

  const toggleEnabled = async (chunk: Chunk, enabled: boolean) => {
    setTogglingId(chunk.id)
    try {
      let updated = chunk
      if (enabled) {
        if (chunk.status === 'rejected') {
          updated = await api.updateChunk(chunk.id, { status: 'pending' })
        }
        if (updated.status !== 'approved') {
          updated = await api.updateChunk(chunk.id, { status: 'approved' })
        }
      } else if (chunk.status === 'approved') {
        updated = await api.updateChunk(chunk.id, { status: 'pending' })
      } else if (chunk.status === 'reviewed') {
        updated = await api.updateChunk(chunk.id, { status: 'pending' })
      }
      onChunkUpdated(updated)
    } catch (error) {
      toast.error('更新启用状态失败: ' + (error as Error).message)
    } finally {
      setTogglingId(null)
    }
  }

  const toggleGroupEnabled = async (members: Chunk[], enabled: boolean) => {
    setTogglingId(members[0]?.id || null)
    try {
      for (const chunk of members) {
        const needsOn = enabled && chunk.status !== 'approved'
        const needsOff = !enabled && (chunk.status === 'approved' || chunk.status === 'reviewed')
        if (!needsOn && !needsOff) continue
        let updated = chunk
        if (enabled) {
          if (chunk.status === 'rejected') {
            updated = await api.updateChunk(chunk.id, { status: 'pending' })
          }
          if (updated.status !== 'approved') {
            updated = await api.updateChunk(updated.id, { status: 'approved' })
          }
        } else {
          updated = await api.updateChunk(chunk.id, { status: 'pending' })
        }
        onChunkUpdated(updated)
      }
    } catch (error) {
      toast.error('更新启用状态失败: ' + (error as Error).message)
    } finally {
      setTogglingId(null)
    }
  }

  const bulkSetEnabled = async (enabled: boolean) => {
    if (!checkedChunks.length) return
    setBulkBusy(true)
    try {
      for (const chunk of checkedChunks) {
        const needsOn = enabled && chunk.status !== 'approved'
        const needsOff = !enabled && (chunk.status === 'approved' || chunk.status === 'reviewed')
        if (!needsOn && !needsOff) continue
        let updated = chunk
        if (enabled) {
          if (chunk.status === 'rejected') {
            updated = await api.updateChunk(chunk.id, { status: 'pending' })
          }
          if (updated.status !== 'approved') {
            updated = await api.updateChunk(updated.id, { status: 'approved' })
          }
        } else {
          updated = await api.updateChunk(chunk.id, { status: 'pending' })
        }
        onChunkUpdated(updated)
      }
      toast.success(enabled ? `已启用 ${checkedChunks.length} 段` : `已停用 ${checkedChunks.length} 段`)
    } catch (error) {
      toast.error('批量更新失败: ' + (error as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }

  const renderChunkCard = (
    chunk: Chunk,
    opts?: {
      nested?: boolean
      expand?: { expanded: boolean; onToggle: () => void }
      pageLabel?: string
      summary?: string
      enableChecked?: boolean
      onEnableChange?: (enabled: boolean) => void
      enableBusy?: boolean
      checkIds?: string[]
      onCheckChange?: (checked: boolean) => void
    },
  ) => {
    const selected = chunk.id === selectedId
    const ocr = !isAutoChunk(chunk) && chunk.ocr_status ? chunk.ocr_status : null
    const enabled = opts?.enableChecked ?? (chunk.status === 'approved')
    const enableBusy = opts?.enableBusy ?? (togglingId === chunk.id)
    const checkIds = opts?.checkIds || [chunk.id]
    const checked = checkIds.every(id => checkedIds.has(id))
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
          opts?.nested && 'rounded-lg',
          selected
            ? 'border-accent-primary bg-bg-accent shadow-[inset_3px_0_0_rgb(var(--accent-primary))]'
            : 'border-border-button bg-bg-base hover:border-accent-primary/40 hover:bg-bg-accent/50',
        )}
      >
        <div className="flex items-start gap-2">
          <label
            className="mt-1 flex shrink-0 items-center"
            onClick={e => e.stopPropagation()}
            onKeyDown={e => e.stopPropagation()}
          >
            <input
              type="checkbox"
              className="size-3.5 accent-[rgb(var(--accent-primary))]"
              checked={checked}
              onChange={event => {
                if (opts?.onCheckChange) {
                  opts.onCheckChange(event.target.checked)
                  return
                }
                toggleChecked(chunk.id, event.target.checked)
              }}
              aria-label={`选择 ${opts?.summary || chunkSummary(chunk)}`}
            />
          </label>
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-semibold tabular-nums text-text-secondary">
                {opts?.pageLabel || `第${chunk.page}页`}
              </span>
              <Badge variant="secondary">{chunkTypeLabel(chunk)}</Badge>
              {isContinuedTable(chunk) && <Badge variant="secondary">续表</Badge>}
              <Badge variant={STATUS_VARIANT[chunk.status] || 'secondary'}>{STATUS_LABEL[chunk.status] || chunk.status}</Badge>
            </div>
            <p className="line-clamp-2 text-sm leading-snug text-text-primary">
              {opts?.summary || chunkSummary(chunk)}
            </p>
            {ocr && (
              <div className="flex flex-wrap gap-2 text-[11px] text-text-secondary">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="cursor-default">识别 {ocr}</span>
                  </TooltipTrigger>
                  <TooltipContent>{chunk.ocr_error || `文字识别状态：${ocr}`}</TooltipContent>
                </Tooltip>
              </div>
            )}
            {opts?.expand && (
              <button
                type="button"
                className="inline-flex origin-left scale-[0.7] items-center rounded px-0.5 text-[10px] font-normal leading-none text-text-secondary/80 transition hover:bg-bg-card hover:text-text-primary"
                title={opts.expand.expanded ? '折叠各页片段' : '展开各页片段'}
                aria-label={opts.expand.expanded ? '折叠各页片段' : '展开各页片段'}
                aria-expanded={opts.expand.expanded}
                onClick={e => {
                  e.stopPropagation()
                  opts.expand?.onToggle()
                }}
              >
                {opts.expand.expanded ? '收起' : '展开'}
              </button>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <Explain text={helpText.chunkStudio.enableChunk} title="启用检索">
              <div
                className="flex items-center gap-1.5"
                onClick={e => e.stopPropagation()}
                onKeyDown={e => e.stopPropagation()}
              >
                <span className="text-[11px] text-text-secondary">{enabled ? '启用' : '停用'}</span>
                <Switch
                  checked={enabled}
                  disabled={enableBusy || bulkBusy}
                  onCheckedChange={checked => {
                    if (opts?.onEnableChange) {
                      void opts.onEnableChange(checked)
                      return
                    }
                    void toggleEnabled(chunk, checked)
                  }}
                  title={enabled ? '已启用检索' : '未启用检索'}
                />
              </div>
            </Explain>
            <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
              <Explain text={helpText.chunkStudio.editChunk} title="修改">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  title="修改"
                  onClick={e => {
                    e.stopPropagation()
                    onEdit(chunk.id)
                  }}
                >
                  <Pencil className="size-3.5" />
                </Button>
              </Explain>
              <Explain text={helpText.chunkStudio.deleteChunk} title="删除">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-8"
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
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 space-y-2 border-b border-border-button bg-bg-base px-3 py-2">
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

        {(() => {
          const tableGroups = listItems.filter(item => item.type === 'table_group')
          const anyExpanded = tableGroups.some(item => item.type === 'table_group' && expandedGroups[item.key])
          const setAllExpanded = (expanded: boolean) => {
            setExpandedGroups(prev => {
              const next = { ...prev }
              for (const item of tableGroups) {
                if (item.type === 'table_group') next[item.key] = expanded
              }
              return next
            })
          }
          return (
            <div className="flex items-center justify-between gap-2 px-0.5">
              <label className="flex items-center gap-2 text-xs text-text-secondary">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  className="size-3.5 accent-[rgb(var(--accent-primary))]"
                  checked={allVisibleChecked}
                  disabled={!visibleIds.length || bulkBusy}
                  onChange={event => toggleSelectAllVisible(event.target.checked)}
                  aria-label="全选当前类型"
                />
                <span>{checkedVisibleCount > 0 ? `已选 ${checkedVisibleCount}` : '全选'}</span>
              </label>
              <div className="flex items-center gap-1">
                {checkedVisibleCount > 0 && (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={bulkBusy}
                      onClick={() => void bulkSetEnabled(true)}
                    >
                      启用
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={bulkBusy}
                      onClick={() => void bulkSetEnabled(false)}
                    >
                      停用
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      disabled={bulkBusy}
                      onClick={() => onDelete(checkedChunks.map(chunk => chunk.id))}
                    >
                      删除
                    </Button>
                  </>
                )}
                {tableGroups.length > 0 && (
                  <button
                    type="button"
                    className="inline-flex size-7 items-center justify-center rounded-md text-text-secondary transition hover:bg-bg-card hover:text-text-primary"
                    title={anyExpanded ? '全部折叠同表' : '全部展开同表'}
                    aria-label={anyExpanded ? '全部折叠同表' : '全部展开同表'}
                    onClick={() => setAllExpanded(!anyExpanded)}
                  >
                    {anyExpanded
                      ? <FoldVertical className="size-3.5" />
                      : <UnfoldVertical className="size-3.5" />}
                  </button>
                )}
              </div>
            </div>
          )
        })()}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-2 p-3">
          {chunks.length === 0 && (
            <p className="px-1 py-6 text-center text-sm text-text-secondary">在 PDF 上拖出方框，创建第一段内容</p>
          )}
          {chunks.length > 0 && visibleChunks.length === 0 && (
            <p className="px-1 py-6 text-center text-sm text-text-secondary">当前类型还没有内容</p>
          )}

          {listItems.map(item => {
            if (item.type === 'single') {
              return renderChunkCard(item.chunk)
            }

            const expanded = Boolean(expandedGroups[item.key])
            const primary = item.members[0]
            const pages = item.members.map(member => member.page)
            const pageLabel = pages.length
              ? pages[0] === pages[pages.length - 1]
                ? `第${pages[0]}页`
                : `第${pages[0]}–${pages[pages.length - 1]}页`
              : `第${primary.page}页`
            const summary = `表 ${item.tableNo}${item.title ? ` ${item.title}` : ''}`.trim()
            const allEnabled = item.members.every(member => member.status === 'approved')
            const groupBusy = item.members.some(member => togglingId === member.id)

            return (
              <div key={item.key} className="space-y-1.5">
                {renderChunkCard(primary, {
                  pageLabel,
                  summary,
                  enableChecked: allEnabled,
                  enableBusy: groupBusy,
                  onEnableChange: enabled => void toggleGroupEnabled(item.members, enabled),
                  checkIds: item.members.map(member => member.id),
                  onCheckChange: checked => toggleGroupChecked(item.members, checked),
                  expand: {
                    expanded,
                    onToggle: () => {
                      setExpandedGroups(prev => ({ ...prev, [item.key]: !expanded }))
                    },
                  },
                })}
                {expanded && item.members.length > 1 && (
                  <div className="ml-3 space-y-1.5 border-l-2 border-border-button pl-2">
                    {item.members.slice(1).map(member => renderChunkCard(member, { nested: true }))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}

export function chunkKind(chunk: Chunk): ChunkKind {
  return schemaChunkKind(chunk)
}

function buildListItems(chunks: Chunk[]): ListItem[] {
  const byTable = new Map<string, Chunk[]>()
  for (const chunk of chunks) {
    const tableNo = tableGroupKey(chunk)
    if (!tableNo) continue
    const members = byTable.get(tableNo) || []
    members.push(chunk)
    byTable.set(tableNo, members)
  }

  const emitted = new Set<string>()
  const items: ListItem[] = []
  for (const chunk of chunks) {
    const tableNo = tableGroupKey(chunk)
    const members = tableNo ? byTable.get(tableNo) : undefined
    if (tableNo && members && members.length > 1) {
      if (emitted.has(tableNo)) continue
      emitted.add(tableNo)
      const ordered = [...members].sort((a, b) => a.page - b.page || a.created_at.localeCompare(b.created_at))
      const title = ordered
        .map(member => String(getBusinessMetadata(member).table_title || '').trim())
        .find(value => value && value !== '(续)' && !value.includes('续')) || ''
      items.push({
        type: 'table_group',
        key: tableNo,
        tableNo,
        title,
        members: ordered,
      })
      continue
    }
    items.push({ type: 'single', chunk })
  }
  return items
}

function tableGroupKey(chunk: Chunk): string {
  const meta = getBusinessMetadata(chunk)
  if (meta.content_type !== 'table') return ''
  const tableNo = String(meta.table_no || '').trim()
  return tableNo
}

function isContinuedTable(chunk: Chunk): boolean {
  const meta = getBusinessMetadata(chunk)
  if (meta.table_kind === 'continued_table') return true
  const split = meta.split
  if (split && typeof split === 'object' && (split as Record<string, unknown>).reason === 'continued_table') {
    return true
  }
  return false
}

function chunkTypeLabel(chunk: Chunk): string {
  const kind = chunkKind(chunk)
  if (kind === 'section') return '标题'
  if (kind === 'table') return '表格'
  if (kind === 'image') return '图片'
  return '手动'
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
