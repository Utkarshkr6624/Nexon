/**
 * Client-side mirror of the backend password policy.
 *
 * This file exists so the user can see, while typing, what the server is about
 * to tell them. It is a UX affordance and nothing more: the server is the sole
 * authority on whether a password is acceptable, and a value that passes every
 * check below can still be rejected by `validate_password_strength` in
 * `backend/app/schemas/user.py`. Nothing here gates a request, and a form must
 * never treat an empty result as authorisation to skip server validation.
 *
 * The ids and labels are the shared vocabulary with the backend's
 * `PASSWORD_RULES` and `password_rule_status`; changing one side without the
 * other silently desynchronises the two checklists.
 */

/** One requirement in the policy, in the shape the form renders. */
export interface PasswordRule {
  /** Stable id, identical to the backend's rule id. Do not reword. */
  id: string
  label: string
  /** The concrete requirement, so the UI never says "the configured minimum". */
  hint?: string
  test: (value: string) => boolean
}

/**
 * The default minimum, mirroring `settings.password_min_length` in the
 * backend. A deployment can raise it, in which case the server's answer wins
 * and this constant only ever costs the user one round trip of feedback.
 */
export const PASSWORD_MIN_LENGTH = 8

/**
 * Code points, not UTF-16 units. Python's `len()` counts characters, so an
 * emoji must not be worth two length points against the server that counts it
 * as one.
 */
function lengthOf(value: string): number {
  return Array.from(value).length
}

// Unicode property escapes rather than [A-Z]/[a-z]/[0-9]: the backend's
// `str.isupper()` / `islower()` / `isdigit()` are Unicode-aware, and a policy
// that rejects `Ä` while Python accepts it is a policy nobody can satisfy.
const HAS_UPPERCASE = /\p{Lu}/u
const HAS_LOWERCASE = /\p{Ll}/u
const HAS_DIGIT = /\p{Nd}/u
/** Anything that is neither a letter nor a digit — a space counts. */
const HAS_SPECIAL = /[^\p{L}\p{N}]/u

/** Ordered, and in the backend's order: the checklist is rendered top to bottom. */
export const PASSWORD_RULES: readonly PasswordRule[] = [
  {
    id: 'min_length',
    label: 'Minimum length',
    hint: `At least ${PASSWORD_MIN_LENGTH} characters`,
    test: (value) => lengthOf(value) >= PASSWORD_MIN_LENGTH,
  },
  {
    id: 'uppercase',
    label: 'Uppercase letter',
    hint: 'At least one uppercase letter',
    test: (value) => HAS_UPPERCASE.test(value),
  },
  {
    id: 'lowercase',
    label: 'Lowercase letter',
    hint: 'At least one lowercase letter',
    test: (value) => HAS_LOWERCASE.test(value),
  },
  {
    id: 'digit',
    label: 'Digit',
    hint: 'At least one digit',
    test: (value) => HAS_DIGIT.test(value),
  },
  {
    id: 'special',
    label: 'Special character',
    hint: 'At least one character that is not a letter or a digit',
    test: (value) => HAS_SPECIAL.test(value),
  },
]

/** Per-rule outcome, in policy order. */
export interface RuleResult {
  id: string
  label: string
  satisfied: boolean
}

/** Evaluate the whole policy. Always returns one result per rule, never short-circuits. */
export function evaluatePasswordRules(value: string): RuleResult[] {
  return PASSWORD_RULES.map((rule) => ({
    id: rule.id,
    label: rule.label,
    satisfied: rule.test(value),
  }))
}
