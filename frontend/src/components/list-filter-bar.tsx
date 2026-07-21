import type { ReactNode } from 'react'
import { Search } from 'lucide-react'
import { Explain } from '@/components/explain'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'

export function ListFilterBar({
  title,
  description,
  titleHelp,
  search,
  onSearchChange,
  searchPlaceholder = '搜索',
  searchHelp,
  leftPanel,
  rightPanel,
  className,
}: {
  title?: string
  description?: string
  titleHelp?: string
  search?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  searchHelp?: string
  leftPanel?: ReactNode
  rightPanel?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between', className)}>
      <div className="min-w-0 flex-1">
        {leftPanel || (
          <>
            {title && (
              titleHelp
                ? (
                  <Explain text={titleHelp} title={title}>
                    <h1 className="text-[18px] font-semibold tracking-tight text-text-primary">{title}</h1>
                  </Explain>
                )
                : <h1 className="text-[18px] font-semibold tracking-tight text-text-primary">{title}</h1>
            )}
            {description && <p className="mt-1.5 text-[15px] leading-relaxed text-text-secondary">{description}</p>}
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {onSearchChange && (
          <Explain text={searchHelp || '输入关键词筛选当前列表。'} title="搜索">
            <label className="relative block min-w-[220px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-secondary" />
              <Input
                className="pl-9"
                value={search}
                onChange={e => onSearchChange(e.target.value)}
                placeholder={searchPlaceholder}
              />
            </label>
          </Explain>
        )}
        {rightPanel}
      </div>
    </div>
  )
}
