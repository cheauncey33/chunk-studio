import type { ReactNode } from 'react'
import { CircleHelp } from 'lucide-react'
import { useHelpMode } from '@/components/help-mode'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

/** 说明模式下显示「?」，点开看白话解释；关闭说明模式时几乎不占空间。 */
export function Explain({
  text,
  title,
  children,
  className,
  side = 'top',
}: {
  text: string
  title?: string
  children?: ReactNode
  className?: string
  side?: 'top' | 'right' | 'bottom' | 'left'
}) {
  const { enabled } = useHelpMode()

  if (!enabled) {
    return children ? <>{children}</> : null
  }

  return (
    <span className={cn('inline-flex max-w-full items-center gap-1', className)}>
      {children}
      <Tooltip delayDuration={80}>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25"
            aria-label={title ? `${title}说明` : '说明'}
            onClick={e => e.preventDefault()}
          >
            <CircleHelp className="size-3" />
          </button>
        </TooltipTrigger>
        <TooltipContent
          side={side}
          className="max-w-xs whitespace-normal break-words bg-bg-base px-3 py-2 text-left text-xs leading-relaxed text-text-primary shadow-lg ring-1 ring-border-button"
        >
          {title && <div className="mb-1 font-semibold text-text-primary">{title}</div>}
          <div className="text-text-secondary">{text}</div>
        </TooltipContent>
      </Tooltip>
    </span>
  )
}

/** 区块标题旁的说明（说明模式开启时出现提示条风格标题）。 */
export function ExplainHeading({
  title,
  text,
  as: Tag = 'h1',
  className,
}: {
  title: string
  text: string
  as?: 'h1' | 'h2' | 'h3' | 'div'
  className?: string
}) {
  return (
    <Explain text={text} title={title} className={className}>
      <Tag className={cn(Tag === 'h1' && 'text-lg font-semibold', Tag === 'h2' && 'text-base font-semibold', Tag === 'h3' && 'text-sm font-semibold')}>
        {title}
      </Tag>
    </Explain>
  )
}

export function HelpModeBanner() {
  const { enabled } = useHelpMode()
  if (!enabled) return null
  return (
    <div className="border-b border-accent-primary/30 bg-bg-accent px-4 py-2 text-center text-xs text-text-primary">
      说明模式已开启：看到带 <span className="mx-0.5 inline-flex size-3.5 items-center justify-center rounded-full bg-accent-primary/20 text-[10px] text-accent-primary">?</span> 的地方，把鼠标移上去就能看到白话解释。
    </div>
  )
}
