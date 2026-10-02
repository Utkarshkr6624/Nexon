import { describe, expect, it } from 'vitest'

import type { Paginated } from '@/types/pagination'

/**
 * `Paginated<T>` is the list envelope the backend emits, and it is the only
 * place the client learns the shape. It is a pure type, so a runtime test
 * cannot catch a drift: the guard has to be a compile-time one, and
 * `npm run typecheck` is what runs it (`tsconfig.app.json` includes `src`, so
 * this file is checked along with the code it describes).
 *
 * A `@ts-expect-error` is an assertion, not a suppression: it fails the build
 * when the line it covers does *not* error. Each one below therefore fails
 * `tsc` if `Paginated<T>` ever drifts back to the old flat shape — which is the
 * drift this file exists to prevent.
 */

/** One row of a paginated list, as a consumer would type it. */
interface Task {
  id: string
  title: string
}

const ENVELOPE: Paginated<Task> = {
  items: [{ id: 't-1', title: 'First' }],
  meta: { total: 1, limit: 20, offset: 0 },
}

/** The shape the client used to declare: the counters sat beside `items`. */
interface FlatPage<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

type IsAssignable<From, To> = [From] extends [To] ? true : false

/** Fails to compile unless `T` is exactly `true`. */
type Expect<T extends true> = T

export type NestedEnvelopeIsAccepted = Expect<IsAssignable<typeof ENVELOPE, Paginated<Task>>>

/**
 * The flat shape must NOT be assignable to the envelope, and this line is
 * asserted rather than suppressed: `@ts-expect-error` fails the build when the
 * line it covers does *not* error. With the counters nested, `FlatPage` fails
 * the constraint and the directive is spent. If the envelope ever regresses to
 * the flat shape, `FlatPage` satisfies `Paginated` — nothing errors, the
 * directive goes unused, and `npm run typecheck` fails. That inversion is the
 * whole guard: it cannot be satisfied by deleting the type.
 */
// @ts-expect-error `FlatPage` must not satisfy `Paginated` — the counters live under `meta`.
export type _FlatEnvelopeIsRejected = Expect<IsAssignable<FlatPage<Task>, Paginated<Task>>>

describe('Paginated<T> envelope', () => {
  it('nests the counters under `meta` and nothing else', () => {
    expect(Object.keys(ENVELOPE).sort()).toEqual(['items', 'meta'])
    expect(Object.keys(ENVELOPE.meta).sort()).toEqual(['limit', 'offset', 'total'])
  })

  it('carries the slice and the counters a consumer reads', () => {
    const { items, meta } = ENVELOPE
    expect(items).toHaveLength(1)
    expect(meta).toEqual({ total: 1, limit: 20, offset: 0 })
    // The counters describe the slice, so they travel with it.
    expect(meta.total).toBeGreaterThanOrEqual(items.length)
  })
})
