import { Link } from 'react-router-dom'
import {
  CalendarRange,
  GitBranch,
  GitCommitHorizontal,
  GitCompareArrows,
  type LucideIcon,
} from 'lucide-react'

import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import { MetricCard } from '@/features/analytics/components/metric-card'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { DeveloperSummaryRead } from '@/types/developer'

/**
 * The account-wide headline counts.
 *
 * ## These are counts, and the row says so
 *
 * Every figure here is a count of something a repository recorded: commits,
 * repositories, distinct days carrying a commit, lines added or removed. None of
 * them is a rate, a duration or a score, and the hint under each tile names the
 * unit in words — so a reader who skims the row still cannot come away thinking
 * the app measured how long anyone worked. In particular `active_days` is a
 * count of *days that had a commit*, never an amount of time.
 *
 * ## The row refuses to render at all when there is nothing to summarise
 *
 * `has_data` is the cold-start flag, and four or six zeroes across the top of an
 * empty account is the worst possible first impression: it looks like a
 * measurement, and it is the opposite of one. On `has_data: false` the row is
 * replaced by the shared empty state, which explains that a repository has to be
 * registered and scanned before any of this exists. A genuine zero *inside* a
 * populated row is still rendered as `0` — those are different facts.
 *
 * ## `MetricCard` is reused, and the reason is its `unavailableReason`
 *
 * It already enforces the two rules this row needs: a figure is tabular and
 * never invented, and a null with a reason replaces the number instead of
 * showing a comparison for a number that does not exist. That is why the tiles
 * here are `MetricCard`s while the eight explained metrics are not — those need
 * their explanation on screen rather than in a tooltip, which is a different job.
 *
 * The summary sentence below the row is rendered **verbatim** from the backend's
 * `summary`. Header wording has one owner on purpose: eight screens paraphrasing
 * the same counts would eventually paraphrase them differently.
 */

export interface SummaryTile {
  key: string
  label: string
  value: number
  /** The unit, in words, under the figure. */
  hint: string
  icon: LucideIcon
}

export interface DeveloperSummaryTilesProps {
  summary: DeveloperSummaryRead
  isLoading?: boolean
  /** A refetch is in flight behind figures already on screen. */
  isStale?: boolean
  /** Where a tile's count leads, when that question has a page. */
  buildHref?: (tile: SummaryTile) => string | null
  className?: string
}

export function DeveloperSummaryTiles({
  summary,
  isLoading = false,
  isStale = false,
  buildHref,
  className,
}: DeveloperSummaryTilesProps) {
  if (isLoading) {
    return <DeveloperSummaryTilesSkeleton className={className} />
  }

  if (!summary.has_data) {
    return (
      <DeveloperEmptyState
        variant="repositories"
        className={cn(
          'rounded-lg border border-border bg-card',
          // A visible frame is kept so the page's vertical rhythm does not
          // collapse the moment the read lands.
          'min-h-[12rem]',
          className,
        )}
      />
    )
  }

  const tiles: SummaryTile[] = [
    {
      key: 'repository_count',
      label: 'Repositories',
      value: summary.repository_count,
      hint: `${formatNumber(summary.active_repository_count)} of them marked active`,
      icon: GitBranch,
    },
    {
      key: 'commits_in_window',
      label: `Commits, last ${formatNumber(summary.window_days)} days`,
      value: summary.commits_in_window,
      hint: 'Commits recorded inside the window, counted once each',
      icon: GitCommitHorizontal,
    },
    {
      key: 'commit_count',
      label: 'Commits, whole history',
      value: summary.commit_count,
      hint: 'Every commit a scan has recorded across every repository',
      icon: GitCommitHorizontal,
    },
    {
      key: 'active_days',
      label: 'Days with a commit',
      value: summary.active_days,
      hint: 'Distinct days carrying at least one commit — not hours spent',
      icon: CalendarRange,
    },
    {
      key: 'change_volume',
      label: 'Lines changed',
      value: summary.change_volume,
      hint: 'Additions plus deletions recorded inside the window',
      icon: GitCompareArrows,
    },
    {
      key: 'repositories_touched',
      label: 'Repositories with commits',
      value: summary.repositories_touched,
      hint: 'Distinct repositories that recorded at least one commit',
      icon: GitBranch,
    },
  ]

  return (
    <div className={cn('space-y-3', className)}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {tiles.map((tile) => {
          const card = (
            <MetricCard
              label={tile.label}
              value={formatNumber(tile.value)}
              hint={tile.hint}
              icon={tile.icon}
              className="h-full"
            />
          )

          const href = buildHref?.(tile) ?? null
          if (!href) return <div key={tile.key} className="min-w-0">{card}</div>

          return (
            <Link
              key={tile.key}
              to={href}
              className={cn(
                'min-w-0 rounded-lg focus-visible:outline-none focus-visible:ring-2',
                'focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              )}
            >
              {card}
            </Link>
          )
        })}
      </div>

      <DeveloperStaleNotice isStale={isStale} subject="the summary" />

      <p className="text-xs leading-relaxed text-muted-foreground">{summary.summary}</p>
    </div>
  )
}

/**
 * The row's loading state.
 *
 * Six tile silhouettes at the same grid the loaded row uses, so the summary band
 * keeps its height and nothing below it moves when the numbers arrive. **No
 * placeholder digits**: a grey `0` on a tile that may well read "Not enough data
 * yet." is a number, and this surface never shows a number it does not have.
 */
export function DeveloperSummaryTilesSkeleton({ className }: { className?: string }) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-3', className)}>
      <span className="sr-only">Loading the developer summary</span>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <div
            key={index}
            aria-hidden="true"
            className="min-w-0 space-y-3 rounded-lg border border-border bg-card p-6"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-2.5 w-2/3 animate-pulse rounded-md bg-muted" />
              </div>
              <div className="size-7 shrink-0 animate-pulse rounded-md bg-muted" />
            </div>
            <div className="h-6 w-20 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-4/5 animate-pulse rounded-md bg-muted" />
          </div>
        ))}
      </div>
    </div>
  )
}