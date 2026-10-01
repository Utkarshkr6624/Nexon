import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import LoginPage from '@/pages/login-page'
import RegisterPage from '@/pages/register-page'

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

    await user.type(screen.getByLabelText('Confirm password'), 'abcd')
    expect(await screen.findByText('Passwords do not match.')).toBeTruthy()
    expect(submit.disabled).toBe(true)

    await user.clear(screen.getByLabelText('Confirm password'))
    await user.type(screen.getByLabelText('Confirm password'), 'abc')
    expect(submit.disabled).toBe(false)

    await user.click(submit)
    expect(await screen.findByText('Choose a username.')).toBeTruthy()
    expect(document.activeElement?.id).toBe('username')

    await user.type(screen.getByLabelText('Username'), '_ada')
    expect(await screen.findByText('Start with a letter or a number.')).toBeTruthy()

    await user.clear(screen.getByLabelText('Username'))
    await user.type(screen.getByLabelText('Username'), 'ada')
    await user.type(screen.getByLabelText('Email'), 'ada@nexus.local')
    await user.type(screen.getByLabelText('Password'), 'Str0ng!pass')
    expect(await screen.findByText('Strong')).toBeTruthy()
  })
})
