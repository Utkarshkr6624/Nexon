import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import RegisterPage from '@/pages/register-page'
import { useAuthStore } from '@/stores/auth-store'

/**
 * The registration form.
 *
 * The password fields carry the live checklist and the strength meter, so a
 * candidate can see what is still missing without a round trip. The
 * confirmation field is the one place the form settles a question entirely on
 * its own, so its mismatch is reported as soon as the field is left — and only
 * then is the submit blocked, because a disabled control cannot be focused and
 * would strand a keyboard user in front of a button that will not say why.
 */

function renderRegister() {
  return render(
    <MemoryRouter initialEntries={['/register']}>
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<p>Login destination</p>} />
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

describe('RegisterPage', () => {
  it('shows the live policy checklist under the password field', async () => {
    const user = userEvent.setup()
    renderRegister()

    // The checklist is on screen from the start, all rows unmet: unlike the
    // strength meter, "no rules met" is a true reading of an empty field and
    // telling someone what is coming is the point of the list.
    const initial = screen.getAllByRole('listitem')
    expect(initial).toHaveLength(5)
    for (const row of initial) {
      expect(row).toHaveTextContent('not met yet')
    }

    await user.type(screen.getByLabelText('Password'), 'Passw0rd!')

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(5)
    // Each row states the requirement the user has to satisfy, which is the
    // only form of this list that is actionable while typing.
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('At least 8 characters — met'),
      expect.stringContaining('An uppercase letter (e.g. A, Z) — met'),
      expect.stringContaining('A lowercase letter (e.g. a, z) — met'),
      expect.stringContaining('A number (e.g. 0, 9) — met'),
      expect.stringContaining('A symbol or space (e.g. ! @ #) — met'),
    ])
  })

  it('names the strength level in text and updates it as the value changes', async () => {
    const user = userEvent.setup()
    renderRegister()

    const password = screen.getByLabelText('Password')

    await user.type(password, 'abc')
    expect(await screen.findByText('Weak')).toBeInTheDocument()

    await user.clear(password)
    await user.type(password, 'Passw0rd!')
    expect(await screen.findByText('Good')).toBeInTheDocument()

    await user.clear(password)
    await user.type(password, 'Str0ng!passphrase!')
    expect(await screen.findByText('Strong')).toBeInTheDocument()
  })

  it('does not render a second checklist or a meter under the confirmation field', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Confirm password'), 'Passw0rd!')

    // The primary password is still empty, so there is exactly one checklist —
    // the confirmation's copy of the same five rules — and no meter at all.
    // Two identical lists, and a meter reading "Good" about a field the user
    // never filled in, would say the same thing twice and mean it of the wrong
    // field.
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText('Good')).not.toBeInTheDocument()
  })

  it('reports a confirmation mismatch only once the field has been left', async () => {
    const user = userEvent.setup()
    renderRegister()

    const password = screen.getByLabelText('Password')
    const confirm = screen.getByLabelText('Confirm password')
    const submit = screen.getByRole('button', { name: 'Create account' })

    await user.type(password, 'Passw0rd!')
    await user.type(confirm, 'Passw0rd?')

    // Not yet: a mismatch nobody has been told about cannot justify blocking
    // the button, because a disabled control cannot be focused.
    expect(screen.queryByText('Passwords do not match.')).not.toBeInTheDocument()
    expect(submit).toBeEnabled()

    await user.tab()
    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    expect(confirm).toHaveAttribute('aria-invalid', 'true')
    expect(submit).toBeDisabled()

    // The moment the values agree again, the explanation and the block go.
    await user.clear(confirm)
    await user.type(confirm, 'Passw0rd!')
    await waitFor(() =>
      expect(screen.queryByText('Passwords do not match.')).not.toBeInTheDocument(),
    )
    expect(submit).toBeEnabled()
  })

  it('asks for the first missing field before it will submit', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Confirm your password.')).toBeInTheDocument()
    // Every missing field is reported at once, and focus goes to the first one
    // in form order rather than to whichever happened to be checked last.
    expect(screen.getByText('Choose a username.')).toBeInTheDocument()
    expect(screen.getByText('Enter your email address.')).toBeInTheDocument()
    expect(document.activeElement?.id).toBe('username')
  })

  it('sends the username problem to the username field and explains the shape', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Username'), '_ada')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Start with a letter or a number.')).toBeInTheDocument()
    expect(document.activeElement?.id).toBe('username')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('refuses a mismatched confirmation without a round trip', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Passw0rd!')
    const confirm = screen.getByLabelText('Confirm password')
    await user.type(confirm, 'Passw0rd?')

    await user.keyboard('{Enter}')

    // The client settles this one itself: there is nothing for the server to
    // add, so the request is never made, and focus goes to the field that has
    // to be fixed.
    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    expect(document.activeElement).toBe(confirm)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('KNOWN LIMITATION: a pointer submit reports the mismatch without moving focus to it', async () => {
    /*
     * DEFECT (reported, not fixed here — the source is not this suite's to
     * change). The form deliberately focuses the offending confirmation field
     * when a submit finds a mismatch, and the keyboard path does exactly that.
     * The pointer path cannot: clicking the button blurs the confirmation
     * field, `confirmBlocked` disables the button, and a control that is
     * disabled under the pointer never dispatches its click — so `onSubmit` and
     * its `confirmRef.current?.focus()` never run.
     *
     * Where focus ends up afterwards differs by environment — jsdom leaves it on
     * the now-disabled button, a browser drops it to `document.body` — so the
     * portable, testable fact is the one asserted here: the field that needs
     * fixing does not receive focus. The message itself is correct and the
     * request is still never made, so nothing is lost but the cursor's position.
     */
    const fetchMock = vi.fn(async () => new Response(null, { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Passw0rd!')
    const confirm = screen.getByLabelText('Confirm password')
    await user.type(confirm, 'Passw0rd?')

    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create account' })).toBeDisabled()
    expect(document.activeElement).not.toBe(confirm)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})