import { useQuery } from '@tanstack/react-query'
import { HEALTH_POLL_INTERVAL_MS, fetchHealth } from '@/services/health'
import type { HealthResponse } from '@/types'

export const HEALTH_QUERY_KEY = ['system', 'health'] as const

export interface UseHealthOptions {
  /** Pass `false` to stop polling (e.g. on a hidden tab). */
  refetchInterval?: number | false
  enabled?: boolean
}

/**
 * Live backend health. This is the only real data dependency in the Phase 1
 * shell; every other surface renders from the module registry.
 */
export function useHealth(options: UseHealthOptions = {}) {
  const { refetchInterval = HEALTH_POLL_INTERVAL_MS, enabled = true } = options

  return useQuery<HealthResponse>({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval,
    enabled,
    staleTime: 5_000,
  })
}
