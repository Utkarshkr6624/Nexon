import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppErrorBoundary, RouteErrorBoundary } from '@/components/feedback/app-error-boundary'

const originalLocation = window.location

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
})

function stubReload(): ReturnType<typeof vi.fn> {
  const reload = vi.fn()
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...originalLocation, reload },
  })
  return reload
}

function Boom(): never {
  throw new Error('Failed to fetch dynamically imported module: /assets/PlannerPage-abc123.js')
}

describe('AppErrorBoundary', () => {
  it('catches a render throw and recovers by reloading the page', async () => {
    const reload = stubReload()
    const user = userEvent.setup()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    render(
      <AppErrorBoundary>
        <Boom />
      </AppErrorBoundary>,
    )

    // The shared error presentation, plus the one action that can actually help.
    expect(screen.getByRole('alert')).toHaveTextContent('NEXUS could not finish loading this view')
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
    consoleError.mockRestore()

    await user.click(screen.getByRole('button', { name: /reload/i }))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('renders its children untouched when nothing throws', () => {
    render(
      <AppErrorBoundary>
        <p>Everything is fine</p>
      </AppErrorBoundary>,
    )

    expect(screen.getByText('Everything is fine')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('surfaces whatever the router caught, without blanking the route', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const router = createMemoryRouter([
      { path: '/', element: <Boom />, errorElement: <RouteErrorBoundary /> },
    ])

    render(<RouterProvider router={router} />)

    expect(screen.getByRole('alert')).toHaveTextContent('NEXUS could not finish loading this view')
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
    consoleError.mockRestore()
  })
})
