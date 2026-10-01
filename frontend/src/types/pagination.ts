/**
 * Offset/limit list envelope reserved for the list endpoints added in later
 * phases. Adopted as the backend's standard shape; nothing in Phase 1 serves
 * it yet, so it carries no runtime code.
 */
export interface Paginated<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

/** Query parameters accepted by paginated `GET` endpoints. */
export interface PaginationParams {
  limit?: number
  offset?: number
}
