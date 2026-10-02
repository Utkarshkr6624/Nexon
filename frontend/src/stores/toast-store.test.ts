import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MAX_VISIBLE_TOASTS, toast, useToastStore } from '@/stores/toast-store'

/**
 * The toast stack.
 *
 * Two contracts are worth a test each. The stack is a fixed *window* rather than
 * a queue, so a failing background poll cannot bury the page — the oldest entry
 * is dropped. And every timer lives beside the store, so a toast dismissed by
 * hand, by its holder, or by the cap stops its own timer instead of firing
 * against a record that is already gone.
 *
 * The store and its timer map are module-scoped, so every test starts from a
 * known-empty stack rather than from whatever the previous one left behind.
 */

function titles(): string[] {
  return useToastStore.getState().toasts.map((entry) => entry.title)
}

beforeEach(() => {
  vi.useFakeTimers()
  useToastStore.getState().dismissAll()
})

afterEach(() => {
  useToastStore.getState().dismissAll()
  vi.useRealTimers()
})

describe('toast store', () => {
  it('returns an id from push and appends the record', () => {
    const id = useToastStore.getState().push({ title: 'Signed in', description: 'Welcome back' })

    expect(typeof id).toBe('string')
    expect(id).not.toBe('')
    expect(useToastStore.getState().toasts).toHaveLength(1)
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      id,
      title: 'Signed in',
      description: 'Welcome back',
      variant: 'default',
    })
  })

  it('gives every pushed toast a distinct id, so dismiss can address one', () => {
    const ids = ['a', 'b', 'c'].map((title) =>
      useToastStore.getState().push({ title, durationMs: 0 }),
    )

    expect(new Set(ids).size).toBe(3)
    expect(useToastStore.getState().toasts.map((entry) => entry.id)).toEqual(ids)
  })

  it('caps the stack and drops the oldest entry on overflow', () => {
    expect(MAX_VISIBLE_TOASTS).toBe(4)

    for (let index = 1; index <= MAX_VISIBLE_TOASTS; index += 1) {
      useToastStore.getState().push({ title: `toast ${index}`, durationMs: 0 })
    }
    expect(titles()).toEqual(['toast 1', 'toast 2', 'toast 3', 'toast 4'])

    // The fifth cannot fit, so the first goes: the newest news is what is on
    // screen, not the oldest.
    useToastStore.getState().push({ title: 'toast 5', durationMs: 0 })
    expect(titles()).toEqual(['toast 2', 'toast 3', 'toast 4', 'toast 5'])
    expect(useToastStore.getState().toasts).toHaveLength(MAX_VISIBLE_TOASTS)

    // And it stays capped however much arrives.
    useToastStore.getState().push({ title: 'toast 6', durationMs: 0 })
    useToastStore.getState().push({ title: 'toast 7', durationMs: 0 })
    expect(useToastStore.getState().toasts).toHaveLength(MAX_VISIBLE_TOASTS)
    expect(titles().at(-1)).toBe('toast 7')
    expect(titles()).not.toContain('toast 1')
    expect(titles()).not.toContain('toast 2')
  })

  it('removes exactly the addressed toast and nothing else', () => {
    const first = useToastStore.getState().push({ title: 'first', durationMs: 0 })
    useToastStore.getState().push({ title: 'second', durationMs: 0 })
    useToastStore.getState().push({ title: 'third', durationMs: 0 })

    useToastStore.getState().dismiss(first)

    expect(titles()).toEqual(['second', 'third'])

    // Dismissing an id that is not in the stack is a no-op, not a wipe.
    useToastStore.getState().dismiss('toast-does-not-exist')
    expect(titles()).toEqual(['second', 'third'])
  })

  it('clears the whole stack on demand', () => {
    useToastStore.getState().push({ title: 'a', durationMs: 0 })
    useToastStore.getState().push({ title: 'b', durationMs: 0 })

    useToastStore.getState().dismissAll()

    expect(useToastStore.getState().toasts).toEqual([])
  })

  describe('timers', () => {
    it('dismisses a toast once its duration has elapsed', () => {
      useToastStore.getState().push({ title: 'transient', durationMs: 5000 })

      vi.advanceTimersByTime(4_999)
      expect(titles()).toEqual(['transient'])

      vi.advanceTimersByTime(1)
      expect(titles()).toEqual([])
    })

    it('clears the timer when the toast is dismissed by hand', () => {
      const id = useToastStore.getState().push({ title: 'manual', durationMs: 5000 })
      // The pending timer is the observable: if the dismissal did not cancel
      // it, one would still be queued against a record that no longer exists.
      expect(vi.getTimerCount()).toBe(1)

      useToastStore.getState().dismiss(id)

      expect(vi.getTimerCount()).toBe(0)
      vi.advanceTimersByTime(60_000)
      expect(titles()).toEqual([])
    })

    it('clears the timer of a toast the cap pushed out', () => {
      for (let index = 0; index < MAX_VISIBLE_TOASTS; index += 1) {
        useToastStore.getState().push({ title: `toast ${index}`, durationMs: 5000 })
      }
      expect(vi.getTimerCount()).toBe(MAX_VISIBLE_TOASTS)

      useToastStore.getState().push({ title: 'overflow', durationMs: 5000 })

      // Four toasts remain and four timers remain: the evicted one's timer went
      // with it instead of firing against a record that is gone.
      expect(vi.getTimerCount()).toBe(MAX_VISIBLE_TOASTS)
      expect(titles()).not.toContain('toast 0')
    })

    it('clears every timer on dismissAll', () => {
      useToastStore.getState().push({ title: 'a', durationMs: 5000 })
      useToastStore.getState().push({ title: 'b', durationMs: 5000 })
      expect(vi.getTimerCount()).toBe(2)

      useToastStore.getState().dismissAll()

      expect(vi.getTimerCount()).toBe(0)
    })

    it('pins a toast open when the duration is zero or negative', () => {
      useToastStore.getState().push({ title: 'pinned', durationMs: 0 })

      expect(vi.getTimerCount()).toBe(0)
      vi.advanceTimersByTime(10 * 60_000)
      expect(titles()).toEqual(['pinned'])
    })
  })

  describe('the imperative façade', () => {
    it('maps each tone to a variant and returns the id', () => {
      const expectations = [
        [toast.info, 'default'],
        [toast.success, 'success'],
        [toast.warning, 'warning'],
        [toast.error, 'destructive'],
      ] as const

      for (const [announce, variant] of expectations) {
        const id = announce('Heads up', 'Body copy')
        const record = useToastStore.getState().toasts.find((entry) => entry.id === id)
        expect(record?.variant).toBe(variant)
        expect(record?.description).toBe('Body copy')
      }
    })

    it('passes durations and actions through the escape hatch', () => {
      const onClick = vi.fn()
      const id = toast.custom({
        title: 'Undo',
        durationMs: 0,
        action: { label: 'Undo', onClick },
      })

      const record = useToastStore.getState().toasts.find((entry) => entry.id === id)
      expect(record?.action?.label).toBe('Undo')

      record?.action?.onClick()
      expect(onClick).toHaveBeenCalledOnce()
    })
  })
})