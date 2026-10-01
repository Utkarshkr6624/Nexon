import { cn } from '@/lib/utils'

export interface BrandMarkProps {
  className?: string
}

/**
 * The NEXUS mark: three stacked bars converging into a single node. Drawn with
 * `currentColor` so it inherits text colour and works in either theme.
 */
export function BrandMark({ className }: BrandMarkProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className={cn('size-5 shrink-0', className)}
    >
      <rect x="3" y="4" width="3" height="6" rx="1" fill="currentColor" opacity="0.45" />
      <rect x="3" y="14" width="3" height="6" rx="1" fill="currentColor" opacity="0.25" />
      <rect x="10.5" y="8" width="3" height="8" rx="1" fill="currentColor" opacity="0.7" />
      <circle cx="18.5" cy="12" r="3" fill="currentColor" />
    </svg>
  )
}

export interface BrandProps {
  /** Hides the wordmark, leaving only the mark. Used by the collapsed sidebar. */
  markOnly?: boolean
  className?: string
}

export function Brand({ markOnly = false, className }: BrandProps) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <span className="flex size-7 items-center justify-center rounded-md border border-border bg-muted text-foreground">
        <BrandMark />
      </span>
      {!markOnly && (
        <span className="flex flex-col leading-none">
          <span className="text-sm font-semibold tracking-tight text-foreground">NEXUS</span>
          <span className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Intelligence
          </span>
        </span>
      )}
    </span>
  )
}
