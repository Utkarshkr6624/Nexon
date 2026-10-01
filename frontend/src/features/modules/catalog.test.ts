import { describe, expect, it } from 'vitest'

import {
  ALL_NAV_ITEMS,
  MODULES,
  NAV_GROUPS,
  SETTINGS_MODULE,
  findModule,
  getModule,
} from '@/features/modules/catalog'

describe('module catalog', () => {
  it('declares every nav entry with a unique route, label, phase and named icon', () => {
    const routes = new Set<string>()

    for (const module of ALL_NAV_ITEMS) {
      expect(module.to).toMatch(/^\/[a-z-]+$/)
      expect(routes.has(module.to)).toBe(false)
      routes.add(module.to)

      expect(module.label.trim()).not.toBe('')
      expect(Number.isInteger(module.phase)).toBe(true)
      expect(module.phase).toBeGreaterThanOrEqual(1)
      expect(module.summary.trim()).not.toBe('')
      expect(module.vision.trim()).not.toBe('')
      expect(module.capabilities.length).toBeGreaterThan(0)
      expect(module.metrics.length).toBeGreaterThan(0)
      // lucide exports forwardRef components; the PascalCase name is its label.
      expect(module.icon.displayName).toMatch(/^[A-Z]/)
    }

    expect(routes.size).toBe(ALL_NAV_ITEMS.length)
  })

  it('renders the sidebar and the command palette from the same, non-duplicated registry', () => {
    const grouped = NAV_GROUPS.flatMap((group) => group.items)
    // Grouping reorders the sidebar, so compare the registry, not its order.
    expect([...grouped].sort((a, b) => a.to.localeCompare(b.to))).toEqual(
      [...MODULES].sort((a, b) => a.to.localeCompare(b.to)),
    )
    expect([...grouped, SETTINGS_MODULE]).toEqual(ALL_NAV_ITEMS)
    expect(grouped).not.toContain(SETTINGS_MODULE)

    // /settings lives in the palette but not in a sidebar group.
    expect(findModule('/settings')).toBe(SETTINGS_MODULE)
    expect(findModule('/does-not-exist')).toBeUndefined()
    expect(() => getModule('/does-not-exist')).toThrow(/Unknown module path/)
  })
})