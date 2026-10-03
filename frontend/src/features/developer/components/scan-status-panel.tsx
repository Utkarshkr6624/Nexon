import { RefreshCw, ScanLine, TriangleAlert } from 'lucide-react'

import { Alert, AlertIcon } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import {
  DEFAULT_STALE_HOURS,
  formatDeveloperInstant,
  formatScanAge,
  isScanStale,
} from '@/features/developer/components/developer-format'
import { NEVER_SCANNED_TITLE } from '@/features/developer/components/developer-vocabulary'
import { ScanStatusBadge } from '@/features/developer/components/developer-badges'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { GitScanStatus, ISODateTimeString, ScanRunRead } from '@/types/developer'

/**
 * How a repository's last scan went, and the record of the attempt.
 *
 * ## A failed scan is a state, not an exception
 *
 * This panel is the whole mechanism behind "a broken repository must never break
 * NEXUS". `POST /developer/repositories/{id}/scan` answers **200** whether or not
 * it could read the directory: the failure arrives as `status: 'error'` with a
 * human sentence in `last_scan_error`, recorded as a `git_scan_runs` row. So
 * there is no code path here that renders a stack trace, and no 500 that empties
 * the page — the panel below is what a broken repository *looks* like, and it
 * looks like a panel.
 *
 * `last_scan_error` is rendered **verbatim**. The backend already sanitised it —
 * absolute paths containing the user's home directory stripped, no traceback
 * allowed through — so paraphrasing it here would throw away the only sentence
 * that says what actually went wrong.
 *
 * ## What "stale" means here, and what it does not
 *
 * Nothing re-reads a repository on its own: a scan is a synchronous `POST` and
 * there is no background scheduler in Phase 8. So the figures on a page are as of
 * the last scan, and this panel says when that was. A repository that has **never
 * been scanned** is not stale — it has no figures to be out of date, and calling
 * it stale would be the same as calling it wrong.
 */

export interface ScanStatusPanelProps {
  /** `null` until the first scan, which is a normal state after registering. */
  status: GitScanStatus | null
  /** The human sentence the failed attempt recorded. Never a traceback. */
  error: string | null
  lastScannedAt: ISODateTimeString | null
  /** Commits the last successful read recorded, when the caller has them. */
  commitCount?: number | null
  isStale?: boolean
  /** A scan is in flight. */
  isScanning?: boolean
  onScan?: () => void
  staleAfterHours?: number
  title?: string
  titleLevel?: 'h3' | 'h4'
  className?: string
}

export function ScanStatusPanel({
  status,
  error,
  lastScannedAt,
  commitCount = null,
  isStale = false,
  isScanning = false,
  onScan,
  staleAfterHours = DEFAULT_STALE_HOURS,
  title = 'Scan status',
  titleLevel = 'h3',
  className,
}: ScanStatusPanelProps) {
  const old = isScanStale(lastScannedAt, staleAfterHours)
  const neverScanned = status === null

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <CardTitle level={titleLevel}>{title}</CardTitle>
            <CardDescription>
              A scan reads the repository with the git CLI on the machine NEXUS runs on. It runs
              when you ask it to — nothing here re-reads a repository on its own.
            </CardDescription>
          </div>
          {onScan && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="shrink-0"
              disabled={isScanning}
              aria-busy={isScanning}
              onClick={onScan}
            >
              {isScanning ? <Spinner size="sm" /> : <RefreshCw aria-hidden="true" />}
              {isScanning ? 'Scanning' : 'Scan now'}
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <ScanStatusBadge status={status} />
          {old && (
            <span className="text-xs text-warning">
              Older than {formatNumber(staleAfterHours)} hours
            </span>
          )}
        </div>

        <DeveloperStaleNotice isStale={isStale} subject="the scan status" />

        {neverScanned ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">{NEVER_SCANNED_TITLE}.</span> Registering
            a repository records its path and proves it is a git work tree; it does not open it.
            Run the first scan to read its commits and branches.
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-foreground">
            <span className="font-medium">
              {lastScannedAt ? `Scanned ${formatScanAge(lastScannedAt)}` : 'Scan finished'}
            </span>
            {lastScannedAt && (
              <span className="text-muted-foreground">
                {' '}
                (<time dateTime={lastScannedAt}>{formatDeveloperInstant(lastScannedAt)}</time>)
              </span>
            )}
            .
            {typeof commitCount === 'number' && (
              <span className="text-muted-foreground">
                {' '}
                {formatNumber(commitCount)} {commitCount === 1 ? 'commit was' : 'commits were'}{' '}
                recorded by it.
              </span>
            )}
          </p>
        )}

        {status === 'error' && (
          <Alert variant="destructive" className="items-start">
            <AlertIcon>
              <TriangleAlert aria-hidden="true" />
            </AlertIcon>
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium text-foreground">
                The last scan could not read this repository
              </p>
              <p className="text-sm leading-relaxed text-foreground/80">
                {error ?? 'The backend recorded no reason for the failure.'}
              </p>
              <p className="text-sm leading-relaxed text-muted-foreground">
                Nothing else is affected. The repository is still registered, its previously
                recorded commits are untouched, and running the scan again after fixing the
                problem will resume from the last commit NEXUS had already read.
              </p>
            </div>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}

/* ---------------------------------------------------------------- scan runs */

export interface ScanRunListProps {
  runs: readonly ScanRunRead[]
  isLoading?: boolean
  /** A refetch is in flight behind rows already on screen. */
  isStale?: boolean
  emptyReason?: string | null
  title?: string
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The record of one scan attempt, whatever its outcome.
 *
 * **`commits_discovered` against `commits_added` is the interesting pair.** They
 * differ on every re-scan of an unchanged repository, and the gap is the
 * idempotent upsert on `(repository_id, commit_hash)` doing its job rather than
 * anything having gone wrong — so the row says so in words, because a reader who
 * does not know that would read the gap as data loss.
 *
 * `status: 'error'` rows are kept in the same list rather than hidden: the record
 * of an attempt that failed is part of the repository's history, and it is the
 * only place the failure sentence is stored.
 */
export function ScanRunList({
  runs,
  isLoading = false,
  isStale = false,
  emptyReason = null,
  title = 'Scan record',
  titleLevel = 'h3',
  className,
}: ScanRunListProps) {
  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        <CardDescription>
          Every attempt is recorded, whatever the outcome. Nothing here is removed when a later
          scan succeeds.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        <DeveloperStaleNotice isStale={isStale} subject="the scan record" />

        {isLoading ? (
          <ScanRunListSkeleton />
        ) : runs.length === 0 ? (
          <DeveloperEmptyState variant="scanRuns" reason={emptyReason} className="py-4" />
        ) : (
          <ul className="divide-y divide-border">
            {runs.map((run) => (
              <li key={run.id} className="min-w-0 space-y-1.5 py-3 first:pt-0 last:pb-0">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <div className="flex min-w-0 items-center gap-2">
                    <ScanStatusBadge status={run.status} size="sm" />
                    <time
                      className="text-xs text-muted-foreground"
                      dateTime={run.scanned_at}
                      title={formatDeveloperInstant(run.scanned_at)}
                    >
                      {formatScanAge(run.scanned_at)}
                    </time>
                  </div>
                  <p className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {formatNumber(run.duration_ms)} ms
                  </p>
                </div>

                <p className="text-xs tabular-nums text-muted-foreground">
                  {formatNumber(run.commits_discovered)}{' '}
                  {run.commits_discovered === 1 ? 'commit' : 'commits'} discovered,{' '}
                  {formatNumber(run.commits_added)} added,{' '}
                  {formatNumber(run.branches_discovered)}{' '}
                  {run.branches_discovered === 1 ? 'branch' : 'branches'} seen.
                </p>

                {run.commits_added < run.commits_discovered && (
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    Fewer commits were added than discovered because the ones already recorded
                    were recognised and left alone — re-scanning does not duplicate history.
                  </p>
                )}

                {run.error && (
                  <p className="text-xs leading-relaxed text-destructive">{run.error}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** The scan record's loading silhouette — three lines per row, no figures. */
export function ScanRunListSkeleton({ className }: { className?: string }) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading the scan record</span>
      {[0, 1, 2].map((row) => (
        <div key={row} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="h-4 w-28 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-14 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="h-3 w-2/3 animate-pulse rounded-md bg-muted" />
        </div>
      ))}
    </div>
  )
}

/** Shown on a page whose repository has never been read. */
export function NeverScannedHint({ className }: { className?: string }) {
  return (
    <p
      className={cn(
        'flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground',
        className,
      )}
    >
      <ScanLine aria-hidden="true" className="size-3.5 shrink-0" />
      This repository has not been scanned yet, so there are no commits, branches or activity
      figures to show. Its commits appear once a scan reads the directory.
    </p>
  )
}