import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface PageHeaderProps {
  title: string
  description?: string
  /** Small label above the title, e.g. the sidebar group. */
  eyebrow?: ReactNode
  /** Status chips rendered next to the title. */
  badges?: ReactNode
  /** Buttons on the right of the header, or below it on narrow screens. */
  actions?: ReactNode
  className?: string
}

/**
 * Standard page masthead. Every route renders one so vertical rhythm is
 * identical across the product.
 */
export function PageHeader({
  title,
  description,
  eyebrow,
  badges,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        'flex flex-col gap-4 border-b border-border pb-6 sm:flex-row sm:items-start sm:justify-between',
        className,
      )}
    >
      <div className="min-w-0 space-y-2">
        {eyebrow && (
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {eyebrow}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
          {badges}
        </div>
        {description && (
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>

      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}
