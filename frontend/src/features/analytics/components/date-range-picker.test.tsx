import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useMemo, useState } from 'react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import {
  DateRangePicker,
  type DateRangePickerProps,
} from '@/features/analytics/components/date-range-picker'
import {
  analyticsKeys,
  useAnalyticsWindow,
  useExportManifest,
  useOverview,
} from '@/features/analytics/hooks'
import {
  parseDateOnly,
  rangeDays,
  resolveWindow,
  type DateOnlyString,
  type WindowPresetId,
} from '@/types/analytics'

/**
 * The analytics window: the seven date-range filters the spec requires, the
 * arithmetic behind them, and the URL/query-key contract they drive.
 *
 * **Every date below is a hand-derived literal, not a `new Date()` compared with
 * itself.** The suite installs a frozen clock in `beforeEach` and anchors it on
 * Thursday 15 January 2026 at 12:00 local — mid-month, mid-week, midday, and
 * far enough from a DST boundary that nothing here can drift into one. A test
 * that computed its expectation with the same function it is exercising proves
 * only that the function is deterministic, not that the window is right.
 *
 * **The component is controlled and owns no dates.** `DateRangePicker` receives
 * a resolved window and reports a choice; the dates come from `resolveWindow`
 * via `useAnalyticsWindow`. Both halves are therefore covered here: the picker
 * as the control it is, and — through a harness wired exactly as
 * `dashboard-page.tsx` wires it — the window it drives all the way to the
 * React Query key and the outgoing request.
 */

const ANCHOR: DateOnlyString = '2026-01-15'

/** Local-noon on a date-only string, so no assertion can fall near midnight. */
function atNoon(value: DateOnlyString): Date {
  const parsed = parseDateOnly(value)
  if (parsed === null) throw new Error(`not a date-only string: ${value}`)
  parsed.setHours(12, 0, 0, 0)
  return parsed
}

/**
 * `formatRangeLabel` is deliberately locale-formatted, so the expectation is
 * built with the same `Intl` shapes and pins the *rule* — both ends abbreviated,
 * the year added to the end only when the window crosses one — rather than
 * hard-coding `Jan` in a locale the machine running the suite may not use.
 */
function expectedRangeLabel(start: DateOnlyString, end: DateOnlyString): string {
  const from = parseDateOnly(start)
  const to = parseDateOnly(end)
  if (from === null || to === null) throw new Error('expected a resolvable window')
  const short = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
  const full = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  const sameYear = from.getFullYear() === to.getFullYear()
  return `${short.format(from)} – ${sameYear ? short.format(to) : full.format(to)}`
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Every request the app made during the test, in order. */
let requests: string[] = []

beforeEach(() => {
  // Only `Date` is faked. Faking the timer functions as well would freeze the
  // clock past `waitFor`'s own polling and stall every async assertion.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(atNoon(ANCHOR))

  requests = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      requests.push(url)
      if (url.includes('/analytics/overview')) return json({ totals: [], daily: [] })
      if (url.includes('/analytics/export')) {
        return json({ datasets: [], columns: {}, content_type: 'text/csv', note: '' })
      }
      return json(
        { error: { code: 'not_found', message: 'Not found', details: null, request_id: 'r1' } },
        404,
      )
    }),
  )
})

afterEach(() => {
  vi.useRealTimers()
})

/* ------------------------------------------------- the picker, on its own */

function renderPicker(overrides: Partial<DateRangePickerProps> = {}) {
  const props: DateRangePickerProps = {
    preset: '7d',
    start: '2026-01-09',
    end: '2026-01-15',
    onPresetChange: vi.fn(),
    onCustomChange: vi.fn(),
    ...overrides,
  }
  const utils = render(
    <AppProviders>
      <DateRangePicker {...props} />
    </AppProviders>,
  )
  return { ...utils, props }
}

function presetButton(label: string): HTMLElement {
  return screen.getByRole('button', { name: label })
}

const PRESET_LABELS = [
  'Today',
  '7 days',
  '30 days',
  '90 days',
  'This month',
  'Last month',
  'Custom',
] as const

describe('DateRangePicker presets', () => {
  it('offers exactly the seven windows the spec requires, in order', () => {
    renderPicker()

    const group = screen.getByRole('group', { name: 'Date range' })
    expect(group).toBeInTheDocument()
    expect(within(group).getAllByRole('button').map((button) => button.textContent)).toEqual([
      ...PRESET_LABELS,
    ])
  })

  it('marks the chosen preset pressed and every other one not', () => {
    // The control is a row of toggle buttons, so the state lives on
    // `aria-pressed` rather than `aria-selected` — there is no listbox or
    // radiogroup role to select within.
    renderPicker({ preset: '30d' })

    for (const label of PRESET_LABELS) {
      expect(presetButton(label)).toHaveAttribute(
        'aria-pressed',
        label === '30 days' ? 'true' : 'false',
      )
    }
  })

  it.each(PRESET_LABELS)('reports %s to the parent and nothing else', async (label) => {
    const user = userEvent.setup()
    const { props } = renderPicker({ preset: '7d' })

    await user.click(presetButton(label))

    const expected: Record<(typeof PRESET_LABELS)[number], WindowPresetId> = {
      Today: 'today',
      '7 days': '7d',
      '30 days': '30d',
      '90 days': '90d',
      'This month': 'this_month',
      'Last month': 'last_month',
      Custom: 'custom',
    }
    expect(props.onPresetChange).toHaveBeenCalledTimes(1)
    expect(props.onPresetChange).toHaveBeenCalledWith(expected[label])
    // A preset needs no editing, so it applies immediately — a custom range is
    // the only thing that waits for an explicit Apply.
    expect(props.onCustomChange).not.toHaveBeenCalled()
  })

  it('shows the window it was handed, in words, while the custom fields are closed', () => {
    renderPicker({ start: '2026-01-01', end: '2026-03-31' })
    expect(screen.getByText(expectedRangeLabel('2026-01-01', '2026-03-31'))).toBeInTheDocument()
  })

  it('opens the custom fields when Custom is chosen, without applying one', async () => {
    const user = userEvent.setup()
    const { props } = renderPicker({ preset: '7d' })

    expect(screen.queryByLabelText('From')).not.toBeInTheDocument()
    await user.click(presetButton('Custom'))

    expect(props.onPresetChange).toHaveBeenCalledWith('custom')
    expect(screen.getByLabelText('From')).toBeInTheDocument()
    expect(screen.getByLabelText('To')).toBeInTheDocument()
    // Still nothing applied: the caption says so in as many words.
    expect(screen.getByText('Pick the two ends, then apply.')).toBeInTheDocument()
    expect(props.onCustomChange).not.toHaveBeenCalled()
  })

  it('seeds the custom fields with the window currently on screen', () => {
    renderPicker({ preset: 'custom', start: '2025-11-03', end: '2025-11-30' })

    expect(screen.getByLabelText<HTMLInputElement>('From')).toHaveValue('2025-11-03')
    expect(screen.getByLabelText<HTMLInputElement>('To')).toHaveValue('2025-11-30')
    // Applied, so the caption is the resolved window rather than an instruction.
    expect(screen.getByText(expectedRangeLabel('2025-11-03', '2025-11-30'))).toBeInTheDocument()
  })

  it('keeps an unapplied custom window out of the parent until Apply', async () => {
    const user = userEvent.setup()
    const { props } = renderPicker({ preset: 'custom', start: '2025-11-03', end: '2025-11-30' })

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2025-11-10' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2025-11-20' } })
    expect(props.onCustomChange).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Apply range' }))
    expect(props.onCustomChange).toHaveBeenCalledTimes(1)
    expect(props.onCustomChange).toHaveBeenCalledWith('2025-11-10', '2025-11-20')
  })

  it('refuses a custom range whose start is after its end', async () => {
    const user = userEvent.setup()
    const { props } = renderPicker({ preset: 'custom', start: '2025-11-03', end: '2025-11-30' })

    // The two inputs also bound each other, so the browser's own date picker
    // cannot offer a day on the wrong side in the first place.
    expect(screen.getByLabelText('From')).toHaveAttribute('max', '2025-11-30')
    expect(screen.getByLabelText('To')).toHaveAttribute('min', '2025-11-03')

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2025-11-20' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2025-11-10' } })

    const apply = screen.getByRole('button', { name: 'Apply range' })
    expect(apply).toBeDisabled()
    await user.click(apply)
    expect(props.onCustomChange).not.toHaveBeenCalled()
  })

  it('will not apply a half-filled custom range', () => {
    renderPicker({ preset: 'custom', start: '2025-11-03', end: '2025-11-30' })

    fireEvent.change(screen.getByLabelText('To'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'Apply range' })).toBeDisabled()
  })

  it('omits the bucket switch unless the caller asked for one', () => {
    const { unmount } = renderPicker({ granularity: 'day' })
    expect(screen.queryByLabelText('Bucket')).not.toBeInTheDocument()
    unmount()

    renderPicker({
      granularity: 'day',
      onGranularityChange: vi.fn(),
    } satisfies Partial<DateRangePickerProps>)
    expect(screen.getByLabelText('Bucket')).toBeInTheDocument()
  })

  it('reports a new bucket through the caller', async () => {
    const user = userEvent.setup()
    const onGranularityChange = vi.fn()
    renderPicker({ granularity: 'day', onGranularityChange })

    await user.selectOptions(screen.getByLabelText('Bucket'), 'week')
    expect(onGranularityChange).toHaveBeenCalledWith('week')
  })
})

/* --------------------------------------------- the arithmetic behind them */

/**
 * Hand-derived against 2026-01-15. Each row states the window and the inclusive
 * day count the spec's "N days" label promises, so a window that is one day
 * short at either end fails here rather than looking plausible in a dashboard.
 */
const SPAN_CASES: ReadonlyArray<{
  preset: WindowPresetId
  label: string
  start: DateOnlyString
  end: DateOnlyString
  days: number
}> = [
  // 15 Jan.
  { preset: 'today', label: 'Today', start: '2026-01-15', end: '2026-01-15', days: 1 },
  // 15 back to 9 Jan inclusive.
  { preset: '7d', label: '7 days', start: '2026-01-09', end: '2026-01-15', days: 7 },
  // 15 Dec 2025 + 17 Jan 2026 = 30 days inclusive.
  { preset: '30d', label: '30 days', start: '2025-12-17', end: '2026-01-15', days: 30 },
  // 18 Oct 2025 + 29 days of Nov + 31 of Dec + 15 of Jan = 90.
  { preset: '90d', label: '90 days', start: '2025-10-18', end: '2026-01-15', days: 90 },
  { preset: 'this_month', label: 'This month', start: '2026-01-01', end: '2026-01-15', days: 15 },
  // A closed previous calendar month, not "the 30 days before today".
  {
    preset: 'last_month',
    label: 'Last month',
    start: '2025-12-01',
    end: '2025-12-31',
    days: 31,
  },
]

describe('date-window resolution', () => {
  it.each(
    SPAN_CASES.map((entry) => [entry.label, entry.start, entry.end, entry.days, entry.preset] as const),
  )('%s resolves to %s..%s (%s days inclusive)', (_label, start, end, days, preset) => {
    const resolved = resolveWindow(preset, ANCHOR)
    expect(resolved).toEqual({ start_date: start, end_date: end })
    expect(rangeDays(resolved.start_date, resolved.end_date)).toBe(days)
  })

  it('resolves the same windows when the anchor is left implicit', () => {
    // This is the call `useAnalyticsWindow` actually makes — `new Date()` and
    // nothing else — so the frozen clock has to produce the same answers as the
    // explicit anchor does.
    for (const entry of SPAN_CASES) {
      expect(resolveWindow(entry.preset)).toEqual({
        start_date: entry.start,
        end_date: entry.end,
      })
    }
  })

  it.each([
    // Anchor, this month, last month. The first is mid-month, the second is the
    // first of a month, the third is the last day of one: a month-boundary
    // assumption shows up as a different answer in one of the three.
    {
      anchor: '2026-01-15',
      thisMonth: '2026-01-01..2026-01-15',
      lastMonth: '2025-12-01..2025-12-31',
    },
    { anchor: '2026-03-01', thisMonth: '2026-03-01..2026-03-01', lastMonth: '2026-02-01..2026-02-28' },
    { anchor: '2026-12-31', thisMonth: '2026-12-01..2026-12-31', lastMonth: '2026-11-01..2026-11-30' },
    // February 2024 has 29 days; a month length hard-coded to 28 would report a
    // 29th that is not there and drop the real one.
    { anchor: '2024-03-01', thisMonth: '2024-03-01..2024-03-01', lastMonth: '2024-02-01..2024-02-29' },
  ])(
    'anchors the calendar months on $anchor',
    ({ anchor, thisMonth, lastMonth }) => {
      expect(range(resolveWindow('this_month', anchor))).toBe(thisMonth)
      expect(range(resolveWindow('last_month', anchor))).toBe(lastMonth)
    },
  )

  it.each([
    // 8 March 2026 is the US spring-forward; 1 November 2026 the fall-back. Both
    // are a day shorter or longer in milliseconds, and the window is bucketed
    // by the local calendar, so neither may shorten a seven-day window.
    { anchor: '2026-03-08', start: '2026-03-02', end: '2026-03-08' },
    { anchor: '2026-11-01', start: '2026-10-26', end: '2026-11-01' },
  ])('keeps a seven-day window whole across the DST change at $anchor', ({ anchor, start, end }) => {
    const resolved = resolveWindow('7d', anchor)
    expect(resolved).toEqual({ start_date: start, end_date: end })
    expect(rangeDays(resolved.start_date, resolved.end_date)).toBe(7)
  })

  it('defaults a custom window with no ends to the last seven days', () => {
    expect(resolveWindow('custom', ANCHOR, {})).toEqual({
      start_date: '2026-01-09',
      end_date: '2026-01-15',
    })
  })

  it('defaults a missing custom end to today', () => {
    expect(resolveWindow('custom', ANCHOR, { start_date: '2025-06-01' })).toEqual({
      start_date: '2025-06-01',
      end_date: '2026-01-15',
    })
  })

  it('sorts a custom window whose defaulted start lands after its end', () => {
    // Only an end was given, so the start falls back to six days back — later
    // than that end. The clamp is what keeps this from becoming a 422.
    expect(resolveWindow('custom', ANCHOR, { end_date: '2025-06-30' })).toEqual({
      start_date: '2025-06-30',
      end_date: '2026-01-09',
    })
  })

  it('clamps an inverted custom window instead of passing it on as a 422', () => {
    // The backend answers an inverted window with 422; an empty result would
    // read exactly like "you did nothing", so the client sorts the ends itself.
    expect(
      resolveWindow('custom', ANCHOR, { start_date: '2026-01-20', end_date: '2026-01-10' }),
    ).toEqual({ start_date: '2026-01-10', end_date: '2026-01-20' })
  })

  it('ignores custom ends that are not real dates', () => {
    // `2026-02-31` is the interesting one: `new Date` would roll it over to
    // 2 March and quietly report a window nobody asked for.
    for (const bad of ['2026-02-31', 'yesterday', '', '2026-1-5', '2026-13-01']) {
      expect(resolveWindow('custom', ANCHOR, { start_date: bad, end_date: bad })).toEqual({
        start_date: '2026-01-09',
        end_date: '2026-01-15',
      })
    }
  })
})

function range(resolved: { start_date: DateOnlyString; end_date: DateOnlyString }): string {
  return `${resolved.start_date}..${resolved.end_date}`
}

/* --------------------------------------------------------- keyboard access */

describe('DateRangePicker keyboard operation', () => {
  it('reaches every preset by Tab and activates it with Enter or Space', async () => {
    const user = userEvent.setup()
    const { props } = renderPicker({ preset: '7d' })

    // The group is the first thing on the page, so the first Tab lands on the
    // first preset and the next six walk the row in the order it is rendered.
    await user.tab()
    for (const label of PRESET_LABELS) {
      expect(presetButton(label)).toHaveFocus()
      await user.tab()
    }

    presetButton('90 days').focus()
    await user.keyboard('{Enter}')
    expect(props.onPresetChange).toHaveBeenNthCalledWith(1, '90d')

    presetButton('30 days').focus()
    await user.keyboard('[Space]')
    expect(props.onPresetChange).toHaveBeenNthCalledWith(2, '30d')
    expect(props.onPresetChange).toHaveBeenCalledTimes(2)
  })

  it('walks from the presets into the custom fields and out to Apply', async () => {
    const user = userEvent.setup()
    renderPicker({ preset: 'custom', start: '2025-11-03', end: '2025-11-30' })

    await user.tab()
    for (const label of PRESET_LABELS) {
      expect(presetButton(label)).toHaveFocus()
      await user.tab()
    }

    // The custom fields follow the preset row, in reading order, and the Apply
    // button is the last stop — so the whole control is usable without a mouse.
    expect(screen.getByLabelText('From')).toHaveFocus()
    await user.tab()
    expect(screen.getByLabelText('To')).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Apply range' })).toHaveFocus()
  })
})

/* ------------------------------- the window in the URL and the query key */

/**
 * The production wiring, reduced to what the picker drives: `useAnalyticsWindow`
 * resolves the URL into a window, the window becomes `AnalyticsListParams`, and
 * the params become the React Query key — exactly as `dashboard-page.tsx` and
 * `analytics-page.tsx` build it. A dedicated `QueryClient` is used here rather
 * than the app's shared one so the cache can be inspected for exactly the keys
 * this harness created.
 */
function WindowHarness() {
  const window = useAnalyticsWindow()
  const [note, setNote] = useState('')

  const params = useMemo(
    () => ({
      start_date: window.range.start_date,
      end_date: window.range.end_date,
      granularity: window.granularity,
    }),
    [window.range.start_date, window.range.end_date, window.granularity],
  )

  useOverview(params)
  useExportManifest()

  return (
    <div>
      <span data-testid="preset">{window.preset}</span>
      <span data-testid="range">{range(params)}</span>
      <span data-testid="key">{JSON.stringify(analyticsKeys.overview(params))}</span>
      <span data-testid="granularity">{window.granularity}</span>
      <input
        aria-label="Unrelated note"
        data-testid="note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      <DateRangePicker
        preset={window.preset}
        start={window.range.start_date}
        end={window.range.end_date}
        granularity={window.granularity}
        onPresetChange={window.setPreset}
        onCustomChange={window.setCustom}
        onGranularityChange={window.setGranularity}
      />
    </div>
  )
}

function renderWindow(initial: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
    },
  })
  const router = createMemoryRouter([{ path: '/', element: <WindowHarness /> }], {
    initialEntries: [initial],
  })
  const utils = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...utils, router, client }
}

function requestsTo(path: string): string[] {
  return requests.filter((url) => url.includes(path))
}

const OVERVIEW_CASES: ReadonlyArray<{
  button: string
  search: string
  start: DateOnlyString
  end: DateOnlyString
}> = [
  { button: 'Today', search: '?range=today', start: '2026-01-15', end: '2026-01-15' },
  // The default preset is not written to the URL, so `?` stays clean.
  { button: '7 days', search: '', start: '2026-01-09', end: '2026-01-15' },
  { button: '30 days', search: '?range=30d', start: '2025-12-17', end: '2026-01-15' },
  { button: '90 days', search: '?range=90d', start: '2025-10-18', end: '2026-01-15' },
  { button: 'This month', search: '?range=this_month', start: '2026-01-01', end: '2026-01-15' },
  { button: 'Last month', search: '?range=last_month', start: '2025-12-01', end: '2025-12-31' },
  // Custom with no ends in the URL resolves to the same seven days, and is not
  // mistaken for a window the user actually typed.
  { button: 'Custom', search: '?range=custom', start: '2026-01-09', end: '2026-01-15' },
]

describe('the window drives the URL and the query key', () => {
  it.each(
    OVERVIEW_CASES.map(
      (entry) => [entry.button, entry.start, entry.end, entry.search] as const,
    ),
  )('%s resolves the key and the URL', async (button, start, end, search) => {
    const user = userEvent.setup()
    const { router } = renderWindow('/')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    await user.click(presetButton(button))

    expect(screen.getByTestId('range')).toHaveTextContent(`${start}..${end}`)
    expect(screen.getByTestId('key')).toHaveTextContent(
      JSON.stringify(['analytics', 'overview', start, end, 'day', null]),
    )
    await waitFor(() => expect(router.state.location.search).toBe(search))
  })

  it('re-runs the windowed read with the new dates and leaves the rest of the app alone', async () => {
    const user = userEvent.setup()
    const { router, client } = renderWindow('/')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))
    await waitFor(() => expect(requestsTo('/analytics/export')).toHaveLength(1))

    // An unrelated part of the page, in the same tree, mid-edit.
    await user.type(screen.getByTestId('note'), 'half a thought')

    await user.click(presetButton('30 days'))
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(2))

    expect(requestsTo('/analytics/overview')[1]).toBe(
      '/api/v1/analytics/overview?start_date=2025-12-17&end_date=2026-01-15&granularity=day',
    )
    // The export manifest's key carries no window, so paging the range neither
    // invalidates nor re-requests it.
    expect(requestsTo('/analytics/export')).toHaveLength(1)
    // The other window stays cached under its own key rather than being
    // overwritten, so returning to it costs no request at all.
    const overviewKeys = client
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey)
      .filter((key) => key[1] === 'overview')
    expect(overviewKeys).toEqual([
      ['analytics', 'overview', '2026-01-09', '2026-01-15', 'day', null],
      ['analytics', 'overview', '2025-12-17', '2026-01-15', 'day', null],
    ])

    expect(screen.getByTestId('note')).toHaveValue('half a thought')
    expect(router.state.location.search).toBe('?range=30d')
  })

  it('serves a window already fetched from cache instead of asking again', async () => {
    const user = userEvent.setup()
    renderWindow('/')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    await user.click(presetButton('30 days'))
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(2))

    await user.click(presetButton('7 days'))
    expect(screen.getByTestId('range')).toHaveTextContent('2026-01-09..2026-01-15')
    expect(requestsTo('/analytics/overview')).toHaveLength(2)
  })

  it('leaves the page’s own search params alone when the window changes', async () => {
    const user = userEvent.setup()
    const { router } = renderWindow('/?tab=projects')

    await user.click(presetButton('This month'))
    await waitFor(() => expect(router.state.location.search).toBe('?tab=projects&range=this_month'))
  })

  it('puts the bucket in the key and the request, not in the window', async () => {
    const user = userEvent.setup()
    renderWindow('/')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    await user.selectOptions(screen.getByLabelText('Bucket'), 'week')

    expect(screen.getByTestId('granularity')).toHaveTextContent('week')
    expect(screen.getByTestId('range')).toHaveTextContent('2026-01-09..2026-01-15')
    expect(screen.getByTestId('key')).toHaveTextContent(
      JSON.stringify(['analytics', 'overview', '2026-01-09', '2026-01-15', 'week', null]),
    )
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(2))
    expect(requestsTo('/analytics/overview')[1]).toBe(
      '/api/v1/analytics/overview?start_date=2026-01-09&end_date=2026-01-15&granularity=week',
    )
  })

  it('writes a custom window to the URL and reads it back on the next render', async () => {
    const user = userEvent.setup()
    const { router } = renderWindow('/')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    await user.click(presetButton('Custom'))
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2025-09-01' } })
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2025-09-30' } })
    await user.click(screen.getByRole('button', { name: 'Apply range' }))

    await waitFor(() => expect(router.state.location.search).toBe(
      '?range=custom&start=2025-09-01&end=2025-09-30',
    ))
    expect(screen.getByTestId('range')).toHaveTextContent('2025-09-01..2025-09-30')
    expect(screen.getByText(expectedRangeLabel('2025-09-01', '2025-09-30'))).toBeInTheDocument()
  })

  it('honours a custom window handed over in the URL', async () => {
    const { router } = renderWindow('/?range=custom&start=2025-04-07&end=2025-04-18')

    expect(screen.getByTestId('preset')).toHaveTextContent('custom')
    expect(screen.getByTestId('range')).toHaveTextContent('2025-04-07..2025-04-18')
    expect(screen.getByLabelText<HTMLInputElement>('From')).toHaveValue('2025-04-07')
    expect(router.state.location.search).toBe('?range=custom&start=2025-04-07&end=2025-04-18')
  })

  it('sends a clamped window when the URL carries an inverted custom range', async () => {
    // A hand-edited or stale link must not put the client in the position of
    // rendering a screen that can only ever be a 422.
    renderWindow('/?range=custom&start=2026-01-20&end=2026-01-10')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    expect(screen.getByTestId('range')).toHaveTextContent('2026-01-10..2026-01-20')
    expect(requestsTo('/analytics/overview')[0]).toBe(
      '/api/v1/analytics/overview?start_date=2026-01-10&end_date=2026-01-20&granularity=day',
    )
  })

  it('falls back to the seven-day default for an unknown or unusable window', async () => {
    renderWindow('/?range=fortnight&start=nonsense&end=2026-99-99')
    await waitFor(() => expect(requestsTo('/analytics/overview')).toHaveLength(1))

    expect(screen.getByTestId('preset')).toHaveTextContent('7d')
    expect(screen.getByTestId('range')).toHaveTextContent('2026-01-09..2026-01-15')
    expect(requestsTo('/analytics/overview')[0]).toBe(
      '/api/v1/analytics/overview?start_date=2026-01-09&end_date=2026-01-15&granularity=day',
    )
  })
})
