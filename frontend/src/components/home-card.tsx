import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function CardContainer({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4', className)}>
      {children}
    </div>
  )
}

export function HomeCard({
  title,
  description,
  meta,
  onClick,
  className,
}: {
  title: string
  description?: string
  meta?: ReactNode
  onClick?: () => void
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex flex-col gap-3 rounded-xl border border-border-button bg-bg-base p-5 text-left transition hover:border-accent-primary/40 hover:bg-bg-accent',
        className,
      )}
    >
      <div>
        <h3 className="line-clamp-1 text-base font-semibold text-text-primary">{title}</h3>
        {description && <p className="mt-1 line-clamp-2 text-sm text-text-secondary">{description || '—'}</p>}
      </div>
      {meta && <div className="mt-auto flex flex-wrap gap-3 text-xs text-text-secondary">{meta}</div>}
    </button>
  )
}
