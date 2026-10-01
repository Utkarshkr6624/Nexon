import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiError } from '@/lib/api-client'

/**
 * `fetch` is always stubbed — these tests must never touch the network. The
 * client resolves `globalThis.fetch` at call time, so stubbing after
 * construction is enough.
 */
afterEach(() => {
  vi.unstubAllGlobals()
})

function client(): ApiClient {
  return new ApiClient({ baseUrl: '/api/v1' })
}

function respond(body: unknown, status: number, headers: Record<string, string> = {}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(typeof body === 'string' ? body : JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json', ...headers },
        }),
    ),
  )
}

describe('ApiError mapping', () => {
  it('maps the backend error envelope onto code, message, details and requestId', async () => {
    respond(
      {
        error: {
          code: 'validation_error',
          message: 'Email is not a valid address',
          details: { email: ['value is not a valid email address'] },
          request_id: 'b1f4c0de-0000-4000-8000-000000000001',
        },
      },
      422,
    )

    const error = await client()
      .get('/auth/login')
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('validation_error')
    expect(apiError.message).toBe('Email is not a valid address')
    expect(apiError.details).toEqual({ email: ['value is not a valid email address'] })
    expect(apiError.requestId).toBe('b1f4c0de-0000-4000-8000-000000000001')
    expect(apiError.status).toBe(422)
    expect(apiError.isValidationError).toBe(true)
    expect(apiError.isTransportError).toBe(false)
    expect(apiError.fieldErrors).toEqual({ email: ['value is not a valid email address'] })
  })

  it('maps a network failure to the transport error, never to an HTTP status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    const error = await client()
      .get('/health')
      .catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('network_error')
    expect(apiError.status).toBe(0)
    expect(apiError.isTransportError).toBe(true)
    expect(apiError.message).toBe('Failed to fetch')
    // A transport failure has no server-side request id to quote.
    expect(apiError.requestId).toBeNull()
  })

  it('falls back to the status when the body is not our envelope, keeping the header id', async () => {
    respond('<html>502 Bad Gateway</html>', 502, { 'x-request-id': 'proxy-req-77' })

    const error = await client()
      .get('/health')
      .catch((cause: unknown) => cause)

    const apiError = error as ApiError
    expect(apiError.status).toBe(502)
    expect(apiError.code).toBe('internal_error')
    expect(apiError.message).toBe('<html>502 Bad Gateway</html>')
    expect(apiError.requestId).toBe('proxy-req-77')
  })
})