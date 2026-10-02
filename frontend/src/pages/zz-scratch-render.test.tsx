import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import AnalyticsPage from '@/pages/analytics-page'
import DashboardPage from '@/pages/dashboard-page'

const RANGE = { start_date: '2026-09-25', end_date: '2026-10-01', granularity: 'day' }

const DAILY = [
  {
    metric_date: '2026-09-28',
    tasks_created: 2,
    tasks_completed: 1,
    tasks_overdue: 0,
    tasks_cancelled: 0,
    tasks_blocked: 0,
    tasks_rescheduled: 0,
    planned_minutes: 120,
    actual_minutes: 95,
    work_sessions: 2,
    calendar_events: 1,
    knowledge_events: 0,
    projects_touched: 1,
    updated_at: '2026-10-01T09:00:00Z',
  },
  {
    metric_date: '2026-09-30',
    tasks_created: 1,
    tasks_completed: 3,
    tasks_overdue: 1,
    tasks_cancelled: 0,
    tasks_blocked: 0,
    tasks_rescheduled: 0,
    planned_minutes: 180,
    actual_minutes: 210,
    work_sessions: 3,
    calendar_events: 0,
    knowledge_events: 2,
    projects_touched: 1,
    updated_at: '2026-10-01T09:00:00Z',
  },
]

const PRODUCTIVITY = {
  score: 72,
  available: true,
  reason_if_unavailable: null,
  components: [
    { name: 'Completion', points: 24, max_points: 30, explanation: 'Four tasks finished.' },
    { name: 'Deadlines', points: 18, max_points: 25, explanation: 'Three on time, one late.' },
  ],
  formula: 'Completion x 0.30 + Deadlines x 0.25 + Focus x 0.25 + Estimation x 0.20.',
  label: 'NEXUS Productivity Score',
  disclaimer: 'A NEXUS-derived metric computed from your own recorded activity.',
  range: RANGE,
  weight_total: 100,
}

const DEADLINES = {
  available: true,
  reason_if_unavailable: null,
  on_time: 3,
  late: 1,
  still_overdue: 2,
  adherence_rate: 75,
  rate: 75,
  overdue_open: 2,
  total_considered: 4,
  range: RANGE,
  components: [],
}

const OVERVIEW = {
  range: RANGE,
  previous_range: { ...RANGE, start_date: '2026-09-18', end_date: '2026-09-24' },
  stale: false,
  is_stale: false,
  aggregates_through: '2026-10-01',
  data_as_of: '2026-10-01',
  totals: [
    { label: 'tasks_created', current: 3, previous: 1, absolute_change: 2, percent_change: 200 },
    { label: 'tasks_completed', current: 4, previous: 2, absolute_change: 2, percent_change: 100 },
    { label: 'actual_minutes', current: 305, previous: 240, absolute_change: 65, percent_change: 27.1 },
  ],
  productivity: PRODUCTIVITY,
  deadlines: DEADLINES,
  consistency: null,
  focus: null,
  estimation: null,
  workload: {
    open_tasks: 7,
    high_priority_open: 2,
    overdue_open: 2,
    scheduled_minutes: 300,
    available_minutes: 600,
    workload_ratio: 50,
    average_daily_scheduled_minutes: 42.9,
    high_priority_tasks: 2,
    overdue_tasks: 2,
    actual_minutes: 305,
    available: true,
    reason_if_unavailable: null,
    comparison: [],
    status_counts: { todo: 7 },
    priority_counts: { high: 2 },
    range: RANGE,
  },
  daily: DAILY,
  reason_if_empty: null,
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

const OVERVIEW_UNAVAILABLE = {
  ...OVERVIEW,
  productivity: {
    ...PRODUCTIVITY,
    score: null,
    available: false,
    reason_if_unavailable: 'Not enough activity yet: no task in this window was completed.',
    components: [],
  },
  totals: [],
  daily: [],
}

let unavailable = false

function stubFetch(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = unavailable ? OVERVIEW_UNAVAILABLE : OVERVIEW

      if (url.includes('/analytics/overview')) return json(body)
      if (url.includes('/analytics/productivity')) return json(unavailable ? { ...PRODUCTIVITY, score: null, available: false, reason_if_unavailable: PRODUCTIVITY.reason_if_unavailable } : PRODUCTIVITY)
      if (url.includes('/analytics/deadlines')) return json(DEADLINES)
      if (url.includes('/analytics/tasks'))
        return json({
          total_tasks: 9,
          completed_tasks: 4,
          open_tasks: 7,
          overdue_tasks: 2,
          cancelled_tasks: 0,
          blocked_tasks: 0,
          completion_rate: 44.4,
          overdue_rate: 22.2,
          avg_completion_days: 3.5,
          avg_cycle_minutes: 120,
          avg_estimate_error_minutes: null,
          tasks_created: 3,
          tasks_completed: 4,
          tasks_cancelled: 0,
          tasks_blocked: 0,
          tasks_overdue: 2,
          tasks_rescheduled: 0,
          available: true,
          reason_if_unavailable: null,
          estimation: null,
          top_overdue: [
            {
              task_id: 't1',
              title: 'Ship the migration',
              due_date: '2026-09-20',
              days_overdue: 11,
              priority: 'high',
            },
          ],
          by_status: { todo: 7, in_progress: 0, blocked: 0, completed: 4, cancelled: 0 },
          by_priority: { low: 1, medium: 4, high: 2, critical: 0 },
          range: RANGE,
        })
      if (url.includes('/analytics/time'))
        return json({
          total_minutes: 305,
          available: true,
          reason_if_unavailable: null,
          unassigned_minutes: 45,
          project_id: null,
          by_project: [
            { key: 'p1', label: 'Atlas rewrite', minutes: 200, share: 65.6 },
            { key: 'p2', label: 'Ledger', minutes: 60, share: 19.7 },
          ],
          by_task: [{ key: 't1', label: 'Ship the migration', minutes: 305, share: 100 }],
          slices: [],
          range: RANGE,
        })
      if (url.includes('/analytics/series')) return json(DAILY)
      if (url.includes('/analytics/projects'))
        return json([
          {
            project_id: 'p1',
            name: 'Atlas rewrite',
            status: 'active',
            total_tasks: 6,
            completed_tasks: 4,
            remaining_tasks: 2,
            overdue_tasks: 1,
            completion_rate: 66.7,
            total_work_minutes: 200,
            avg_task_actual_minutes: 50,
            estimation: null,
            velocity: { tasks_per_week: 2, estimated_minutes_per_week: 300, weeks_measured: 1, definition: 'Tasks completed per calendar week.' },
            velocity_tasks_per_week: 2,
            weekly_completed: [4],
            work_minutes: 200,
            estimated_minutes: 240,
            actual_minutes: 200,
            avg_task_minutes: 50,
            activity_events: 12,
            available: true,
            reason_if_unavailable: null,
            range: RANGE,
          },
        ])
      if (url.includes('/analytics/knowledge'))
        return json({
          available: true,
          reason_if_unavailable: null,
          notes_created: 3,
          notes_updated: 1,
          concepts_created: 2,
          resources_added: 4,
          bookmarks_added: 1,
          links_created: 6,
          notes_published: 0,
          documents_added: 0,
          interactions: 12,
          most_used_tags: [{ key: 't', label: 'migration', count: 4 }],
          most_active_concepts: [{ key: 'c', label: 'Event sourcing', count: 3 }],
          top_tags: [{ tag_id: 't', name: 'migration', note_count: 4 }],
          notes_by_status: { draft: 2, published: 1 },
          range: RANGE,
        })
      if (url.includes('/analytics/learning'))
        return json({
          available: true,
          reason_if_unavailable: null,
          study_events: 2,
          study_minutes: 90,
          knowledge_linked_tasks: null,
          knowledge_interactions: 5,
          notes_created: 3,
          notes_updated: 1,
          projects_touched: 1,
          basis: "Derived from calendar_events with event_type='study'.",
          definition: 'Nothing is inferred about mastery.',
          range: RANGE,
        })
      if (url.includes('/api/v1/tasks'))
        return json({
          items: [
            {
              id: 't1',
              project_id: 'p1',
              owner_id: 'o1',
              parent_id: null,
              title: 'Ship the migration',
              description: null,
              status: 'in_progress',
              priority: 'high',
              start_date: null,
              due_date: '2026-09-28',
              estimated_minutes: 60,
              actual_minutes: 0,
              completed_at: null,
              position: 0,
              created_at: '2026-09-01T00:00:00Z',
              updated_at: '2026-09-01T00:00:00Z',
              tag_ids: [],
              is_overdue: true,
              has_blocked_dependencies: false,
            },
          ],
          meta: { total: 1, limit: 8, offset: 0 },
        })
      if (url.includes('/api/v1/activity'))
        return json({
          items: [
            {
              id: 'a1',
              user_id: 'o1',
              project_id: 'p1',
              task_id: 't1',
              event_type: 'task_completed',
              metadata: {},
              created_at: new Date(Date.now() - 60_000).toISOString(),
            },
          ],
          meta: { total: 1, limit: 6, offset: 0 },
        })
      if (url.includes('/health'))
        return json({
          status: 'healthy',
          app: 'NEXUS',
          version: '0.1.0',
          environment: 'development',
          database: { status: 'connected', latency_ms: 1.23 },
          uptime_seconds: 3725.5,
          timestamp: '2026-10-01T09:00:00Z',
        })

      return json({ error: { code: 'not_found', message: 'Not found', details: null, request_id: 'r1' } }, 404)
    }),
  )
}

function renderPage(element: React.ReactElement, route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  unavailable = false
  stubFetch()
})

describe('phase 6 pages render from the API', () => {
  it('renders the overview tab from /analytics/overview', async () => {
    renderPage(<AnalyticsPage />, '/analytics')

    expect(await screen.findByRole('heading', { name: 'Analytics' })).toBeInTheDocument()
    // The score, its contributors and the formula come off the response.
    expect(await screen.findByText('72')).toBeInTheDocument()
    expect(screen.getByText(/Completion x 0.30/)).toBeInTheDocument()
    // A real comparison: arrow plus words, not colour alone.
    expect(screen.getByText(/up 2 tasks/)).toBeInTheDocument()
    // The 90% chunk of the recorded time, formatted from the API's minutes.
    expect(screen.getByText('5h 5m')).toBeInTheDocument()
  })

  it('renders the backend reason verbatim and never a zero', async () => {
    unavailable = true
    renderPage(<AnalyticsPage />, '/analytics')

    expect(
      await screen.findAllByText(/Not enough activity yet: no task in this window was completed/),
    ).not.toHaveLength(0)
  })

  it('fetches a tab only when that tab is open', async () => {
    const user = userEvent.setup()
    renderPage(<AnalyticsPage />, '/analytics?tab=overview')

    await screen.findByRole('heading', { name: 'Analytics' })
    const calls = () => (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]))
    expect(calls().some((url) => url.includes('/analytics/learning'))).toBe(false)

    await user.click(screen.getByRole('tab', { name: 'Deadlines' }))
    await waitFor(() => expect(calls().some((url) => url.includes('/analytics/deadlines'))).toBe(true))
    // The tab is in the URL, so the view is shareable.
    expect(screen.getByRole('tab', { name: 'Deadlines' })).toHaveAttribute('aria-selected', 'true')
  })

  it('renders the intelligence dashboard from the API', async () => {
    renderPage(<DashboardPage />, '/dashboard')

    expect(await screen.findByRole('heading', { name: /Good (morning|afternoon|evening)|Still up/ })).toBeInTheDocument()
    expect(await screen.findByText('72')).toBeInTheDocument()
    expect(screen.getByText('5h 5m')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('Ship the migration')).toBeInTheDocument()
    expect(screen.getByText('Atlas rewrite')).toBeInTheDocument()
    expect(screen.getByText('Backend health')).toBeInTheDocument()
    // The Phase 1 placeholder copy is gone.
    expect(screen.queryByText(/Phase 1 delivers/)).not.toBeInTheDocument()
    expect(screen.queryByText('Illustrative entries.')).not.toBeInTheDocument()
  })
})
