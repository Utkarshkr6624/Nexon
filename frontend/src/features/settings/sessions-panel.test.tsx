import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SessionsPanel } from '@/features/settings/sessions-panel'
import { useAuthStore } from '@/stores/auth-store'
import type { AuthSession, User } from '@/types/api'

/**
 * The active-sessions panel, against a mocked backend.
 *
 * A revoked or expired row is a real row somebody may want to clear out, but it
 * is not a *device*: it must stay out of every count, and it must not give
 * "Sign out everywhere" something to do. Getting this wrong is not cosmetic —
 * "3 devices signed in" is a security answer, and one that overstates itself
 * invites a user to keep looking for a device they do not have.
 *
 * `expires_at` is a wire field, so the mock builds it relative to now rather
 * than from a fixed date: a fixture pinned to a literal would silently start
 * passing as "expired" on one side of the fixture's own lifetime and failing on
 * the other.
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

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS

const CHROME_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
const FIREFOX_MAC =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5) Gecko/20100101 Firefox/128.0'

function iso(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString()
}

function session(overrides: Partial<AuthSession> & Pick<AuthSession, 'id'>): AuthSession {
  return {
    user_agent: CHROME_WINDOWS,
    ip_address: '10.0.0.4',
    created_at: iso(-7 * DAY_MS),
    last_used_at: iso(-2 * HOUR_MS),
    expires_at: iso(DAY_MS),
    revoked_at: null,
    is_current: false,
    ...overrides,
  }
}

const CURRENT = session({
  id: '11111111-1111-4111-8111-111111111111',
  is_current: true,
})

/** A row the backend has not filtered out yet, but whose token is long gone. */
const EXPIRED = session({
  id: '22222222-2222-4222-8222-222222222222',
  user_agent: FIREFOX_MAC,
  ip_address: '10.0.0.9',
  last_used_at: iso(-9 * DAY_MS),
  expires_at: iso(-HOUR_MS),
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function respondWith(sessions: AuthSession[]): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        json({ sessions, current_id: CURRENT.id }),
    ),
  )
}

function renderPanel() {
  // A fresh client per render: the panel is a single query, and a shared one
  // would let a cached list from a previous test answer this one.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SessionsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  useAuthStore.setState({
    accessToken: 'access-token',
    refreshToken: 'refresh-token',
    user: USER,
    status: 'authenticated',
    pending: false,
    error: null,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SessionsPanel device count', () => {
  it('does not count an expired session as a signed-in device', async () => {
    respondWith([CURRENT, EXPIRED])
    renderPanel()

    expect(await screen.findByText('Firefox on macOS')).toBeInTheDocument()

    // One live device, and it is this one. Two live rows would read "2 devices
    // signed in · 1 other".
    expect(screen.getByText('1 device signed in — this one.')).toBeInTheDocument()
    expect(screen.queryByText(/2 devices signed in/)).not.toBeInTheDocument()
    expect(screen.queryByText(/1 other device signed in/)).not.toBeInTheDocument()

    // "Sign out everywhere" acts on the *other* devices; with none live there is
    // nothing for it to do, and offering it would sign this device out over a
    // session the user still holds.
    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toBeDisabled()
  })

  it('still lists an expired session, marked as expired', async () => {
    respondWith([CURRENT, EXPIRED])
    renderPanel()

    expect(await screen.findByText('Firefox on macOS')).toBeInTheDocument()
    // Kept in the list — it is still a row to clear out — but never presented
    // as something that can be used.
    expect(screen.getByText('Expired')).toBeInTheDocument()
    expect(screen.getByText(/^Expired /)).toBeInTheDocument()
    expect(screen.queryByText(/^Last active: 9 days ago$/)).not.toBeInTheDocument()

    // The live row keeps the reassuring wording.
    expect(screen.getByText('This device')).toBeInTheDocument()
    expect(screen.getByText(/^Last active: /)).toBeInTheDocument()
  })

  it('counts and offers the sign-out for a live session on another device', async () => {
    const other = session({ id: '33333333-3333-4333-8333-333333333333' })
    respondWith([CURRENT, other, EXPIRED])
    renderPanel()

    expect(await screen.findByText('2 devices signed in · 1 other')).toBeInTheDocument()
    // Two live devices: this one and one other — the expired row is not one.
    expect(screen.queryByText(/2 others/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out everywhere' })).toBeEnabled()
  })
})
