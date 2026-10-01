import { apiClient } from '@/lib/api-client'
import type { HealthResponse } from '@/types'

/** Resolved against the `/api/v1` base URL configured on the client. */
export const HEALTH_ENDPOINT = '/health'

/** The health card is a liveness surface, not a metrics pipeline. */
export const HEALTH_POLL_INTERVAL_MS = 30_000

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiClient.get<HealthResponse>(HEALTH_ENDPOINT, {
    signal,
    // Liveness is public; sending a bearer token is unnecessary.
    auth: false,
  })
}
