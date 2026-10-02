import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { MetricCard } from '@/features/analytics/components/metric-card'
import { ScoreCard } from '@/features/analytics/components/score-card'
import { StalenessBanner } from '@/features/analytics/components/staleness-banner'
import { NO_VALUE, formatNumber } from '@/features/analytics/format'
import type { AnalyticsStaleness, StalenessStatus } from '@/features/analytics/hooks'
import type {
  ComparisonTotal,
  ConsistencyRead,
  FocusRead,
  MetricRange,
  ProductivityRead,
  ScoredRead,
  ScoreComponent,
} from '@/types/analytics'

/**
 * Component tests for the three cards every analytics view is built from.
 *
 * **These are the components that decide what a missing number looks like.**
 * The wire contract says a figure that could not be computed arrives as `null`
 * with a `reason_if_unavailable`, and the spec is explicit that
 * `"Not enough activity yet"` is the correct rendering of that while
 * `"0% productivity"` is a lie. Whether the screen gets that right is decided
 * here rather than in the formatter alone, so every payload below is fixed,
 * hand-checked and asserted on the exact number.
 *
 * The components are imported for real; only the clock is stubbed, and only for
 * the banner's "5 minutes ago" title.
 */

/** The real providers, which is where `TooltipProvider` comes from. */
function renderCard(node: ReactNode) {
  return render(<AppProviders>{node}</AppProviders>)
}

const RANGE: MetricRange = {
  start_date: '2026-01-01',
  end_date: '2026-01-30',
  granularity: 'day',
}

/* ------------------------------------------------------------- score fixtures */

/**
 * The spec's own worked example: a productivity score of 78 broken into
 * `24 + 19 + 18 + 17`, which is exactly 78 of a possible 100. Every
 * contributor test below leans on that identity, so the numbers are not
 * decorative — they are the arithmetic the card claims a reader can check.
 */
const PRODUCTIVITY_COMPONENTS: ScoreComponent[] = [
  { name: 'Completion', points: 24, max_points: 30, explanation: '8 of 10 tasks completed (80%).' },
  { name: 'Consistency', points: 19, max_points: 25, explanation: '14 active days of 30 (46.7%).' },
  { name: 'Deadline rate', points: 18, max_points: 25, explanation: '8 of 9 finished tasks met their due date (88.9%).' },
  { name: 'Focus time', points: 17, max_points: 20, explanation: '9 completed work sessions, 810 focused minutes.' },
]

const PRODUCTIVITY: ProductivityRead = {
  score: 78,
  available: true,
  reason_if_unavailable: null,
  components: PRODUCTIVITY_COMPONENTS,
  formula:
    '0.30 x completion + 0.25 x consistency + 0.25 x deadline adherence + 0.20 x focus, clamped to 0-100.',
  label: 'NEXUS Productivity Score',
  disclaimer: 'Derived from your own recorded activity. Not a scientifically validated measure.',
  range: RANGE,
  weight_total: 100,
}

/** 14 active days of 30 and a 5-day best streak: `28 + 7 = 35` of 100. */
const CONSISTENCY: ConsistencyRead = {
  score: 35,
  available: true,
  reason_if_unavailable: null,
  active_days: 14,
  window_days: 30,
  work_sessions: 9,
  session_count: 9,
  active_day_ratio: 0.4667,
  longest_streak: 5,
  current_streak: 2,
  formula: '0.60 x active-day ratio + 0.40 x longest streak over the window, clamped to 0-100.',
  label: 'NEXUS Consistency Score',
  disclaimer: 'Derived from days with recorded activity. Not a claim about habit or wellbeing.',
  range: RANGE,
  components: [
    { name: 'Active days', points: 28, max_points: 60, explanation: '14 of 30 days had recorded activity (46.7%).' },
    { name: 'Longest streak', points: 7, max_points: 40, explanation: 'Best run was 5 consecutive active days.' },
  ],
}

/** 810 of 1000 recorded minutes focused and 9 of 20 planned sessions run: `65 + 9 = 74`. */
const FOCUS: FocusRead = {
  score: 74,
  available: true,
  reason_if_unavailable: null,
  avg_session_minutes: 40,
  completed_planned_sessions: 9,
  interruptions: 3,
  reschedules: 2,
  focused_minutes: 810,
  total_minutes: 1000,
  formula: '0.80 x focused share of recorded time + 0.20 x planned sessions completed, clamped to 0-100.',
  label: 'NEXUS Focus Score',
  disclaimer: 'Derived from recorded work sessions. Not a measure of human attention.',
  range: RANGE,
  components: [
    { name: 'Focused minutes', points: 65, max_points: 80, explanation: '810 of 1000 recorded minutes were uninterrupted.' },
    { name: 'Planned sessions', points: 9, max_points: 20, explanation: '9 of 20 planned sessions ran to completion.' },
  ],
}

function unavailable(read: ScoredRead, reason: string | null): ScoredRead {
  return { ...read, score: null, available: false, reason_if_unavailable: reason, components: [] }
}

/* ------------------------------------------------------------- empty copy */

/** Copied from `EmptyAnalytics`; the card picks the row by score kind. */
const EMPTY_COPY = {
  productivity:
    'The score weighs four things at once: completed work, deadlines met, focus held and estimates kept. Complete or schedule a task and it can be computed.',
  consistency:
    'Recorded activity on at least part of the window is what this counts. A single completed task or work session is enough to start the streak.',
  focus:
    'This score is built from work sessions, and needs at least one planned session run to completion. Nothing is inferred about attention.',
}

describe('ScoreCard', () => {
  it('shows the score out of 100 with the disclaimer that qualifies it', () => {
    renderCard(<ScoreCard score={PRODUCTIVITY} />)

    expect(screen.getByRole('heading', { name: 'NEXUS Productivity Score' })).toBeInTheDocument()
    // The headline is its own text node, so "78" and the "/ 100" suffix are
    // separately assertable and cannot be confused with a contributor figure.
    expect(screen.getByText('78')).toBeInTheDocument()
    expect(screen.getByText('/ 100')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Derived from your own recorded activity. Not a scientifically validated measure.',
      ),
    ).toBeInTheDocument()
    // The "cannot be measured" note belongs to the other branch and must not
    // appear next to a number that was measured.
    expect(screen.queryByText('Not measurable for this window.')).toBeNull()
  })

  it('adds the contributor points up to the headline score', () => {
    renderCard(<ScoreCard score={PRODUCTIVITY} />)

    const bars = screen.getAllByRole('progressbar')
    const points = bars.map((bar) => Number(bar.getAttribute('aria-valuenow')))
    const maximums = bars.map((bar) => Number(bar.getAttribute('aria-valuemax')))
    expect(points).toEqual([24, 19, 18, 17])
    expect(maximums).toEqual([30, 25, 25, 20])
    // 24 + 19 + 18 + 17 === 78: the claim the card makes by showing points
    // rather than percentages, checked against the number actually rendered.
    expect(points.reduce((total, value) => total + value, 0)).toBe(78)
    expect(maximums.reduce((total, value) => total + value, 0)).toBe(100)

    expect(screen.getByText('Contributors')).toBeInTheDocument()
    expect(screen.getByText('24 of 30')).toBeInTheDocument()
    expect(screen.getByText('8 of 10 tasks completed (80%).')).toBeInTheDocument()
    expect(
      screen.getByRole('progressbar', { name: 'Completion: 24 of 30 points' }),
    ).toHaveAttribute('aria-valuenow', '24')
  })

  it('prints the backend formula verbatim so the score is not a black box', () => {
    renderCard(<ScoreCard score={PRODUCTIVITY} />)

    expect(screen.getByText('How this was computed')).toBeInTheDocument()
    expect(screen.getByText(PRODUCTIVITY.formula)).toBeInTheDocument()
  })

  it('lists supporting facts under the score', () => {
    renderCard(
      <ScoreCard
        score={CONSISTENCY}
        facts={[
          { label: 'Days with activity', value: '14 of 30' },
          { label: 'Best run', value: '5 days' },
        ]}
      />,
    )

    const facts = screen.getByText('Days with activity').closest('dl')
    expect(facts).not.toBeNull()
    expect(within(facts as HTMLElement).getByText('14 of 30')).toBeInTheDocument()
    expect(within(facts as HTMLElement).getByText('5 days')).toBeInTheDocument()
    // 28 + 7 === 35, the same checkable identity the headline claims.
    expect(
      screen.getAllByRole('progressbar').map((bar) => Number(bar.getAttribute('aria-valuenow'))),
    ).toEqual([28, 7])
  })

  it('gives the consistency score its own empty-state copy', () => {
    renderCard(<ScoreCard score={unavailable(CONSISTENCY, null)} />)

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.getByText(EMPTY_COPY.consistency)).toBeInTheDocument()
  })

  it('gives the focus score its own empty-state copy', () => {
    renderCard(<ScoreCard score={unavailable(FOCUS, null)} />)

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.getByText(EMPTY_COPY.focus)).toBeInTheDocument()
  })

  it('falls back to the productivity copy for a label it does not recognise', () => {
    renderCard(<ScoreCard score={unavailable(PRODUCTIVITY, null)} />)

    expect(screen.getByText(EMPTY_COPY.productivity)).toBeInTheDocument()
    // The three treatments are genuinely different sentences, not one string
    // reused: a missing focus score and a missing deadline rate are filled by
    // different behaviour, so the copy has to differ.
    expect(EMPTY_COPY.productivity).not.toBe(EMPTY_COPY.consistency)
    expect(EMPTY_COPY.consistency).not.toBe(EMPTY_COPY.focus)
  })

  it('shows the reason verbatim and never renders 0 for an unavailable score', () => {
    renderCard(
      <ScoreCard
        score={unavailable(PRODUCTIVITY, 'No completed tasks and no work sessions in this window.')}
      />,
    )

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(
      screen.getByText('No completed tasks and no work sessions in this window.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Not measurable for this window.')).toBeInTheDocument()
    // The whole point of the branch: no score line at all, and so no `0`.
    expect(screen.queryByText('0')).toBeNull()
    expect(screen.queryByText('/ 100')).toBeNull()
    expect(screen.queryAllByRole('progressbar')).toHaveLength(0)
    expect(screen.queryByText('Contributors')).toBeNull()
  })

  it('treats available:false as unavailable even when a score rode along', () => {
    // `unavailable` is `!available || score === null`, so a contradictory
    // payload must not slip a `0` through by way of the score field.
    renderCard(<ScoreCard score={{ ...PRODUCTIVITY, available: false, score: 0 }} />)

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('renders a measured zero as a real score, not as a missing one', () => {
    const zeroed: ProductivityRead = {
      ...PRODUCTIVITY,
      score: 0,
      components: PRODUCTIVITY_COMPONENTS.map((component) => ({ ...component, points: 0 })),
    }
    renderCard(<ScoreCard score={zeroed} />)

    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByText('/ 100')).toBeInTheDocument()
    // The two states must be distinguishable in both directions: a real zero
    // never claims there was nothing to measure, and a missing score never
    // claims a number.
    expect(screen.queryByText('Not enough activity yet')).toBeNull()
    expect(screen.queryByText('Not measurable for this window.')).toBeNull()
    expect(
      screen.getByText(
        'Derived from your own recorded activity. Not a scientifically validated measure.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Completion: 0 of 30 points' })).toHaveAttribute(
      'aria-valuenow',
      '0',
    )
  })

  it('hides the contributor breakdown in the compact variant but keeps the score', () => {
    renderCard(<ScoreCard score={PRODUCTIVITY} compact />)

    expect(screen.getByText('78')).toBeInTheDocument()
    expect(screen.getByText('/ 100')).toBeInTheDocument()
    expect(screen.getByText(PRODUCTIVITY.formula)).toBeInTheDocument()
    expect(screen.queryByText('Contributors')).toBeNull()
    expect(screen.queryAllByRole('progressbar')).toHaveLength(0)
  })

  it('survives a payload with no label on the unavailable path', () => {
    // `metricFor` reads `score.label`, and the unavailable path is exactly the
    // one a partial response takes, so a missing label must not throw and take
    // the surrounding page with it.
    const payload: Record<string, unknown> = {
      ...PRODUCTIVITY,
      score: null,
      available: false,
      reason_if_unavailable: null,
      components: [],
    }
    delete payload.label

    renderCard(<ScoreCard score={payload as unknown as ScoredRead} />)

    expect(screen.getByText('Not enough activity yet')).toBeInTheDocument()
    expect(screen.getByText(EMPTY_COPY.productivity)).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- MetricCard */

/** 27 tasks this week against 22 last: the spec's weekly worked example. */
const COMPLETED: Pick<ComparisonTotal, 'absolute_change' | 'percent_change'> = {
  absolute_change: 5,
  percent_change: 22.727272727272727,
}

describe('MetricCard', () => {
  it('shows the label, the value and the hint that defines it', () => {
    renderCard(
      <MetricCard label="Tasks completed" value="27" hint="Finished in the selected window." />,
    )

    expect(screen.getByText('Tasks completed')).toBeInTheDocument()
    expect(screen.getByText('27')).toBeInTheDocument()
    expect(screen.getByText('Finished in the selected window.')).toBeInTheDocument()
  })

  it('renders the dash, not 0, when the figure is null', () => {
    // The shipped call sites pass `formatNumber(null)`; the card has to render
    // whatever that contract produces without inventing a zero of its own.
    renderCard(<MetricCard label="Tasks completed" value={formatNumber(null)} />)

    expect(screen.getByText(NO_VALUE)).toBeInTheDocument()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('states the comparison in words as well as with an arrow', () => {
    renderCard(
      <MetricCard
        label="Tasks completed"
        value="27"
        comparison={COMPLETED}
        comparisonUnit="tasks"
      />,
    )

    // 5 of 22 is 22.727…%, which the shared formatter rounds to +23%.
    expect(screen.getByText('up 5 tasks (+23%) from the previous period')).toBeInTheDocument()
    const arrow = screen.getByText('↑')
    // The glyph is decorative: the sentence above is what a screen reader and a
    // reader who cannot see the tint actually get, so direction is never
    // carried by colour or by the arrow alone.
    expect(arrow).toHaveAttribute('aria-hidden', 'true')
    expect(arrow.parentElement).toHaveClass('text-success')
  })

  it('points the arrow down for a fall and says so in the sentence', () => {
    renderCard(
      <MetricCard
        label="Work time"
        value="2h 15m"
        comparison={{ absolute_change: -3, percent_change: -15 }}
        comparisonUnit="tasks"
      />,
    )

    expect(screen.getByText('↓')).toBeInTheDocument()
    expect(screen.getByText('down 3 tasks (−15%) from the previous period')).toBeInTheDocument()
  })

  it('keeps the up arrow when a rise is bad news', () => {
    // Direction and goodness are separate questions: 2 more overdue tasks is an
    // up arrow in a warning tone, and the words must still say "up".
    renderCard(
      <MetricCard
        label="Tasks overdue"
        value="2"
        higherIsBetter={false}
        comparison={{ absolute_change: 2, percent_change: 14.285714285714286 }}
        comparisonUnit="tasks"
      />,
    )

    expect(screen.getByText('up 2 tasks (+14%) from the previous period')).toBeInTheDocument()
    expect(screen.getByText('↑')).toBeInTheDocument()
    expect(screen.getByText('↑').parentElement).toHaveClass('text-warning')
  })

  it('omits the percentage when the previous period was zero', () => {
    // A percentage change from a zero baseline does not exist; it is `null` on
    // the wire, never `Infinity` and never "+0%".
    renderCard(
      <MetricCard
        label="Work sessions"
        value="3"
        comparison={{ absolute_change: 3, percent_change: null }}
        comparisonUnit="sessions"
      />,
    )

    expect(screen.getByText('up 3 sessions from the previous period')).toBeInTheDocument()
  })

  it('says so plainly when there is no previous period at all', () => {
    renderCard(
      <MetricCard
        label="Work sessions"
        value="0"
        comparison={{ absolute_change: null, percent_change: null }}
      />,
    )

    expect(screen.getByText('no comparison with the previous period')).toBeInTheDocument()
    // No arrow at all: pointing up or down at a figure that was never computed
    // would be a claim.
    expect(screen.queryByText('↑')).toBeNull()
    expect(screen.queryByText('↓')).toBeNull()
    expect(screen.queryByText('→')).toBeNull()
  })

  it('replaces the value with the reason when the figure is unavailable', () => {
    renderCard(
      <MetricCard
        label="Deadline adherence"
        value={formatNumber(null)}
        comparison={COMPLETED}
        comparisonUnit="tasks"
        unavailableReason="No finished task in this window carried a due date."
      />,
    )

    expect(
      screen.getByText('No finished task in this window carried a due date.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Not measurable for this window.')).toBeInTheDocument()
    // A comparison on a number that does not exist is never printed.
    expect(screen.queryByText('up 5 tasks (+23%) from the previous period')).toBeNull()
    expect(screen.queryByText('↑')).toBeNull()
  })

  it('renders before any figure has arrived without inventing a delta', () => {
    // `MetricCard` has no loading prop of its own: the pages own the skeleton
    // (`HeadlineSkeleton` on the dashboard). What the card must do is stay
    // honest when handed the placeholder value and nothing to compare against.
    renderCard(
      <MetricCard
        label="Current workload"
        value={formatNumber(null)}
        hint="Planned time against available hours."
      />,
    )

    expect(screen.getByText('Current workload')).toBeInTheDocument()
    expect(screen.getByText(NO_VALUE)).toBeInTheDocument()
    expect(screen.getByText('Planned time against available hours.')).toBeInTheDocument()
    expect(screen.queryByText('0')).toBeNull()
    expect(screen.queryByText(/previous period/)).toBeNull()
  })

  it('scales the value for a headline row and a supporting panel', () => {
    const { unmount } = renderCard(<MetricCard label="Tasks completed" value="27" size="lg" />)
    expect(screen.getByText('27')).toHaveClass('text-3xl')
    unmount()

    renderCard(<MetricCard label="Tasks completed" value="27" />)
    expect(screen.getByText('27')).toHaveClass('text-2xl')
  })

  it('puts the long explanation behind a labelled trigger', async () => {
    const user = userEvent.setup()
    renderCard(
      <MetricCard
        label="Tasks completed"
        value="27"
        explanation="Finished tasks in the window, counting any completion timestamp inside it."
      />,
    )

    const trigger = screen.getByRole('button', { name: 'More about Tasks completed' })
    expect(screen.queryByRole('tooltip')).toBeNull()

    await user.hover(trigger)
    const tooltip = await screen.findByRole('tooltip')
    expect(tooltip).toHaveTextContent(
      'Finished tasks in the window, counting any completion timestamp inside it.',
    )
  })
})

/* ----------------------------------------------------------- StalenessBanner */

/** Exactly the five minutes the freshness titles are asserted against. */
const NOW = '2026-01-30T09:10:00Z'
const UPDATED_AT = '2026-01-30T09:05:00Z'

function staleness(status: StalenessStatus, message: string): AnalyticsStaleness {
  return {
    status,
    aggregatesThrough: '2026-01-28',
    windowEnd: '2026-01-30',
    missingDays: status === 'fresh' ? 0 : 2,
    windowDays: 30,
    reportedStale: status !== 'fresh',
    message,
  }
}

describe('StalenessBanner', () => {
  beforeEach(() => {
    // Only `Date` is faked: the tooltip's 200ms delay and user-event's own
    // timers must keep running for real.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reports a fresh window without an age when nothing was stamped', () => {
    renderCard(
      <StalenessBanner
        {...staleness('fresh', 'Every day in this window is calculated, through 2026-01-30.')}
        updatedAt={null}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(within(alert).getByRole('heading', { name: 'Up to date' })).toBeInTheDocument()
    expect(alert).toHaveTextContent('Every day in this window is calculated, through 2026-01-30.')
    expect(alert).toHaveClass('text-success')
    // A live region, so a recalculation announces its own result.
    expect(alert).toHaveAttribute('aria-live', 'polite')
  })

  it('ages the aggregate stamp into the fresh title', () => {
    renderCard(
      <StalenessBanner
        {...staleness('fresh', 'Every day in this window is calculated, through 2026-01-30.')}
        updatedAt={UPDATED_AT}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Updated 5 minutes ago' })).toBeInTheDocument()
  })

  it('warns that part of the window is uncalculated and how to fix it', () => {
    renderCard(
      <StalenessBanner
        {...staleness(
          'partial',
          'Aggregates run through 2026-01-28; 2 of 30 days in this window have not been calculated.',
        )}
        updatedAt={UPDATED_AT}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(
      within(alert).getByRole('heading', { name: 'These figures are incomplete' }),
    ).toBeInTheDocument()
    expect(alert).toHaveTextContent(
      'Aggregates run through 2026-01-28; 2 of 30 days in this window have not been calculated. Recalculate to bring them up to date.',
    )
    expect(alert).toHaveClass('text-warning')
  })

  it('says calculating rather than reporting zero activity', () => {
    // An empty aggregate array and a window with no activity are the same thing
    // on the wire and completely different claims.
    renderCard(
      <StalenessBanner
        {...staleness('calculating', 'No aggregates have been written for this window yet.')}
        updatedAt={null}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(within(alert).getByRole('heading', { name: 'Calculating…' })).toBeInTheDocument()
    expect(alert).toHaveTextContent(
      'No aggregates have been written for this window yet. Recalculate to compute the aggregates for this window.',
    )
    expect(alert).toHaveClass('text-warning')
  })

  it('admits when freshness cannot be determined', () => {
    renderCard(
      <StalenessBanner
        {...staleness('unknown', 'The backend did not report an aggregate date.')}
        updatedAt={null}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(within(alert).getByRole('heading', { name: 'Freshness is unknown' })).toBeInTheDocument()
    // The unknown branch gets no instruction appended: there is nothing to
    // recalculate confidently when the coverage is not known.
    expect(alert).toHaveTextContent('The backend did not report an aggregate date.')
    expect(alert.textContent).not.toContain('Recalculate to')
  })

  it('prints nothing beyond the title when there is nothing to say', () => {
    renderCard(<StalenessBanner {...staleness('fresh', '')} updatedAt={null} />)

    // No placeholder dash, no default message, no action: the banner collapses
    // to the single fact it can support.
    expect(screen.getByRole('alert').textContent).toBe('Up to date')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('reports how many days a recalculation wrote, in the singular and the plural', () => {
    const { unmount } = renderCard(
      <StalenessBanner {...staleness('partial', 'Incomplete.')} updatedAt={null} rowsWritten={1} />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Incomplete. Recalculate to bring them up to date. 1 day recalculated.',
    )
    unmount()

    renderCard(
      <StalenessBanner
        {...staleness('partial', 'Incomplete.')}
        updatedAt={null}
        rowsWritten={30}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Incomplete. Recalculate to bring them up to date. 30 days recalculated.',
    )
  })

  it('withholds the recalculation result while the request is in flight', () => {
    renderCard(
      <StalenessBanner
        {...staleness('partial', 'Incomplete.')}
        updatedAt={null}
        rowsWritten={30}
        isRecalculating
        onRecalculate={() => undefined}
      />,
    )

    expect(screen.queryByText(/days recalculated/)).toBeNull()
    const button = screen.getByRole('button', { name: /Recalculate/ })
    expect(button).toBeDisabled()
    expect(within(button).getByRole('status')).toHaveTextContent('Loading')
  })

  it('recalculates on demand when the caller offers the action', async () => {
    const user = userEvent.setup()
    const onRecalculate = vi.fn()
    renderCard(
      <StalenessBanner
        {...staleness('partial', 'Incomplete.')}
        updatedAt={null}
        onRecalculate={onRecalculate}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Recalculate/ }))
    expect(onRecalculate).toHaveBeenCalledTimes(1)
  })
})
