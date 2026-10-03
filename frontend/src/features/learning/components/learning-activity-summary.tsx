import type { ReactNode } from 'react'
import {
  BookmarkCheck,
  CalendarRange,
  Clock,
  Layers,
  Target,
  type LucideIcon,
} from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { MetricCard } from '@/features/analytics/components/metric-card'
import { Heatmap, type HeatmapDay } from '@/features/analytics/components/heatmap'
import { LazyChart, TrendChart } from '@/features/analytics/components/lazy-charts'
import { formatNumber } from '@/features/analytics/format'
import {
  LearningEmptyState,
  LearningRegionError,
  LearningStaleNotice,
} from '@/features/learning/components/learning-empty-state'
import {
  describeStreak,
  describeWindow,
  formatBucketLabel,
  formatLearningInstant,
  streakPhrase,
} from '@/features/learning/components/learning-format'
import {
  ACTIVITY_TYPE_ORDER,
  ACTIVITY_TYPE_META,
  STREAK_BAND_DAYS,
} from '@/features/learning/components/learning-vocabulary'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type {
  LearningActivitySeriesRead,
  LearningSummaryRead,
} from '@/types/learning'

/**
 * The account-wide counts and the shape of the recorded window.
 *
 * ## These are counts of records, and the hints say so
 *
 * Every tile names what it counts: activities *recorded*, minutes *recorded*, and
 * goals *written down*. Nothing here is a rate of learning, a duration of effort
 * or a score, and there is deliberately no "strongest skill" tile and no
 * "average level" — both would be a claim about the person with nothing
 * attributing it, which is the one thing this surface may not do. The summary
 * sentence under the row is rendered **verbatim** from the backend's `summary`,
 * so the wording has one owner rather than eight paraphrases.
 *
 * ## The whole row refuses to render when there is nothing to summarise
 *
 * `has_data: false` is the cold-start flag, and six zeroes across the top of an
 * empty account is the worst possible first impression: it looks like a
 * measurement and it is the opposite of one. On that flag the tiles are replaced
 * by the shared empty state, which explains what has to be recorded first. A
 * genuine zero *inside* a populated row is still rendered as `0` — different
 * facts.
 *
 * ## The streak is a run of records, not of hours
 *
 * `describeStreak` reads the bucket width off the payload and counts consecutive
 * buckets that carry at least one activity, so the sentence is true whether the
 * backend answered with daily or weekly buckets. It never converts a count of
 * records into a claim about how long anyone spent: `streakPhrase` names the
 * bucket unit and states how many of the window's buckets carried something.
 * A run of zero is stated as a run of zero, with no red accent, because an empty
 * window is an ordinary state and a progress bar for a judgement is not.
 */
export interface LearningActivitySummaryProps {
  summary: LearningSummaryRead | null
  /** The window's bucket series. Omit for the tiles alone. */
  series?: LearningActivitySeriesRead | null
  /**
   * Recorded-event counts keyed by `activity_type`, or null to leave the
   * breakdown out entirely.
   *
   * The series carries no per-type breakdown of its own, so the caller supplies
   * one — and it must be a **complete** count of the window to be true. A
   * breakdown assembled from one page of a paginated trail would report fewer
   * events than the window holds while reading as the whole, so the page passes
   * null unless the rows it has cover every activity the backend counted.
   */
  byType?: Record<string, number> | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  title?: string
  subtitle?: ReactNode
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/** The streak band, the chart and the by-type breakdown. */
function LearningWindowDetail({
  series,
  byType,
  titleLevel,
}: {
  series: LearningActivitySeriesRead
  byType: Record<string, number> | null
  titleLevel: 'h3' | 'h4'
}) {
  const streak = describeStreak(series.buckets)
  const daily = streak.granularity === 'day' && series.buckets.length > 0

  const chartRows = series.buckets.map((bucket) => ({
    label: formatBucketLabel(bucket.bucket_start),
    activities: bucket.activities,
    minutes: bucket.minutes,
  }))

  const heatmapDays: HeatmapDay[] = daily
    ? series.buckets.map((bucket) => ({
        date: bucket.bucket_start.slice(0, 10),
        value: bucket.activities,
      }))
    : []

  return (
    <div className="space-y-4">
      {heatmapDays.length > 0 ? (
        <Heatmap
          title="Days with recorded activity"
          subtitle={streakPhrase(streak)}
          days={heatmapDays.slice(-STREAK_BAND_DAYS)}
          start={series.window_start.slice(0, 10)}
          end={series.window_end.slice(0, 10)}
          valueName="learning activities recorded"
        />
      ) : (
        <Card className="min-w-0">
          <CardHeader className="pb-4">
            <CardTitle level={titleLevel}>Continuity</CardTitle>
            <CardDescription>{streakPhrase(streak)}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed text-muted-foreground">
              The window was bucketed by {streak.granularity}, so there is no day-by-day grid to
              draw. The series below carries the same counts at the grain the backend answered with,
              over {describeWindow(series.window_days)}.
            </p>
          </CardContent>
        </Card>
      )}

      {chartRows.length > 0 && (
        <LazyChart
          title="Recorded activity over the window"
          subtitle={`Buckets are zero-filled, so a quiet stretch shows as zero rather than as a gap.`}
        >
          <TrendChart
            title="Recorded activity over the window"
            subtitle="Every bucket in the range is plotted, including the empty ones."
            data={chartRows}
            series={[
              { key: 'activities', label: 'Activities recorded', unit: 'count', colorIndex: 0 },
              { key: 'minutes', label: 'Recorded minutes', unit: 'minutes', colorIndex: 1 },
            ]}
          />
        </LazyChart>
      )}

      {byType && <ActivityTypeBreakdown byType={byType} titleLevel={titleLevel} />}
    </div>
  )
}

/**
 * What the window's trail is made of, grouped by activity type.
 *
 * **The weakest kind of evidence is listed last and is never merged with the
 * strongest.** `resource_viewed` records that a page was opened, and a summary
 * that added it to `concept_learned` would present the two as equivalent
 * evidence; keeping them as separate rows with separate counts is the only way a
 * reader can weigh the trail themselves.
 *
 * A type absent from `by_type` renders a dash rather than a zero: the response
 * did not carry that key, and `0` would claim a measurement the server did not
 * make.
 */
function ActivityTypeBreakdown({
  byType,
  titleLevel,
}: {
  byType: Record<string, number>
  titleLevel: 'h3' | 'h4'
}) {
  const rows = ACTIVITY_TYPE_ORDER.filter((type) => byType[type] !== undefined)
  if (rows.length === 0) return null

  return (
    <Card className="min-w-0">
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>What the trail is made of</CardTitle>
        <CardDescription>
          Counts of recorded events, each kind listed separately. None of them is a measure of
          understanding, and a page that was opened is not the same evidence as a concept recorded.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border">
          {rows.map((type) => {
            const meta = ACTIVITY_TYPE_META[type]
            const Icon = meta.icon
            return (
              <li key={type} className="flex min-w-0 items-start gap-3 py-2 first:pt-0 last:pb-0">
                <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">{meta.label}</p>
                  <p className="text-xs leading-relaxed text-muted-foreground">{meta.description}</p>
                </div>
                <p className="shrink-0 text-sm font-medium tabular-nums text-foreground">
                  {formatNumber(byType[type])}
                </p>
              </li>
            )
          })}
        </ul>
      </CardContent>
    </Card>
  )
}

export function LearningActivitySummary({
  summary,
  series = null,
  byType = null,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  title = 'Learning summary',
  subtitle,
  titleLevel = 'h3',
  className,
}: LearningActivitySummaryProps) {
  if (isLoading) return <LearningSummarySkeleton title={title} className={className} />

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <LearningRegionError error={error} onRetry={onRetry} subject="the learning summary" />
      </div>
    )
  }

  if (!summary || !summary.has_data) {
    return (
      <LearningEmptyState
        variant="summary"
        className={cn('rounded-lg border border-border bg-card', 'min-h-[12rem]', className)}
      />
    )
  }

  const tiles: { key: string; label: string; value: string; hint: string; icon: LucideIcon }[] = [
    {
      key: 'goal_count',
      label: 'Goals',
      value: formatNumber(summary.goal_count),
      hint: 'Learning goals written down for this account, archived ones included',
      icon: Target,
    },
    {
      key: 'active_goal_count',
      label: 'Goals still open',
      value: formatNumber(summary.active_goal_count),
      hint: 'Not completed and not archived — the goals you have left open',
      icon: BookmarkCheck,
    },
    {
      key: 'skill_count',
      label: 'Skills',
      value: formatNumber(summary.skill_count),
      hint: 'Tracked skills. Each one carries a level you set or a labelled estimate',
      icon: Layers,
    },
    {
      key: 'activities_in_window',
      label: `Activities, last ${formatNumber(summary.window_days)} days`,
      value: formatNumber(summary.activities_in_window),
      hint: 'Learning activities recorded inside the window, counted once each',
      icon: CalendarRange,
    },
    {
      key: 'minutes_in_window',
      label: 'Recorded minutes',
      value: formatNumber(summary.minutes_in_window),
      hint: 'Minutes attached to those activities. Zero means every one was an event, not a span',
      icon: Clock,
    },
  ]

  return (
    <div className={cn('space-y-4', className)}>
      <Card className="min-w-0">
        <CardHeader className="pb-4">
          <CardTitle level={titleLevel}>{title}</CardTitle>
          {subtitle && <CardDescription>{subtitle}</CardDescription>}
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
            {tiles.map((tile) => (
              <MetricCard
                key={tile.key}
                label={tile.label}
                value={tile.value}
                hint={tile.hint}
                icon={tile.icon}
                className="h-full"
              />
            ))}
          </div>

          <LearningStaleNotice isStale={isStale} subject="the summary" />

          <p className="text-xs leading-relaxed text-muted-foreground">{summary.summary}</p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {summary.latest_activity_at
              ? `Most recent recorded activity ${formatLearningInstant(summary.latest_activity_at)}.`
              : 'No activity has been recorded yet, so there is no most recent one.'}
            {summary.minutes_in_window === 0 &&
              ' The minutes figure is zero because every activity in the window was an event rather than a span — that is a measurement, not a missing value.'}
          </p>
        </CardContent>
      </Card>

      {series && series.buckets.length > 0 && (
        <LearningWindowDetail series={series} byType={byType} titleLevel={titleLevel} />
      )}
    </div>
  )
}

/**
 * The summary's loading state.
 *
 * Five tile silhouettes and two detail blocks at the same layout the loaded row
 * uses, so nothing below moves when the numbers arrive. **No placeholder
 * digits and no filled heatmap cells**: a grey `0` on a tile that may well read
 * "Not enough data yet." is a number, and this surface never shows a number it
 * does not have.
 */
export function LearningSummarySkeleton({
  title,
  className,
}: {
  title?: string
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">
        {title ? `Loading the ${title.toLowerCase()}` : 'Loading the learning summary'}
      </span>

      <div aria-hidden="true" className="min-w-0 space-y-3 rounded-lg border border-border bg-card p-6">
        <div className="h-4 w-48 animate-pulse rounded-md bg-muted" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="min-w-0 space-y-2 rounded-lg border border-border bg-card p-6">
              <div className="h-2.5 w-3/4 animate-pulse rounded-md bg-muted" />
              <div className="h-6 w-16 animate-pulse rounded-md bg-muted" />
              <div className="h-3 w-full animate-pulse rounded-md bg-muted" />
            </div>
          ))}
        </div>
        <div className="h-3 w-4/5 animate-pulse rounded-md bg-muted" />
      </div>

      <div
        aria-hidden="true"
        className="min-w-0 space-y-3 rounded-lg border border-border bg-card p-6"
      >
        <div className="h-4 w-32 animate-pulse rounded-md bg-muted" />
        <div className="h-3 w-3/4 animate-pulse rounded-md bg-muted" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  )
}