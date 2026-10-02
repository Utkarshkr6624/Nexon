import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  CHART_COLORS,
  NO_VALUE,
  TOTAL_LABELS,
  chartColor,
  formatDelta,
  formatHours,
  formatMetricDate,
  formatMinutes,
  formatNumber,
  formatPercent,
  formatScore,
  formatShortDate,
  formatSigned,
  formatSignedPercent,
  formatUpdatedAgo,
  totalLabel,
  type DeltaDirection,
  type TotalLabel,
} from '@/features/analytics/format'

/**
 * Monday 5 January 2026, 12:00 UTC — the same anchor the backend fixtures use,
 * so a stamp written here and a `daily_metrics.updated_at` written there read
 * identically. Every relative-time assertion is expressed as an offset from it
 * rather than as a wall-clock string, which keeps the tests readable and makes
 * the bucket boundaries explicit.
 */
const NOW_MS = Date.UTC(2026, 0, 5, 12, 0, 0)

/** An ISO instant `offsetMs` before {@link NOW_MS}. Negative offsets are future. */
function stampAt(offsetMs: number): string {
  return new Date(NOW_MS - offsetMs).toISOString()
}

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

const NOT_A_NUMBER = Number.NaN

describe('NO_VALUE', () => {
  it('is the em dash, so a missing figure is visibly different from a zero', () => {
    expect(NO_VALUE).toBe('—')
    expect(NO_VALUE).not.toBe(formatNumber(0))
    expect(NO_VALUE).not.toBe(formatPercent(0))
  })
})

describe('chartColor', () => {
  it('hands out the five tokens in order, which is what keeps a legend readable', () => {
    expect(CHART_COLORS).toHaveLength(5)
    expect(CHART_COLORS.map((_, index) => chartColor(index))).toEqual([...CHART_COLORS])
  })

  it('wraps the index instead of running off the end of the palette', () => {
    // A series longer than the palette must reuse colour 1 rather than hand
    // Recharts an `undefined` fill, which it renders as a black slice.
    expect(chartColor(5)).toBe(CHART_COLORS[0])
    expect(chartColor(12)).toBe(CHART_COLORS[2])
    expect(chartColor(100_000)).toBe(CHART_COLORS[0])
    expect(chartColor(1_000_003)).toBe(CHART_COLORS[3])
  })

  it('never returns undefined for an index outside the cycle', () => {
    // `%` keeps the sign of a negative dividend, so a negative index misses the
    // palette entirely and takes the fallback. That is the `??` branch, and the
    // contract that matters is that the result is still a usable colour string.
    for (const index of [-1, -5, -7, 0, 4, 1e9]) {
      expect(typeof chartColor(index)).toBe('string')
      expect(chartColor(index)).toBeTruthy()
    }
    expect(chartColor(-1)).toBe('hsl(var(--chart-1))')
  })
})

describe('formatNumber', () => {
  it('renders a missing measurement as the em dash rather than as NaN or nothing', () => {
    expect(formatNumber(null)).toBe(NO_VALUE)
    expect(formatNumber(undefined)).toBe(NO_VALUE)
    expect(formatNumber(NOT_A_NUMBER)).toBe(NO_VALUE)
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe(NO_VALUE)
    expect(formatNumber(Number.NEGATIVE_INFINITY)).toBe(NO_VALUE)
  })

  it('renders a real zero as 0, which is the case the dash must not swallow', () => {
    expect(formatNumber(0)).toBe('0')
    // A negative zero from a JSON payload keeps its sign; documented rather
    // than asserted away, because `Intl` treats -0 as a sign to print.
    expect(formatNumber(-0)).toBe('-0')
  })

  it('pads to the requested number of fraction digits', () => {
    expect(formatNumber(1234.567, 2)).toBe('1,234.57')
    expect(formatNumber(1234.567, 0)).toBe('1,235')
    expect(formatNumber(1234.5, 0)).toBe('1,235')
    expect(formatNumber(0.5, 0)).toBe('1')
    expect(formatNumber(7, 2)).toBe('7.00')
  })

  it('keeps the sign on a negative count', () => {
    expect(formatNumber(-1234)).toBe('-1,234')
  })
})

describe('formatPercent', () => {
  it('renders null and undefined as the dash, never as 0% or NaN%', () => {
    // 0% would be the worst possible lie: it claims the user completed no
    // percentage of no tasks, which reads as a real measurement.
    expect(formatPercent(null)).toBe(NO_VALUE)
    expect(formatPercent(undefined)).toBe(NO_VALUE)
    expect(formatPercent(NOT_A_NUMBER)).toBe(NO_VALUE)
    expect(formatPercent(0)).toBe('0%')
  })

  it('appends the unit to the whole number by default', () => {
    // The spec's worked example: 8 of 10 tasks completed is an 80% rate.
    expect(formatPercent(80)).toBe('80%')
    expect(formatPercent(85.7, 1)).toBe('85.7%')
    expect(formatPercent(22.727, 1)).toBe('22.7%')
    expect(formatPercent(100)).toBe('100%')
  })

  it('keeps the trailing zero an explicit precision asked for', () => {
    expect(formatPercent(12.5, 1)).toBe('12.5%')
    expect(formatPercent(12, 2)).toBe('12.00%')
  })
})

describe('formatMinutes', () => {
  it('splits minutes into hours and minutes at the exact boundaries', () => {
    expect(formatMinutes(0)).toBe('0m')
    expect(formatMinutes(45)).toBe('45m')
    expect(formatMinutes(59)).toBe('59m')
    expect(formatMinutes(60)).toBe('1h')
    expect(formatMinutes(61)).toBe('1h 1m')
    expect(formatMinutes(90)).toBe('1h 30m')
    expect(formatMinutes(135)).toBe('2h 15m')
    expect(formatMinutes(1440)).toBe('24h')
  })

  it('rounds to whole minutes first, because the API reports whole minutes', () => {
    expect(formatMinutes(0.4)).toBe('0m')
    expect(formatMinutes(0.6)).toBe('1m')
    expect(formatMinutes(59.5)).toBe('1h')
    expect(formatMinutes(119.4)).toBe('1h 59m')
  })

  it('renders a missing duration as the dash', () => {
    expect(formatMinutes(null)).toBe(NO_VALUE)
    expect(formatMinutes(undefined)).toBe(NO_VALUE)
    expect(formatMinutes(NOT_A_NUMBER)).toBe(NO_VALUE)
  })

  it('keeps the minus sign in front of a negative duration', () => {
    expect(formatMinutes(-90)).toBe('-1h 30m')
    expect(formatMinutes(-45)).toBe('-45m')
  })
})

describe('formatHours', () => {
  it('converts minutes to hours at one decimal place by default', () => {
    expect(formatHours(90)).toBe('1.5h')
    expect(formatHours(60)).toBe('1.0h')
    expect(formatHours(0)).toBe('0.0h')
    expect(formatHours(30)).toBe('0.5h')
    expect(formatHours(135)).toBe('2.3h')
  })

  it('honours an explicit precision', () => {
    expect(formatHours(90, 0)).toBe('2h')
    expect(formatHours(90, 2)).toBe('1.50h')
    expect(formatHours(0, 2)).toBe('0.00h')
  })

  it('renders a missing duration as the dash and not as 0.0h', () => {
    expect(formatHours(null)).toBe(NO_VALUE)
    expect(formatHours(undefined)).toBe(NO_VALUE)
    expect(formatHours(NOT_A_NUMBER)).toBe(NO_VALUE)
  })
})

describe('formatSigned', () => {
  it('puts an explicit sign in front of a change', () => {
    expect(formatSigned(3)).toBe('+3')
    expect(formatSigned(-3)).toBe('−3')
    expect(formatSigned(-2.5, 1)).toBe('−2.5')
  })

  it('uses the typographic minus so it lines up with the digits above it', () => {
    expect(formatSigned(-3)).toContain('−')
    expect(formatSigned(-3)).not.toContain('-')
  })

  it('leaves an exact zero unsigned, because no change is an answer, not a claim', () => {
    expect(formatSigned(0)).toBe('0')
    expect(formatSigned(0, 2)).toBe('0.00')
  })

  it('rounds to whole units unless told otherwise', () => {
    expect(formatSigned(2.6)).toBe('+3')
    expect(formatSigned(2.4)).toBe('+2')
    expect(formatSigned(1.44, 1)).toBe('+1.4')
  })

  it('renders a missing change as the dash, with no sign', () => {
    expect(formatSigned(null)).toBe(NO_VALUE)
    expect(formatSigned(undefined)).toBe(NO_VALUE)
    expect(formatSigned(NOT_A_NUMBER)).toBe(NO_VALUE)
  })

  it('documents that a positive change smaller than the display precision keeps its sign', () => {
    // `formatSignedPercent` explicitly collapses a rounded-away value to `0%`;
    // `formatSigned` does not, so 0.4 of a task renders as "+0". Asserted as-is
    // because the test suite must not paper over current behaviour — the value
    // of fixing it is a product decision, not a formatting one.
    expect(formatSigned(0.4)).toBe('+0')
    expect(formatSigned(-0.4)).toBe('−0')
  })
})

describe('formatScore', () => {
  it('rounds a score to a whole number out of 100', () => {
    expect(formatScore(78)).toBe('78')
    expect(formatScore(78.4)).toBe('78')
    expect(formatScore(78.5)).toBe('79')
    expect(formatScore(0)).toBe('0')
    expect(formatScore(100)).toBe('100')
  })

  it('never prints a signed zero for a score just below the baseline', () => {
    expect(formatScore(-0.4)).toBe('0')
  })

  it('renders an uncomputed score as the dash', () => {
    expect(formatScore(null)).toBe(NO_VALUE)
    expect(formatScore(undefined)).toBe(NO_VALUE)
    expect(formatScore(NOT_A_NUMBER)).toBe(NO_VALUE)
  })
})

describe('formatSignedPercent', () => {
  it('signs a percentage change, and leaves a true zero bare', () => {
    expect(formatSignedPercent(12)).toBe('+12%')
    expect(formatSignedPercent(-8)).toBe('−8%')
    expect(formatSignedPercent(0)).toBe('0%')
  })

  it('renders a change from a zero baseline as missing, not as +0%', () => {
    expect(formatSignedPercent(null)).toBe(NO_VALUE)
    expect(formatSignedPercent(undefined)).toBe(NO_VALUE)
    expect(formatSignedPercent(NOT_A_NUMBER)).toBe(NO_VALUE)
    // A sub-precision change is not a change: `0%`, because the backend
    // declined to state one, and `+0%` would claim an increase of nothing.
    expect(formatSignedPercent(0.4)).toBe('0%')
    expect(formatSignedPercent(-0.4)).toBe('0%')
  })

  it('keeps the precision the caller asks for', () => {
    // The spec's weekly worked example: 27 completed against 22 is +22.7%.
    expect(formatSignedPercent(22.727, 1)).toBe('+22.7%')
    expect(formatSignedPercent(-8.06, 1)).toBe('−8.1%')
    expect(formatSignedPercent(12, 2)).toBe('+12.00%')
  })
})

describe('formatUpdatedAgo', () => {
  it('reads as "just now" for anything under three quarters of a minute', () => {
    expect(formatUpdatedAgo(stampAt(0), NOW_MS)).toBe('just now')
    expect(formatUpdatedAgo(stampAt(30 * SECOND), NOW_MS)).toBe('just now')
    expect(formatUpdatedAgo(stampAt(44 * SECOND), NOW_MS)).toBe('just now')
  })

  it('crosses into minutes at 45 seconds, not at 60', () => {
    expect(formatUpdatedAgo(stampAt(45 * SECOND), NOW_MS)).toBe('1 minute ago')
    expect(formatUpdatedAgo(stampAt(MINUTE), NOW_MS)).toBe('1 minute ago')
    expect(formatUpdatedAgo(stampAt(2 * MINUTE), NOW_MS)).toBe('2 minutes ago')
    expect(formatUpdatedAgo(stampAt(59 * MINUTE), NOW_MS)).toBe('59 minutes ago')
  })

  it('crosses into hours at 60 minutes and rounds to the nearest hour', () => {
    expect(formatUpdatedAgo(stampAt(HOUR), NOW_MS)).toBe('1 hour ago')
    // 90 minutes is nearer two hours than one, so it says two.
    expect(formatUpdatedAgo(stampAt(90 * MINUTE), NOW_MS)).toBe('2 hours ago')
    expect(formatUpdatedAgo(stampAt(23 * HOUR), NOW_MS)).toBe('23 hours ago')
  })

  it('crosses into days at 24 hours and pluralises correctly', () => {
    expect(formatUpdatedAgo(stampAt(DAY), NOW_MS)).toBe('1 day ago')
    expect(formatUpdatedAgo(stampAt(2 * DAY), NOW_MS)).toBe('2 days ago')
    expect(formatUpdatedAgo(stampAt(90 * DAY), NOW_MS)).toBe('90 days ago')
  })

  it('says "never updated" for a missing or unparseable stamp', () => {
    expect(formatUpdatedAgo(null, NOW_MS)).toBe('never updated')
    expect(formatUpdatedAgo(undefined, NOW_MS)).toBe('never updated')
    expect(formatUpdatedAgo('', NOW_MS)).toBe('never updated')
    expect(formatUpdatedAgo('yesterday', NOW_MS)).toBe('never updated')
    expect(formatUpdatedAgo('soon', NOW_MS)).toBe('never updated')
    // A date-only string *is* parseable — as UTC midnight — so it is a valid
    // stamp here and reads as a real age rather than as "never updated".
    expect(formatUpdatedAgo('2026-01-05', NOW_MS)).toBe('12 hours ago')
  })

  it('clamps a clock-skewed future stamp to "just now" instead of printing NaN', () => {
    // A daily_metrics stamp from a client running ahead of the server is not an
    // error worth surfacing, and it must never render as "NaN minutes ago".
    expect(formatUpdatedAgo(stampAt(-MINUTE), NOW_MS)).toBe('just now')
    expect(formatUpdatedAgo(stampAt(-30 * DAY), NOW_MS)).toBe('just now')
  })

  it('defaults its clock to the real one when the caller does not pass one', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(NOW_MS)
      expect(formatUpdatedAgo(new Date(NOW_MS - 5 * SECOND).toISOString())).toBe('just now')
      expect(formatUpdatedAgo(new Date(NOW_MS - 3 * HOUR).toISOString())).toBe('3 hours ago')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('formatShortDate', () => {
  // "Now" is pinned so the year suffix below is a decision of the function, not
  // of the day the suite happens to run.
  afterEach(() => {
    vi.useRealTimers()
  })

  function pinClock(): void {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 0, 5, 9, 0, 0))
  }

  it('renders a missing window edge as the dash', () => {
    expect(formatShortDate(null)).toBe(NO_VALUE)
    expect(formatShortDate(undefined)).toBe(NO_VALUE)
    expect(formatShortDate('')).toBe(NO_VALUE)
  })

  it('renders a malformed date as the dash rather than as a wrong one', () => {
    expect(formatShortDate('not-a-date')).toBe(NO_VALUE)
    expect(formatShortDate('2026')).toBe(NO_VALUE)
    expect(formatShortDate('2026-01')).toBe(NO_VALUE)
  })

  it('omits the year for the current year and includes it otherwise', () => {
    pinClock()
    expect(formatShortDate('2026-01-05')).toBe('5 Jan')
    expect(formatShortDate('2025-03-14')).toBe('14 Mar 2025')
  })

  it('reads the date as a local calendar date, not a UTC instant', () => {
    // Anchored at local midnight so a viewer east or west of UTC still sees the
    // day the bucket was keyed on.
    pinClock()
    expect(formatShortDate('2026-01-01')).toBe('1 Jan')
    expect(formatShortDate('2026-12-31')).toBe('31 Dec')
  })

  it('lets Date roll an impossible day over rather than rejecting it', () => {
    // `2026-02-31` has no calendar day, but `Date` shifts it to 3 March. The
    // stricter `isDateOnly` guard lives in @/types/analytics and is applied
    // before a value reaches this function; this documents the fallback, so a
    // future guard here is a visible change.
    pinClock()
    expect(formatShortDate('2026-02-31')).toBe('3 Mar')
  })
})

describe('formatMetricDate', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders a missing bucket as the dash', () => {
    expect(formatMetricDate(null)).toBe(NO_VALUE)
    expect(formatMetricDate(undefined)).toBe(NO_VALUE)
    expect(formatMetricDate('')).toBe(NO_VALUE)
  })

  it('renders a malformed bucket as the dash', () => {
    expect(formatMetricDate('not-a-date')).toBe(NO_VALUE)
    expect(formatMetricDate('2026')).toBe(NO_VALUE)
    expect(formatMetricDate('2026-01')).toBe(NO_VALUE)
  })

  it('labels a bucket with day and month, adding the year only when it differs', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date(2026, 0, 5, 9, 0, 0))
      expect(formatMetricDate('2026-01-05')).toBe('5 Jan')
      expect(formatMetricDate('2026-03-12')).toBe('12 Mar')
      expect(formatMetricDate('2025-12-31')).toBe('31 Dec 2025')
    } finally {
      vi.useRealTimers()
    }
  })

  it('never slices off the UTC part of a timestamped bucket', () => {
    // The planner surface already shipped this bug: `'2026-01-05T00:00:00Z'.slice(0, 10)`
    // looked right and behaved differently in every non-UTC browser.
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date(2026, 0, 5, 9, 0, 0))
      expect(formatMetricDate('2026-01-05' as never)).toBe('5 Jan')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('formatDelta', () => {
  it('reports "unknown" with no arrow when neither operand exists', () => {
    // This is what a `null` previous period produces. Pointing up or down at a
    // figure that was never computed would be a claim the data does not make.
    expect(formatDelta(null, null)).toEqual({
      direction: 'unknown',
      arrow: '',
      label: 'no comparison with the previous period',
      percent: NO_VALUE,
      absolute: NO_VALUE,
      tone: 'neutral',
    })
    expect(formatDelta(undefined, undefined).direction).toBe('unknown')
    expect(formatDelta(NOT_A_NUMBER, Number.POSITIVE_INFINITY).direction).toBe('unknown')
  })

  it('reports "up" as a rise, with the arrow, tone and sentence agreeing', () => {
    expect(formatDelta(5, 22.7, { unit: 'tasks' })).toEqual({
      direction: 'up',
      arrow: '↑',
      label: 'up 5 tasks (+23%) from the previous period',
      percent: '+23%',
      absolute: '+5',
      tone: 'success',
    })
  })

  it('matches the spec worked example: 27 completed against 22 is a rise of 5', () => {
    const delta = formatDelta(27 - 22, ((27 - 22) / 22) * 100, { unit: 'tasks' })
    expect(delta.absolute).toBe('+5')
    // The percentage is whole points here: `formatDelta` has no digits option,
    // so 22.727…% is displayed as +23% rather than +22.7%.
    expect(delta.percent).toBe('+23%')
    expect(delta.label).toBe('up 5 tasks (+23%) from the previous period')
  })

  it('reports "down" as a fall, and tone it as a warning when more is better', () => {
    expect(formatDelta(-3, -12, { unit: 'tasks' })).toEqual({
      direction: 'down',
      arrow: '↓',
      label: 'down 3 tasks (−12%) from the previous period',
      percent: '−12%',
      absolute: '−3',
      tone: 'warning',
    })
  })

  it('reports "flat" with a right arrow and no tone', () => {
    expect(formatDelta(0, 0)).toEqual({
      direction: 'flat',
      arrow: '→',
      label: 'no change (0%) from the previous period',
      percent: '0%',
      absolute: '0',
      tone: 'neutral',
    })
  })

  it('drops the percentage tail when the previous period was zero', () => {
    expect(formatDelta(0, null).label).toBe('no change from the previous period')
    expect(formatDelta(0, null).percent).toBe(NO_VALUE)
  })

  it('treats a change far below the display precision as no change', () => {
    expect(formatDelta(1e-10, null).direction).toBe('flat')
    expect(formatDelta(-1e-10, null).label).toBe('no change from the previous period')
  })

  it('falls back to the percentage when only the percentage exists', () => {
    // A zero previous period leaves a percentage with no absolute change, so the
    // sentence is built from the percentage alone and the unit is not claimed.
    expect(formatDelta(null, 22.7, { unit: 'tasks' })).toEqual({
      direction: 'up',
      arrow: '↑',
      label: 'up +23% from the previous period',
      percent: '+23%',
      absolute: NO_VALUE,
      tone: 'success',
    })
    expect(formatDelta(undefined, -50).label).toBe('down −50% from the previous period')
  })

  it('separates movement from goodness, so a rise in bad news is still an up arrow', () => {
    // Three more overdue tasks: the arrow reports movement, the tone reports
    // that the movement does not help.
    const overdue = formatDelta(3, 20, { unit: 'tasks', higherIsBetter: false })
    expect(overdue.direction).toBe('up')
    expect(overdue.arrow).toBe('↑')
    expect(overdue.tone).toBe('warning')

    const improving = formatDelta(-3, -20, { unit: 'tasks', higherIsBetter: false })
    expect(improving.direction).toBe('down')
    expect(improving.arrow).toBe('↓')
    expect(improving.tone).toBe('success')
  })

  it('renders the absolute change through the caller-supplied formatter', () => {
    const delta = formatDelta(90, 12.5, { format: formatMinutes, unit: 'of work' })
    expect(delta.label).toBe('up 1h 30m of work (+13%) from the previous period')
    // The `absolute` part stays a signed count whatever the label formatter is:
    // it is the number next to the arrow, not the sentence.
    expect(delta.absolute).toBe('+90')
  })

  it('uses a bare count sentence when no unit is given', () => {
    expect(formatDelta(4, 10).label).toBe('up 4 (+10%) from the previous period')
  })

  it('keeps an absolute rise with a zero percentage', () => {
    // Absolute is the direction of record; the percentage only restates it, and
    // a zero percentage is rendered unsigned rather than as "+0%".
    expect(formatDelta(5, 0)).toEqual({
      direction: 'up',
      arrow: '↑',
      label: 'up 5 (0%) from the previous period',
      percent: '0%',
      absolute: '+5',
      tone: 'success',
    })
  })

  it('produces a direction for every DeltaDirection', () => {
    const cases: Array<[DeltaDirection, ReturnType<typeof formatDelta>]> = [
      ['up', formatDelta(2, 10)],
      ['down', formatDelta(-2, -10)],
      ['flat', formatDelta(0, 0)],
      ['unknown', formatDelta(null, null)],
    ]
    for (const [direction, parts] of cases) {
      expect(parts.direction).toBe(direction)
      // Every direction carries a sentence that says the same thing in words.
      expect(parts.label).toContain('previous period')
    }
  })
})

describe('totalLabel', () => {
  it('names a known daily_metrics column and records whether more is better', () => {
    expect(totalLabel('tasks_created')).toEqual({
      label: 'Tasks created',
      unit: 'count',
      higherIsBetter: true,
    })
    expect(totalLabel('tasks_overdue')).toEqual({
      label: 'Tasks overdue',
      unit: 'count',
      higherIsBetter: false,
    })
  })

  it('marks the two duration columns as minutes so the caller renders a duration', () => {
    expect(totalLabel('planned_minutes')).toEqual({
      label: 'Time planned',
      unit: 'minutes',
      higherIsBetter: true,
    })
    expect(totalLabel('actual_minutes')).toEqual({
      label: 'Time recorded',
      unit: 'minutes',
      higherIsBetter: true,
    })
  })

  it('falls back to a readable label for a column it has never heard of', () => {
    // A backend column added without a frontend mapping must still render, not
    // throw and not show a raw snake_case token as if it were a label.
    expect(totalLabel('focus_minutes')).toEqual({
      label: 'focus minutes',
      unit: 'count',
      higherIsBetter: true,
    })
    expect(totalLabel('')).toEqual({ label: '', unit: 'count', higherIsBetter: true })
  })

  it('maps every column it declares, with only the four bad-news columns inverted', () => {
    const all: TotalLabel[] = Object.values(TOTAL_LABELS)
    expect(all).toHaveLength(12)
    expect(all.every((entry) => entry.label.length > 0)).toBe(true)
    expect(all.filter((entry) => entry.unit === 'minutes').map((entry) => entry.label)).toEqual([
      'Time planned',
      'Time recorded',
    ])
    expect(
      all
        .filter((entry) => !entry.higherIsBetter)
        .map((entry) => entry.label)
        .sort(),
    ).toEqual(['Tasks blocked', 'Tasks cancelled', 'Tasks overdue', 'Tasks rescheduled'])
  })

  it('agrees with its own table for every key it declares', () => {
    for (const key of Object.keys(TOTAL_LABELS)) {
      expect(totalLabel(key)).toEqual(TOTAL_LABELS[key])
    }
  })
})

describe('the missing-value contract', () => {
  /**
   * The single invariant this module exists to protect: `null` and `undefined`
   * render as the em dash, on every helper, with no exceptions to remember.
   * A formatter that prints `NaN`, `Infinity`, `0%` or an empty string here has
   * turned "not measured" into a measurement.
   */
  function outputsFor(missing: null | undefined): Array<[string, string]> {
    const now = NOW_MS
    const delta = formatDelta(missing, missing)
    return [
      ['formatNumber', formatNumber(missing)],
      ['formatNumber(2)', formatNumber(missing, 2)],
      ['formatPercent', formatPercent(missing)],
      ['formatPercent(1)', formatPercent(missing, 1)],
      ['formatMinutes', formatMinutes(missing)],
      ['formatHours', formatHours(missing)],
      ['formatSigned', formatSigned(missing)],
      ['formatScore', formatScore(missing)],
      ['formatSignedPercent', formatSignedPercent(missing)],
      ['formatUpdatedAgo', formatUpdatedAgo(missing, now)],
      ['formatShortDate', formatShortDate(missing)],
      ['formatMetricDate', formatMetricDate(missing)],
      ['formatDelta.percent', delta.percent],
      ['formatDelta.absolute', delta.absolute],
      ['formatDelta.label', delta.label],
    ]
  }

  it('renders every missing measurement as a non-empty, non-numeric placeholder', () => {
    for (const missing of [null, undefined] as const) {
      for (const [name, output] of outputsFor(missing)) {
        expect(output.length, name).toBeGreaterThan(0)
        expect(output, name).not.toMatch(/NaN|Infinity|undefined|null/)
      }
    }
  })

  it('never dresses a missing measurement up as a number', () => {
    for (const missing of [null, undefined] as const) {
      for (const [name, output] of outputsFor(missing)) {
        expect(output, name).not.toMatch(/^[+\-−]?\d/)
        expect(output, name).not.toBe('0%')
      }
      // The delta has no arrow to offer when nothing was computed.
      expect(formatDelta(missing, missing).arrow).toBe('')
    }
  })
})