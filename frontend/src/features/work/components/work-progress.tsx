import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

export interface WorkProgressProps {
  /** Percentage already done. Clamped by the progress primitive. */
  value: number
  label?: string
  className?: string
}

/**
 * Percentage plus bar. The bar is decorative — the number beside it is what
 * carries the value, so the two cannot disagree.
 */
export function WorkProgress({ value, label, className }: WorkProgressProps) {
  const clamped = Number.isFinite(value) ? Math.min(Math.max(value, 0), 100) : 0
  const rounded = Math.round(clamped)
  const variant = clamped >= 100 ? 'success' : clamped < 0 ? 'destructive' : 'default'

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <Progress
        value={clamped}
        variant={variant}
        aria-label={label ?? 'Progress'}
        className="h-1.5 flex-1"
      />
      <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {label ? `${label} ${rounded}%` : `${rounded}%`}
      </span>
    </div>
  )
}