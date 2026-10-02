import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import { NO_VALUE } from '@/features/analytics/format'
import { RecommendationCard } from '@/features/risk/components/recommendation-card'
import { RecommendationListSkeleton } from '@/features/risk/components/recommendation-list-skeleton'
import { RiskCard } from '@/features/risk/components/risk-card'
import { RiskListSkeleton } from '@/features/risk/components/risk-list-skeleton'
import { RiskScoreMeter } from '@/features/risk/components/risk-score-meter'
import { RiskSummaryTiles } from '@/features/risk/components/risk-summary-tiles'
import { SeverityBadge } from '@/features/risk/components/severity-badge'
import type {
  RecommendationRead,
  RiskRead,
  RiskSeverity,
  RiskSummaryRead,
} from '@/types/risk'

/**
 * Component tests for the cards the Phase 7 risk surface is built from.
 *
 * **The components are mounted for real and only the router is supplied.** No
 * component under test is mocked, because every claim below is a decision these
 * components make on their own: which word stands beside a band, whether a score
 * that never arrived prints as a zero, whether an evidence line keeps its
 * number, and which transitions are legal from the status a row is in. Mocking
 * any of them would assert the test's own fixtures back at itself.
 *
 * The assertions are behavioural rather than structural. There are no snapshots
 * here, because a snapshot records the markup of the day and says nothing about
 * whether a reader who cannot see colour can still read the severity, which is
 * the requirement these components exist to discharge.
 */

const TASK_ID = '11111111-1111-4111-8111-111111111111'
const RISK_ID = '22222222-2222-4222-8222-222222222222'
const SUGGESTION_ID = '33333333-3333-4333-8333-333333333333'

/**
 * The router and the tooltip host are the only context these components need:
 * `RiskSummaryTiles` and `RiskCard` render links, and `RiskScoreMeter` and
 * `MetricCard` put their long explanations behind a Radix tooltip, which needs a
 * provider above it. `AppProviders` is deliberately not used — it mounts the
 * shared query client, and nothing in this file reads a query.
 */
function renderRiskUI(node: ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider delayDuration={200}>{node}</TooltipProvider>
    </MemoryRouter>,
  )
}

function risk(overrides: Partial<RiskRead> = {}): RiskRead {
  return {
    id: RISK_ID,
    risk_type: 'deadline',
    severity: 'high',
    // 50-74 is the backend's `high` band, so the fixture is internally
    // consistent: the score on the meter and the band beside it cannot disagree.
    score: 60,
    title: 'Two tasks are due in the next two days with less time booked than they need',
    description:
      'The recorded work sessions before the due date cover 120 of about 300 estimated minutes.',
    evidence: [
      {
        label: 'Time needed',
        detail: '300 minutes of estimated work across the two tasks.',
        // Rendered through `formatSigned(value, 1)`, so both the sign and the
        // one decimal place are the formatter's and are asserted as printed.
        contribution: 34,
      },
      {
        label: 'Time booked',
        detail: '120 minutes of recorded work sessions fall before either due date.',
        contribution: -12.5,
      },
    ],
    evidence_strength: 'medium',
    entity_type: 'task',
    entity_id: TASK_ID,
    status: 'active',
    detected_at: '2026-01-09T08:00:00Z',
    resolved_at: null,
    metadata: {},
    recommendations: [],
    ...overrides,
  }
}

function suggestion(overrides: Partial<RecommendationRead> = {}): RecommendationRead {
  return {
    id: SUGGESTION_ID,
    recommendation_type: 'block_time',
    priority: 'high',
    title: 'Schedule another 180 minutes before 9 Jan',
    description:
      'Add two work sessions for Draft the migration plan before its due date.',
    reason:
      'It needs about 300 minutes and 120 are booked, so 180 minutes have no slot yet.',
    entity_type: 'task',
    entity_id: TASK_ID,
    risk_id: RISK_ID,
    status: 'new',
    created_at: '2026-01-09T08:05:00Z',
    responded_at: null,
    expires_at: null,
    metadata: {},
    ...overrides,
  }
}

/* ------------------------------------------------------------ SeverityBadge */

describe('SeverityBadge', () => {
  it('prints the band word beside an icon, for every band', () => {
    const bands: RiskSeverity[] = ['critical', 'high', 'medium', 'low']

    for (const band of bands) {
      const { unmount } = renderRiskUI(<SeverityBadge severity={band} />)

      // The word is the chip's entire accessible content. That is the whole
      // point of the component: a red/amber/blue/grey ladder is the design
      // system's natural move and it is the move that fails first in dark mode,
      // in print, for a colour-blind reader and for a screen reader.
      const word = band.charAt(0).toUpperCase() + band.slice(1)
      const chip = screen.getByText(word)
      expect(chip.textContent).toBe(word)

      // The icon reinforces the word and is hidden from the announcement: a
      // reader hearing "warning triangle, High" has been told the same thing
      // twice, which is noise rather than information.
      const icon = chip.querySelector('svg')
      expect(icon).not.toBeNull()
      expect(icon).toHaveAttribute('aria-hidden', 'true')

      // The band threshold rides on the chip itself, so a reader can check the
      // score on the meter against the boundary rather than trusting it.
      expect(chip.getAttribute('title')).toContain('out of 100')

      unmount()
    }
  })

  it('gives the four bands four different words and four different shapes', () => {
    // Two bands sharing a word would leave a reader with no way to tell them
    // apart once the colour is gone, which is the failure the ladder of
    // `OctagonAlert -> TriangleAlert -> CircleAlert -> ShieldCheck` prevents.
    const rendered = (['critical', 'high', 'medium', 'low'] as const).map((band) => {
      const { unmount } = renderRiskUI(<SeverityBadge severity={band} />)
      const chip = screen.getByText(band.charAt(0).toUpperCase() + band.slice(1))
      const shapes = [...chip.querySelectorAll('svg')].map((svg) => svg.innerHTML).join('')
      unmount()
      return { word: chip.textContent, shapes }
    })

    expect(new Set(rendered.map((entry) => entry.word)).size).toBe(4)
    expect(new Set(rendered.map((entry) => entry.shapes)).size).toBe(4)
  })

  it('still renders a word for a band this build does not know', () => {
    // A newer engine can send a severity this client was never taught. The chip
    // must show the server's own word rather than an icon with no label, which a
    // screen reader would announce as nothing at all.
    renderRiskUI(<SeverityBadge severity={'elevated' as RiskSeverity} />)

    expect(screen.getByText('elevated')).toBeInTheDocument()
    expect(screen.getByText('elevated').parentElement?.querySelector('svg')).not.toBeNull()
  })
})

/* ----------------------------------------------------------- RiskScoreMeter */

describe('RiskScoreMeter', () => {
  it('prints the score as digits and carries it as an accessible value', () => {
    renderRiskUI(<RiskScoreMeter score={62} severity="high" />)

    // The bar is the decoration; the digits are the content. A meter alone is
    // invisible to a screen reader's summary and collapses in print.
    expect(screen.getByText('62')).toBeInTheDocument()
    expect(screen.getByText('/ 100')).toBeInTheDocument()

    const bar = screen.getByRole('progressbar', { name: 'Risk score 62 out of 100' })
    expect(bar).toHaveAttribute('aria-valuenow', '62')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
  })

  it('rounds a fractional score rather than printing its decimals', () => {
    renderRiskUI(<RiskScoreMeter score={61.6} severity="high" />)

    expect(screen.getByText('62')).toBeInTheDocument()
    expect(screen.queryByText('61.6')).toBeNull()
  })

  it('renders the em dash, not "0 / 100", when no score arrived', () => {
    // `score` is typed `number` because a detector that could not judge never
    // persists a row. The guard is here for the malformed-payload path, where
    // `score || 0` would print "0 / 100" and claim a measurement that does not
    // exist and is in any case minimal.
    renderRiskUI(<RiskScoreMeter score={null as unknown as number} severity="high" />)

    expect(screen.getByText(NO_VALUE)).toBeInTheDocument()
    expect(screen.queryByText('/ 100')).toBeNull()
    expect(screen.queryByText('0')).toBeNull()
    // The bar is left empty rather than filled to zero, and it says so by name.
    const bar = screen.getByRole('progressbar', { name: 'Risk score not available' })
    expect(bar).toHaveAttribute('aria-valuenow', '0')
  })

  it('declines a score that is not a number at all', () => {
    renderRiskUI(<RiskScoreMeter score={Number.NaN} severity="high" />)

    expect(screen.getByText(NO_VALUE)).toBeInTheDocument()
    expect(screen.queryByText('NaN')).toBeNull()
    expect(screen.queryByText('/ 100')).toBeNull()
  })

  it('clamps a score outside 0-100 into the bar without rewriting the figure', () => {
    // The fill is bounded; the printed number is whatever the server sent, so a
    // payload out of range shows as a data problem rather than being smoothed
    // into a plausible figure.
    renderRiskUI(<RiskScoreMeter score={140} severity="critical" />)

    expect(screen.getByText('140')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Risk score 140 out of 100' })).toHaveAttribute(
      'aria-valuenow',
      '100',
    )
  })

  it('names the band it is scoring against on the explanation trigger', () => {
    renderRiskUI(<RiskScoreMeter score={30} severity="medium" />)

    expect(
      screen.getByRole('button', { name: 'More about the risk score scale' }),
    ).toBeInTheDocument()
  })
})

/* ----------------------------------------------------------------- RiskCard */

describe('RiskCard', () => {
  it('renders the title, the band and the score it was given', () => {
    const row = risk()
    renderRiskUI(<RiskCard risk={row} />)

    expect(screen.getByRole('heading', { name: row.title })).toBeInTheDocument()
    expect(screen.getByText(row.description)).toBeInTheDocument()
    // The band word, not a coloured dot.
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('60')).toBeInTheDocument()
  })

  it('lists the evidence lines under Why with the numbers that produced the score', () => {
    const row = risk()
    renderRiskUI(<RiskCard risk={row} />)

    const why = screen.getByRole('heading', { name: 'Why' }).closest('section') as HTMLElement

    // Both lines, both details, both contributions — the part of the score the
    // reader is asked to argue with, printed rather than summarised.
    expect(within(why).getAllByRole('listitem')).toHaveLength(2)
    expect(within(why).getByText('Time needed')).toBeInTheDocument()
    expect(
      within(why).getByText('300 minutes of estimated work across the two tasks.'),
    ).toBeInTheDocument()
    expect(within(why).getByText('+34.0')).toBeInTheDocument()
    expect(within(why).getByText('Time booked')).toBeInTheDocument()
    expect(
      within(why).getByText('120 minutes of recorded work sessions fall before either due date.'),
    ).toBeInTheDocument()
    // U+2212 MINUS SIGN, not a hyphen: the formatter signs the contribution so
    // the parts can be read against the total rather than merely added to it.
    expect(within(why).getByText('−12.5')).toBeInTheDocument()

    // The strength is described in words about how much was recorded. It is
    // explicitly not confidence, so it is never rendered as a percentage.
    expect(within(why).getByText(/Evidence strength: medium\./)).toBeInTheDocument()
    expect(why.textContent).not.toMatch(/%/)
  })

  it('names the affected entity by kind and links it when a destination exists', () => {
    const row = risk()
    renderRiskUI(
      <RiskCard risk={row} entityHref={(candidate) => `/tasks/${candidate.entity_id}`} />,
    )

    const link = screen.getByRole('link', { name: 'Task' })
    expect(link).toHaveAttribute('href', `/tasks/${TASK_ID}`)
  })

  it('names a finding about the account as a whole instead of inventing a subject', () => {
    const row = risk({ risk_type: 'workload', entity_type: null, entity_id: null })
    renderRiskUI(<RiskCard risk={row} />)

    expect(screen.getByText(/The account as a whole/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Task' })).toBeNull()
  })

  it('says "No suggested action yet" when the rule produced none', () => {
    // The schema documents the empty suggestion list as a real state, not a
    // failure, so the card states it rather than leaving a gap where an action
    // block would have been.
    renderRiskUI(<RiskCard risk={risk()} />)

    expect(screen.getByText(/^No suggested action yet\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open suggestion' })).toBeNull()
  })

  it('shows the suggestion it does have, with its reason', () => {
    renderRiskUI(
      <RiskCard
        risk={risk({
          recommendations: [
            {
              id: SUGGESTION_ID,
              recommendation_type: 'block_time',
              priority: 'high',
              title: 'Schedule another 180 minutes before 9 Jan',
              reason: '180 minutes of estimated work have no booked session before the due date.',
              status: 'new',
              created_at: '2026-01-09T08:05:00Z',
            },
          ],
        })}
        onOpenRecommendation={vi.fn()}
      />,
    )

    expect(screen.getByText('Schedule another 180 minutes before 9 Jan')).toBeInTheDocument()
    expect(
      screen.getByText('180 minutes of estimated work have no booked session before the due date.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/^No suggested action yet\./)).toBeNull()
    expect(screen.getByRole('button', { name: 'Open suggestion' })).toBeInTheDocument()
  })

  it('renders no transition button at all unless the caller supplied that handler', () => {
    const TRANSITIONS = ['Acknowledge', 'Mark resolved', 'Dismiss', 'Open suggestion']
    const { unmount } = renderRiskUI(<RiskCard risk={risk()} />)

    // A read-only placement of this card must not offer a control that would do
    // nothing when pressed. The score's explanation trigger is not a transition,
    // so the claim is made about these four names rather than about every button
    // on the card.
    for (const name of TRANSITIONS) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    unmount()

    renderRiskUI(<RiskCard risk={risk()} onAcknowledge={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument()
    // The two answers this placement did not wire up stay absent.
    expect(screen.queryByRole('button', { name: 'Mark resolved' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
  })

  it('calls each supplied handler once, with the row it was rendered from', async () => {
    const user = userEvent.setup()
    const onAcknowledge = vi.fn()
    const onDismiss = vi.fn()
    const onResolve = vi.fn()
    const row = risk()
    renderRiskUI(
      <RiskCard
        risk={row}
        onAcknowledge={onAcknowledge}
        onDismiss={onDismiss}
        onResolve={onResolve}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Acknowledge' }))
    await user.click(screen.getByRole('button', { name: 'Mark resolved' }))
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))

    for (const handler of [onAcknowledge, onDismiss, onResolve]) {
      expect(handler).toHaveBeenCalledOnce()
      expect(handler.mock.calls[0]?.[0]).toEqual(row)
    }
  })

  it('withdraws acknowledge once the risk has been acknowledged, and keeps the rest', () => {
    // Acknowledging means "still true, stop asking". Offering it again would
    // offer a transition the backend refuses with a 409.
    renderRiskUI(
      <RiskCard
        risk={risk({ status: 'acknowledged' })}
        onAcknowledge={vi.fn()}
        onDismiss={vi.fn()}
        onResolve={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Mark resolved' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
  })

  it('replaces the buttons with the closing sentence on a terminal risk', () => {
    renderRiskUI(
      <RiskCard
        risk={risk({ status: 'dismissed', resolved_at: '2026-01-10T09:00:00Z' })}
        onAcknowledge={vi.fn()}
        onDismiss={vi.fn()}
        onResolve={vi.fn()}
      />,
    )

    for (const name of ['Acknowledge', 'Mark resolved', 'Dismiss']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    expect(screen.getByText(/^Closed\. Dismissed/)).toBeInTheDocument()
  })

  it('attributes a failed transition to the card it happened on', () => {
    renderRiskUI(
      <RiskCard
        risk={risk()}
        onAcknowledge={vi.fn()}
        actionError="This risk has already been closed, so this action no longer applies to it."
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(
      'This risk has already been closed, so this action no longer applies to it.',
    )
  })

  it('disables every transition while one of them is in flight', () => {
    renderRiskUI(
      <RiskCard
        risk={risk()}
        onAcknowledge={vi.fn()}
        onDismiss={vi.fn()}
        onResolve={vi.fn()}
        isActing
      />,
    )

    // The accessible name must NOT move while the call is out. The spinner that
    // replaces the icon used to carry its own sr-only "Loading", which a screen
    // reader folded into the button's name — so "Acknowledge" became
    // "LoadingAcknowledge" at the exact moment the user was waiting on it. The
    // busy state now lives on the button as `aria-busy`, which is where it
    // belongs, and the label is pinned here so the regression cannot return.
    for (const name of ['Acknowledge', 'Mark resolved', 'Dismiss']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('aria-busy', 'true')
    }
    expect(screen.queryByText('Loading')).not.toBeInTheDocument()
  })

  it('states plainly that a finding arrived with no evidence lines', () => {
    renderRiskUI(<RiskCard risk={risk({ evidence: [] })} />)

    expect(screen.getByText('This finding carries no recorded evidence lines.')).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------- RiskSummaryTiles */

describe('RiskSummaryTiles', () => {
  const SUMMARY: RiskSummaryRead = {
    critical: 1,
    high: 2,
    medium: 3,
    low: 4,
    total: 10,
    needs_attention: true,
  }

  it('renders four bands with their counts, in the backend\'s own order', () => {
    renderRiskUI(<RiskSummaryTiles summary={SUMMARY} />)

    const tiles = screen.getAllByRole('link')
    expect(tiles.map((tile) => tile.getAttribute('href'))).toEqual([
      '/risks?severity=critical',
      '/risks?severity=high',
      '/risks?severity=medium',
      '/risks?severity=low',
    ])
    // Most severe first, which is the order `RISK_SEVERITIES` and the backend's
    // own sort both use, so the header cannot disagree with the list below it.
    expect(tiles.map((tile) => tile.querySelector('p')?.textContent)).toEqual([
      'Critical',
      'High',
      'Medium',
      'Low',
    ])
    expect(tiles.map((tile) => tile.textContent)).toEqual([
      expect.stringContaining('1'),
      expect.stringContaining('2'),
      expect.stringContaining('3'),
      expect.stringContaining('4'),
    ])
  })

  it('states the band threshold on each tile rather than hiding it behind hover', () => {
    renderRiskUI(<RiskSummaryTiles summary={SUMMARY} />)

    expect(screen.getByText('A score of 75 or above out of 100.')).toBeInTheDocument()
    expect(screen.getByText('A score from 50 to 74 out of 100.')).toBeInTheDocument()
    expect(screen.getByText('A score from 25 to 49 out of 100.')).toBeInTheDocument()
    expect(screen.getByText('A score below 25 out of 100.')).toBeInTheDocument()
  })

  it('marks the band being filtered and only that one', () => {
    renderRiskUI(<RiskSummaryTiles summary={SUMMARY} activeSeverity="high" />)

    expect(screen.getByRole('link', { name: /High/ })).toHaveAttribute('aria-current', 'true')
    for (const band of ['critical', 'medium', 'low']) {
      expect(screen.getByRole('link', { name: new RegExp(band, 'i') })).not.toHaveAttribute(
        'aria-current',
      )
    }
  })

  it('renders nothing at all when no risk is recorded', () => {
    // Four zeroes across the top of an empty Risk Center is the worst possible
    // first impression: it looks like a measurement, and the engine running and
    // finding nothing is the opposite of one.
    renderRiskUI(
      <RiskSummaryTiles
        summary={{ critical: 0, high: 0, medium: 0, low: 0, total: 0, needs_attention: false }}
      />,
    )

    expect(screen.getByText('No significant risk detected yet')).toBeInTheDocument()
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    expect(screen.queryByText('Critical')).toBeNull()
  })

  it('reports the attention sentence in both directions and the total', () => {
    const { unmount } = renderRiskUI(<RiskSummaryTiles summary={SUMMARY} />)
    expect(screen.getByText(/At least one recorded risk is high or critical\./)).toBeTruthy()
    expect(screen.getByText(/10 risks recorded in total\./)).toBeTruthy()
    unmount()

    renderRiskUI(
      <RiskSummaryTiles
        summary={{ critical: 0, high: 0, medium: 1, low: 0, total: 1, needs_attention: false }}
      />,
    )
    expect(screen.getByText(/No recorded risk is currently high or critical\./)).toBeTruthy()
    expect(screen.getByText(/1 risk recorded in total\./)).toBeTruthy()
  })

  it('lets a caller own the href, because the band lives in this page\'s URL', () => {
    renderRiskUI(
      <RiskSummaryTiles
        summary={SUMMARY}
        basePath="/nowhere"
        buildHref={(band) => `/custom?band=${band}`}
      />,
    )

    expect(screen.getAllByRole('link').map((tile) => tile.getAttribute('href'))).toEqual([
      '/custom?band=critical',
      '/custom?band=high',
      '/custom?band=medium',
      '/custom?band=low',
    ])
  })
})

/* ------------------------------------------------------- RecommendationCard */

describe('RecommendationCard', () => {
  it('renders WHAT, WHY and the proposed action, all three of them', () => {
    const row = suggestion()
    renderRiskUI(<RecommendationCard recommendation={row} />)

    expect(screen.getByRole('heading', { name: row.title })).toBeInTheDocument()
    const why = screen.getByRole('heading', { name: 'Why' }).closest('section') as HTMLElement
    expect(within(why).getByText(row.reason)).toBeInTheDocument()
    const action = screen.getByRole('heading', { name: 'Suggested action' }).closest(
      'section',
    ) as HTMLElement
    expect(within(action).getByText(row.description)).toBeInTheDocument()

    // The reason is never elided and never replaced with a fallback: the backend
    // makes an empty reason unconstructible, so a `?? ''` here would restore
    // exactly the bare imperative the schema exists to prevent.
    expect(why.textContent).toContain('180 minutes')
  })

  it('offers only the transitions legal from a new suggestion', () => {
    renderRiskUI(
      <RecommendationCard
        recommendation={suggestion()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onComplete={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Not for me' })).toBeInTheDocument()
    // "Mark completed" is reachable only after an acceptance, which is the
    // lifecycle the backend enforces; a rendered-but-forbidden button is a
    // control that fails on click.
    expect(screen.queryByRole('button', { name: 'Mark completed' })).toBeNull()
  })

  it('offers only completion once the suggestion has been accepted', () => {
    renderRiskUI(
      <RecommendationCard
        recommendation={suggestion({ status: 'accepted', responded_at: '2026-01-09T09:00:00Z' })}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onComplete={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Mark completed' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Not for me' })).toBeNull()
    expect(screen.getByText(/^Answered /)).toBeInTheDocument()
  })

  it('replaces the buttons with a factual sentence once a suggestion is closed', () => {
    renderRiskUI(
      <RecommendationCard
        recommendation={suggestion({ status: 'rejected', responded_at: '2026-01-09T09:00:00Z' })}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onComplete={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText(/^Declined\./)).toBeInTheDocument()
  })

  it('calls each supplied handler once, with the row it was rendered from', async () => {
    const user = userEvent.setup()
    const onAccept = vi.fn()
    const onReject = vi.fn()
    const row = suggestion()
    const { unmount } = renderRiskUI(
      <RecommendationCard recommendation={row} onAccept={onAccept} onReject={onReject} />,
    )

    await user.click(screen.getByRole('button', { name: 'Accept' }))
    expect(onAccept).toHaveBeenCalledOnce()
    expect(onAccept.mock.calls[0]?.[0]).toEqual(row)
    unmount()

    const onComplete = vi.fn()
    renderRiskUI(
      <RecommendationCard
        recommendation={suggestion({ status: 'accepted' })}
        onComplete={onComplete}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Mark completed' }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('renders no transition button that the caller did not wire up', () => {
    renderRiskUI(<RecommendationCard recommendation={suggestion()} onAccept={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Not for me' })).toBeNull()
  })

  it('links to the finding that raised it, and drops the link when there is none', () => {
    const { unmount } = renderRiskUI(
      <RecommendationCard
        recommendation={suggestion()}
        riskHref={(row) => (row.risk_id === null ? null : '/risks')}
      />,
    )
    expect(screen.getByRole('link', { name: /View the finding/ })).toHaveAttribute('href', '/risks')
    unmount()

    renderRiskUI(
      <RecommendationCard
        recommendation={suggestion({ risk_id: null })}
        riskHref={() => '/risks'}
      />,
    )
    expect(screen.queryByRole('link', { name: /View the finding/ })).toBeNull()
  })

  it('carries the priority as the same icon-and-word chip a risk band uses', () => {
    renderRiskUI(<RecommendationCard recommendation={suggestion()} />)

    // The backend derives priority from the severity of the risk behind it, so
    // showing them with different chrome would imply they could disagree.
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('New')).toBeInTheDocument()
    expect(screen.getByText('block time')).toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- skeletons */

describe('list skeletons', () => {
  it('announces the risk placeholder once and marks the region busy', () => {
    renderRiskUI(<RiskListSkeleton />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(status).toHaveTextContent('Loading detected risks')

    // One announcement for the whole region rather than one per row, and
    // nothing else in the region's text: the placeholders must not read as
    // counts, because a pulse in the shape of a number is a number.
    expect(status.textContent).toBe('Loading detected risks')
    // One visually hidden sentence plus three card silhouettes.
    expect(status.children).toHaveLength(4)
    for (const row of [...status.children].slice(1)) {
      expect(row).toHaveAttribute('aria-hidden', 'true')
    }
  })

  it('honours the requested number of risk placeholders, never fewer than one', () => {
    const { rerender } = renderRiskUI(<RiskListSkeleton count={5} />)
    expect(screen.getByRole('status').children).toHaveLength(6)

    rerender(
      <MemoryRouter>
        <TooltipProvider delayDuration={200}>
          <RiskListSkeleton count={0} />
        </TooltipProvider>
      </MemoryRouter>,
    )
    // A zero would collapse the list's vertical rhythm the moment the data
    // lands, so the silhouette never goes below one card.
    expect(screen.getByRole('status').children).toHaveLength(2)
  })

  it('announces the suggestion placeholder once and marks the region busy', () => {
    renderRiskUI(<RecommendationListSkeleton count={2} />)

    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(status.textContent).toBe('Loading suggestions')
    expect(status.children).toHaveLength(3)
  })
})

/* ----------------------------------------------------- the language contract */

describe('risk surface language', () => {
  /**
   * The brief's language rule, enforced on the components that carry the copy.
   *
   * Everything on this surface describes what the recorded data shows. It never
   * characterises the person, and it never manufactures urgency: a finding is a
   * measurement about tasks and hours, not a verdict about the reader. The four
   * words below are the ones the spec rules out by name, and this is the cheapest
   * guard against one creeping into a template string.
   */
  const FORBIDDEN = /failing|unproductive|lazy|burnout/i

  it('renders a risk and a suggestion without any of the four ruled-out words', () => {
    renderRiskUI(
      <>
        <RiskCard risk={risk({ status: 'dismissed', resolved_at: '2026-01-10T09:00:00Z' })} />
        <RecommendationCard
          recommendation={suggestion({ status: 'rejected', responded_at: '2026-01-09T09:00:00Z' })}
        />
      </>,
    )

    expect(document.body.textContent ?? '').not.toMatch(FORBIDDEN)
  })
})