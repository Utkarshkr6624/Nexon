/**
 * Presentation helpers for the career surface.
 *
 * **Every formatter here takes a nullable and returns a sentence or a dash.**
 * The career types are almost entirely nullable *on purpose*: every field on a
 * profile is something a person typed, and an absent one means they did not
 * supply it. Turning "you did not say" into a guessed value — a location read
 * off a repository's path, an employer inferred from a commit, a year derived
 * from a commit date — would be inventing a qualification, which is the single
 * thing this surface is forbidden to do.
 *
 * The other rule here is **date range honesty**. `ended_on: null` means a role is
 * *current*, which is a fact about the record; `started_on: null` means the user
 * did not give one, which is an absence. Those are different, and a formatter
 * that rendered both as "Present" would tell a reader a date they never supplied.
 *
 * `.ts` rather than `.tsx` because nothing here renders: these are pure
 * functions, and a JSX-free `.tsx` would only import the metadata tables for no
 * reason.
 */

import { NO_VALUE, formatNumber, formatShortDate } from '@/features/analytics/format'
import {
  CAREER_LEVEL_SCALE,
  evidenceSourceMeta,
} from '@/features/career/components/career-vocabulary'
import type {
  ISODateTimeString,
  SkillGapRead,
  SkillLevelSource,
  SkillRead,
} from '@/types/learning'
import type { DateOnlyString } from '@/types/work'

/* ------------------------------------------------------------------- dates */

/**
 * A date-only column on the profile — a start, an end, an evidence date.
 *
 * `formatShortDate` is reused rather than reimplemented: it is the house
 * formatter for `YYYY-MM-DD`, and date-only values are rendered in local time
 * deliberately, because there is no instant here to convert and a UTC shift
 * would move a start date onto the wrong day for most of the world.
 */
export function formatCareerDate(value: DateOnlyString | null | undefined): string {
  return formatShortDate(value)
}

/** An instant, rendered in UTC, for the created/updated stamps on a profile. */
export function formatCareerInstant(value: ISODateTimeString | null | undefined): string {
  if (typeof value !== 'string' || value.trim().length === 0) return NO_VALUE
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return NO_VALUE
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date)
}

/**
 * A dated record's period, in words.
 *
 * Four states, and they are genuinely four:
 *
 * - both dates → `"March 2021 — June 2024"`
 * - start only, no end → `"March 2021 — current"`, because an open end on a
 *   role or a programme means it is current
 * - end only → `"Until June 2024"`, with no invented start
 * - neither → `"No dates given"`, which is what an undated record is
 *
 * The year is always spelled out here even when it is the current one, unlike
 * the short-date helper: a CV line read without its year is a date range that
 * cannot be checked.
 */
export function describeRecordPeriod(
  startedOn: DateOnlyString | null | undefined,
  endedOn: DateOnlyString | null | undefined,
): string {
  const start = startedOn ? formatRecordMonth(startedOn) : null
  const end = endedOn ? formatRecordMonth(endedOn) : null

  if (start && end) return `${start} — ${end}`
  if (start) return `${start} — current`
  if (end) return `Until ${end}`
  return 'No dates given'
}

/**
 * The month and year of a `YYYY-MM-DD` value, in UTC.
 *
 * **UTC deliberately.** The day a course started is a day in a calendar, not an
 * instant, and reading it in a western timezone shifts it to the previous month
 * for every date in the first nine days of one.
 */
export function formatRecordMonth(value: DateOnlyString | null | undefined): string {
  if (typeof value !== 'string' || value.trim().length === 0) return NO_VALUE
  const match = /^(\d{4})-(\d{2})-/.exec(value.trim())
  if (!match) return NO_VALUE
  const year = Number(match[1])
  const month = Number(match[2])
  if (!Number.isFinite(year) || !Number.isFinite(month)) return NO_VALUE
  const date = new Date(Date.UTC(year, month - 1, 1))
  if (Number.isNaN(date.getTime())) return NO_VALUE
  return new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date)
}

/**
 * Where a piece of evidence came from, in a sentence.
 *
 * The one thing this formatter must never do is merge "you typed this" with
 * "NEXUS derived this": the first is a claim the user is making and the second
 * is an inference about a record they created, and a reader deciding whether to
 * trust a profile needs to know which one they are looking at.
 */
export function describeEvidenceSource(source: string | null | undefined): string {
  return evidenceSourceMeta(source).phrase
}

/* ------------------------------------------------------------------- links */

/**
 * A profile link, shown as its host rather than as its whole URL.
 *
 * `CareerProfileRead.links` is a plain `string[]` of whatever the user pasted, so
 * the value is not guaranteed to parse. **A link that cannot be parsed is
 * rendered as plain text and not as an anchor** — an `<a href>` built from a
 * string that is not a URL is either dead or, worse, navigable somewhere the
 * user did not intend.
 */
export function describeLinkLabel(url: string): string {
  try {
    const parsed = new URL(url)
    const host = parsed.host.replace(/^www\./, '')
    const path = parsed.pathname.replace(/\/+$/, '')
    if (path && path !== '/') return `${host}${path}`
    return host
  } catch {
    return url
  }
}

/** True when a link string parses as an absolute http(s) URL. */
export function isNavigableLink(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:'
  } catch {
    return false
  }
}

/* ------------------------------------------------------------------ levels */

/**
 * A skill level on its scale, in words: `3 of 5`.
 *
 * Clamped to the scale the backend enforces, and a `null` is a dash rather than
 * a zero. On this surface a level is never absent — `current_level` is a plain
 * `number` — so the dash only covers a client handed a partial object.
 */
export function formatCareerLevel(
  level: number | null | undefined,
  scale: number = CAREER_LEVEL_SCALE,
): string {
  if (level === null || level === undefined || !Number.isFinite(level)) return NO_VALUE
  const top = Number.isFinite(scale) && scale > 0 ? Math.trunc(scale) : CAREER_LEVEL_SCALE
  return `${Math.min(Math.max(Math.trunc(level), 1), top)} of ${top}`
}

/**
 * The one sentence a skill figure is allowed to appear in.
 *
 * **The `level_source` is a required argument, not an option.** "3 of 5" on its
 * own is a verdict about a person, and the difference between a claim and an
 * inference is exactly what a reader of a career profile cannot check for
 * themselves. So the sentence is assembled in one place and every surface that
 * shows a level — the overview grid, the development areas panel — goes through
 * it.
 */
export function describeCareerLevel(
  level: number,
  source: SkillLevelSource,
  scale: number = CAREER_LEVEL_SCALE,
): string {
  const formatted = formatCareerLevel(level, scale)
  if (formatted === NO_VALUE) return NO_VALUE
  return source === 'user_defined'
    ? `${formatted}, self-assessed by you`
    : `${formatted}, estimated by NEXUS from recorded activities`
}

/**
 * How long ago something was last recorded, from the backend's own day count.
 *
 * The count is taken as given rather than recomputed from a timestamp, because
 * `days_since_last_activity` is null for a skill with nothing recorded against it
 * and `0` for one recorded today — a difference that recomputing from a rounded
 * "N days ago" string would flatten.
 */
export function describeCareerDaysSince(
  days: number | null | undefined,
  noun = 'activity',
): string {
  if (days === null || days === undefined || !Number.isFinite(days)) {
    return 'Nothing has been recorded against this skill yet.'
  }
  const count = Math.max(0, Math.trunc(days))
  if (count === 0) return `The most recent ${noun} was recorded today.`
  if (count === 1) return `The most recent ${noun} was recorded yesterday.`
  return `The most recent ${noun} was recorded ${formatNumber(count)} days ago.`
}

/**
 * The evidence behind a skill, in words.
 *
 * A zero here is a **real measurement** — nothing has been recorded against this
 * skill — and is said in words rather than dashed, because the dash is reserved
 * for a figure the response did not carry.
 */
export function describeCareerEvidence(count: number | null | undefined): string {
  if (count === null || count === undefined || !Number.isFinite(count)) return NO_VALUE
  const total = Math.max(0, Math.trunc(count))
  if (total === 0) return 'No learning activities recorded against this skill.'
  return `${formatNumber(total)} learning ${total === 1 ? 'activity' : 'activities'} recorded.`
}

/**
 * A skill's last recorded activity, or the absence of one.
 *
 * A null `last_activity_at` means **nothing has ever been recorded against the
 * skill**, which is a different state from one whose last activity is old, and
 * the development-areas panel depends on telling them apart.
 */
export function describeCareerLastActivity(skill: Pick<SkillRead, 'last_activity_at'>): string {
  if (!skill.last_activity_at) return 'No activity has been recorded against this skill.'
  return `Last recorded activity ${formatCareerInstant(skill.last_activity_at)}.`
}

/* -------------------------------------------------------------------- counts */

/**
 * A count from a `by_type` record, or null when the key is absent.
 *
 * **`Record<string, number>` is a sparse wire shape**: a type the response does
 * not carry is not the same as a type with a count of zero, and reading a
 * missing key as `0` would claim the backend counted something it never sent.
 * The caller renders null as "not in this response".
 */
export function countFromRecord(
  counts: Record<string, number> | null | undefined,
  key: string,
): number | null {
  const value = counts?.[key]
  if (value === undefined || value === null || !Number.isFinite(value)) return null
  return value
}

/* --------------------------------------------------- development areas */

export const DEFAULT_DEVELOPMENT_EVIDENCE_THRESHOLD = 3

/**
 * The rows this panel shows, chosen by an explicit and inspectable rule.
 *
 * Exported so the page can show the same count in its heading, and so the rule
 * has one owner: a panel that computed its own membership differently from the
 * heading above it would put two different numbers on the same screen.
 */
export function developmentAreasFromGaps(
  gaps: readonly SkillGapRead[],
  threshold: number | null = DEFAULT_DEVELOPMENT_EVIDENCE_THRESHOLD,
): SkillGapRead[] {
  return gaps.filter((gap) => {
    if (!gap.available) return false
    if (gap.gap <= 0) return false
    if (threshold === null) return true
    return gap.evidence_last_30d < threshold
  })
}

/**
 * The one sentence a row is allowed to say.
 *
 * Exported because the empty state, the heading and the row all need to know
 * what "limited evidence" means, and three hand-written variants of that sentence
 * would eventually disagree about whether a zero is a measurement.
 */
export function describeDevelopmentSentence(
  gap: SkillGapRead,
  windowDays: number | null,
): string {
  const recent = gap.evidence_last_30d
  const noun = recent === 1 ? 'activity' : 'activities'
  const window =
    windowDays === null ? 'the window' : `the last ${formatNumber(windowDays)} days`

  const evidence =
    recent === 0
      ? `No related learning ${noun === 'activity' ? 'activity' : 'activities'} recorded in ${window}.`
      : `${formatNumber(recent)} related learning ${noun} in ${window}.`

  return `${describeCareerLevel(gap.current_level, gap.level_source)}. ${evidence}`
}
