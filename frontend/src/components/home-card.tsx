import type { ReactNode, MouseEvent } from 'react'
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
  titleAction,
  actions,
  className,
}: {
  title: string
  description?: string
  meta?: ReactNode
  onClick?: () => void
  /** Shown immediately to the right of the title when hovering the title. */
  titleAction?: ReactNode
  /** Top-right controls; clicks must stopPropagation so the card onClick still works. */
  actions?: ReactNode
  className?: string
}) {
  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onClick()
              }
            }
          : undefined
      }
      className={cn(
        'group relative flex flex-col gap-3.5 rounded-2xl border border-border-button bg-bg-base p-6 text-left transition hover:border-accent-primary/40 hover:bg-bg-accent',
        onClick && 'cursor-pointer',
        className,
      )}
    >
      {actions && (
        <div
          className="absolute right-3 top-3 z-10 flex items-center gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100"
          onClick={(event: MouseEvent) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {actions}
        </div>
      )}
      <div className={cn(actions && 'pr-16')}>
        <div className="group/title inline-flex max-w-full items-center gap-1">
          <h3 className="line-clamp-1 min-w-0 text-[17px] font-semibold text-text-primary">{title}</h3>
          {titleAction && (
            <div
              className="shrink-0 opacity-0 transition group-hover/title:opacity-100 focus-within:opacity-100"
              onClick={(event: MouseEvent) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {titleAction}
            </div>
          )}
        </div>
        {description && (
          <p className="mt-1.5 line-clamp-2 text-[15px] leading-relaxed text-text-secondary">
            {description || '—'}
          </p>
        )}
      </div>
      {meta && <div className="mt-auto flex flex-wrap gap-3 text-[15px] text-text-secondary">{meta}</div>}
    </div>
  )
}
