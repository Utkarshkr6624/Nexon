import { useEffect, useLayoutEffect } from 'react'
import type { ReactNode } from 'react'
import { DARK_MEDIA_QUERY, useThemeStore } from '@/stores/theme-store'
import type { ResolvedTheme } from '@/stores/theme-store'

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.style.colorScheme = resolved
}

export interface ThemeProviderProps {
  children: ReactNode
}

/**
 * Owns the single side effect of the theme store: the `.dark` class on
 * <html>. The class is applied in a layout effect so it is present before the
 * browser paints, which is what keeps a reload from flashing the wrong theme.
 */
export function ThemeProvider({ children }: ThemeProviderProps) {
  const preference = useThemeStore((state) => state.preference)
  const resolvedTheme = useThemeStore((state) => state.resolvedTheme)
  const syncWithSystem = useThemeStore((state) => state.syncWithSystem)

  // Recompute the effective theme when the preference changes, and keep it
  // correct when the OS switches appearance while the app is open.
  useLayoutEffect(() => {
    syncWithSystem()
  }, [preference, syncWithSystem])

  useLayoutEffect(() => {
    applyTheme(resolvedTheme)
  }, [resolvedTheme])

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    const media = window.matchMedia(DARK_MEDIA_QUERY)
    const handleChange = () => syncWithSystem()
    media.addEventListener('change', handleChange)
    return () => media.removeEventListener('change', handleChange)
  }, [syncWithSystem])

  return children
}
