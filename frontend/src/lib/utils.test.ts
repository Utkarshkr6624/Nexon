import { describe, expect, it } from 'vitest'

import { cn } from '@/lib/utils'

const enabled = false as boolean

describe('cn', () => {
  it('joins conditional class names and drops falsy values', () => {
    expect(cn('a', enabled && 'skipped', undefined, null, ['b', 'c'], { d: true, e: false })).toBe(
      'a b c d',
    )
  })

  it('lets a later Tailwind utility win over an earlier one in the same group', () => {
    expect(cn('px-2 py-1', 'px-6')).toBe('py-1 px-6')
    // Different groups are both kept.
    expect(cn('text-sm', 'font-semibold')).toBe('text-sm font-semibold')
  })
})