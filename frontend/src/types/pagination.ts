/**
 * Offset/limit list envelope reserved for the list endpoints added in later
 * phases. Adopted as the backend's standard shape; nothing in Phase 1 serves
 * it yet, so it carries no runtime code.
 *
 * This mirrors `Page[T]` and `PageMeta` in `backend/app/schemas/common.py`
 * field for field — the counters are nested under `meta`, they are required,
 * and none of them is optional or defaulted. Change it there first, then here;
 * the type is the only place the client learns the shape, and nothing
 * consumes it yet, so a drift here would not be caught by the compiler until
 * the first paginated endpoint already speaks the old shape.
 */

/** Counters describing the slice returned in a {@link Paginated}. */
export interface PageMeta {
  total: number
  limit: number
  offset: number
}

export interface Paginated<T> {
  items: T[]
  meta: PageMeta
}

/** Query parameters accepted by paginated `GET` endpoints. */
export interface PaginationParams {
  limit?: number
  offset?: number
}
