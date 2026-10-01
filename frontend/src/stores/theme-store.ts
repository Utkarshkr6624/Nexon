import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const DARK_MEDIA_QUERY = '(prefers-color-scheme: dark)'

/** Zustand persistence key for the raw preference (`light` | `dark` | `system`). */
export const THEME_PREFERENCE_KEY = 'nexus.theme'

/**
 * Plain-string key holding the *effective* theme. The blocking script in
 * index.html reads it before the bundle executes and applies `.dark` to <html>
 * at first paint. That script can only parse `light` / `dark`, so the effective
 * theme is mirrored here and the preference is persisted separately — otherwise
 * a stored light theme would paint dark for one frame on every reload.
 */
export const THEME_EFFECTIVE_KEY = 'nexus-theme'

function isPreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}

function isResolved(value: unknown): value is ResolvedTheme {
  return value === 'light' || value === 'dark'
}

function prefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia(DARK_MEDIA_QUERY).matches
}

function resolve(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return prefersDark() ? 'dark' : 'light'
  return preference
}

function readRaw(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeRaw(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Private mode or storage disabled: the theme still applies for this page.
  }
}

/** Reads the persisted preference synchronously, falling back to the OS. */
function readPreference(): ThemePreference {
  const raw = readRaw(THEME_PREFERENCE_KEY)
  if (!raw) return 'system'
  try {
    const state: unknown = JSON.parse(raw)
    const preference = (state as { state?: { preference?: unknown } })?.state?.preference
    return isPreference(preference) ? preference : 'system'
  } catch {
    return 'system'
  }
}

function readEffectiveFallback(preference: ThemePreference): ResolvedTheme {
  const mirrored = readRaw(THEME_EFFECTIVE_KEY)
  if (isResolved(mirrored)) return mirrored
  return resolve(preference)
}

interface ThemeState {
  preference: ThemePreference
  resolvedTheme: ResolvedTheme
  setTheme: (preference: ThemePreference) => void
  /** Re-resolves the effective theme, e.g. after the OS preference changes. */
  syncWithSystem: () => void
}

function commit(resolved: ResolvedTheme): void {
  writeRaw(THEME_EFFECTIVE_KEY, resolved)
}

const initialPreference = typeof window === 'undefined' ? ('system' as const) : readPreference()
const initialResolved = readEffectiveFallback(initialPreference)
commit(initialResolved)

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      preference: initialPreference,
      resolvedTheme: initialResolved,
      setTheme: (preference) => {
        const resolved = resolve(preference)
        commit(resolved)
        set({ preference, resolvedTheme: resolved })
      },
      syncWithSystem: () => {
        const resolved = resolve(get().preference)
        commit(resolved)
        set({ resolvedTheme: resolved })
      },
    }),
    {
      name: THEME_PREFERENCE_KEY,
      storage: createJSONStorage(() => window.localStorage),
      // Only the preference is persisted. The effective theme is always derived
      // from it plus the current OS setting, never restored from this key.
      partialize: (state) => ({ preference: state.preference }),
    },
  ),
)
