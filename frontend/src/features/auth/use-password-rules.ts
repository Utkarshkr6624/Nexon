import { useMemo } from 'react'

import { evaluatePasswordRules } from './password-rules'
import type { RuleResult } from './password-rules'
import { scorePassword } from './password-strength'
import type { StrengthResult } from './password-strength'

export interface PasswordRuleState {
  results: RuleResult[]
  strength: StrengthResult
  allSatisfied: boolean
}

/**
 * Live policy feedback for a password field.
 *
 * A plain `useMemo` on the value, with no context and no store: the rule set
 * is a module constant, so the value is the only thing that can change the
 * answer and keying on it is the entire cache. Wrapping this in a provider
 * would only add a re-render boundary the parent already owns.
 */
export function usePasswordRules(value: string): PasswordRuleState {
  return useMemo(() => {
    const results = evaluatePasswordRules(value)
    const strength = scorePassword(value)
    return {
      results,
      strength,
      // An empty value can never satisfy the length rule, so a field the user
      // has not touched reports false and a submit button bound to this stays
      // disabled. Still a hint to the form, not a gate: the server decides.
      allSatisfied: results.every((result) => result.satisfied),
    }
  }, [value])
}
