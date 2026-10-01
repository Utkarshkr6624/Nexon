import { useCallback, useSyncExternalStore } from 'react'

const NOOP_UNSUBSCRIBE = () => undefined

function matches(query: string): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia(query).matches
}

/**
 * SSR-safe media query subscription. `useSyncExternalStore` keeps the value in
 * sync with the browser and avoids the stale-snapshot warning that an effect
 * based implementation produces when a query changes.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
        return NOOP_UNSUBSCRIBE
      }
      const list = window.matchMedia(query)
      list.addEventListener('change', onStoreChange)
      return () => list.removeEventListener('change', onStoreChange)
    },
    [query],
  )

  const getSnapshot = useCallback(() => matches(query), [query])
  const getServerSnapshot = useCallback(() => false, [])

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

export const MOBILE_BREAKPOINT = '(max-width: 767px)'
export const DESKTOP_BREAKPOINT = '(min-width: 1024px)'
export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_BREAKPOINT)
}

export function useIsDesktop(): boolean {
  return useMediaQuery(DESKTOP_BREAKPOINT)
}

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery(REDUCED_MOTION_QUERY)
}
