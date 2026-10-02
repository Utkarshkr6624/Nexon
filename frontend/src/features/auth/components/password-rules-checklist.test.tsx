import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PasswordRulesChecklist } from '@/features/auth/components/password-rules-checklist'
import { evaluatePasswordRules } from '@/features/auth/password-rules'

/**
 * The live policy checklist.
 *
 * The state of a row has to survive greyscale. Two channels carry it: the icon
 * *changes shape* between a check and a hollow circle, and each row carries an
 * `sr-only` "— met" / "— not met yet" suffix. Colour only reinforces both. A
 * test that only looked at the class names would pass against a list that
 * rendered one glyph in two colours, which is the exact failure this component
 * exists to prevent.
 */

function renderList(value: string) {
  render(<PasswordRulesChecklist results={evaluatePasswordRules(value)} />)
  return screen.getAllByRole('listitem')
}

/** The glyph a row renders, identified by shape rather than by colour. */
function rowShape(row: HTMLElement): string {
  return row.querySelector('svg')?.getAttribute('class') ?? ''
}

describe('PasswordRulesChecklist', () => {
  it('renders one row per rule, in policy order', () => {
    const rows = renderList('abc')

    expect(rows).toHaveLength(5)
    // The row states the requirement, not the rule's name: this string is what
    // the user reads while typing, so it is pinned rather than derived.
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('At least 8 characters'),
      expect.stringContaining('An uppercase letter (e.g. A, Z)'),
      expect.stringContaining('A lowercase letter (e.g. a, z)'),
      expect.stringContaining('A number (e.g. 0, 9)'),
      expect.stringContaining('A symbol or space (e.g. ! @ #)'),
    ])
  })

  it('distinguishes met from unmet in text, not only in colour', () => {
    // One rule short of compliant: only the length rule is unmet.
    const rows = renderList('aA1!aaa')

    expect(rows[0]).toHaveTextContent('At least 8 characters — not met yet')
    expect(rows[0]).not.toHaveTextContent('— met')
    for (const row of rows.slice(1)) {
      expect(row).toHaveTextContent('— met')
      expect(row).not.toHaveTextContent('not met')
    }
  })

  it('changes the icon shape between states, so greyscale still reads', () => {
    const rows = renderList('aA1!aaa')

    const unmet = rowShape(rows[0] as HTMLElement)
    const met = rowShape(rows[1] as HTMLElement)

    expect(unmet).not.toBe(met)
    expect(met).toMatch(/check/i)
    expect(unmet).toMatch(/circle/i)
  })

  it('marks every row as unmet for an empty value', () => {
    const rows = renderList('')

    for (const row of rows) {
      expect(row).toHaveTextContent('not met yet')
    }
    const shapes = new Set(rows.map((row) => rowShape(row)))
    expect(shapes.size).toBe(1)
  })

  it('marks every row as met for a compliant value', () => {
    const rows = renderList('Passw0rd!')

    for (const row of rows) {
      expect(row).toHaveTextContent('— met')
    }
    const shapes = new Set(rows.map((row) => rowShape(row)))
    expect(shapes.size).toBe(1)
  })

  it('is a list, so the row count is announced rather than implied', () => {
    render(<PasswordRulesChecklist results={evaluatePasswordRules('Passw0rd!')} />)

    const list = screen.getByRole('list')
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
  })
})