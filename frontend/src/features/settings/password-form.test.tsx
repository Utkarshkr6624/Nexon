import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { MAX_STRENGTH_SCORE, scorePassword } from '@/features/auth/password-strength'
import { PasswordForm } from '@/features/settings/password-form'
import { useAuthStore } from '@/stores/auth-store'
import type { User } from '@/types/api'

/**
 * The change-password form's strength meter.
 *
 * The score runs 0..`MAX_STRENGTH_SCORE`, not 0..100, so the bar has to say so.
 * Without an explicit `max` the primitive's default applies and a genuinely
 * strong password renders a 10%-wide bar announced as "9 of 100" — a meter that
 * contradicts itself and, read at a glance, looks broken. The scale is the
 * contract between `scorePassword` and the `progressbar` role, and it is
 * checked at the ARIA boundary a screen reader actually reads.
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

/** Clears every rule, and long and varied enough to collect both bonuses. */
const STRONG_PASSWORD = 'Str0ng!passphrase!'

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

describe('PasswordForm strength meter', () => {
  it('reports the score on the meter scale rather than on 0-100', async () => {
    expect(MAX_STRENGTH_SCORE).toBe(10)
    expect(scorePassword(STRONG_PASSWORD).level).toBe('strong')

    const user = userEvent.setup()
    render(<PasswordForm />)

    await user.type(screen.getByLabelText('New password'), STRONG_PASSWORD)

    const meter = screen.getByRole('progressbar', { name: 'Password strength: Strong' })
    expect(meter).toHaveAttribute('aria-valuemin', '0')
    expect(meter).toHaveAttribute('aria-valuemax', '10')
    // 100 would be the primitive's default, i.e. the bug.
    expect(meter).not.toHaveAttribute('aria-valuemax', '100')
    expect(meter).toHaveAttribute('aria-valuenow', String(scorePassword(STRONG_PASSWORD).score))
  })

  it('scales the bar with the score as the value changes', async () => {
    const user = userEvent.setup()
    render(<PasswordForm />)

    const input = screen.getByLabelText('New password')
    await user.type(input, 'abc')

    const weak = screen.getByRole('progressbar')
    expect(weak).toHaveAttribute('aria-valuemax', '10')
    expect(weak).toHaveAttribute('aria-valuenow', String(scorePassword('abc').score))
    // A bar that cannot be wrong at the low end either: the scale is the point.
    expect(scorePassword('abc').score).toBeLessThan(MAX_STRENGTH_SCORE)
  })
})
