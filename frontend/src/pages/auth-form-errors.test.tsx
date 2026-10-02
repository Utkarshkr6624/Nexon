import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LoginPage from '@/pages/login-page'
import RegisterPage from '@/pages/register-page'
import { useToastStore } from '@/stores/toast-store'

afterEach(() => {
  vi.unstubAllGlobals()
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function envelope(code: string, message: string) {
  return { error: { code, message, details: null, request_id: 'req-1' } }
}

const USER = {
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

describe('tmp auth pages', () => {
  it('login validates and shows the password toggle', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<p>register here</p>} />
          <Route path="/forgot-password" element={<p>forgot here</p>} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter your email address.')).toBeTruthy()
    expect(document.activeElement?.id).toBe('email')

    const toggle = screen.getByRole('button', { name: 'Show password' })
    expect(toggle.getAttribute('aria-pressed')).toBe('false')
    await user.click(toggle)
    expect(screen.getByLabelText('Show password').getAttribute('aria-pressed')).toBe('true')
    expect((document.getElementById('password') as HTMLInputElement).type).toBe('text')

    await user.type(screen.getByLabelText('Email'), 'nope')
    expect(await screen.findByText(/valid email address/)).toBeTruthy()

    await user.click(screen.getByRole('link', { name: 'Forgot password?' }))
    expect(await screen.findByText('forgot here')).toBeTruthy()
  })

  it('register shows meter, checklist and blocks on mismatch', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/register']}>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const password = screen.getByLabelText('Password')
    await user.type(password, 'abc')
    expect(await screen.findByText('Weak')).toBeTruthy()
    expect(screen.getAllByRole('listitem').length).toBe(5)

    const submit = screen.getByRole('button', { name: 'Create account' }) as HTMLButtonElement
    expect(submit.disabled).toBe(false)

    // A mismatch the user has not yet been told about must not disable the
    // button: a disabled control cannot be focused, so the explanation would
    // be unreachable.
    const confirm = screen.getByLabelText('Confirm password')
    await user.type(confirm, 'abcd')
    expect(screen.queryByText('Passwords do not match.')).toBeNull()
    expect(submit.disabled).toBe(false)

    await user.click(confirm)
    await user.tab()
    expect(await screen.findByText('Passwords do not match.')).toBeTruthy()
    expect(submit.disabled).toBe(true)

    await user.clear(confirm)
    await user.type(confirm, 'abc')
    expect(submit.disabled).toBe(false)

    await user.click(submit)
    expect(await screen.findByText('Choose a username.')).toBeTruthy()
    expect(document.activeElement?.id).toBe('username')

    await user.type(screen.getByLabelText('Username'), '_ada')
    expect(await screen.findByText('Start with a letter or a number.')).toBeTruthy()

    await user.clear(screen.getByLabelText('Username'))
    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.clear(screen.getByLabelText('Password'))
    await user.type(screen.getByLabelText('Password'), 'Str0ng!pass')
    expect(await screen.findByText('Good')).toBeTruthy()
    await user.clear(screen.getByLabelText('Password'))
    await user.type(screen.getByLabelText('Password'), 'Str0ng!passphrase!')
    expect(await screen.findByText('Strong')).toBeTruthy()
  })
})

describe('tmp auth network paths', () => {
  it('maps a 409 to a field error, not a banner', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json(envelope('conflict', 'An account with this email already exists.'), 409),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/register']}>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Str0ng!pass')
    await user.type(screen.getByLabelText('Confirm password'), 'Str0ng!pass')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('An account with this email already exists.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByLabelText('Email').getAttribute('aria-invalid')).toBe('true')
  })

  it('maps a 409 username conflict to the username field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(envelope('conflict', 'That username is already taken.'), 409)),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/register']}>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Str0ng!pass')
    await user.type(screen.getByLabelText('Confirm password'), 'Str0ng!pass')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('That username is already taken.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('toasts and navigates to the intended destination on a good login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/login')) {
          return json({ access_token: 'a', refresh_token: 'r', token_type: 'bearer' })
        }
        if (url.includes('/auth/me')) return json(USER)
        return json({}, 404)
      }),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/projects' } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/projects" element={<h1>Projects destination</h1>} />
        </Routes>
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Str0ng!pass')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Projects destination')).toBeTruthy()
    const pushed = useToastStore.getState().toasts
    expect(pushed.map((t) => t.title)).toContain('Signed in')
    expect(pushed[0]?.description).toContain('Ada Lovelace')
  })

  it('shows a human 401 on the login form', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(envelope('unauthorized', 'Incorrect email or password'), 401)),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Incorrect email or password')
  })
})
