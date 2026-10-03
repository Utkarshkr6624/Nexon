import type { ReactNode } from 'react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { BranchBadge } from '@/features/developer/components/developer-badges'
import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import { formatDeveloperInstant } from '@/features/developer/components/developer-format'
import { formatNumber } from '@/features/analytics/format'
import { formatRelative } from '@/types/knowledge'
import { cn } from '@/lib/utils'
import type { BranchRead } from '@/types/developer'

/**
 * The branches the last scan observed.
 *
 * ## Two roles, two chips, never one
 *
 * `is_default` and `is_current` are rendered separately because on a **detached
 * HEAD** exactly one of them is false — there is a default branch, and no branch
 * is checked out. A combined "current/default" chip would have to hide one of the
 * two in that state and lose the difference, which is precisely the state a
 * reader most needs to be told about. Neither is ever inferred here: the chips
 * repeat what the scan reported.
 *
 * ## A detached HEAD is not a failure
 *
 * The empty branch list says a repository has no commits, and a repository on a
 * detached HEAD still has branches — the difference is in the badges, not in an
 * error. Nothing on this surface treats "HEAD is detached" as something wrong.
 *
 * ## The nullable dates say what they mean
 *
 * `last_committed_at` is null for a branch whose head git could not date, and
 * `head_commit_hash` is null for a branch git could not resolve a head for.
 * Neither renders as a zero or an empty string: the row states which one is
 * missing and why it is missing.
 */

export interface BranchRowProps {
  branch: BranchRead
  className?: string
}

export function BranchRow({ branch, className }: BranchRowProps) {
  const head = branch.head_commit_hash?.trim()

  return (
    <li className={cn('min-w-0 space-y-1.5 py-3 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 truncate font-mono text-sm font-medium text-foreground">
          {branch.name}
        </p>
        <div className="flex shrink-0 items-center gap-1.5">
          {branch.is_current && <BranchBadge role="current" size="sm" />}
          {branch.is_default && <BranchBadge role="default" size="sm" />}
        </div>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono text-foreground/80" title={head ?? undefined}>
          {head ? head.slice(0, 8) : 'Head commit not recorded'}
        </span>
        {branch.last_committed_at ? (
          <time dateTime={branch.last_committed_at} title={formatDeveloperInstant(branch.last_committed_at)}>
            Head committed {formatRelative(branch.last_committed_at)}
          </time>
        ) : (
          <span>Head commit not dated by git</span>
        )}
      </div>
    </li>
  )
}

/** The list's loading silhouette: a name line and a metadata line per row. */
export function BranchListSkeleton({
  count = 4,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading branches</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="h-3.5 w-1/3 animate-pulse rounded-md bg-muted" />
          <div className="flex gap-3">
            <div className="h-3 w-16 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-24 animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      ))}
    </div>
  )
}

export interface BranchListProps {
  branches: readonly BranchRead[]
  isLoading?: boolean
  /** A refetch is in flight behind rows already on screen. */
  isStale?: boolean
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  title?: string
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The branch card: a title, a sentence about what it covers, and the rows.
 *
 * The count sentence is stated explicitly rather than left to the rows, so a
 * paginated list cannot be read as the whole set: `total` is the backend's
 * figure and `shown` is the length of what arrived.
 */
export function BranchList({
  branches,
  isLoading = false,
  isStale = false,
  emptyReason = null,
  title = 'Branches',
  subtitle,
  skeletonCount = 4,
  titleLevel = 'h3',
  className,
}: BranchListProps) {
  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <DeveloperStaleNotice isStale={isStale} subject="the branch list" />

        {isLoading ? (
          <BranchListSkeleton count={skeletonCount} />
        ) : branches.length === 0 ? (
          <DeveloperEmptyState variant="branches" reason={emptyReason} className="py-4" />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(branches.length)}{' '}
              {branches.length === 1 ? 'branch' : 'branches'} recorded at the last scan.
            </p>
            <ul className="divide-y divide-border">
              {branches.map((branch) => (
                <BranchRow key={branch.id} branch={branch} />
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  )
}