import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cn } from '@/lib/utils'

export function Breadcrumb({ className, ...props }: React.ComponentProps<'nav'>) {
  return <nav aria-label="breadcrumb" className={cn('text-sm text-text-secondary', className)} {...props} />
}

export function BreadcrumbList({ className, ...props }: React.ComponentProps<'ol'>) {
  return <ol className={cn('flex flex-wrap items-center gap-1.5', className)} {...props} />
}

export function BreadcrumbItem({ className, ...props }: React.ComponentProps<'li'>) {
  return <li className={cn('inline-flex items-center gap-1.5', className)} {...props} />
}

export function BreadcrumbLink({
  className,
  asChild,
  ...props
}: React.ComponentProps<'a'> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'a'
  return <Comp className={cn('transition-colors hover:text-text-primary', className)} {...props} />
}

export function BreadcrumbPage({ className, ...props }: React.ComponentProps<'span'>) {
  return <span className={cn('font-medium text-text-primary', className)} {...props} />
}

export function BreadcrumbSeparator({ children = '/', className, ...props }: React.ComponentProps<'li'>) {
  return (
    <li role="presentation" aria-hidden className={cn('[&>svg]:size-3.5', className)} {...props}>
      {children}
    </li>
  )
}
