import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

export type SelectOption = {
  value: string
  label: string
  hint?: string
  group?: string
  icon?: ReactNode
}

/** RAGFlow-like searchable dropdown (single select). */
export function SearchableSelect({
  value,
  options,
  placeholder = '请选择',
  searchPlaceholder = '搜索…',
  emptyText = '没有找到数据。',
  onChange,
  className,
}: {
  value: string
  options: SelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  emptyText?: string
  onChange: (value: string) => void
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const selected = options.find(item => item.value === value)
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(item =>
      `${item.label} ${item.hint || ''} ${item.group || ''}`.toLowerCase().includes(q),
    )
  }, [options, query])

  const grouped = useMemo(() => {
    const map = new Map<string, SelectOption[]>()
    for (const item of filtered) {
      const key = item.group || ''
      const list = map.get(key) || []
      list.push(item)
      map.set(key, list)
    }
    return [...map.entries()]
  }, [filtered])

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex h-9 w-full items-center gap-2 rounded-lg border border-[#e5e7eb] bg-white px-3 text-left text-sm text-[#374151] transition hover:border-[#d1d5db]"
      >
        <span className="min-w-0 flex-1 truncate">
          {selected ? (
            <span className="inline-flex items-center gap-2">
              {selected.icon}
              <span className="text-[#111827]">{selected.label}</span>
              {selected.hint && <span className="text-[#9ca3af]">{selected.hint}</span>}
            </span>
          ) : (
            <span className="text-[#9ca3af]">{placeholder}</span>
          )}
        </span>
        <ChevronDown className={cn('size-4 shrink-0 text-[#9ca3af] transition', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute left-0 right-0 z-40 mt-1 overflow-hidden rounded-lg border border-[#e5e7eb] bg-white shadow-lg">
          <div className="border-b border-[#e5e7eb] p-2">
            <div className="flex h-8 items-center gap-2 rounded-md border border-[#e5e7eb] bg-white px-2">
              <Search className="size-3.5 text-[#9ca3af]" />
              <input
                autoFocus
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="h-full w-full bg-transparent text-sm text-[#111827] outline-none placeholder:text-[#9ca3af]"
              />
            </div>
          </div>
          <div className="max-h-56 overflow-y-auto py-1">
            {!filtered.length && (
              <div className="px-3 py-8 text-center text-sm text-[#9ca3af]">{emptyText}</div>
            )}
            {grouped.map(([group, items]) => (
              <div key={group || 'all'}>
                {group && (
                  <div className="px-3 py-1.5 text-xs font-medium text-[#6b7280]">{group}</div>
                )}
                {items.map(item => {
                  const active = item.value === value
                  return (
                    <button
                      key={item.value}
                      type="button"
                      className={cn(
                        'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition',
                        active ? 'bg-[#eff6ff] text-[#1d4ed8]' : 'text-[#111827] hover:bg-[#f9fafb]',
                      )}
                      onClick={() => {
                        onChange(item.value)
                        setOpen(false)
                        setQuery('')
                      }}
                    >
                      {item.icon}
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {item.hint && <span className="text-xs text-[#9ca3af]">{item.hint}</span>}
                      {active && <Check className="size-3.5 shrink-0 text-[#3b82f6]" />}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Multi-select dropdown showing selected count / names. */
export function SearchableMultiSelect({
  values,
  options,
  placeholder = '请选择',
  searchPlaceholder = '搜索…',
  emptyText = '没有找到数据。',
  onChange,
  className,
}: {
  values: string[]
  options: SelectOption[]
  placeholder?: string
  searchPlaceholder?: string
  emptyText?: string
  onChange: (values: string[]) => void
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const selectedSet = useMemo(() => new Set(values), [values])

  useEffect(() => {
    if (!open) return
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(item =>
      `${item.label} ${item.hint || ''}`.toLowerCase().includes(q),
    )
  }, [options, query])

  const summary = useMemo(() => {
    if (!values.length) return ''
    const labels = values
      .map(id => options.find(item => item.value === id)?.label || id)
      .filter(Boolean)
    if (labels.length <= 2) return labels.join('、')
    return `${labels.slice(0, 2).join('、')} 等 ${labels.length} 个`
  }, [values, options])

  const toggle = (id: string) => {
    const next = new Set(selectedSet)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onChange([...next])
  }

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex h-9 w-full items-center gap-2 rounded-lg border border-[#e5e7eb] bg-white px-3 text-left text-sm transition hover:border-[#d1d5db]"
      >
        <span className={cn('min-w-0 flex-1 truncate', summary ? 'text-[#111827]' : 'text-[#9ca3af]')}>
          {summary || placeholder}
        </span>
        <ChevronDown className={cn('size-4 shrink-0 text-[#9ca3af] transition', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute left-0 right-0 z-40 mt-1 overflow-hidden rounded-lg border border-[#e5e7eb] bg-white shadow-lg">
          <div className="border-b border-[#e5e7eb] p-2">
            <div className="flex h-8 items-center gap-2 rounded-md border border-[#e5e7eb] px-2">
              <Search className="size-3.5 text-[#9ca3af]" />
              <input
                autoFocus
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="h-full w-full bg-transparent text-sm outline-none placeholder:text-[#9ca3af]"
              />
            </div>
          </div>
          <div className="max-h-56 overflow-y-auto py-1">
            {!filtered.length && (
              <div className="px-3 py-8 text-center text-sm text-[#9ca3af]">{emptyText}</div>
            )}
            {filtered.map(item => {
              const active = selectedSet.has(item.value)
              return (
                <button
                  key={item.value}
                  type="button"
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition',
                    active ? 'bg-[#eff6ff]' : 'hover:bg-[#f9fafb]',
                  )}
                  onClick={() => toggle(item.value)}
                >
                  <span
                    className={cn(
                      'grid size-4 shrink-0 place-items-center rounded border',
                      active
                        ? 'border-[#3b82f6] bg-[#3b82f6] text-white'
                        : 'border-[#d1d5db] bg-white',
                    )}
                  >
                    {active && <Check className="size-3" />}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[#111827]">{item.label}</span>
                  {item.hint && <span className="text-xs text-[#9ca3af]">{item.hint}</span>}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
