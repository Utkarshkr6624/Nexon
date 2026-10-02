import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LoginPage from '@/pages/login-page'
import { useAuthStore } from '@/stores/auth-store'
import type { User } from '@/types/api'

/**
 * The sign-in form.
 *
 * Three promises are made to the person typing into it: it stays enabled while
 * it works rather than silently doing nothing, it says what is wrong before it
 * asks the server, and it never sends a round trip for input it already knows
 * is unusable. The store is module-scoped and persisted, so each test starts
 * from an explicitly anonymous session.
 */

const USER: User = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'ada@nexus.local',
  username: 'ada',
  display_name: 'Ada Lovelace',
  avatar_url: null,
  role: 'user',
  permissions: [],
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderLogin(initialEntry: Parameters<typeof MemoryRouter>[0]['initialEntries'] = ['/login']) {
  return render(
    <MemoryRouter initialEntries={initialEntry}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<p>Register destination</p>} />
        <Route path="/forgot-password" element={<p>Forgot password destination</p>} />
        <Route path="/dashboard" element={<p>Dashboard destination</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    user: null,
    status: 'anonymous',
    pending: false,
    error: null,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('LoginPage', () => {
  it('names the screen with the title the auth shell actually renders', () => {
    renderLogin()

    // The brand is the shell's, not the page heading's.
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /NEXUS/i })).not.toBeInTheDocument()
  })

  it('disables and announces the submit while the request is in flight', async () => {
    // A request that never settles is the honest way to hold the pending state.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Promise<Response>(() => undefined)),
    )
    const user = userEvent.setup()
    renderLogin()

    const submit = screen.getByRole('button', { name: 'Sign in' })
    expect(submit).toBeEnabled()

    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(submit)

    // The accessible name carries both the spinner's own label and the
    // button's, so it is matched on the visible wording rather than exactly.
    const busy = await screen.findByRole('button', { name: /Signing in/ })
    expect(busy).toBeDisabled()
    expect(busy).toHaveAttribute('aria-busy', 'true')
    // The fields lock too, so a half-edited credential cannot be submitted twice.
    expect(screen.getByLabelText('Email')).toBeDisabled()
    expect(screen.getByLabelText('Password')).toBeDisabled()
  })

  it('rejects an empty submit inline, without a round trip', async () => {
    const fetchMock = vi.fn(async () => json({}, 500))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderLogin()

    // Nothing is wrong with the form before it has been attempted.
    expect(screen.queryByText('Enter your email address.')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    // The first offending field takes focus, and no request was made.
    expect(document.activeElement?.id).toBe('email')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reports a malformed address and stops before the password round trip', async () => {
    const fetchMock = vi.fn(async () => json({}, 500))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Email'), 'ada@nexus')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/valid email address/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  it('points recovery at the route the router actually serves', () => {
    renderLogin()

    const link = screen.getByRole('link', { name: 'Forgot password?' })
    expect(link).toHaveAttribute('href', '/forgot-password')
    // And the account-creation link points the other way.
    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute(
      'href',
      '/register',
    )
  })

  it('navigates to the dashboard on a good sign-in', async () => {
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
            session_id: '22222222-2222-4222-8222-222222222222',
          })
        }
        if (url.includes('/auth/me')) return json(USER)
        return json({}, 404)
      }),
    )
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Dashboard destination')).toBeInTheDocument()
    await waitFor(() => expect(useAuthStore.getState().status).toBe('authenticated'))
    expect(useAuthStore.getState().accessToken).toBe('access-token')
  })
})