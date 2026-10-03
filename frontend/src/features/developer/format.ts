/**
 * Presentation helpers for the developer intelligence surface.
 *
 * **Every formatter here takes `number | null` and returns {@link NO_VALUE} for
 * `null`.** That is the whole job of this file, and it is the same rule
 * `features/analytics/format.ts` follows: a nullable figure renders as "no
 * measurement", and a `0` renders as `0`. Collapsing the two — the default
 * `toFixed` on a `null`, or `?? 0` at a call site — is how a screen ends up
 * claiming a developer changed 0 lines when the backend simply could not count
 * them.
 *
 * **Nothing in this file counts time.** There is no `formatHours`, no
 * "focus", no "productivity" and no `minutes`, because a git timestamp records
 * that work happened at an instant and nothing about how long it took. The one
 * duration here is {@link formatScanDuration}, which renders how long *the scan
 * of the repository* took — a measurement of git, not of a person. Every
 * sentence that accompanies a figure describes counts: commits, active days,
 * lines changed, branches touched.
 *
 * The chart tokens and the generic number/percent helpers are re-exported from
 * the analytics surface rather than reimplemented, because they are the same
 * tokens: two copies of the colour cycle would give two charts in one app two
 * unrelated palettes.
 */

import {
  CHART_COLORS,
  chartColor,
  formatNumber,
  formatPercent,
  formatSigned,
  formatUpdatedAgo,
} from '@/features/analytics/format'
import {
  DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY,
  MAX_DEVELOPER_WINDOW_DAYS,
  NOT_ENOUGH_DATA,
  type ActivityGranularity,
  type DeveloperMetricRead,
  type DeveloperMetricUnit,
  type GitScanStatus,
  type ISODateTimeString,
} from '@/types/developer'

export { CHART_COLORS, chartColor, formatNumber, formatPercent, formatSigned, formatUpdatedAgo }

/** What a `null` looks like everywhere on this surface. */
export const NO_VALUE = '—'

/**
 * `18` → `18 commits`, `1` → `1 commit`, `0` → `0 commits`, `null` → `—`.
 *
 * **"0 commits" and "—" are different answers and must never be swapped.** A
 * window with no commits is a measurement; a commit count nobody could derive is
 * an absence, and printing `0` for it asserts work that git did not record.
 */
export function formatCommits(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'commit' : 'commits'}`
}

/** `1180` → `1,180 lines`. Additions and deletions are counts of lines, never
 *  of effort. */
export function formatLines(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'line' : 'lines'}`
}

/** `42` → `42 files`; `1` → `1 file`. */
export function formatFiles(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'file' : 'files'}`
}

/** `3` → `3 repositories`. */
export function formatRepositories(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'repository' : 'repositories'}`
}

/** `12` → `12 branches`. */
export function formatBranches(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'branch' : 'branches'}`
}

/**
 * Active days, optionally against the window they were counted in.
 *
 * `17` over a 30-day window → `17 of 30 days`. The denominator is included
 * because "17 active days" alone is a number without a scale — a fortnight and
 * a quarter both tolerate it. A null denominator falls back to the bare count
 * rather than dividing by a window nobody stated.
 */
export function formatActiveDays(
  value: number | null | undefined,
  windowDays?: number | null,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  if (windowDays === null || windowDays === undefined || !Number.isFinite(windowDays)) {
    return `${formatNumber(rounded)} ${rounded === 1 ? 'day' : 'days'}`
  }
  const days = Math.round(windowDays)
  return `${formatNumber(rounded)} of ${formatNumber(days)} ${days === 1 ? 'day' : 'days'}`
}

/**
 * A 0–1 ratio as written: `0.566…` → `0.57`.
 *
 * Prefer {@link formatRatioPercent} where the ratio is a proportion of
 * something the reader can see — `consistency` reads as `57%`, not `0.57`.
 */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return value.toFixed(digits)
}

/** `0.566…` → `57%`. A real zero is `0%`, never a dash. */
export function formatRatioPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return formatPercent(value * 100, digits)
}

/**
 * `recent_momentum`, as a multiple: `1.8` → `1.8×`.
 *
 * The backend's definition is commits in the last 7 days divided by commits in
 * the 7 days before that, and `1.8×` states it without implying a rate of work.
 * **A null is a dash, not `0×`.** The ratio is null precisely when the
 * denominator was zero, which means "no comparison exists" — the opposite of
 * "nothing changed".
 */
export function formatMomentum(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return `${value.toFixed(digits)}×`
}

/** `2.03` → `2.03 commits per day`. The unit is *recorded commits*, not hours. */
export function formatCommitFrequency(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return `${value.toFixed(digits)} ${Math.abs(value) === 1 ? 'commit' : 'commits'} per day`
}

/** `30` → `Last 30 days`; `1` → `Last day`. Null is the dash. */
export function formatWindowLabel(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const days = Math.round(value)
  if (days === 1) return 'Last day'
  return `Last ${formatNumber(days)} days`
}

/** The trailing window a caption should quote, never wider than the API accepts. */
export function formatMaxWindow(): string {
  return `Last ${formatNumber(MAX_DEVELOPER_WINDOW_DAYS)} days`
}

/* ------------------------------------------------------------------ levels */

/**
 * How many commits a window recorded, as a band.
 *
 * **A band over a count, not a verdict about a person.** The thresholds are
 * fixed here rather than left to a chart so the same number cannot be described
 * as "light" on one page and "busy" on another, and the ordering runs from
 * `none` upward: a null is its own answer ({@link NO_VALUE} in the formatter),
 * never the `none` band, because "nothing was counted" and "nothing was
 * recorded" are different claims.
 */
export type ActivityLevel = 'none' | 'low' | 'moderate' | 'high' | 'very-high'

const ACTIVITY_BANDS: readonly { upTo: number; level: ActivityLevel; label: string }[] = [
  { upTo: 0, level: 'none', label: 'No commits recorded' },
  { upTo: 5, level: 'low', label: 'Low commit activity' },
  { upTo: 20, level: 'moderate', label: 'Moderate commit activity' },
  { upTo: 50, level: 'high', label: 'High commit activity' },
  { upTo: Number.POSITIVE_INFINITY, level: 'very-high', label: 'Very high commit activity' },
]

/** The band a commit count falls into, or null when there is no count. */
export function activityLevel(value: number | null | undefined): ActivityLevel | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null
  return ACTIVITY_BANDS.find((band) => value <= band.upTo)?.level ?? 'very-high'
}

/** The band as a sentence: `12` → `Moderate commit activity`. Null → `—`. */
export function formatActivityLevel(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const band = ACTIVITY_BANDS.find((entry) => value <= entry.upTo)
  return band?.label ?? 'Very high commit activity'
}

/* ------------------------------------------------------------------ instants */

/**
 * How long a **scan** took: `842` → `842ms`, `1250` → `1.3s`.
 *
 * This is the wall-clock of the git command, not of anyone. Nothing on this
 * surface may be read as a measure of how long a person worked, which is why
 * this is the only duration in the file and it is named for the scan.
 */
export function formatScanDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  if (value < 0) return NO_VALUE
  if (value < 1000) return `${Math.round(value)}ms`
  return `${(value / 1000).toFixed(1)}s`
}

/**
 * "Scanned 5 minutes ago" from `git_repositories.last_scanned_at`.
 *
 * Null is `Never scanned` rather than a dash: a repository that has never been
 * read is a specific, actionable state, and the page has to tell the user to
 * scan it.
 */
export function formatScannedAgo(
  lastScannedAt: ISODateTimeString | null | undefined,
  nowMs: number = Date.now(),
): string {
  // An unparseable stamp is checked here rather than handed to the shared
  // relative formatter, which would answer "Scanned never updated" — a sentence
  // about an update that did not happen, attached to a scan that did.
  if (!lastScannedAt || Number.isNaN(Date.parse(lastScannedAt))) return 'Never scanned'
  return `Scanned ${formatUpdatedAgo(lastScannedAt, nowMs)}`
}

/** `3 Jul 2026, 14:22` for a commit timestamp; null is the dash. */
export function formatInstant(value: ISODateTimeString | null | undefined): string {
  if (!value) return NO_VALUE
  const at = Date.parse(value)
  if (Number.isNaN(at)) return NO_VALUE
  const date = new Date(at)
  const now = new Date()
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(date)
}

/**
 * The axis label for one activity bucket.
 *
 * Buckets are anchored on an instant (a day, a start-of-week in UTC, a month),
 * so the label is derived from the instant rather than from a slice of the
 * string — slicing `YYYY-MM-DDTHH:mm` would silently re-anchor the bucket in
 * local time, which is the bug this form exists to avoid.
 */
export function formatBucketLabel(
  bucketStart: ISODateTimeString | null | undefined,
  granularity: ActivityGranularity = DEVELOPER_DEFAULT_ACTIVITY_GRANULARITY,
): string {
  if (!bucketStart) return NO_VALUE
  const at = Date.parse(bucketStart)
  if (Number.isNaN(at)) return NO_VALUE
  const date = new Date(at)
  const now = new Date()
  if (granularity === 'month') {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short',
      ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
    }).format(date)
  }
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(date)
}

/**
 * A branch name, or what its absence means.
 *
 * `null` on a repository is a **detached HEAD**, which is a normal state and not
 * an error — so it is spelled out rather than dashed.
 */
export function formatBranchName(name: string | null | undefined): string {
  return name ? name : 'Detached HEAD'
}

/** The first seven characters of a SHA, or the dash. Never the full hash. */
export function formatShortHash(hash: string | null | undefined): string {
  if (!hash) return NO_VALUE
  return hash.slice(0, 7)
}

/** A detected language, or the dash. Never "Other" — uncounted is not a language. */
export function formatLanguage(language: string | null | undefined): string {
  return language ? language : NO_VALUE
}

/* ------------------------------------------------------------------- states */

/**
 * What the last scan did, in one sentence.
 *
 * A failed scan is **data**: the repository carries `last_scan_status: 'error'`
 * and a human sentence in `last_scan_error`, and that sentence is rendered
 * verbatim. It is safe to print because the backend sanitizes it — the user's
 * home directory is stripped and no traceback ever reaches this surface. That
 * pair, rather than an exception, is the whole mechanism behind "a broken
 * repository must never break NEXUS".
 */
export function formatScanOutcome(
  status: GitScanStatus | null | undefined,
  error: string | null | undefined,
): string {
  if (!status) return 'Not scanned yet'
  if (error) return error
  if (status === 'error') return 'The last scan failed without a message.'
  return 'Last scan completed successfully.'
}

/**
 * One metric, formatted by its own unit.
 *
 * The unit is data rather than decoration, so a ratio cannot be printed as a
 * line count. Two keys are special-cased because their unit alone reads badly:
 * `recent_momentum` is a 0–1 ratio that means `1.8×`, and `consistency` is a
 * ratio that means `57%`.
 *
 * **`available: false` and `value: null` both render as the dash**, and neither
 * is ever coerced to a zero — see {@link metricUnavailableReason} for the
 * sentence that goes with it.
 */
export function formatMetricValue(metric: DeveloperMetricRead): string {
  if (!metric.available || metric.value === null) return NO_VALUE
  const value = metric.value
  if (metric.key === 'recent_momentum') return formatMomentum(value)
  switch (metric.unit) {
    case 'ratio':
      return formatRatioPercent(value)
    case 'lines':
      return formatLines(value)
    case 'days':
      return formatActiveDays(value, metric.window_days)
    case 'count':
    case 'score':
      return formatNumber(value)
  }
}

/**
 * Why a metric could not be computed, or null when it could.
 *
 * The backend always supplies a reason and falls back to
 * {@link NOT_ENOUGH_DATA}, so the fallback here keeps the one sentence the
 * contract defines rather than letting a page invent a second phrasing for the
 * same condition.
 */
export function metricUnavailableReason(metric: DeveloperMetricRead): string | null {
  if (metric.available && metric.value !== null) return null
  return metric.reason_if_unavailable ?? NOT_ENOUGH_DATA
}

/** The unit a metric is measured in, as a noun for a caption. Null is the dash. */
export function formatMetricUnit(unit: DeveloperMetricUnit | null | undefined): string {
  if (!unit) return NO_VALUE
  switch (unit) {
    case 'count':
      return 'count'
    case 'lines':
      return 'lines'
    case 'days':
      return 'days'
    case 'ratio':
      return 'ratio'
    case 'score':
      return 'score'
  }
}