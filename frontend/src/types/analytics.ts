/**
 * Wire types for the Phase 6 analytics surface.
 *
 * Mirrors `backend/app/schemas/analytics.py`.
 *
 * **A number that could not be computed is `null`, never `0`.** Every rate,
 * score and error figure on this surface is nullable, and every *score* also
 * carries `available` plus `reason_if_unavailable`. That pairing is the whole
 * contract a client needs: `score: null` with `available: false` is a positive
 * claim — "we looked, and there was nothing to measure" — and rendering it as
 * `0%` would assert the opposite. So this module types those fields as
 * `number | null` and never offers a `?:` convenience, because a defaulted
 * number is exactly the coercion the backend went to the trouble of avoiding.
 *
 * Date-only values are `YYYY-MM-DD` strings, matching the backend's `date`
 * columns. They are NOT timestamps: the window is inclusive of both ends and is
 * bucketed by the calendar, not by an instant.
 */

import type { ISODateTimeString, UUIDString } from './api'
import type { DateOnlyString, StatusMeta } from './work'

// Re-exported so a consumer of the analytics vocabulary needs one import.
export type { DateOnlyString, ISODateTimeString, StatusMeta, UUIDString }

/* -------------------------------------------------------------- vocabulary */

/** How a time series is bucketed. `day`/`week`/`month` bucket the daily rows. */
export type Granularity = 'day' | 'week' | 'month'

/** The metrics `GET /analytics/trends` will plot. An allowlist, not a free text
 * field: the backend resolves the name against a column list and a 422s on
 * anything else, so offering a free-text input here would only let the user
 * build a request that cannot succeed. */
export const ANALYTICS_TREND_METRICS = [
  'tasks_created',
  'tasks_completed',
  'tasks_overdue',
  'tasks_cancelled',
  'tasks_blocked',
  'tasks_rescheduled',
  'planned_minutes',
  'actual_minutes',
  'work_sessions',
  'calendar_events',
  'knowledge_events',
  'projects_touched',
] as const
export type TrendMetric = (typeof ANALYTICS_TREND_METRICS)[number]

/** The three export datasets. A fixed vocabulary, like the metric list. */
export const ANALYTICS_CSV_DATASETS = [
  'daily_metrics',
  'task_performance',
  'work_sessions',
] as const
export type AnalyticsCsvDataset = (typeof ANALYTICS_CSV_DATASETS)[number]

/** Longest window the backend will accept, in days. A wider one is a 422. */
export const MAX_ANALYTICS_RANGE_DAYS = 366

/** Sent on every list read; both ends default to the last 7 days server-side. */
export interface AnalyticsListParams {
  start_date?: DateOnlyString
  end_date?: DateOnlyString
  granularity?: Granularity
  /** Narrows `/analytics/time` and `/analytics/projects` to one project. */
  project_id?: UUIDString
}

/** A resolved window, always with both ends present. */
export interface AnalyticsRange {
  start_date: DateOnlyString
  end_date: DateOnlyString
  granularity?: Granularity
}

/* ------------------------------------------------------------- date window */

export type WindowPresetId =
  | 'today'
  | '7d'
  | '30d'
  | '90d'
  | 'this_month'
  | 'last_month'
  | 'custom'

export interface WindowPreset {
  id: WindowPresetId
  label: string
  /** `undefined` for the calendar and custom windows, which are not a span. */
  days?: number
}

/**
 * The window shortcuts the picker offers, in the order they are offered.
 *
 * The defaults match the backend's (`end_date` = today, `start_date` = today
 * minus `ANALYTICS_DEFAULT_RANGE_DAYS - 1`), so a page that sends no dates and a
 * page that sends the resolved window ask the same question.
 */
export const WINDOW_PRESETS: readonly WindowPreset[] = [
  { id: 'today', label: 'Today', days: 1 },
  { id: '7d', label: '7 days', days: 7 },
  { id: '30d', label: '30 days', days: 30 },
  { id: '90d', label: '90 days', days: 90 },
  { id: 'this_month', label: 'This month' },
  { id: 'last_month', label: 'Last month' },
  { id: 'custom', label: 'Custom' },
]

export const DEFAULT_WINDOW_PRESET: WindowPresetId = '7d'

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** Local-calendar `YYYY-MM-DD`. Never `toISOString()`: that is UTC, and near
 * midnight it silently moves the window by a day. */
export function toDateOnly(date: Date): DateOnlyString {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function todayDateOnly(now: Date = new Date()): DateOnlyString {
  return toDateOnly(now)
}

/** Parses `YYYY-MM-DD` strictly, in local time. `null` when it is not one. */
export function parseDateOnly(value: string | null | undefined): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return null
  const date = new Date(year, month - 1, day)
  if (Number.isNaN(date.getTime())) return null
  // Rejects 2026-02-31 and friends, which `Date` would roll over silently.
  return toDateOnly(date) === value ? date : null
}

export function isDateOnly(value: string | null | undefined): value is DateOnlyString {
  return parseDateOnly(value) !== null
}

export function shiftDays(date: DateOnlyString, days: number): DateOnlyString {
  const parsed = parseDateOnly(date)
  if (!parsed) return date
  parsed.setDate(parsed.getDate() + days)
  return toDateOnly(parsed)
}

/** Inclusive day count, so a single day is `1` and not `0`. */
export function rangeDays(start: DateOnlyString, end: DateOnlyString): number {
  const from = parseDateOnly(start)
  const to = parseDateOnly(end)
  if (!from || !to) return 0
  return Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1
}

/** `Mar 3 – Mar 9, 2026`, shortened when the window crosses a year. */
export function formatRangeLabel(start: DateOnlyString, end: DateOnlyString): string {
  const from = parseDateOnly(start)
  const to = parseDateOnly(end)
  if (!from || !to) return 'Selected window'

  const sameYear = from.getFullYear() === to.getFullYear()
  const short = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
  const full = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })

  const fromLabel = short.format(from)
  const toLabel = sameYear ? short.format(to) : full.format(to)
  return `${fromLabel} – ${toLabel}`
}

export function isWindowPreset(value: string | null | undefined): value is WindowPresetId {
  return value !== null && WINDOW_PRESETS.some((preset) => preset.id === value)
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

/**
 * Resolves a preset to a concrete, inclusive window.
 *
 * The caller supplies the `today` anchor so a URL resolved on one side of
 * midnight and one resolved on the other cannot disagree inside a single
 * render — the same reason the planner keys its queries on an explicit date.
 */
export function resolveWindow(
  preset: WindowPresetId,
  today: DateOnlyString = todayDateOnly(),
  custom?: { start_date?: DateOnlyString; end_date?: DateOnlyString } | null,
): AnalyticsRange {
  const now = parseDateOnly(today) ?? new Date()

  if (preset === 'custom') {
    const start = isDateOnly(custom?.start_date) ? custom.start_date : shiftDays(today, -6)
    const end = isDateOnly(custom?.end_date) ? custom.end_date : today
    // An inverted custom window is a 422 server-side; clamp rather than show a
    // screen that only ever renders an error.
    return start <= end ? { start_date: start, end_date: end } : { start_date: end, end_date: start }
  }

  if (preset === 'this_month') {
    const first = startOfMonth(now)
    return { start_date: toDateOnly(first), end_date: toDateOnly(now) }
  }

  if (preset === 'last_month') {
    const first = startOfMonth(now)
    const previousEnd = new Date(first)
    previousEnd.setDate(previousEnd.getDate() - 1)
    return {
      start_date: toDateOnly(startOfMonth(previousEnd)),
      end_date: toDateOnly(previousEnd),
    }
  }

  const days = WINDOW_PRESETS.find((entry) => entry.id === preset)?.days ?? 7
  return { start_date: shiftDays(today, -(days - 1)), end_date: today }
}

/* ------------------------------------------------------------------ shapes */

/** The window a figure was computed over, echoed back by the backend. */
export interface MetricRange {
  start_date: DateOnlyString
  end_date: DateOnlyString
  granularity: Granularity
}

/** One `daily_metrics` row. Zeroes here are real measurements, not absences. */
export interface DailyMetricRead {
  metric_date: DateOnlyString
  tasks_created: number
  tasks_completed: number
  tasks_overdue: number
  tasks_cancelled: number
  tasks_blocked: number
  tasks_rescheduled: number
  planned_minutes: number
  actual_minutes: number
  work_sessions: number
  calendar_events: number
  knowledge_events: number
  projects_touched: number
  updated_at: ISODateTimeString | null
}

/** One line of a score's breakdown, so the parts can be checked against the
 * total rather than taken on trust. */
export interface ScoreComponent {
  name: string
  points: number
  max_points: number
  explanation: string
}

/** One headline figure against the same-length period immediately before it.
 * `percent_change` is `null` — not `Infinity`, not `0` — when the previous
 * period was zero or absent, so it is rendered as "no comparison", never as
 * "+0%". */
export interface ComparisonTotal {
  label: string
  current: number
  previous: number | null
  absolute_change: number | null
  percent_change: number | null
}

export interface ProductivityRead {
  score: number | null
  available: boolean
  reason_if_unavailable: string | null
  components: ScoreComponent[]
  formula: string
  label: string
  disclaimer: string
  range: MetricRange | null
  weight_total: number
}

export interface ConsistencyRead {
  score: number | null
  available: boolean
  reason_if_unavailable: string | null
  active_days: number
  window_days: number
  work_sessions: number
  session_count: number
  active_day_ratio: number | null
  longest_streak: number
  current_streak: number
  formula: string
  label: string
  disclaimer: string
  range: MetricRange | null
  components: ScoreComponent[]
}

export interface FocusRead {
  score: number | null
  available: boolean
  reason_if_unavailable: string | null
  avg_session_minutes: number | null
  completed_planned_sessions: number
  interruptions: number
  reschedules: number
  focused_minutes: number
  total_minutes: number
  formula: string
  label: string
  disclaimer: string
  range: MetricRange | null
  components: ScoreComponent[]
}

export interface DeadlineAdherenceRead {
  available: boolean
  reason_if_unavailable: string | null
  on_time: number
  late: number
  still_overdue: number
  adherence_rate: number | null
  rate: number | null
  overdue_open: number
  total_considered: number
  range: MetricRange | null
  components: ScoreComponent[]
}

export interface EstimationAccuracyRead {
  available: boolean
  reason_if_unavailable: string | null
  sample_count: number
  pairs_compared: number
  absolute_error: number | null
  mean_absolute_error: number | null
  percentage_error: number | null
  mean_percentage_error: number | null
  /** Mean signed error as `estimated - actual`; negative means under-estimated. */
  bias: number | null
  median_error: number | null
  under_estimation_rate: number | null
  over_estimation_rate: number | null
  underestimation_rate: number | null
  overestimation_rate: number | null
  range: MetricRange | null
}

export interface WorkloadRead {
  open_tasks: number
  high_priority_open: number
  overdue_open: number
  scheduled_minutes: number
  /** `null` when no availability rules exist — unconfigured, not zero. */
  available_minutes: number | null
  workload_ratio: number | null
  average_daily_scheduled_minutes: number | null
  high_priority_tasks: number
  overdue_tasks: number
  actual_minutes: number
  available: boolean
  reason_if_unavailable: string | null
  comparison: ComparisonTotal[]
  status_counts: Record<string, number>
  priority_counts: Record<string, number>
  range: MetricRange | null
}

export interface TimeBucketRead {
  key: string
  label: string
  minutes: number
  /** `null` when the window's total is zero; never a fabricated 0%. */
  share: number | null
}

export interface TimeSlice {
  project_id: UUIDString
  project_name: string
  minutes: number
  share: number | null
}

export interface TimeDistributionRead {
  total_minutes: number
  available: boolean
  reason_if_unavailable: string | null
  unassigned_minutes: number
  project_id: UUIDString | null
  by_project: TimeBucketRead[]
  by_task: TimeBucketRead[]
  slices: TimeSlice[]
  range: MetricRange | null
}

export interface VelocityRead {
  tasks_per_week: number | null
  estimated_minutes_per_week: number | null
  weeks_measured: number
  definition: string
}

export interface ProjectAnalyticsRead {
  project_id: UUIDString
  name: string
  status: string
  total_tasks: number
  completed_tasks: number
  remaining_tasks: number
  overdue_tasks: number
  completion_rate: number | null
  total_work_minutes: number
  avg_task_actual_minutes: number | null
  estimation: EstimationAccuracyRead | null
  velocity: VelocityRead | null
  velocity_tasks_per_week: number | null
  weekly_completed: number[]
  work_minutes: number
  estimated_minutes: number
  actual_minutes: number
  avg_task_minutes: number | null
  activity_events: number
  available: boolean
  reason_if_unavailable: string | null
  range: MetricRange | null
}

export interface OverdueTaskRead {
  task_id: UUIDString
  title: string
  due_date: DateOnlyString | null
  days_overdue: number | null
  priority: string
}

export interface TaskAnalyticsRead {
  total_tasks: number
  completed_tasks: number
  open_tasks: number
  overdue_tasks: number
  cancelled_tasks: number
  blocked_tasks: number
  completion_rate: number | null
  overdue_rate: number | null
  avg_completion_days: number | null
  avg_cycle_minutes: number | null
  avg_estimate_error_minutes: number | null
  tasks_created: number
  tasks_completed: number
  tasks_cancelled: number
  tasks_blocked: number
  tasks_overdue: number
  tasks_rescheduled: number
  available: boolean
  reason_if_unavailable: string | null
  estimation: EstimationAccuracyRead | null
  top_overdue: OverdueTaskRead[]
  by_status: Record<string, number>
  by_priority: Record<string, number>
  range: MetricRange | null
}

export interface LearningAnalyticsRead {
  available: boolean
  reason_if_unavailable: string | null
  study_events: number
  study_minutes: number
  /** Always `null` today: the knowledge rows carry no task or project id, so
   * the correlation is not computable rather than zero. */
  knowledge_linked_tasks: number | null
  knowledge_interactions: number
  notes_created: number
  notes_updated: number
  projects_touched: number
  basis: string
  definition: string
  range: MetricRange | null
}

export interface TagCountRead {
  key: string
  label: string
  count: number
}

export interface TagCount {
  tag_id: string
  name: string
  note_count: number
}

export interface KnowledgeAnalyticsRead {
  available: boolean
  reason_if_unavailable: string | null
  notes_created: number
  notes_updated: number
  concepts_created: number
  resources_added: number
  bookmarks_added: number
  links_created: number
  notes_published: number
  documents_added: number
  interactions: number
  most_used_tags: TagCountRead[]
  most_active_concepts: TagCountRead[]
  top_tags: TagCount[]
  notes_by_status: Record<string, number>
  range: MetricRange | null
}

export interface TrendPoint {
  bucket: DateOnlyString | null
  label: string
  value: number
  previous: number | null
  absolute_change: number | null
  percent_change: number | null
  period_start: DateOnlyString | null
  period_end: DateOnlyString | null
}

export interface OverviewRead {
  range: MetricRange
  previous_range: MetricRange | null
  /** True when no aggregate has been written for part of the window. */
  stale: boolean
  is_stale: boolean
  aggregates_through: DateOnlyString | null
  data_as_of: DateOnlyString | null
  totals: ComparisonTotal[]
  productivity: ProductivityRead
  deadlines: DeadlineAdherenceRead
  consistency: ConsistencyRead | null
  focus: FocusRead | null
  estimation: EstimationAccuracyRead | null
  workload: WorkloadRead | null
  daily: DailyMetricRead[]
  reason_if_empty: string | null
}

export interface RebuildRead {
  rows_written: number
  /**
   * Echoed back for convenience. The 202 body carries `rows_written` only, so a
   * client must not depend on these; the window it asked for is the window it
   * got.
   */
  start_date?: DateOnlyString
  end_date?: DateOnlyString
}

export interface CsvExportManifestRead {
  datasets: string[]
  columns: Record<string, string[]>
  content_type: string
  note: string
}

/* ------------------------------------------------------------------ aliases */

/*
 * Shorter names for the same shapes. The module above is written to mirror the
 * backend schemas field for field, which produces some long identifiers; these
 * are the spellings call sites reach for instead. They are aliases, never new
 * types — `OverdueTask` *is* `OverdueTaskRead`, so the two cannot drift.
 */

/** A task past its due date, as `/analytics/tasks` reports it. */
export type OverdueTask = OverdueTaskRead

/** One slice of the time-distribution breakdown. */
export type TimeBucket = TimeBucketRead

/**
 * The three score reads, unified.
 *
 * Productivity, consistency and focus differ in their extra fields but share the
 * whole of what a score display reads — the number, `available`, the reason, the
 * contributors, the formula and the disclaimer — so one card can render any of
 * them without knowing which it holds.
 */
export type ScoredRead = ProductivityRead | ConsistencyRead | FocusRead

/** How one `OverviewRead.totals` row should read on screen. */
export interface TotalMeta {
  label: string
  /** Minutes get a duration renderer; every other column is a plain count. */
  unit: 'count' | 'minutes'
  higherIsBetter: boolean
}

/** Query params for `GET /analytics/trends`: the window plus the metric. */
export type TrendParams = AnalyticsListParams & { metric?: TrendMetric }
