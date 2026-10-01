import { ApiError } from '@/lib/api-client'

/**
 * Normalises anything thrown by a query function or store action into an
 * `ApiError`, so UI code has exactly one error shape to branch on and never
 * has to render a raw `Error` message that might contain internals.
 */
export function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause

  if (cause instanceof DOMException && cause.name === 'AbortError') {
    return new ApiError({ status: 0, code: 'aborted', message: 'Request cancelled' })
  }

  return new ApiError({
    status: 0,
    code: 'unknown_error',
    message: cause instanceof Error ? cause.message : 'Something went wrong',
  })
}

/** True when React Query (or the caller) cancelled the request. */
export function isAbortError(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError'
}
