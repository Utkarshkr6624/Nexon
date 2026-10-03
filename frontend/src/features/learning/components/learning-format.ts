/**
 * Presentation helpers for the learning surface.
 *
 * **Every formatter here takes a nullable and returns a sentence rather than a
 * substitute value.** That is the whole job of this file. `LearningGoalRead` and
 * its neighbours are full of genuine nulls that mean *this was not measured* —
 * a goal with no deadline, an activity with no duration, a skill with nothing
 * ever recorded against it — and the two ways to get them wrong (`?? 0`,
 * `value || 0`) both end up asserting something the backend declined to say. A
 * goal with no target date has not "0 days remaining"; an activity with no
 * duration was not a zero-minute session.
 *
 * The other rule this file discharges is **attribution**: a level never renders
 * as a bare number, so every level phrase takes a `SkillLevelSource` and names
 * who said it. A helper that returned `"3/5"` would let three call sites end up
 * with three different ways of saying it, and one of them would say nothing at
 * all.
 *
 * `.ts` rather than `.tsx` because nothing here renders: these are pure
 * functions, and a JSX-free `.tsx` would only import the metadata tables for no
 * reason.
 */

import { NO_VALUE, formatMinutes, formatNumber, formatShortDate } from '@/features/analytics/format'
import {
  LEARNING_LEVEL_SCALE,
  levelSourcePhrase,
} from '@/features/learning/components/learning-vocabulary'
import type {
  ISODateTimeString,
  LearningActivityBucketRead,
  LearningGoalRead,
  SkillLevelSource,
} from '@/types/learning'
import type { DateOnlyString } from '@/types/work'

/* --------------------------------------------------------------------- levels */

/**
 * A level on its scale, in words: `3 of 5`.
 *
 * Clamped to the scale the backend enforces so a payload carrying a `6` renders
 * as the top of the scale rather than as an out-of-range number nobody has ever
 * been asked to interpret. A `null` is a dash, because a level is never absent
 * on this surface — `current_level` is a plain `number` — and the dash is there
 * only for the case where a client was handed a partial object.
 */
export function formatLevelOfScale(
  level: number | null | undefined,
  scale: number = LEARNING_LEVEL_SCALE,
): string {
  if (level === null || level === undefined || !Number.isFinite(level)) return NO_VALUE
  const top = Number.isFinite(scale) && scale > 0 ? Math.trunc(scale) : LEARNING_LEVEL_SCALE
  return `${Math.min(Math.max(Math.trunc(level), 1), top)} of ${top}`
}

/**
 * A level with the claim attached, which is the only form allowed on a card.
 *
 * `"3 of 5, self-assessed by you"` for a level the person set, and
 * `"3 of 5, estimated by NEXUS from recorded activities"` for one NEXUS derived.
 * The second is longer on purpose: an inference has to show what it rests on,
 * and a reader who cannot see the difference between a claim and an estimate
 * cannot argue with either.
 */
export function describeLevelClaim(
  level: number | null | undefined,
  source: SkillLevelSource,
  scale: number = LEARNING_LEVEL_SCALE,
): string {
  const formatted = formatLevelOfScale(level, scale)
  if (formatted === NO_VALUE) return NO_VALUE
  return `${formatted}, ${levelSourcePhrase(source)}`
}

/**
 * The distance between where a skill is and where its target sits.
 *
 * A `gap` of `0` is a **real measurement** — the recorded level has reached the
 * target — and says so, rather than rendering as a dash, which would read as
 * "not measured". `null` is the only value that becomes a dash.
 */
export function describeGap(gap: number | null | undefined): string {
  if (gap === null || gap === undefined || !Number.isFinite(gap)) return NO_VALUE
  const levels = Math.max(0, Math.trunc(gap))
  if (levels === 0) return 'No gap — the recorded level has reached the target'
  return `${formatNumber(levels)} ${levels === 1 ? 'level' : 'levels'} between the recorded level and the target`
}

/* --------------------------------------------------------------- dates & time */

/**
 * An instant, rendered in UTC.
 *
 * **UTC deliberately, and for the same reason the developer surface pins it:**
 * the series buckets are floored to UTC midnight, so a local-zone formatter
 * would slide a bucket into its neighbour and make Monday's activity appear on
 * a Sunday. A reader's own activity is arguably more natural in local time, but
 * the *window* it is counted in is a UTC construct and showing the two in
 * different zones on one card is worse than showing both in one.
 */
export function formatLearningInstant(value: ISODateTimeString | null | undefined): string {
  const date = parseInstant(value)
  if (!date) return NO_VALUE
  return formatUtc(date, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** The clock part, for a row whose day is already stated beside it. */
export function formatLearningClock(value: ISODateTimeString | null | undefined): string {
  const date = parseInstant(value)
  if (!date) return NO_VALUE
  return formatUtc(date, { hour: '2-digit', minute: '2-digit' })
}

/**
 * A date-only column — a goal's target date, a record's start.
 *
 * `formatShortDate` is reused rather than reimplemented: it is the house
 * formatter for `YYYY-MM-DD`, and a second copy would be a second place for the
 * "include the year only when it differs" rule to be got wrong. Date-only values
 * are rendered in local time deliberately — there is no instant here to convert.
 */
export function formatLearningDate(value: DateOnlyString | null | undefined): string {
  return formatShortDate(value)
}

/**
 * A target date, in words, against today.
 *
 * A null target date means **no deadline was set**, which is a legitimate state
 * and reads as one; it is never phrased as "0 days remaining". A date that has
 * passed is described as *past its target date* rather than as "overdue", because
 * "overdue" is a verdict about a plan and the only fact on offer is that the
 * date is behind today's.
 */
export function describeTargetDate(
  targetDate: DateOnlyString | null | undefined,
  nowMs: number = Date.now(),
): string {
  if (!targetDate) return 'No target date set.'

  const today = startOfUtcDay(new Date(nowMs))
  const due = parseDateOnly(targetDate)
  if (!due) return `Target date ${targetDate}.`

  const days = Math.round((due.getTime() - today.getTime()) / DAY_MS)
  const shown = formatShortDate(targetDate)

  if (days === 0) return `Target date ${shown} — today.`
  if (days > 0) {
    return `Target date ${shown} — ${formatNumber(days)} ${days === 1 ? 'day' : 'days'} from today.`
  }
  const past = Math.abs(days)
  return `Target date ${shown} — ${formatNumber(past)} ${past === 1 ? 'day' : 'days'} past it.`
}

/** `"Last 30 days"`, for the sentence a window count needs to be true. */
export function describeWindow(windowDays: number | null | undefined): string {
  if (windowDays === null || windowDays === undefined || !Number.isFinite(windowDays)) {
    return 'the whole recorded history'
  }
  const days = Math.max(0, Math.trunc(windowDays))
  return `the last ${formatNumber(days)} ${days === 1 ? 'day' : 'days'}`
}

/* -------------------------------------------------------------------- amounts */

/**
 * How long an activity took — or, more often, that it has no duration at all.
 *
 * `duration_minutes` is null for an activity that was an **event** rather than a
 * span: a page was opened, and opening a page has no length. Saying "0 minutes"
 * there would claim a measured zero-length session, and saying "—" alone would
 * leave a reader guessing whether the figure was missing or the activity was.
 */
export function describeDuration(durationMinutes: number | null | undefined): string {
  if (durationMinutes === null || durationMinutes === undefined) {
    return 'Recorded as an event — no duration was attached to it.'
  }
  if (!Number.isFinite(durationMinutes)) {
    return 'Recorded as an event — no duration was attached to it.'
  }
  const minutes = Math.max(0, Math.trunc(durationMinutes))
  if (minutes === 0) return 'Recorded with a duration of 0 minutes, as a measurement.'
  return `${formatMinutes(minutes)} of recorded time.`
}

/**
 * How much of a goal is done, and whose figure that is.
 *
 * `progress` is the user's own percentage — NEXUS deliberately does not compute
 * it from activity, because a percentage derived from the *absence* of a record
 * would be a claim about commitment rather than about progress. So the sentence
 * always says the figure is the user's, and a `0` reads as a real measurement
 * ("the figure you set has not moved") rather than as a hole.
 */
export function describeProgress(goal: LearningGoalRead): string {
  const value = Number.isFinite(goal.progress) ? Math.min(Math.max(Math.trunc(goal.progress), 0), 100) : 0
  if (value === 0) return '0% — the progress figure is the one you set, and it has not moved.'
  if (value === 100) return '100% — the progress figure you set is complete.'
  return `${formatNumber(value)}% — the progress figure is the one you set.`
}

/**
 * A count of recorded learning activities, in words.
 *
 * Zero is a real measurement on this surface — `evidence_count` is a plain
 * `number` — so it is rendered as "No learning activities recorded" rather than
 * as a dash. The distinction that matters is between *none have been recorded*
 * (this sentence) and *none could be counted* (the backend's
 * `reason_if_unavailable`, shown separately by the cards).
 */
export function describeEvidenceCount(count: number | null | undefined): string {
  if (count === null || count === undefined || !Number.isFinite(count)) return NO_VALUE
  const total = Math.max(0, Math.trunc(count))
  if (total === 0) return 'No learning activities recorded against this skill.'
  return `${formatNumber(total)} learning ${total === 1 ? 'activity' : 'activities'} recorded against this skill.`
}

/**
 * How long ago something was last recorded, from the backend's own day count.
 *
 * The count is taken as given rather than recomputed from a timestamp, because
 * `days_since_last_activity` is null for a skill with no recorded activity and
 * `0` for one recorded today — a difference that recomputing from a rounded
 * "N days ago" string would flatten.
 */
export function describeDaysSince(
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
 * When a skill was last touched, from its own timestamp.
 *
 * A null `last_activity_at` means **nothing has ever been recorded against the
 * skill**, which is different from "the last activity was a long time ago" — the
 * first is a cold skill and the second is one with history that has gone quiet,
 * and only the second is a thing a reader might want to do something about.
 */
export function describeLastActivity(lastActivityAt: ISODateTimeString | null | undefined): string {
  if (!lastActivityAt) return 'Nothing has been recorded against this skill yet.'
  const date = parseInstant(lastActivityAt)
  if (!date) return 'Nothing has been recorded against this skill yet.'
  return `Last recorded activity ${formatLearningInstant(lastActivityAt)}.`
}

/**
 * Where an activity came from, in words.
 *
 * `source_type`/`source_id` are the traceable half of "evidence is traceable":
 * a null pair means the person typed the activity in, and anything else names
 * the record it was derived from. **Derived is never presented as typed** —
 * that would claim a provenance the row does not have.
 */
export function describeActivitySource(sourceType: string | null | undefined): string {
  const source = sourceType?.trim()
  if (!source) return 'Entered by you.'
  switch (source) {
    case 'manual':
      return 'Entered by you.'
    case 'task':
      return 'Derived from a task record.'
    case 'note':
      return 'Derived from a note.'
    case 'project':
      return 'Derived from a project.'
    case 'repository':
      return 'Derived from a repository scan.'
    default:
      return `Derived from a ${source} record.`
  }
}

/* ---------------------------------------------------------------- the timeline */

/** The x-axis label for one activity bucket, in UTC for the reason above. */
export function formatBucketLabel(bucketStart: string | null | undefined): string {
  const date = parseInstant(bucketStart)
  if (!date) return NO_VALUE
  return formatUtc(date, { day: 'numeric', month: 'short' }, { includeYear: true })
}

/**
 * What the buckets say about continuity, with the grain **derived from the data
 * rather than assumed**.
 *
 * The width of a bucket is read off `bucket_end - bucket_start` rather than
 * taken from a requested grain, so the run is described in days, weeks or months
 * according to what the rows actually are. A surface that hard-coded "days"
 * would quietly lie the moment a caller asked the backend for weekly buckets,
 * and the number beside it — a count of *records*, never of hours — would still
 * be right, which is the worst combination: a correct count attached to a false
 * unit.
 */
export interface LearningStreak {
  /** Consecutive buckets, counting back from the most recent active one. */
  runLength: number
  /** Derived from the bucket width, never from the requested granularity. */
  granularity: 'day' | 'week' | 'month'
  /** How many buckets across the whole series carried at least one activity. */
  activeBuckets: number
  /** How many buckets the series covers in total. */
  totalBuckets: number
  /** The most recent bucket that carried an activity, or null if none did. */
  latestActiveAt: ISODateTimeString | null
}

export function describeStreak(
  buckets: readonly LearningActivityBucketRead[] | null | undefined,
): LearningStreak {
  const rows = buckets ?? []
  const empty: LearningStreak = {
    runLength: 0,
    granularity: 'day',
    activeBuckets: 0,
    totalBuckets: rows.length,
    latestActiveAt: null,
  }
  if (rows.length === 0) return empty

  const widthDays = bucketWidthInDays(rows)
  const granularity: LearningStreak['granularity'] =
    widthDays > 45 ? 'month' : widthDays > 1.5 ? 'week' : 'day'

  let runLength = 0
  let latestActiveAt: ISODateTimeString | null = null
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const bucket = rows[index]
    if (!bucket) continue
    if (bucket.activities > 0) {
      runLength += 1
      latestActiveAt = bucket.bucket_start
    } else if (runLength > 0) {
      break
    }
  }

  return {
    runLength,
    granularity,
    activeBuckets: rows.filter((bucket) => bucket.activities > 0).length,
    totalBuckets: rows.length,
    latestActiveAt,
  }
}

/**
 * The streak, said out loud.
 *
 * **`0` is stated as "no run", not as a failure.** A window with nothing in it
 * is an ordinary state for an account that has not started recording, and a
 * streak counter that renders `0` beside a red accent is a progress bar for a
 * judgement NEXUS has no standing to make.
 */
export function streakPhrase(streak: LearningStreak): string {
  if (streak.totalBuckets === 0) {
    return 'No activity buckets to read. The window has no series to summarise yet.'
  }

  const unit = streak.granularity === 'day' ? 'day' : streak.granularity
  const plural = streak.runLength === 1 ? unit : `${unit}s`
  const active = `${formatNumber(streak.activeBuckets)} of the ${formatNumber(
    streak.totalBuckets,
  )} ${unit}${streak.totalBuckets === 1 ? '' : 's'} in the window carried at least one recorded activity.`

  if (streak.runLength === 0) return active
  return `${formatNumber(streak.runLength)} consecutive ${plural} with recorded activity, ending ${
    formatLearningInstant(streak.latestActiveAt)
  }. ${active}`
}

/* -------------------------------------------------------------------- helpers */

const DAY_MS = 24 * 60 * 60 * 1000

function parseInstant(value: string | null | undefined): Date | null {
  if (typeof value !== 'string' || value.trim().length === 0) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `YYYY-MM-DD` read as UTC midnight, so a day count cannot drift by an offset. */
function parseDateOnly(value: DateOnlyString): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim())
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
  return new Date(Date.UTC(year, month - 1, day))
}

function startOfUtcDay(date: Date): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
}

/** The width of one bucket in days, read off the first row that states one. */
function bucketWidthInDays(rows: readonly LearningActivityBucketRead[]): number {
  for (const bucket of rows) {
    const start = parseInstant(bucket.bucket_start)
    const end = parseInstant(bucket.bucket_end)
    if (!start || !end) continue
    const width = (end.getTime() - start.getTime()) / DAY_MS
    if (Number.isFinite(width) && width > 0) return width
  }
  return 1
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