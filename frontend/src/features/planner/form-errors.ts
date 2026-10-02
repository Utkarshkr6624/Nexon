import type { ApiError } from '@/lib/api-client'

/**
 * Flattens a 422's `details.errors[]` into `{ field: message }`, per
 * `docs/api-conventions.md`.
 *
 * Entries that are not field-scoped are dropped and the first message wins when
 * a field repeats, so a form renders one message per input rather than a stack
 * of them. Anything that survives the filter but belongs to no field is still
 * shown, because it is a statement about the request rather than one field.
 */
export function fieldErrorMessages(error: ApiError | null): Record<string, string> {
  if (!error) return {}
  const { errors } = error.fieldErrors
  if (!Array.isArray(errors)) return {}

  const messages: Record<string, string> = {}
  for (const entry of errors as Array<{ field?: unknown; message?: unknown }>) {
    const field = entry?.field
    const message = entry?.message
    if (typeof field !== 'string' || typeof message !== 'string') continue
    if (field === '' || field === 'body' || field in messages) continue
    messages[field] = message
  }
  return messages
}

/**
 * A banner is only worth showing when no field already says it. A 422 whose
 * messages all landed on inputs is on screen inline; repeating it above the form
 * is noise.
 */
export function bannerError(error: ApiError | null): ApiError | null {
  if (!error) return null
  return error.isValidationError && Object.keys(fieldErrorMessages(error)).length > 0 ? null : error
}

export const TEXTAREA_CLASSES =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background aria-[invalid=true]:border-destructive'