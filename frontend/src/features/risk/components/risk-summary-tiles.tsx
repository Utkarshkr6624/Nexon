import { Link } from 'react-router-dom'

import { MetricCard } from '@/features/analytics/components/metric-card'
import { RiskEmptyState } from '@/features/risk/components/risk-empty-state'
import { SEVERITY_META } from '@/features/risk/components/risk-vocabulary'
import { cn } from '@/lib/utils'
import { RISK_SEVERITIES, type RiskSeverity, type RiskSummaryRead } from '@/types/risk'

/**
 * The Critical / High / Medium / Low header row.
 *
 * **Each tile is a link, not a static figure.** The count is a question, and
 * clicking it should answer it by showing that band. `MetricCard` is reused
 * rather than rebuilt because it already enforces the two rules this row needs:
 * the figure is tabular and never invents a value, and the tile does not lean on
 * colour to mean anything — the label names the band in words.
 *
 * **The row refuses to render at all when nothing is recorded.** Four zeroes
 * across the top of an empty Risk Center is the worst possible first impression:
 * it looks like a measurement, and it is the opposite of one — the detection
 * engine ran, looked, and did not find a condition. The brief asks for "No
 * significant risk detected yet" there, so {@link RiskEmptyState} takes the
 * place of the row. Zero is a real count *within* a populated row; it is not a
 * reason to build a wall out of four of them.
 *
 * **The band filter is a link because the API has no severity filter.**
 * `GET /risks` accepts `status` and `risk_type` only, so a band is chosen in the
 * URL and applied to the returned page by `useRisks`. The default href writes
 * `?severity=` onto the Risk Center path, which keeps the choice shareable and
 * survives a reload, the same argument the analytics window makes for putting
 * its range in the URL. A page that maps the band somewhere else passes
 * `buildHref`; nothing here depends on the route table.
 *
 * `needs_attention` is rendered as a sentence rather than as a fifth tile.
 * It is the one bit the dashboard leads with, and it is computed server-side so
 * the widget and the Risk Center cannot disagree — so it is shown, not
 * recomputed, and never used to tint a tile.
 */

export interface RiskSummaryTilesProps {
  summary: RiskSummaryRead
  /** The band currently being filtered, so its tile can be marked current. */
  activeSeverity?: RiskSeverity | null
  /** Builds each tile's href. Defaults to `?severity=<band>`. */
  buildHref?: (severity: RiskSeverity) => string
  /** Path the default `buildHref` writes onto. The page owns the route. */
  basePath?: string
  className?: string
}

function defaultBuildHref(basePath: string): (severity: RiskSeverity) => string {
  return (severity) => {
    const params = new URLSearchParams()
    params.set('severity', severity)
    return `${basePath}?${params.toString()}`
  }
}

export function RiskSummaryTiles({
  summary,
  activeSeverity = null,
  buildHref,
  basePath = '/risks',
  className,
}: RiskSummaryTilesProps) {
  const hrefFor = buildHref ?? defaultBuildHref(basePath)

  if (summary.total === 0) {
    return (
      <RiskEmptyState
        variant="risks"
        className={cn(
          'rounded-lg border border-border bg-card',
          // A visible frame is kept so the row does not collapse the page's
          // vertical rhythm the moment the last risk is resolved.
          'min-h-[10rem]',
          className,
        )}
      />
    )
  }

  const counts: Record<RiskSeverity, number> = {
    critical: summary.critical,
    high: summary.high,
    medium: summary.medium,
    low: summary.low,
  }

  return (
    <div className={cn('space-y-2', className)}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {RISK_SEVERITIES.map((severity) => {
          const meta = SEVERITY_META[severity]
          const active = activeSeverity === severity
          return (
            <Link
              key={severity}
              to={hrefFor(severity)}
              aria-current={active ? 'true' : undefined}
              className={cn(
                'min-w-0 rounded-lg focus-visible:outline-none focus-visible:ring-2',
                'focus-visible:ring-ring focus-visible:ring-offset-2',
                'focus-visible:ring-offset-background',
                active && 'ring-2 ring-ring ring-offset-2 ring-offset-background',
              )}
            >
              <MetricCard
                label={meta.label}
                value={counts[severity]}
                icon={meta.icon}
                // The band threshold is the hint, not a tooltip: the header row
                // is the first thing read on the page and a band definition
                // hidden behind hover is a definition nobody opens.
                hint={meta.description}
              />
            </Link>
          )
        })}
      </div>

      <p className="text-xs text-muted-foreground">
        {summary.needs_attention
          ? 'At least one recorded risk is high or critical.'
          : 'No recorded risk is currently high or critical.'}{' '}
        {summary.total === 1 ? '1 risk' : `${summary.total} risks`} recorded in total.
      </p>
    </div>
  )
}
