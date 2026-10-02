import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import SettingsPage from '@/pages/settings-page'

/** The router is real; only the entry point differs, so history behaves as shipped. */
function renderSettings(initial: string) {
  const router = createMemoryRouter([{ path: '/settings', element: <SettingsPage /> }], {
    initialEntries: [initial],
  })
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  )
  return router
}

describe('settings tab routing', () => {
  beforeEach(() => window.history.replaceState({}, '', '/'))

  it('defaults to profile with no query param', () => {
    const router = renderSettings('/settings')
    expect(router.state.location.search).toBe('')
    expect(screen.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true')
  })

  it('falls back to profile for an unknown tab', () => {
    renderSettings('/settings?tab=bogus')
    expect(screen.getByRole('tab', { name: 'Profile' })).toHaveAttribute('aria-selected', 'true')
  })

  it('reads a tab from the URL', () => {
    renderSettings('/settings?tab=security')
    expect(screen.getByRole('tab', { name: 'Security' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Change password' })).toBeInTheDocument()
  })

  it('writes the tab to the URL and walks back and forward', async () => {
    const user = userEvent.setup()
    const router = renderSettings('/settings')

    await user.click(screen.getByRole('tab', { name: 'Security' }))
    await waitFor(() => expect(router.state.location.search).toBe('?tab=security'))

    await user.click(screen.getByRole('tab', { name: 'Sessions' }))
    await waitFor(() => expect(router.state.location.search).toBe('?tab=sessions'))

    await router.navigate(-1)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Security' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    )
    expect(router.state.location.search).toBe('?tab=security')

    await router.navigate(1)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Sessions' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    )
    expect(router.state.location.search).toBe('?tab=sessions')
  })

  it('returns to the bare URL when Profile is chosen again', async () => {
    const user = userEvent.setup()
    const router = renderSettings('/settings?tab=preferences')

    await user.click(screen.getByRole('tab', { name: 'Profile' }))
    await waitFor(() => expect(router.state.location.search).toBe(''))
  })
})