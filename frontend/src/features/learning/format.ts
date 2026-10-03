/**
 * Presentation helpers for the Phase 9 learning and career surface.
 *
 * **Every formatter here takes `number | null` and returns {@link NO_VALUE} for
 * `null`.** That is the whole job of the file, and it is the same rule
 * `features/analytics/format.ts` and `features/developer/format.ts` follow: a
 * nullable figure renders as "no measurement" and a `0` renders as `0`.
 * Collapsing the two — the default `toFixed` on a `null`, or `?? 0` at a call
 * site — is how a screen ends up claiming a learner completed 0% of nothing, or
 * that an account has no repositories when the truth is that nobody scanned
 * them.
 *
 * **A level is never rendered on its own.** {@link formatSkillLevel} is the only
 * way to put a level on screen, and it always carries its `level_source` beside
 * it: `3/5 · self-assessed` or `3/5 · system estimate`. A source that is missing
 * yields the dash rather than a bare number, because a number nobody can
 * attribute is not a level. A *target* level is the one exception —
 * {@link formatTargetLevel} may stand alone — because a target is a claim the
 * user made about where they want to get to, not a measurement of where they
 * are.
 *
 * **Nothing here counts understanding.** The unit throughout is *recorded
 * activity*: `formatSessionCount` counts sessions somebody logged,
 * `formatLearningMinutes` sums durations they supplied, and `formatStreakDays`
 * counts consecutive days carrying a record. `resource_viewed` is in the
 * vocabulary because a page really was opened, and a screen that lumps it in
 * with `concept_learned` on a chart is making a claim this file exists to stop.
 *
 * **Sentences come from the backend where the backend has one.** A gap's
 * `explanation`, a metric's `definition` and `explanation`, a summary's
 * `summary` and an unavailable metric's `reason_if_unavailable` are all rendered
 * verbatim; {@link metricUnavailableReason} and {@link gapUnavailableReason} are
 * the two that decide between "the backend said why" and "the fallback sentence".
 *
 * The chart tokens and the generic number/percent/minute helpers are re-exported
 * from the analytics surface rather than reimplemented, because they are the same
 * tokens: two copies of the colour cycle would give two charts in one app two
 * unrelated palettes.
 */

import {
  CHART_COLORS,
  NO_VALUE,
  chartColor,
  formatNumber,
  formatPercent,
  formatSigned,
  formatUpdatedAgo,
} from '@/features/analytics/format'

import type { DateOnlyString } from '@/types/analytics'
import { todayDateOnly } from '@/types/analytics'
import {
  INSUFFICIENT_DATA_MESSAGE,
  LEARNING_MIN_EVIDENCE_FOR_ESTIMATE,
  MAX_SKILL_LEVEL,
  MIN_SKILL_LEVEL,
  type CareerEvidenceType,
  type CareerRecordKind,
  type LearningActivityType,
  type LearningGoalStatus,
  type LearningMetricRead,
  type LearningMetricUnit,
  type SkillGapRead,
  type SkillLevelSource,
} from '@/types/learning'
import type { ISODateTimeString } from '@/types/api'

export {
  CHART_COLORS,
  NO_VALUE,
  chartColor,
  formatNumber,
  formatPercent,
  formatSigned,
  formatUpdatedAgo,
  INSUFFICIENT_DATA_MESSAGE,
}

/* ------------------------------------------------------------------ counts */

/**
 * `6` → `6 sessions`; `1` → `1 session`; `0` → `0 sessions`; `null` → `—`.
 *
 * **"0 sessions" and "—" are different answers and must never be swapped.** A
 * window in which nobody recorded a session is a measurement; a count nobody
 * could derive is an absence, and printing `0` for the second asserts activity
 * that does not exist.
 */
export function formatSessionCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'session' : 'sessions'}`
}

/**
 * `6` → `6 activities`; `1` → `1 activity`; `0` → `0 activities`; `null` → `—`.
 *
 * An *activity* is a record somebody made, not evidence that anything was
 * learned; the count is of rows in `learning_activities` and nothing else. The
 * same renderer covers an evidence count, which is also a count of records.
 */
export function formatActivityCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'activity' : 'activities'}`
}

/**
 * Recorded learning time: `135` → `2h 15m`; `0` → `0m`; `null` → `—`.
 *
 * **A null here means nobody said how long it took**, which is not the same as a
 * session that took no time — the backend sums only the activities that carried
 * a duration and returns null when none did. `0m` therefore means "recorded, and
 * the recorded duration was zero", which is a real and slightly odd answer, and
 * it stays distinguishable from the dash beside it.
 */
export function formatLearningMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const minutes = Math.round(value)
  const sign = minutes < 0 ? '-' : ''
  const total = Math.abs(minutes)
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (hours === 0) return `${sign}${rest}m`
  if (rest === 0) return `${sign}${formatNumber(hours)}h`
  return `${sign}${formatNumber(hours)}h ${rest}m`
}

/**
 * `3` → `3 skills`; `0` → `0 skills`; `null` → `—`. A count of tracked skills,
 * never a claim about any of them.
 */
export function formatSkillCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  return `${formatNumber(rounded)} ${rounded === 1 ? 'skill' : 'skills'}`
}

/**
 * Days carrying a recorded activity, against the window they were counted in.
 *
 * `12` over a 30-day window → `12 of 30 days recorded`. The denominator is
 * included because "12 active days" alone is a number without a scale — a
 * fortnight and a quarter tolerate it very differently. A missing denominator
 * falls back to the bare count rather than dividing by a window nobody stated.
 */
export function formatActiveDays(
  value: number | null | undefined,
  windowDays?: number | null,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Math.round(value)
  if (windowDays === null || windowDays === undefined || !Number.isFinite(windowDays)) {
    return `${formatNumber(rounded)} recorded ${rounded === 1 ? 'day' : 'days'}`
  }
  const days = Math.round(windowDays)
  return `${formatNumber(rounded)} of ${formatNumber(days)} ${days === 1 ? 'day' : 'days'} recorded`
}

/**
 * Consecutive days carrying a recorded activity.
 *
 * **`0` renders as `No consecutive days recorded`, not as a dash** — a streak of
 * zero is what the buckets say when the last day carries nothing. A null is the
 * one case that yields the insufficient-data sentence rather than a number: the
 * caller had nothing to count days from, which is not the same as counting none.
 *
 * This counts *recorded days*. It is not a measure of a habit, of consistency,
 * or of a person, and a screen must not pair it with a judgemental word.
 */
export function formatStreakDays(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return INSUFFICIENT_DATA_MESSAGE
  }
  const rounded = Math.round(value)
  if (rounded <= 0) return 'No consecutive days recorded'
  if (rounded === 1) return '1-day streak of recorded activity'
  return `${formatNumber(rounded)}-day streak of recorded activity`
}

/**
 * How long ago anything was recorded against a skill.
 *
 * `0` → `Activity recorded today`; `1` → `Activity recorded yesterday`;
 * `null` → `Nothing has been recorded`. The null case is a specific state — no
 * `learning_activities` row names this skill — and it is spelled out rather than
 * dashed, because "never" and "an unknown number of days ago" are different
 * answers and the gap list has to be able to say which it is.
 */
export function formatDaysSinceLastActivity(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'Nothing has been recorded'
  }
  const days = Math.round(value)
  if (days <= 0) return 'Activity recorded today'
  if (days === 1) return 'Activity recorded yesterday'
  return `No recorded activity for ${formatNumber(days)} days`
}

/* ------------------------------------------------------------------ levels */

/**
 * How a `level_source` may be described.
 *
 * `user_defined` becomes **self-assessed** and `system_estimate` becomes
 * **system estimate**. The wording is the product rule in miniature: the first
 * is a claim the person is making and NEXUS merely records, and the second is an
 * inference it must be able to show its working for. There is no third label,
 * because a level nobody can attribute is not a level.
 */
export const LEVEL_SOURCE_LABELS: Record<SkillLevelSource, string> = {
  user_defined: 'self-assessed',
  system_estimate: 'system estimate',
}

/** The attribution for a level source; the dash when there is none to give. */
export function formatLevelSource(source: SkillLevelSource | null | undefined): string {
  if (!source) return NO_VALUE
  return LEVEL_SOURCE_LABELS[source] ?? NO_VALUE
}

/**
 * A level, with the only words allowed next to it.
 *
 * `3` with `user_defined` → `3/5 · self-assessed`.
 * `3` with `system_estimate` → `3/5 · system estimate`.
 * `3` with **no source** → {@link NO_VALUE} — never a bare `3/5`, and never
 * `3/5 · unknown`, because "unknown" is not an attribution and rendering it
 * would be the same unattributed number wearing a label.
 *
 * This is the sentence the product wants: *"Your current self-assessed level is
 * 2/5. NEXUS recorded 6 related learning activities in the last 30 days."*
 * Written as a tile, the level is the number and the explanation beside it is
 * the backend's own `explanation`.
 */
export function formatSkillLevel(
  level: number | null | undefined,
  source: SkillLevelSource | null | undefined,
): string {
  if (level === null || level === undefined || !Number.isFinite(level)) return NO_VALUE
  if (!source) return NO_VALUE
  return `${Math.round(level)}/${MAX_SKILL_LEVEL} · ${LEVEL_SOURCE_LABELS[source]}`
}

/**
 * Where the user wants to get to: `4` → `4/5`; `null` → `—`.
 *
 * May stand alone, unlike {@link formatSkillLevel}: a target is a claim the user
 * made, not a measurement of anybody, so there is no inference to attribute.
 */
export function formatTargetLevel(level: number | null | undefined): string {
  if (level === null || level === undefined || !Number.isFinite(level)) return NO_VALUE
  return `${Math.round(level)}/${MAX_SKILL_LEVEL}`
}

/**
 * The distance from a skill's current level to its target.
 *
 * `0` → `At target level`, which is a **measurement** and is paired with
 * `available: true` by the backend. `3` → `3 levels to go`. `null` → the dash:
 * the levels could not be compared, and `reason_if_unavailable` says why.
 */
export function formatSkillGap(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const gap = Math.round(value)
  if (gap <= 0) return 'At target level'
  return `${formatNumber(gap)} ${gap === 1 ? 'level' : 'levels'} to go`
}

/**
 * How much recorded evidence backs a system estimate: `42` → `42%`.
 *
 * **Never a measure of the user.** `confidence` is 0–100 and means how much
 * evidence NEXUS had, nothing else. It is `0` for a `user_defined` level as a
 * plain measurement — nothing was inferred, so there is no confidence to
 * report — and `0%` therefore renders as `0%` rather than the dash.
 */
export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return formatPercent(Math.round(value))
}

/**
 * What is standing behind a level, as a sentence.
 *
 * A `user_defined` level is the user's own claim and cites nothing. A
 * `system_estimate` cites the records it was derived from, and below
 * {@link LEARNING_MIN_EVIDENCE_FOR_ESTIMATE} activities says the estimate was
 * refused rather than offering one.
 */
export function formatEvidenceBacking(
  evidenceCount: number | null | undefined,
  source: SkillLevelSource | null | undefined,
): string {
  if (source === 'user_defined') return 'Set by you'
  if (evidenceCount === null || evidenceCount === undefined || !Number.isFinite(evidenceCount)) {
    return INSUFFICIENT_DATA_MESSAGE
  }
  const count = Math.round(evidenceCount)
  if (count < LEARNING_MIN_EVIDENCE_FOR_ESTIMATE) {
    return `${formatActivityCount(count)} recorded — not enough to estimate a level yet`
  }
  return `${formatActivityCount(count)} recorded`
}

/* --------------------------------------------------------------- percentages */

/**
 * A goal's own progress: `42` → `42%`; `0` → `0%`; `null` → `—`.
 *
 * The figure is 0–100 as the user set it, and it is the user's number: NEXUS
 * does not derive it from activity, because a percentage inferred from the
 * absence of an activity is a claim about commitment rather than about
 * progress. A `null` here is the mean over no goals at all — see
 * `goal_progress` in the feature vector.
 */
export function formatProgressPercent(
  value: number | null | undefined,
  digits = 0,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return formatPercent(value, digits)
}

/**
 * A 0–1 ratio as a percentage: `0.566…` → `57%`; `0` → `0%`; `null` → `—`.
 *
 * Used for `completion_rate` and `learning_consistency`. **The null is
 * load-bearing**: `completion_rate` is null over an empty denominator, which
 * means "no goal has reached a terminal state", not "none of your goals are
 * complete". Printing `0%` there would be the single most misleading number this
 * surface could show.
 */
export function formatRatioPercent(
  value: number | null | undefined,
  digits = 0,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return formatPercent(value * 100, digits)
}

/**
 * Recorded activities per tracked skill, per week: `1.4` → `1.4 per skill per
 * week`; `null` → `—`.
 *
 * Null when no activity named a skill, because dividing by a per-skill
 * denominator that does not exist is a number about nothing. A real `0` means
 * skills are tracked and none of them recorded anything in the window.
 */
export function formatSkillActivityFrequency(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return `${value.toFixed(digits)} per skill per week`
}

/** `0.92` → `0.92`, for the raw ratio beside a percentage. */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return value.toFixed(digits)
}

/* --------------------------------------------------------------- deadlines */

/** How a deadline should be read, for the tone a card picks. */
export type DeadlineUrgency = 'overdue' | 'today' | 'soon' | 'later' | 'unknown'

/**
 * The direction of a deadline distance, in days from today.
 *
 * Negative is **overdue**, zero is **due today**, and the two are kept apart
 * because a goal due today is not a late goal. `unknown` is a null distance:
 * `goal_deadline_distance_days` is null when no goal carries a deadline at all,
 * and a card that tinted that "overdue" would be inventing a deadline.
 */
export function deadlineUrgency(days: number | null | undefined): DeadlineUrgency {
  if (days === null || days === undefined || !Number.isFinite(days)) return 'unknown'
  const rounded = Math.round(days)
  if (rounded < 0) return 'overdue'
  if (rounded === 0) return 'today'
  if (rounded <= 7) return 'soon'
  return 'later'
}

/**
 * How far away a deadline is, with the direction in words.
 *
 * `-5` → `Overdue by 5 days`; `0` → `Due today`; `1` → `Due tomorrow`;
 * `12` → `Due in 12 days`; `null` → {@link NO_VALUE}.
 *
 * **A null is not a zero.** `goal_deadline_distance_days` is null when no goal
 * carries a `target_date`, and "no deadline" must never render as "due today".
 */
export function formatDeadlineDistance(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return NO_VALUE
  const rounded = Math.round(days)
  if (rounded < 0) {
    const overdue = Math.abs(rounded)
    return `Overdue by ${formatNumber(overdue)} ${overdue === 1 ? 'day' : 'days'}`
  }
  if (rounded === 0) return 'Due today'
  if (rounded === 1) return 'Due tomorrow'
  return `Due in ${formatNumber(rounded)} days`
}

/**
 * A deadline distance measured from a date rather than supplied by the backend.
 *
 * The same wording as {@link formatDeadlineDistance}, for a `target_date` the
 * client is holding rather than a figure the API computed. `today` is a
 * parameter so a caller can render consistently; it defaults to the real clock.
 * An unparseable date yields the dash rather than a guess.
 */
export function formatDeadlineFromDate(
  targetDate: DateOnlyString | null | undefined,
  today: DateOnlyString = todayDateOnly(),
): string {
  if (!targetDate) return NO_VALUE
  const [year, month, day] = targetDate.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return NO_VALUE
  const [todayYear, todayMonth, todayDay] = today.split('-').map(Number)
  if (
    todayYear === undefined ||
    todayMonth === undefined ||
    todayDay === undefined ||
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(todayYear) ||
    !Number.isFinite(todayMonth) ||
    !Number.isFinite(todayDay)
  ) {
    return NO_VALUE
  }
  // Compared at UTC midnight on both sides so the answer does not change with
  // the viewer's own timezone: a deadline is a date, not an instant.
  const target = Date.UTC(year, month - 1, day)
  const now = Date.UTC(todayYear, todayMonth - 1, todayDay)
  return formatDeadlineDistance(Math.round((target - now) / 86_400_000))
}

/* ------------------------------------------------------------------ windows */

/** `30` → `Last 30 days`; `1` → `Last day`; `null` → `—`. */
export function formatWindowLabel(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return NO_VALUE
  const rounded = Math.round(days)
  if (rounded <= 1) return 'Last day'
  return `Last ${formatNumber(rounded)} days`
}

/** The 1–5 scale as a caption, e.g. `1–5`. */
export const SKILL_LEVEL_SCALE_LABEL = `${MIN_SKILL_LEVEL}–${MAX_SKILL_LEVEL}`

/* ------------------------------------------------------------------ instants */

/** `14:22` style: a bucket edge or an activity instant. Null is the dash. */
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

/** `Mar 2024` for a month-precision field; null is the dash. */
export function formatMonthYear(value: DateOnlyString | null | undefined): string {
  if (!value) return NO_VALUE
  const [year, month] = value.split('-').map(Number)
  if (year === undefined || month === undefined) return NO_VALUE
  if (!Number.isFinite(year) || !Number.isFinite(month)) return NO_VALUE
  const date = new Date(year, month - 1, 1)
  if (Number.isNaN(date.getTime())) return NO_VALUE
  return new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric' }).format(date)
}

/**
 * A dated record's span: `Mar 2024 – present`.
 *
 * **`ended_on` null means current**, which is a real state and the normal one for
 * employment in progress — not an unknown date, and not a dash. The two ends may
 * both be null, in which case the record carries no dates at all and the dash is
 * the honest answer.
 */
export function formatRecordSpan(
  startedOn: DateOnlyString | null | undefined,
  endedOn: DateOnlyString | null | undefined,
): string {
  const start = formatMonthYear(startedOn)
  const end = formatMonthYear(endedOn)
  if (start === NO_VALUE && end === NO_VALUE) return NO_VALUE
  if (start === NO_VALUE) return end
  if (end === NO_VALUE) return `${start} – present`
  return `${start} – ${end}`
}

/** `6 Mar 2026` — a full date, for evidence rows and deadlines. */
export function formatDateOnly(value: DateOnlyString | null | undefined): string {
  if (!value) return NO_VALUE
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return NO_VALUE
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return NO_VALUE
  const date = new Date(year, month - 1, day)
  if (Number.isNaN(date.getTime())) return NO_VALUE
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

/* -------------------------------------------------------------- vocabulary */

function humanise(value: string): string {
  return value.replace(/_/g, ' ')
}

/** What kind of event was recorded, in words. `resource_viewed` reads as
 *  "resource viewed" — a page was opened, which is the whole claim. */
export const ACTIVITY_TYPE_LABELS: Record<LearningActivityType, string> = {
  study_session: 'Study session',
  task_completed: 'Task completed',
  note_created: 'Note created',
  resource_viewed: 'Resource viewed',
  concept_learned: 'Concept learned',
  project_completed: 'Project completed',
  coding_activity: 'Coding activity',
}

/**
 * The activity type as a label, with a humanised fallback.
 *
 * The fallback exists because `activity_type` is a closed union on the wire but
 * a string in the response, and a backend that grows a member must not make the
 * client render `undefined`.
 */
export function activityTypeLabel(value: LearningActivityType | null | undefined): string {
  if (!value) return NO_VALUE
  return ACTIVITY_TYPE_LABELS[value] ?? humanise(value)
}

/** Where a goal sits. `archived` is not `completed`, and the labels keep them
 *  apart so a count of outstanding work cannot include a dismissed goal. */
export const GOAL_STATUS_LABELS: Record<LearningGoalStatus, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  paused: 'Paused',
  completed: 'Completed',
  archived: 'Archived',
}

export function goalStatusLabel(value: LearningGoalStatus | null | undefined): string {
  if (!value) return NO_VALUE
  return GOAL_STATUS_LABELS[value] ?? humanise(value)
}

/** A thing worth putting forward. `certification` is one the *user* holds; it is
 *  labelled, never issued, here. */
export const EVIDENCE_TYPE_LABELS: Record<CareerEvidenceType, string> = {
  project_completed: 'Project completed',
  feature_shipped: 'Feature shipped',
  repository_activity: 'Repository activity',
  skill_activity: 'Skill activity',
  learning_milestone: 'Learning milestone',
  certification: 'Certification',
  achievement: 'Achievement',
}

export function evidenceTypeLabel(value: CareerEvidenceType | null | undefined): string {
  if (!value) return NO_VALUE
  return EVIDENCE_TYPE_LABELS[value] ?? humanise(value)
}

/** A CV section rather than an achievements section. */
export const RECORD_KIND_LABELS: Record<CareerRecordKind, string> = {
  education: 'Education',
  experience: 'Experience',
  certification: 'Certification',
}

export function recordKindLabel(value: CareerRecordKind | null | undefined): string {
  if (!value) return NO_VALUE
  return RECORD_KIND_LABELS[value] ?? humanise(value)
}

/**
 * Where a row came from.
 *
 * `manual` → `Added by you`. Anything else is a subsystem the backend derived it
 * from, and the raw value is printed rather than mapped: a client cannot verify
 * a derivation it did not perform, so inventing a friendly name for it would be
 * asserting something it has no evidence for. A row with no source says so
 * rather than claiming the user added it.
 */
export function formatEvidenceSource(source: string | null | undefined): string {
  if (!source) return NO_VALUE
  if (source === 'manual') return 'Added by you'
  return `From ${humanise(source)}`
}

/* ------------------------------------------------------------------ metrics */

/** The unit a metric is measured in, as a noun for a caption. Null is the dash. */
export function formatMetricUnit(unit: LearningMetricUnit | null | undefined): string {
  if (!unit) return NO_VALUE
  switch (unit) {
    case 'count':
      return 'count'
    case 'minutes':
      return 'minutes'
    case 'days':
      return 'days'
    case 'ratio':
      return 'ratio'
    case 'percent':
      return 'percent'
  }
}

/**
 * One metric, formatted by its own unit.
 *
 * The unit is data rather than decoration, so a ratio cannot be printed as a
 * count of sessions. **`available: false` and `value: null` both render as the
 * dash**, and neither is ever coerced to a zero — see {@link metricUnavailableReason}
 * for the sentence that goes with it.
 */
export function formatMetricValue(metric: LearningMetricRead): string {
  if (!metric.available || metric.value === null) return NO_VALUE
  const value = metric.value
  switch (metric.unit) {
    case 'count':
      return formatNumber(value)
    case 'minutes':
      return formatLearningMinutes(value)
    case 'days':
      return `${formatNumber(value)} ${Math.round(value) === 1 ? 'day' : 'days'}`
    case 'ratio':
      return formatRatioPercent(value)
    case 'percent':
      return formatPercent(value)
  }
}

/**
 * Why a metric could not be computed, or null when it could.
 *
 * The backend always supplies a reason, and the fallback is
 * {@link INSUFFICIENT_DATA_MESSAGE} rather than a second phrasing invented here:
 * a stat tile, a chart and a form hint must all render one sentence for one
 * condition.
 */
export function metricUnavailableReason(metric: LearningMetricRead): string | null {
  if (metric.available && metric.value !== null) return null
  return metric.reason_if_unavailable ?? INSUFFICIENT_DATA_MESSAGE
}

/**
 * The sentence a figure could not carry.
 *
 * Prefers the backend's own reason and falls back to
 * {@link INSUFFICIENT_DATA_MESSAGE}. Exists as one function so a chart legend, a
 * table caption and an empty state cannot disagree about what "no measurement"
 * is called.
 */
export function insufficientData(reason?: string | null): string {
  return reason ?? INSUFFICIENT_DATA_MESSAGE
}

/* --------------------------------------------------------------------- gaps */

/**
 * Why a gap could not be measured, or null when it could.
 *
 * `available: true` with `gap: 0` is a real measurement — the user is at their
 * target — and must never be rendered with the reason attached. Only
 * `available: false` reaches the fallback.
 */
export function gapUnavailableReason(gap: SkillGapRead): string | null {
  if (gap.available) return null
  return insufficientData(gap.reason_if_unavailable)
}

/**
 * The one sentence explaining a gap, verbatim.
 *
 * The backend composes it and refuses to build one without a digit in it, so it
 * always names both levels and the evidence count: *"Target 4/5, current
 * self-assessed 2/5. NEXUS recorded 6 related learning activities in the last 30
 * days."* Nothing here rewrites it — a client that paraphrased this sentence
 * could lose the attribution that makes it honest.
 */
export function formatGapExplanation(gap: SkillGapRead): string {
  return gap.explanation || insufficientData(gap.reason_if_unavailable)
}