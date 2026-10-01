import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The store and the no-flash inline script in index.html both read
 * `localStorage`. If the keys ever drift, every reload paints the wrong theme
 * for a frame, so the agreement is asserted rather than assumed.
 */

// `new URL()` here would be jsdom's, which resolves against the jsdom document
// URL rather than the file system.
const INDEX_HTML = readFileSync(
  resolve(fileURLToPath(import.meta.url), '..', '..', '..', 'index.html'),
  'utf8',
)

type ThemeModule = typeof import('@/stores/theme-store')

async function loadStore(): Promise<ThemeModule> {
  vi.resetModules()
  return import('@/stores/theme-store')
}

function prefersDark(matches: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        media: query,
        matches,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  )
}

beforeEach(() => {
  window.localStorage.clear()
  prefersDark(false)
})

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe('theme store', () => {
  it('persists under the key the index.html bootstrap script reads', async () => {
    const { THEME_EFFECTIVE_KEY, THEME_PREFERENCE_KEY } = await loadStore()

    expect(THEME_EFFECTIVE_KEY).toBe('nexus-theme')
    expect(INDEX_HTML).toContain(`'${THEME_EFFECTIVE_KEY}'`)
    expect(INDEX_HTML).not.toContain(`'${THEME_PREFERENCE_KEY}'`)
  })

  it('defaults to the OS preference and mirrors the effective theme for first paint', async () => {
    prefersDark(true)
    const { useThemeStore, THEME_EFFECTIVE_KEY, THEME_PREFERENCE_KEY } = await loadStore()

    expect(useThemeStore.getState().preference).toBe('system')
    expect(useThemeStore.getState().resolvedTheme).toBe('dark')
    // The effective theme is a bare word so the blocking script can read it.
    expect(window.localStorage.getItem(THEME_EFFECTIVE_KEY)).toBe('dark')
    // `persist` only writes once the user expresses a choice, so the preference
    // key stays absent until then — the effective key is what boots the page.
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBeNull()
  })

  it('setTheme writes an explicit preference and derives the effective theme from it', async () => {
    const {
      useThemeStore,
      THEME_EFFECTIVE_KEY,
      THEME_PREFERENCE_KEY,
    } = await loadStore()

    useThemeStore.getState().setTheme('light')
    expect(useThemeStore.getState().preference).toBe('light')
    expect(useThemeStore.getState().resolvedTheme).toBe('light')
    expect(window.localStorage.getItem(THEME_EFFECTIVE_KEY)).toBe('light')

    // An explicit choice must survive an OS that now prefers dark.
    prefersDark(true)
    useThemeStore.getState().syncWithSystem()
    expect(useThemeStore.getState().resolvedTheme).toBe('light')

    useThemeStore.getState().setTheme('system')
    useThemeStore.getState().syncWithSystem()
    expect(useThemeStore.getState().resolvedTheme).toBe('dark')
    expect(JSON.parse(window.localStorage.getItem(THEME_PREFERENCE_KEY) ?? '{}')).toEqual({
      state: { preference: 'system' },
      version: 0,
    })
  })
})