/**
 * Presentation helpers for the analytics surface.
 *
 * **Every formatter here takes `number | null` and returns a dash for `null`.**
 * That is the whole job of this file: a nullable figure is rendered as "no
 * measurement", and a `0` is rendered as `0`. Collapsing the two — the default
 * `toFixed` on a `null`, or `?? 0` at a call site — is how a screen ends up
 * claiming a user completed 0% of nothing.
 */

import type { DateOnlyString, ISODateTimeString } from '@/types/analytics'

/** What a `null` looks like everywhere on this surface. */
export const NO_VALUE = '—'

/**
 * The five chart tokens, as `hsl()` strings.
 *
 * Recharts colours SVG attributes, not classes, so it needs the resolved value
 * rather than `bg-chart-1`. The cycle is fixed: a slice keeps its colour between
 * renders, which is what makes a legend readable at all.
 */
export const CHART_COLORS = [
  'hsl(var(--chart-1))',
  'hsl(var(--chart-2))',
  'hsl(var(--chart-3))',
  'hsl(var(--chart-4))',
  'hsl(var(--chart-5))',
] as const

export function chartColor(index: number): string {
  return CHART_COLORS[index % CHART_COLORS.length] ?? 'hsl(var(--chart-1))'
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return `${formatNumber(value, digits)}%`
}

/** `135` → `2h 15m`. Minutes are the unit the API reports, so nothing converts. */
export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const minutes = Math.round(value)
  if (minutes === 0) return '0m'
  const sign = minutes < 0 ? '-' : ''
  const total = Math.abs(minutes)
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (hours === 0) return `${sign}${rest}m`
  if (rest === 0) return `${sign}${hours}h`
  return `${sign}${hours}h ${rest}m`
}

export function formatHours(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  return `${formatNumber(value / 60, digits)}h`
}

/** `1.4` → `1.4 tasks`; used where the unit is a noun rather than a symbol. */
export function formatSigned(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const formatted = formatNumber(Math.abs(value), digits)
  if (value > 0) return `+${formatted}`
  if (value < 0) return `−${formatted}`
  return formatted
}

/** "Updated 5 minutes ago" / "Updated just now", from a `daily_metrics` stamp. */
export function formatUpdatedAgo(
  updatedAt: ISODateTimeString | null | undefined,
  nowMs: number = Date.now(),
): string {
  if (!updatedAt) return 'never updated'
  const at = Date.parse(updatedAt)
  if (Number.isNaN(at)) return 'never updated'

  const seconds = Math.max(0, Math.round((nowMs - at) / 1000))
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/** "14 March" / "14 March 2025" for a date-only window edge. */
export function formatShortDate(value: DateOnlyString | null | undefined): string {
  if (!value) return NO_VALUE
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return NO_VALUE
  const date = new Date(year, month - 1, day)
  if (Number.isNaN(date.getTime())) return NO_VALUE
  const now = new Date()
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    ...(year === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(date)
}

/** Score out of 100, or the reason it could not be computed. */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return NO_VALUE
  return `${Math.round(score)}`
}

/**
 * A percentage carrying its own sign: `+12%`, `−8%`.
 *
 * **`formatSignedPercent(null)` is {@link NO_VALUE}** — never `NaN%`, never
 * `undefined%`. A percentage change from a zero baseline does not exist, and
 * printing a sign on a value the backend declined to compute is a claim the
 * data does not make. A true zero renders as `0%` with no sign, because
 * "changed by no amount" is a real answer and not a missing one.
 */
export function formatSignedPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE
  const rounded = Number(value.toFixed(digits))
  if (rounded === 0) return '0%'
  // U+2212 MINUS SIGN rather than a hyphen: it lines up with the digits above it.
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded).toFixed(digits)}%`
}

/** `12 Mar` for a metric bucket. Anchored at local midnight, never sliced off a
 * UTC instant string — that is the bug the planner surface already documents. */
export function formatMetricDate(value: DateOnlyString | null | undefined): string {
  if (!value) return NO_VALUE
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return NO_VALUE
  const date = new Date(year, month - 1, day)
  if (Number.isNaN(date.getTime())) return NO_VALUE
  const now = new Date()
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    ...(year === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(date)
}

/* ----------------------------------------------------------------- deltas */

export type DeltaDirection = 'up' | 'down' | 'flat' | 'unknown'

export interface DeltaParts {
  direction: DeltaDirection
  /** Decorative glyph. The `label` below carries the same meaning in words. */
  arrow: string
  /** Spoken form, e.g. "up 3 tasks (+12%) from the previous period". */
  label: string
  percent: string
  absolute: string
  tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger'
}

export interface FormatDeltaOptions {
  /** Renders the absolute change: a count, a duration, a score. */
  format?: (value: number) => string
  /** What the unit is called in the sentence, e.g. `tasks`. */
  unit?: string
  /** Whether a rise is good. Affects `tone` only — the arrow never lies. */
  higherIsBetter?: boolean
}

/**
 * A period-over-period change, as an arrow, a sentence and a tone.
 *
 * **Direction and goodness are decided separately.** "3 more tasks overdue" is
 * an *up* arrow in a *warning* tone, because the arrow reports movement and the
 * tone reports whether that movement helps. Deciding goodness first would either
 * invert the arrow or lose the tone, and either way a reader who cannot see
 * colour learns nothing from a coloured number.
 *
 * When neither operand exists the result is `unknown`: no arrow at all, because
 * pointing up or down at a figure that was never computed is a claim.
 */
export function formatDelta(
  absolute: number | null | undefined,
  percent: number | null | undefined,
  options: FormatDeltaOptions = {},
): DeltaParts {
  const {
    format = (value: number) => formatNumber(value),
    unit,
    higherIsBetter = true,
  } = options

  const hasAbsolute = absolute !== null && absolute !== undefined && Number.isFinite(absolute)
  const hasPercent = percent !== null && percent !== undefined && Number.isFinite(percent)

  if (!hasAbsolute && !hasPercent) {
    return {
      direction: 'unknown',
      arrow: '',
      label: 'no comparison with the previous period',
      percent: NO_VALUE,
      absolute: NO_VALUE,
      tone: 'neutral',
    }
  }

  // The absolute change is the direction of record; the percentage only
  // restates it and can be absent alone (a zero previous period).
  const signSource = hasAbsolute ? (absolute as number) : (percent as number)
  const direction: DeltaDirection =
    Math.abs(signSource) < 1e-9 ? 'flat' : signSource > 0 ? 'up' : 'down'

  const percentText = hasPercent ? formatSignedPercent(percent as number) : NO_VALUE
  const absoluteText = hasAbsolute ? formatSigned(absolute as number) : NO_VALUE
  const tail = hasPercent ? ` (${percentText})` : ''

  const label =
    direction === 'flat'
      ? `no change${tail} from the previous period`
      : hasAbsolute
        ? `${direction} ${format(Math.abs(absolute as number))}${unit ? ` ${unit}` : ''}${tail} from the previous period`
        : `${direction} ${percentText} from the previous period`

  const isGood = direction === 'up' ? higherIsBetter : !higherIsBetter
  return {
    direction,
    arrow: direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→',
    label,
    percent: percentText,
    absolute: absoluteText,
    tone: direction === 'up' || direction === 'down' ? (isGood ? 'success' : 'warning') : 'neutral',
  }
}

/* ------------------------------------------------------------- total labels */

export interface TotalLabel {
  label: string
  /** Minutes get a duration renderer; every other column is a plain count. */
  unit: 'count' | 'minutes'
  higherIsBetter: boolean
}

/**
 * How each `OverviewRead.totals` row should read.
 *
 * The backend sends a `daily_metrics` column name as the label, because the
 * comparison is computed over those columns and the name is the honest one. The
 * screen wants English, so the mapping lives here rather than being re-derived
 * per call site — and `higherIsBetter` lives beside it, because knowing that
 * "tasks overdue" is a column is not the same as knowing that rising is bad.
 */
export const TOTAL_LABELS: Record<string, TotalLabel> = {
  tasks_created: { label: 'Tasks created', unit: 'count', higherIsBetter: true },
  tasks_completed: { label: 'Tasks completed', unit: 'count', higherIsBetter: true },
  tasks_overdue: { label: 'Tasks overdue', unit: 'count', higherIsBetter: false },
  tasks_cancelled: { label: 'Tasks cancelled', unit: 'count', higherIsBetter: false },
  tasks_blocked: { label: 'Tasks blocked', unit: 'count', higherIsBetter: false },
  tasks_rescheduled: { label: 'Tasks rescheduled', unit: 'count', higherIsBetter: false },
  planned_minutes: { label: 'Time planned', unit: 'minutes', higherIsBetter: true },
  actual_minutes: { label: 'Time recorded', unit: 'minutes', higherIsBetter: true },
  work_sessions: { label: 'Work sessions', unit: 'count', higherIsBetter: true },
  calendar_events: { label: 'Calendar events', unit: 'count', higherIsBetter: true },
  knowledge_events: { label: 'Knowledge events', unit: 'count', higherIsBetter: true },
  projects_touched: { label: 'Projects touched', unit: 'count', higherIsBetter: true },
}

const FALLBACK_TOTAL: TotalLabel = { label: 'Metric', unit: 'count', higherIsBetter: true }

/** Total metadata for a backend column name, with a human fallback. */
export function totalLabel(label: string): TotalLabel {
  return TOTAL_LABELS[label] ?? { ...FALLBACK_TOTAL, label: label.replace(/_/g, ' ') }
}
