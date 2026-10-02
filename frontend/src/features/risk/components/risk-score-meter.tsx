import { Info } from 'lucide-react'

import { Progress, type ProgressVariant } from '@/components/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatScore, NO_VALUE } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { RiskSeverity } from '@/types/risk'

/**
 * The 0-100 risk score, drawn and written down.
 *
 * **The bar is the decoration; the digits are the content.** A meter is the
 * obvious way to show a score and the wrong way to show it *alone*: it is
 * invisible to a screen reader's summary, it collapses to a rectangle in print,
 * and two scores a reader wants to compare precisely are two widths they have to
 * eyeball. So the number is always rendered as text beside the bar, the bar
 * carries the value through `aria-label` for the same reason `Progress` requires
 * one, and the bar's colour tracks the severity band rather than the magnitude.
 *
 * **`formatScore` is the analytics formatter, not a local one.** It returns an
 * em dash for a value that is not a finite number, which is the only honest
 * rendering for a score that failed to arrive: the alternative — `score || 0` —
 * prints "0 / 100" and claims the measurement exists and is minimal, when the
 * truth is that nothing came back. The type says `score` is always a real
 * number, because a detector that could not judge never produces a row; the
 * guard is here for the malformed-payload path, where rendering "0 / 100" would
 * be the worse failure.
 *
 * The chip beside the meter is what ties the two together. The meter alone
 * cannot say "how bad is 62?", and the band alone cannot say "by how much?";
 * showing both is what makes the score arguable rather than decorative.
 */

const METER_VARIANT: Record<RiskSeverity, ProgressVariant> = {
  critical: 'destructive',
  high: 'warning',
  medium: 'default',
  low: 'success',
}

const METER_EXPLANATION: Record<RiskSeverity, string> = {
  critical: 'A score of 75 or above out of 100 puts this in the critical band.',
  high: 'A score from 50 to 74 out of 100 puts this in the high band.',
  medium: 'A score from 25 to 49 out of 100 puts this in the medium band.',
  low: 'A score below 25 out of 100 puts this in the low band.',
}

export interface RiskScoreMeterProps {
  /** 0-100. A non-finite value renders as an em dash rather than as `0`. */
  score: number
  severity: RiskSeverity
  /** Names the number, e.g. "Risk score". */
  label?: string
  /** `sm` inside a card header, `default` on a detail screen. */
  size?: 'sm' | 'default'
  className?: string
}

export function RiskScoreMeter({
  score,
  severity,
  label = 'Risk score',
  size = 'default',
  className,
}: RiskScoreMeterProps) {
  const text = formatScore(score)
  // `formatScore` returning an em dash means the value was not usable, so the
  // bar is left empty rather than filled to zero.
  const measurable = text !== NO_VALUE && Number.isFinite(score)
  const value = measurable ? Math.min(Math.max(score, 0), 100) : 0

  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          {label}
        </p>
        <p
          className={cn(
            'font-semibold tabular-nums tracking-tight text-foreground',
            size === 'sm' ? 'text-sm' : 'text-lg',
          )}
        >
          {text}
          {measurable && (
            <span className="ml-1 text-xs font-normal text-muted-foreground">/ 100</span>
          )}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Progress
          value={value}
          max={100}
          variant={METER_VARIANT[severity]}
          className={cn('flex-1', size === 'sm' && 'h-1.5')}
          aria-label={measurable ? `${label} ${text} out of 100` : `${label} not available`}
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={`More about the ${label.toLowerCase()} scale`}
              className={cn(
                'flex size-5 shrink-0 items-center justify-center rounded-md text-muted-foreground',
                'transition-colors hover:text-foreground focus-visible:outline-none',
                'focus-visible:ring-2 focus-visible:ring-ring',
              )}
            >
              <Info className="size-3.5" aria-hidden="true" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs text-pretty leading-relaxed">
            {METER_EXPLANATION[severity]} The score is computed from the evidence listed on this
            card, not chosen by hand.
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
