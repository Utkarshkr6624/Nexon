import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ErrorState } from '@/components/feedback/error-state'
import { ApiError } from '@/lib/api-client'

describe('ErrorState', () => {
  it('explains a server failure, quotes the request id and never leaks internals', () => {
    render(
      <ErrorState
        error={
          new ApiError({
            status: 500,
            code: 'internal_error',
            message: 'Internal Server Error',
            requestId: 'b1f4c0de-0000-4000-8000-0000000000ff',
          })
        }
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('The backend hit an unexpected error')
    expect(alert).toHaveTextContent('Request ID')
    expect(alert).toHaveTextContent('b1f4c0de-0000-4000-8000-0000000000ff')

    // Only the backend's user-safe message reaches the surface.
    expect(screen.queryByText(/Traceback|at Object\.|\.py:line|stack/i)).not.toBeInTheDocument()
  })

  it('gives transport failures its own copy and wires up the retry action', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(
      <ErrorState
        onRetry={onRetry}
        error={
          new ApiError({ status: 0, code: 'network_error', message: 'Failed to fetch' })
        }
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the NEXUS backend')
    // No request id exists for a request that never reached the server.
    expect(screen.queryByText(/Request ID/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})