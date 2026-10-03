import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { FileCode2, GitBranch, Minus, PlusCircle, UserRound } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import {
  commitFilesPhrase,
  commitLineDelta,
  commitShortHash,
  formatDeveloperClock,
  formatDeveloperInstant,
} from '@/features/developer/components/developer-format'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { CommitRead, UUIDString } from '@/types/developer'

/**
 * The recorded commit history, newest first.
 *
 * ## One row per commit, and the row is the evidence
 *
 * This is the only place on the surface where the underlying facts are shown one
 * by one — who committed, when, on which branch, and how many lines moved. It
 * is deliberately literal: no grouping into "sessions", no streak markers, no
 * "you were active for three hours" line. A commit is what git recorded, and a
 * timeline of commits is a true statement about the repository.
 *
 * ## The nulls on a commit are not failures
 *
 * `author_name` is null when git recorded none, so the row says "Author not
 * recorded" rather than an empty avatar. `branch` is null when the scan could not
 * attribute the commit to a branch — attribution is explicitly best-effort and
 * the backend refuses to guess — so the row says exactly that instead of
 * implying the commit belongs nowhere. **Neither null is filled with a guess**,
 * because a fabricated author is a fabricated person.
 *
 * `additions`, `deletions` and `files_changed` are plain `number` and *may*
 * genuinely be `0`: a commit that only moved a file, or one git reported no
 * numstat for, is stored as `0` rather than dropped. So a zero here renders as
 * `0` and the phrase reads "no files recorded" — "recorded as zero" and "never
 * counted" are different facts and the row does not merge them.
 *
 * ## Additions and deletions are a diff, not a verdict
 *
 * The `+` / `−` pair is shown with the minus sign aligned to the digits above it
 * and no colour or trend attached: a large deletion count is not a mistake, and
 * the row makes no claim about whether the change was good.
 */

export interface CommitTimelineItemProps {
  commit: CommitRead
  /** Resolved repository name, when the caller already has it. */
  repositoryName?: string | null
  repositoryHref?: string | null
  className?: string
}

export function CommitTimelineItem({
  commit,
  repositoryName = null,
  repositoryHref = null,
  className,
}: CommitTimelineItemProps) {
  const author = commit.author_name?.trim()
  const branch = commit.branch?.trim()

  return (
    <li className={cn('min-w-0 space-y-2 py-3 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 text-sm font-medium leading-snug text-foreground">
          {commit.message}
        </p>
        <p className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <time dateTime={commit.committed_at} title={formatDeveloperInstant(commit.committed_at)}>
            {formatDeveloperInstant(commit.committed_at)} {formatDeveloperClock(commit.committed_at)}
          </time>
        </p>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono text-foreground/80" title={commit.commit_hash}>
          {commitShortHash(commit)}
        </span>

        <span className="flex min-w-0 items-center gap-1">
          <UserRound aria-hidden="true" className="size-3 shrink-0" />
          {author && author.length > 0 ? (
            <span className="truncate">{author}</span>
          ) : (
            <span>Author not recorded</span>
          )}
        </span>

        <span className="flex min-w-0 items-center gap-1">
          <GitBranch aria-hidden="true" className="size-3 shrink-0" />
          {branch && branch.length > 0 ? (
            <span className="truncate">{branch}</span>
          ) : (
            <span>Branch not attributable</span>
          )}
        </span>

        {repositoryName && (
          repositoryHref ? (
            <Link
              to={repositoryHref}
              className="min-w-0 truncate font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {repositoryName}
            </Link>
          ) : (
            <span className="min-w-0 truncate font-medium text-foreground">{repositoryName}</span>
          )
        )}
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="flex items-center gap-1 tabular-nums text-muted-foreground">
          <PlusCircle aria-hidden="true" className="size-3 text-chart-2" />
          {commitLineDelta(commit.additions, commit.deletions)}
        </span>
        <span className="flex items-center gap-1 tabular-nums text-muted-foreground">
          <FileCode2 aria-hidden="true" className="size-3" />
          {commitFilesPhrase(commit.files_changed)}
        </span>
      </div>
    </li>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The timeline's loading silhouette.
 *
 * A message line, a metadata line and a diff line per row, and **no digits
 * anywhere** — a grey `+0 / −0` would be a diff, and this surface never shows a
 * diff it does not have. Announced once for the whole list rather than per row.
 */
export function CommitTimelineSkeleton({
  count = 6,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading the commit timeline</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <div className="h-3.5 w-3/5 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-16 shrink-0 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex gap-3">
            <div className="h-3 w-16 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-24 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-20 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex gap-3">
            <div className="h-3 w-20 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-28 animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      ))}
    </div>
  )
}

/* ---------------------------------------------------------------------- list */

export interface CommitTimelineProps {
  commits: readonly CommitRead[]
  isLoading?: boolean
  /** A refetch is in flight behind rows already on screen. */
  isStale?: boolean
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  /** Resolves a repository id to a display name. Omit for a single repository. */
  repositoryName?: (repositoryId: UUIDString) => string | null
  repositoryHref?: (repositoryId: UUIDString) => string | null
  title?: string
  /** The sentence under the title; states what the timeline covers. */
  subtitle?: ReactNode
  /** Row silhouettes to draw while loading. */
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The timeline card: a title, a sentence about scope, and the rows.
 *
 * The scope sentence is the caller's because only the caller knows the window or
 * the repository — a global timeline and one repository's history say different
 * things above the same rows, and a shared card that guessed would be wrong one
 * of the two times.
 */
export function CommitTimeline({
  commits,
  isLoading = false,
  isStale = false,
  emptyReason = null,
  repositoryName,
  repositoryHref,
  title = 'Commit timeline',
  subtitle,
  skeletonCount = 6,
  titleLevel = 'h3',
  className,
}: CommitTimelineProps) {
  const nameFor = repositoryName ?? (() => null)
  const hrefFor = repositoryHref ?? (() => null)

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <DeveloperStaleNotice isStale={isStale} subject="the commit timeline" />

        {isLoading ? (
          <CommitTimelineSkeleton count={skeletonCount} />
        ) : commits.length === 0 ? (
          <DeveloperEmptyState
            variant="commits"
            reason={emptyReason}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(commits.length)}{' '}
              {commits.length === 1 ? 'commit' : 'commits'} shown, newest first.
            </p>
            <ul className="divide-y divide-border">
              {commits.map((commit) => (
                <CommitTimelineItem
                  key={commit.id}
                  commit={commit}
                  repositoryName={nameFor(commit.repository_id)}
                  repositoryHref={hrefFor(commit.repository_id)}
                />
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Shown in a timeline with no pagination control, so a reader knows it is partial.
 *
 * `total` is the backend's figure and `shown` is the length of the page that
 * arrived; the two disagree on every paginated read, and a timeline that said
 * nothing about it would let a reader mistake page one for the whole history.
 */
export function TimelineScopeNote({ total, shown }: { total: number; shown: number }) {
  if (total <= shown) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Minus aria-hidden="true" className="size-3" />
        Every commit a scan has recorded is shown here.
      </p>
    )
  }

  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Minus aria-hidden="true" className="size-3" />
      Showing {formatNumber(shown)} of {formatNumber(total)} recorded commits. This is a
      position in the list, not the whole history.
    </p>
  )
}