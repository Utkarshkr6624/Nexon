import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react'

import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { formatUpdatedAgo } from '@/features/analytics/format'
import type { AnalyticsStaleness } from '@/features/analytics/hooks'
import type { ISODateTimeString } from '@/types/analytics'

/**
 * How old the figures on screen are, and the action that moves them.
 *
 * **The spec forbids silently showing stale numbers.** A dashboard that renders
 * last Tuesday's totals without saying so is making a claim it cannot support,
 * so this states it in words on every render: fresh, partial, or still
 * calculating. "Calculating…" is shown for an empty aggregate rather than
 * "0 activity" — those two are the same array on the wire and completely
 * different claims.
 *
 * **Recalculate calls `POST /analytics/rebuild` for the window on screen**, which
 * is what turns a partial window into a fresh one. The button is deliberately
 * always present: recomputation is idempotent, so a click on an already-fresh
 * window costs nothing and saves the reader from working out whether the banner
 * is worth acting on.
 *
 * Props are the `AnalyticsStaleness` read spread flat, so a caller passes the
 * hook's result straight through without re-nesting it.
 */
export interface StalenessBannerProps extends AnalyticsStaleness {
  /** The newest `daily_metrics.updated_at` behind the figures, if any. */
  updatedAt?: ISODateTimeString | null
  /** What the last `POST /rebuild` wrote, once it has answered. */
  rowsWritten?: number | null
  isRecalculating?: boolean
  onRecalculate?: () => void
  className?: string
}

export function StalenessBanner({
  status,
  message,
  updatedAt,
  rowsWritten,
  isRecalculating = false,
  onRecalculate,
  className,
}: StalenessBannerProps) {
  const fresh = status === 'fresh'
  const age = updatedAt ? formatUpdatedAgo(updatedAt) : null

  const title =
    status === 'calculating'
      ? 'Calculating…'
      : status === 'partial'
        ? 'These figures are incomplete'
        : status === 'unknown'
          ? 'Freshness is unknown'
          : age
            ? `Updated ${age}`
            : 'Up to date'

  const description =
    status === 'calculating'
      ? `${message} Recalculate to compute the aggregates for this window.`
      : status === 'partial'
        ? `${message} Recalculate to bring them up to date.`
        : message

  return (
    <Alert
      variant={fresh ? 'success' : 'warning'}
      className={className}
      // A live region, so a recalculation announces its own result.
      aria-live="polite"
    >
      <AlertIcon>
        {fresh ? <CheckCircle2 aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
      </AlertIcon>
      <div className="min-w-0 flex-1 space-y-1">
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>
          {description}
          {rowsWritten !== null && rowsWritten !== undefined && !isRecalculating && (
            <> {rowsWritten} day{rowsWritten === 1 ? '' : 's'} recalculated.</>
          )}
        </AlertDescription>
      </div>
      {onRecalculate && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={isRecalculating}
          onClick={onRecalculate}
        >
          {isRecalculating ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
          Recalculate
        </Button>
      )}
    </Alert>
  )
}
