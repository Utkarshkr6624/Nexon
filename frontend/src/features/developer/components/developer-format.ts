/**
 * Presentation helpers for the developer surface.
 *
 * **Every formatter here takes `number | string | null | undefined` and returns
 * `NO_VALUE` (`—`) rather than a substituted value.** That is the whole job of
 * this file. `DeveloperRead` is full of genuine nulls that mean *this was not
 * measured* — `latest_commit_at` on an empty repository, `current_branch` on a
 * detached HEAD, a metric's `value` when its denominator was zero — and the two
 * ways to get them wrong (`?? 0`, `value || 0`) both end up asserting something
 * the backend declined to say. "This repository has no commits" is not
 * "0 commits", and the second one is how a dashboard comes to claim someone
 * committed today.
 *
 * `.ts` rather than `.tsx` because nothing here renders: these are pure
 * functions, and a JSX-free `.tsx` would only import the metadata tables for no
 * reason.
 */

import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import { METRIC_UNIT_META } from '@/features/developer/components/developer-vocabulary'
import type {
  ActivityGranularity,
  CommitRead,
  DeveloperMetricRead,
  DeveloperMetricUnit,
  ISODateTimeString,
  RepositoryRead,
} from '@/types/developer'

/* ------------------------------------------------------------------ thresholds */

/**
 * How old a scan has to be before the UI calls its figures stale.
 *
 * A commit lands the moment someone pushes, and nothing re-reads the directory
 * on its own — there is no background scheduler in Phase 8 — so "last scanned"
 * is the only thing standing between a reader and a set of figures that quietly
 * stopped matching the repository. A day is long enough that a repository nobody
 * has touched still reads as current, and short enough that one that has moved
 * on does not.
 */
export const DEFAULT_STALE_HOURS = 24

const HOUR_MS = 60 * 60 * 1000

/* --------------------------------------------------------------- metric values */

/**
 * A metric's figure, formatted by its own unit.
 *
 * The unit decides the precision — a ratio to two decimals, everything else
 * whole — because that is the one thing a caller cannot safely guess. A `null`
 * value is the backend saying the metric could not be computed, so it renders as
 * `—` and the caller is expected to show the reason instead of the dash; this
 * function has no opinion about which.
 */
export function formatDeveloperMetricValue(
  value: number | null | undefined,
  unit: DeveloperMetricUnit,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const digits = METRIC_UNIT_META[unit]?.digits ?? 0
  return formatNumber(value, digits)
}

/**
 * A metric's figure with its unit named in words.
 *
 * `"42 commits"`, `"1,204 lines"`, `"17 days"`, `"0.62 ratio"`. The noun is
 * there so the figure cannot be read as the wrong kind of thing — a bare `42`
 * beside the label "change volume" is a page-width away from being read as 42
 * commits.
 */
export function formatDeveloperMetricValueWithUnit(metric: DeveloperMetricRead): string {
  const value = formatDeveloperMetricValue(metric.value, metric.unit)
  if (value === NO_VALUE) return NO_VALUE
  const unit = METRIC_UNIT_META[metric.unit]
  return unit ? `${value} ${unit.label.toLowerCase()}` : value
}

/* ------------------------------------------------------------- bucket labels */

/**
 * The x-axis label for one activity bucket.
 *
 * **Rendered in UTC, deliberately.** `bucket_start` is an instant anchored at
 * the start of a UTC day, week or month, so formatting it in the reader's local
 * zone would slide a Monday-start week into Sunday and make a bucket that holds
 * Monday's commits appear to belong to the week before. Every formatter here
 * pins `timeZone: 'UTC'` for that reason — the same trap the analytics surface
 * documents for its date-only columns.
 */
export function formatActivityBucketLabel(
  bucketStart: string | null | undefined,
  granularity: ActivityGranularity,
): string {
  const date = parseInstant(bucketStart)
  if (!date) return NO_VALUE

  switch (granularity) {
    case 'day':
      return formatUtc(date, { day: 'numeric', month: 'short' }, { includeYear: true })
    case 'week':
      return `Week of ${formatUtc(date, { day: 'numeric', month: 'short' }, { includeYear: true })}`
    case 'month':
      return formatUtc(date, { month: 'long', year: 'numeric' })
    default:
      return formatUtc(date, { day: 'numeric', month: 'short' }, { includeYear: true })
  }
}

/** The same label, but for an absolute timestamp — the scan line, the commit line. */
export function formatDeveloperInstant(value: string | null | undefined): string {
  const date = parseInstant(value)
  if (!date) return NO_VALUE
  return formatUtc(date, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Hour and minute, for the tooltip on a commit whose day is already stated above. */
export function formatDeveloperClock(value: string | null | undefined): string {
  const date = parseInstant(value)
  if (!date) return NO_VALUE
  return formatUtc(date, { hour: '2-digit', minute: '2-digit' })
}

/* --------------------------------------------------------------- scan timings */

/**
 * How long ago a scan ran, in words.
 *
 * A null `last_scanned_at` reads **"Never scanned"** rather than "never
 * updated": a repository can be registered and never read, and that is a
 * different state from one that was read long ago. Both are absences, and
 * neither is a failure.
 */
export function formatScanAge(
  lastScannedAt: ISODateTimeString | null | undefined,
  nowMs: number = Date.now(),
): string {
  const at = parseInstant(lastScannedAt)
  if (!at) return 'Never scanned'

  const elapsed = Math.max(0, nowMs - at.getTime())
  const minutes = Math.round(elapsed / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/**
 * Whether a scan is old enough that its figures should be flagged.
 *
 * A repository that has never been scanned is **not** stale — it has no figures
 * to be out of date, and calling it stale would be the same as calling it wrong.
 * The never-scanned state is a state of its own.
 */
export function isScanStale(
  lastScannedAt: ISODateTimeString | null | undefined,
  staleAfterHours: number = DEFAULT_STALE_HOURS,
  nowMs: number = Date.now(),
): boolean {
  const at = parseInstant(lastScannedAt)
  if (!at) return false
  const threshold = Number.isFinite(staleAfterHours) ? Math.max(0, staleAfterHours) : DEFAULT_STALE_HOURS
  return nowMs - at.getTime() > threshold * HOUR_MS
}

/* --------------------------------------------------------------- commit facts */

/**
 * A commit's short hash, or the full one when there is no short form.
 *
 * A commit carries both, and the short one is the display form; slicing the full
 * SHA here as a fallback keeps a payload that only has the long one from
 * rendering a 40-character column.
 */
export function commitShortHash(commit: CommitRead): string {
  const short = commit.short_hash?.trim()
  if (short) return short
  return commit.commit_hash.slice(0, 8)
}

/**
 * The commit's change size, as a noun phrase.
 *
 * `"3 files changed"`, `"1 file changed"`, `"no files recorded"`. The last one
 * is a real recorded zero — git reported no numstat — and it is deliberately not
 * a dash, because `files_changed` is a plain `number` on the wire and a `0` is a
 * measurement rather than a missing one.
 */
export function commitFilesPhrase(filesChanged: number): string {
  if (!Number.isFinite(filesChanged) || filesChanged <= 0) return 'no files recorded'
  const rounded = Math.trunc(filesChanged)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'file' : 'files'} changed`
}

/** `+128 / −12`, with the minus sign aligned to the digits above it. */
export function commitLineDelta(additions: number, deletions: number): string {
  const added = Number.isFinite(additions) ? Math.max(0, Math.trunc(additions)) : 0
  const removed = Number.isFinite(deletions) ? Math.max(0, Math.trunc(deletions)) : 0
  return `+${formatNumber(added)} / −${formatNumber(removed)}`
}

/* ---------------------------------------------------------------- repositories */

/** The branch a repository's HEAD points at, in words rather than as a bare null. */
export function describeCurrentBranch(repository: RepositoryRead): string {
  if (repository.current_branch) return repository.current_branch
  if (repository.commit_count === 0) return 'No branch yet — this repository has no commits.'
  return 'Detached HEAD — no branch is checked out.'
}

/** The directory name at the end of the resolved path, for a compact header. */
export function repositoryDirectoryName(localPath: string): string {
  const trimmed = localPath.replace(/[/\\]+$/, '')
  const parts = trimmed.split(/[/\\]/)
  return parts[parts.length - 1] || trimmed
}

/**
 * A commit count, stated once and in words.
 *
 * Exported from here rather than from the timeline component so the sentence has
 * one owner: a page header that says "7 commits recorded" and a timeline that
 * says "7 commits shown" are quoting the same number through the same helper, and
 * cannot drift into disagreeing about the singular.
 */
export function describeCommitCount(count: number): string {
  return `${formatNumber(count)} ${count === 1 ? 'commit' : 'commits'} recorded`
}

/* ------------------------------------------------------------------ languages */

/**
 * One language and how much of it was counted.
 *
 * **The unit is data, not decoration.** A repository's tracked-file distribution
 * and a repository list's primary-language distribution are both real and they
 * are not the same measurement, so each row names which one it is and the chart's
 * axis noun is read from the data rather than assumed.
 */
export interface LanguageCount {
  language: string
  count: number
  unit: 'files' | 'repositories'
}

/**
 * Counts the primary language across a repository list.
 *
 * The one language breakdown derivable from data the list page already holds:
 * `RepositoryRead.primary_language` names the most common tracked extension for
 * each repository. Repositories whose extensions the scan did not recognise
 * contribute nothing — the backend deliberately has no "other" bucket, because a
 * bucket of unrecognised extensions is a category rather than a language.
 *
 * Sorted by count and then by name, so the bars keep a stable order across
 * refetches and a reader can find the same row twice.
 */
export function languageCountsFromRepositories(
  repositories: readonly { primary_language: string | null }[],
): LanguageCount[] {
  const counts = new Map<string, number>()
  for (const repository of repositories) {
    const language = repository.primary_language?.trim()
    if (!language) continue
    counts.set(language, (counts.get(language) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([language, count]) => ({ language, count, unit: 'repositories' as const }))
    .sort((a, b) => b.count - a.count || a.language.localeCompare(b.language))
}

/** The noun the axis and the tooltip call the counted thing. */
export function languageUnitNoun(unit: LanguageCount['unit']): string {
  return unit === 'files' ? 'tracked files' : 'repositories'
}

/* -------------------------------------------------------------------- helpers */

function parseInstant(value: string | null | undefined): Date | null {
  if (typeof value !== 'string' || value.trim().length === 0) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatUtc(
  date: Date,
  options: Intl.DateTimeFormatOptions,
  settings: { includeYear?: boolean } = {},
): string {
  const now = new Date()
  return new Intl.DateTimeFormat(undefined, {
    ...options,
    ...(settings.includeYear && now.getUTCFullYear() === date.getUTCFullYear()
      ? {}
      : { year: 'numeric' }),
    timeZone: 'UTC',
  }).format(date)
}