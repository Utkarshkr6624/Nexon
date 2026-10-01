import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { useAuthStore } from '@/stores/auth-store'
import { useThemeStore } from '@/stores/theme-store'

/**
 * End-to-end smoke test for the application shell: routing guards, sign-in,
 * the live health card, the ⌘K palette and the theme toggle, against a mocked
 * backend. It exercises the real router and the real stores.
 */

const USER = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@nexus.local',
  full_name: 'Ada Lovelace',
  is_active: true,
  is_verified: true,
  is_superuser: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const HEALTH = {
  status: 'healthy',
  app: 'NEXUS',
  version: '0.1.0',
  environment: 'development',
  database: { status: 'connected', latency_ms: 1.23 },
  uptime_seconds: 3725.5,
  timestamp: '2026-01-01T00:00:00Z',
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type DataRouter = ReturnType<typeof createBrowserRouter>

let router: DataRouter

beforeAll(async () => {
  window.history.replaceState({}, '', '/login')
  const module = await import('@/routes/router')
  router = module.router
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return json({
          access_token: 'access-token',
          refresh_token: 'refresh-token',
          token_type: 'bearer',
          expires_in: 3600,
        })
      }
      if (url.includes('/auth/me')) return json(USER)
      if (url.includes('/auth/logout')) return new Response(null, { status: 204 })
      if (url.includes('/health')) return json(HEALTH)
      return json(
        { error: { code: 'not_found', message: 'Not found', details: null, request_id: 'r1' } },
        404,
      )
    }),
  )
  useThemeStore.getState().setTheme('dark')
})

function renderApp() {
  return render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  )
}

describe('application shell', () => {
  it('redirects an anonymous visitor from a protected route to /login', async () => {
    window.history.replaceState({}, '', '/dashboard')
    await router.navigate('/dashboard')
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Sign in to NEXUS' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/login')
  })

  it('signs in against the real auth endpoints and lands on the dashboard', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { name: 'Sign in to NEXUS' })
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('heading', { name: /Good (morning|afternoon|evening), Ada/ })).toBeInTheDocument()
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().accessToken).toBe('access-token')
  })

  it('renders the shell, the sidebar groups and the live health card', async () => {
    renderApp()
    await screen.findByText(/Good (morning|afternoon|evening), Ada/)

    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Knowledge' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Experiments' })).toBeInTheDocument()
    expect(screen.getByText('Backend health')).toBeInTheDocument()

    // "healthy" is shown twice by design: once as the KPI, once as the status badge.
    await waitFor(() => expect(screen.getAllByText('healthy').length).toBeGreaterThan(0))
    expect(screen.getByText('0.1.0')).toBeInTheDocument()
    expect(screen.getByText('development')).toBeInTheDocument()
    expect(screen.getByText('connected')).toBeInTheDocument()
    expect(screen.getByText('1h 2m')).toBeInTheDocument()
  })

  it('opens the command palette with Ctrl+K and navigates to a module', async () => {
    const user = userEvent.setup()
    renderApp()
    await screen.findByText(/Good (morning|afternoon|evening), Ada/)

    await user.keyboard('{Control>}k{/Control}')

    const dialog = await screen.findByRole('dialog', { name: 'Command palette' })
    const filter = within(dialog).getByLabelText('Filter destinations')

    await user.type(filter, 'planner')
    await waitFor(() => {
      expect(within(dialog).getAllByRole('option')).toHaveLength(1)
    })

    await user.keyboard('{Enter}')
    expect(router.state.location.pathname).toBe('/planner')
    expect(await screen.findByText('Planned — Phase 3')).toBeInTheDocument()
  })

  it('applies the theme preference to the document element', async () => {
    const user = userEvent.setup()
    await router.navigate('/dashboard')
    renderApp()
    await screen.findByText(/Good (morning|afternoon|evening), Ada/)

    await user.click(screen.getByRole('button', { name: /Theme:/ }))
    await user.click(await screen.findByRole('menuitemradio', { name: 'Light' }))

    expect(useThemeStore.getState().resolvedTheme).toBe('light')
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))
  })

  it('degrades to a retryable error state when the backend is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/me')) return json(USER)
        throw new TypeError('Failed to fetch')
      }),
    )
    const { queryClient } = await import('@/app/query-client')
    queryClient.clear()

    await router.navigate('/dashboard')
    renderApp()

    // The shared query client retries transport failures twice before settling.
    expect(
      await screen.findByText('Cannot reach the NEXUS backend', {}, { timeout: 10_000 }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    // No stack traces, no raw transport internals.
    expect(screen.queryByText(/TypeError/)).not.toBeInTheDocument()
  })
})