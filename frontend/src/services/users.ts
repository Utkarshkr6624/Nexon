import { apiClient } from '@/lib/api-client'
import type { ProfileUpdatePayload, User, UserDeletionPayload } from '@/types'

export const USER_ENDPOINTS = {
  me: '/users/me',
} as const

/**
 * Update the caller's own profile. Ownership is implicit in the path — there is
 * no id to target — so this can only ever edit the authenticated account.
 */
export function updateProfileRequest(payload: ProfileUpdatePayload): Promise<User> {
  return apiClient.patch<User>(USER_ENDPOINTS.me, payload)
}

/**
 * Delete the caller's account permanently.
 *
 * `apiClient.delete` takes options rather than a body, and the backend requires
 * the `{ password, confirm }` payload in the request body — a token left in a
 * shared browser must not be enough to destroy an account. `apiClient.request`
 * is called directly to thread that body through, which is the same primitive
 * the `delete` helper wraps; the alternative would have been widening the
 * helper's signature in `lib/api-client.ts`, which this module does not own.
 */
export function deleteAccountRequest(payload: UserDeletionPayload): Promise<void> {
  return apiClient.request<void>(USER_ENDPOINTS.me, {
    method: 'DELETE',
    body: payload,
    parse: 'none',
  })
}