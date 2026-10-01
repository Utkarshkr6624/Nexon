import { apiClient } from '@/lib/api-client'
import type { SessionListResponse } from '@/types'

export const SESSION_ENDPOINTS = {
  list: '/auth/sessions',
  byId: (id: string) => `/auth/sessions/${id}`,
} as const

/**
 * The caller's own device sign-ins, with `is_current` marking the one this
 * request was made with. Authenticated, because "which of these am I?" is only
 * meaningful relative to the presented token.
 */
export function fetchSessions(): Promise<SessionListResponse> {
  return apiClient.get<SessionListResponse>(SESSION_ENDPOINTS.list)
}

/**
 * Revoke one of the caller's sessions.
 *
 * A session id that belongs to somebody else answers **404, never 403**. That
 * is not cosmetic: a 403 would confirm the id exists and turn this endpoint into
 * a probe for which session ids are real, whereas a 404 is what an id that never
 * existed also returns. Callers should therefore treat a 404 as "nothing to do
 * here" when the row was just revoked optimistically, not as a permission fault.
 */
export function revokeSession(sessionId: string): Promise<void> {
  return apiClient.delete<void>(SESSION_ENDPOINTS.byId(sessionId), { parse: 'none' })
}