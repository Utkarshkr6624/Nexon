import { useId, useMemo, type KeyboardEvent } from 'react'

import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import { formatActivityBucketLabel } from '@/features/developer/components/developer-format'
import { GRANULARITY_META } from '@/features/developer/components/developer-vocabulary'
import { ChartShell } from '@/features/analytics/components/chart-shell'
import { LazyChart, TrendChart } from '@/features/analytics/components/lazy-charts'
import type { ChartRow, ChartSeries } from '@/features/analytics/components/trend-chart'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import {
  ACTIVITY_GRANULARITIES,
  type ActivityGranularity,
  type DeveloperActivityRead,
} from '@/types/developer'

/**
 * The commit activity series, with its granularity control.
 *
 * ## Nothing is drawn here
 *
 * The chart itself is `TrendChart` from the analytics surface, inside its
 * `LazyChart` boundary — recharts is ~250 ms of module evaluation and a commit
 * timeline does not need it until the reader scrolls to it. This file owns the
 * three decisions that are specific to git: which buckets become rows, what the
 * subtitle claims, and what the empty state says. Everything about the frame, the
 * tooltip, the axis typography and the fixed body height is `ChartShell`'s, so a
 * change to the chart look reaches this surface for free.
 *
 * ## Zero-filled buckets are the point
 *
 * `DeveloperActivityRead.buckets` is dense by contract: a quiet Tuesday arrives
 * with `commits: 0` rather than being skipped. The rows are built one-for-one
 * from that array with **no filtering**, because dropping the empty buckets here
 * would silently compress the timeline and make a sparse fortnight look as dense
 * as a busy one — which is a misreading of the data, not a presentation choice.
 *
 * ## Granularity is a control, not a filter
 *
 * `day`, `week` and `month` change how the same commits are *bucketed*, so the
 * control re-reads the series rather than re-slicing what is already on screen.
 * The caller owns the selected value and refetches; this component only renders
 * the control and passes the change up, which keeps the window and the
 * granularity in the same place as the rest of the page's query state.
 */

/** One plotted point: the bucket's label and the figures it recorded. */
interface ActivityRow extends ChartRow {
  label: string
  commits: number
  additions: number
  deletions: number
  files_changed: number
}

const SERIES: readonly ChartSeries[] = [
  { key: 'commits', label: 'Commits', colorIndex: 0, unit: 'count' },
  { key: 'additions', label: 'Lines added', colorIndex: 1, unit: 'count' },
  { key: 'deletions', label: 'Lines removed', colorIndex: 2, unit: 'count' },
]

export interface ActivityGranularityControlProps {
  value: ActivityGranularity
  /** Refetch at another grain. Omit for a read-only control. */
  onChange?: (granularity: ActivityGranularity) => void
  /** Disables the control, e.g. while the series is loading. */
  disabled?: boolean
  className?: string
}

/**
 * Day / week / month, as an exclusive choice.
 *
 * **Roving tabindex, per the radiogroup pattern**: exactly one option sits in the
 * tab order, arrows move between them and wrap, Home and End jump to the ends.
 * Without that, a keyboard user tabs through three controls to change one thing
 * and never hears which is selected.
 *
 * With no `onChange` the control renders disabled rather than absent — a
 * read-only placement still states the grain the chart was drawn at, which is
 * information, and an invisible control would make the reader wonder whether one
 * exists elsewhere.
 */
export function ActivityGranularityControl({
  value,
  onChange,
  disabled = false,
  className,
}: ActivityGranularityControlProps) {
  const baseId = useId().replace(/:/g, '')
  const readOnly = disabled || !onChange

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return
    const last = ACTIVITY_GRANULARITIES.length - 1
    let next: number
    if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = last
    else if (event.key === 'ArrowRight') next = index === last ? 0 : index + 1
    else next = index === 0 ? last : index - 1

    const target = ACTIVITY_GRANULARITIES[next]
    if (!target || !onChange) return
    event.preventDefault()
    onChange(target)
    document.getElementById(`${baseId}-${target}`)?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label="Activity granularity"
      className={cn(
        'inline-flex items-stretch gap-0.5 rounded-md border border-border p-0.5',
        className,
      )}
    >
      {ACTIVITY_GRANULARITIES.map((option, index) => {
        const meta = GRANULARITY_META[option]
        const selected = option === value
        return (
          <button
            key={option}
            id={`${baseId}-${option}`}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={readOnly}
            title={meta.description}
            onClick={() => onChange?.(option)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              'rounded px-2 py-1 text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              'disabled:pointer-events-none disabled:opacity-60',
              selected ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {meta.shortLabel}
          </button>
        )
      })}
    </div>
  )
}

export interface DeveloperActivitySectionProps {
  /** `undefined` while the first read is in flight. */
  activity: DeveloperActivityRead | null | undefined
  isLoading?: boolean
  /** A refetch is in flight behind a chart already on screen. */
  isStale?: boolean
  /** The granularity the series on screen was built at. */
  granularity: ActivityGranularity
  /** Refetch at another grain. Omit for a read-only chart. */
  onGranularityChange?: (granularity: ActivityGranularity) => void
  /** What the series is narrowed to — a repository name, or nothing. */
  scopeLabel?: string | null
  title?: string
  className?: string
}

/**
 * The activity chart, its grain control, and its non-loaded states.
 *
 * **The empty state is "no commits in this window", not "no data".** The buckets
 * were still read; `empty` here means the *window* holds no commits at all, which
 * is a different sentence from a metric that could not be computed, and the copy
 * says which one it is. `total_commits` is carried on the response precisely so
 * the chart's axis and the caption beneath it cannot quote different sums.
 */
export function DeveloperActivitySection({
  activity,
  isLoading = false,
  isStale = false,
  granularity,
  onGranularityChange,
  scopeLabel = null,
  title = 'Recorded commit activity',
  className,
}: DeveloperActivitySectionProps) {
  const rows = useMemo<ActivityRow[]>(() => {
    if (!activity) return []
    return activity.buckets.map((bucket) => ({
      label: formatActivityBucketLabel(bucket.bucket_start, granularity),
      commits: bucket.commits,
      additions: bucket.additions,
      deletions: bucket.deletions,
      files_changed: bucket.files_changed,
    }))
  }, [activity, granularity])

  const meta = GRANULARITY_META[granularity]
  const total = activity?.total_commits ?? null
  const grain = meta.shortLabel.toLowerCase()
  const scope = scopeLabel ? ` for ${scopeLabel}` : ''

  // The "empty buckets are plotted" clause is only true when there are buckets.
  // `DeveloperActivityRead.buckets` is dense by contract, so this only differs on
  // a degenerate response — and a subtitle that promises buckets the chart is not
  // showing is a sentence the page cannot back.
  const subtitle = !activity
    ? `One point per ${grain}.`
    : rows.length > 0
      ? `One point per ${grain} across the last ${formatNumber(activity.window_days)} days${scope}. ` +
        `Empty buckets are plotted as zero commits rather than skipped.`
      : `One point per ${grain} across the last ${formatNumber(activity.window_days)} days${scope}.`

  // A window with no commits is a **measured** zero, not an absence of
  // measurement, so it is not routed through `TrendChart`'s `isEmpty` branch:
  // that renders `EmptyAnalytics`, whose title — "Not enough activity yet" —
  // is the analytics surface's claim about task data. This branch keeps
  // `ChartShell` and swaps only the body, so the card's frame, height and
  // actions are unchanged and the words are this surface's own.
  const empty = activity !== undefined && activity !== null && activity.total_commits === 0
  const emptyReason =
    activity && empty
      ? `No commit was recorded${scopeLabel ? ` in ${scopeLabel}` : ' anywhere'} during the ` +
        `last ${formatNumber(activity.window_days)} days. Every bucket in that range was still ` +
        `read, and each is a recorded zero — a quiet stretch is a fact, not missing data.`
      : null

  return (
    <div className={cn('space-y-3', className)}>
      <DeveloperStaleNotice isStale={isStale} subject="the activity series" />

      <LazyChart title={title} subtitle={subtitle}>
        {empty ? (
          // The empty branch keeps `ChartShell` — the same frame, the same fixed
          // body height and the same actions slot — and swaps only the body, so
          // the card does not change shape between the loaded and empty states.
          <ChartShell
            title={title}
            subtitle={subtitle}
            empty={
              <DeveloperEmptyState
                variant="activity"
                reason={emptyReason}
                className="h-full"
              />
            }
            actions={
              <ActivityGranularityControl
                value={granularity}
                onChange={onGranularityChange}
                disabled={isLoading}
              />
            }
          >
            {null}
          </ChartShell>
        ) : (
          <TrendChart
            title={title}
            subtitle={subtitle}
            data={rows}
            series={SERIES}
            xKey="label"
            kind="area"
            isLoading={isLoading}
            actions={
              <ActivityGranularityControl
                value={granularity}
                onChange={onGranularityChange}
                disabled={isLoading}
              />
            }
          />
        )}
      </LazyChart>

      {total !== null && (
        <p className="text-xs text-muted-foreground">
          {formatNumber(total)} {total === 1 ? 'commit' : 'commits'} recorded across{' '}
          {formatNumber(rows.length)} {grain} buckets{scope}.
        </p>
      )}
    </div>
  )
}