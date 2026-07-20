import type { ReactNode } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'

export function ListFilterBar({
  title,
  description,
  search,
  onSearchChange,
  searchPlaceholder = '搜索',
  leftPanel,
  rightPanel,
  className,
}: {
  title?: string
  description?: string
  search?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  leftPanel?: ReactNode
  rightPanel?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between', className)}>
      <div className="min-w-0 flex-1">
        {leftPanel || (
          <>
            {title && <h1 className="text-lg font-semibold text-text-primary">{title}</h1>}
            {description && <p className="mt-1 text-sm text-text-secondary">{description}</p>}
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {onSearchChange && (
          <label className="relative block min-w-[220px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-secondary" />
            <Input
              className="pl-9"
              value={search}
              onChange={e => onSearchChange(e.target.value)}
              placeholder={searchPlaceholder}
            />
          </label>
        )}
        {rightPanel}
      </div>
    </div>
  )
}
