import { CircleAlert, CircleHelp, TriangleAlert } from 'lucide-react'

import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { formatMinutes } from '@/features/planner/datetime'

export interface OverloadMeterProps {
  /**
   * `null` means the user has declared no availability for this day. That is a
   * different answer from `0` and is never rendered as a zero-length bar.
   */
  availableMinutes: number | null
  scheduledMinutes: number
  /** `null` whenever availability is undeclared. */
  overloadMinutes?: number | null
  /** `scheduled ÷ available`; `null` when it cannot be expressed. */
  ratio?: number | null
  overloaded?: boolean
  /** Names the day, e.g. `Wednesday`. */
  label?: string
  className?: string
}

/**
 * Capacity against commitments.
 *
 * **The overload state carries an icon and words, never colour alone**, and the
 * unknown state is a sentence rather than an empty bar: a 0% meter next to a
 * full schedule says "you are fine" when the truth is "nothing was ever
 * measured here".
 */
export function OverloadMeter({
  availableMinutes,
  scheduledMinutes,
  overloadMinutes,
  ratio,
  overloaded = false,
  label,
  className,
}: OverloadMeterProps) {
  const heading = label ?? 'Capacity'

  if (availableMinutes === null) {
    return (
      <div className={cn('flex flex-col gap-2 rounded-lg border border-dashed border-border p-3', className)}>
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{heading}</p>
        <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <CircleHelp className="size-4 text-muted-foreground" aria-hidden="true" />
          No availability set
        </p>
        {/* No bar at all: a zero-width meter would read as a healthy 0%. */}
        <div className="h-1.5 w-full rounded-full border border-dashed border-border" aria-hidden="true" />
        <p className="text-xs leading-relaxed text-muted-foreground">
          {formatMinutes(scheduledMinutes)} is on this day, but no hours were declared for it, so it is
          not measured against anything. Set your weekly hours to see whether this day is overloaded.
        </p>
      </div>
    )
  }

  const max = Math.max(availableMinutes, scheduledMinutes, 1)
  const percent = ratio === null || ratio === undefined ? null : Math.round(ratio * 100)

  return (
    <div className={cn('flex flex-col gap-2 rounded-lg border border-border p-3', className)}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{heading}</p>

      <div className="flex items-center gap-2">
        <Progress
          value={scheduledMinutes}
          max={max}
          variant={overloaded ? 'destructive' : scheduledMinutes > 0 ? 'default' : 'success'}
          aria-label={`${formatMinutes(scheduledMinutes)} scheduled of ${formatMinutes(availableMinutes)} available`}
          className="h-1.5 flex-1"
        />
        <span className="w-12 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
          {percent === null ? '—' : `${percent}%`}
        </span>
      </div>

      <p className="text-xs text-muted-foreground">
        {formatMinutes(scheduledMinutes)} scheduled of {formatMinutes(availableMinutes)} available
      </p>

      {overloaded ? (
        <p className="flex items-center gap-1.5 text-sm font-medium text-destructive">
          {overloadMinutes && overloadMinutes > 0 ? (
            <TriangleAlert className="size-4" aria-hidden="true" />
          ) : (
            <CircleAlert className="size-4" aria-hidden="true" />
          )}
          Overloaded by {formatMinutes(overloadMinutes ?? scheduledMinutes)}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">Within your declared hours</p>
      )}
    </div>
  )
}