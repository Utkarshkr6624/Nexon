import { describe, expect, it } from 'vitest'

import {
  PASSWORD_MIN_LENGTH,
  PASSWORD_RULES,
  evaluatePasswordRules,
} from '@/features/auth/password-rules'

/**
 * The client's copy of the server policy.
 *
 * This mirror is a UX affordance and not a gate, but a mirror that disagrees
 * with the original is worse than no mirror: it tells someone their password is
 * short when the server thinks it is fine. These tests therefore pin the rule
 * *identities* and the boundaries as well as the outcomes, so an edit that
 * renames a rule or moves a threshold fails here rather than in production.
 */

function satisfiedIds(value: string): string[] {
  return evaluatePasswordRules(value)
    .filter((result) => result.satisfied)
    .map((result) => result.id)
}

function byId(value: string, id: string): boolean {
  const result = evaluatePasswordRules(value).find((candidate) => candidate.id === id)
  if (!result) throw new Error(`No rule with id "${id}"`)
  return result.satisfied
}

describe('password policy mirror', () => {
  it('keeps the rule ids and labels the backend renders its checklist with', () => {
    // `backend/app/schemas/user.py` PASSWORD_RULES: same ids, same order, same
    // labels. `password_rule_status` on the server is what these ids line up
    // with; a rename on one side alone desynchronises the two checklists.
    expect(PASSWORD_RULES.map((rule) => rule.id)).toEqual([
      'min_length',
      'uppercase',
      'lowercase',
      'digit',
      'special',
    ])
    expect(PASSWORD_RULES.map((rule) => rule.label)).toEqual([
      'Minimum length',
      'Uppercase letter',
      'Lowercase letter',
      'Digit',
      'Special character',
    ])
    for (const rule of PASSWORD_RULES) {
      expect(rule.hint?.trim()).not.toBe('')
      expect(typeof rule.test('')).toBe('boolean')
    }
  })

  it('states the requirement rather than naming the rule, because that is what the UI shows', () => {
    // The checklist renders `hint` and nothing else. A row reading "Digit" or
    // "Minimum length" makes the reader translate it into an instruction and
    // guess at the parts it leaves out — how many characters, whether a space
    // counts, which characters are uppercase.
    expect(PASSWORD_RULES.map((rule) => rule.hint)).toEqual([
      // Rendered from the constant, so a deployment that raises
      // `settings.password_min_length` says so without a client change.
      `At least ${PASSWORD_MIN_LENGTH} characters`,
      'An uppercase letter (e.g. A, Z)',
      'A lowercase letter (e.g. a, z)',
      'A number (e.g. 0, 9)',
      'A symbol or space (e.g. ! @ #)',
    ])

    // The length row names the deployment's actual number, never the word
    // "minimum" — that is the one requirement a user cannot guess.
    expect(PASSWORD_RULES[0]?.hint).toContain(String(PASSWORD_MIN_LENGTH))

    // Every row fits one line in the card at 12px, or the checklist turns into
    // a paragraph the user has to scroll past.
    for (const rule of PASSWORD_RULES) {
      expect((rule.hint ?? '').length).toBeLessThanOrEqual(36)
    }
  })

  it('carries the requirement through to every result, in the rule order', () => {
    const results = evaluatePasswordRules('abc')
    expect(results.map((result) => result.hint)).toEqual(PASSWORD_RULES.map((rule) => rule.hint))
  })

  it('returns one result per rule, in policy order, for every input', () => {
    for (const value of ['', 'a', 'Passw0rd!', '  ', '😀😀😀😀😀😀😀😀']) {
      const results = evaluatePasswordRules(value)
      expect(results).toHaveLength(PASSWORD_RULES.length)
      expect(results.map((result) => result.id)).toEqual(PASSWORD_RULES.map((rule) => rule.id))
      expect(results.map((result) => result.label)).toEqual(
        PASSWORD_RULES.map((rule) => rule.label),
      )
      expect(results.map((result) => result.hint)).toEqual(
        PASSWORD_RULES.map((rule) => rule.hint),
      )
      // Never short-circuits: the checklist renders every row on every render,
      // so a partial result would drop rows out of the list.
      expect(results.every((result) => typeof result.satisfied === 'boolean')).toBe(true)
    }
  })

  it('reports every rule unmet for an empty value', () => {
    expect(evaluatePasswordRules('')).toEqual(
      PASSWORD_RULES.map((rule) => ({
        id: rule.id,
        label: rule.label,
        hint: rule.hint,
        satisfied: false,
      })),
    )
  })

  it('accepts the length rule on the exact minimum and one character below it', () => {
    const short = 'aB1!' + 'x'.repeat(PASSWORD_MIN_LENGTH - 5)
    const long = 'aB1!' + 'x'.repeat(PASSWORD_MIN_LENGTH - 4)

    expect(PASSWORD_MIN_LENGTH).toBe(8)
    expect(short).toHaveLength(PASSWORD_MIN_LENGTH - 1)
    expect(long).toHaveLength(PASSWORD_MIN_LENGTH)

    expect(byId(short, 'min_length')).toBe(false)
    expect(byId(long, 'min_length')).toBe(true)
  })

  it('scores the character-class rules independently of each other', () => {
    // Any non-empty value satisfies at least one rule — a letter is lowercase or
    // uppercase, anything else is special — so the classes are checked one at a
    // time rather than as a partition.
    expect(satisfiedIds('abcdefg')).toEqual(['lowercase'])
    expect(satisfiedIds('ABCDEFG')).toEqual(['uppercase'])
    expect(satisfiedIds('1234567')).toEqual(['digit'])
    expect(satisfiedIds('!@#$%^&')).toEqual(['special'])
    // One character short of the minimum, but the classes are all there: the
    // length rule must still report false on its own.
    expect(satisfiedIds('aA1!aA1')).toEqual(['uppercase', 'lowercase', 'digit', 'special'])
    expect(satisfiedIds('!!!!!!!!')).toEqual(['min_length', 'special'])
    expect(satisfiedIds('aA1!aA1!')).toEqual([
      'min_length',
      'uppercase',
      'lowercase',
      'digit',
      'special',
    ])
  })

  it('counts a space as the special character the backend counts it as', () => {
    // `not (char.isalpha() or char.isdigit())` in `user.py` accepts a space, and
    // enumerating punctuation would reject good passphrases. Both sides agree.
    expect(byId('Pass w0rd', 'special')).toBe(true)
  })

  it('counts length in code points, the way Python len() does', () => {
    // Seven code points, eight UTF-16 units. Python's len() says seven, so the
    // server rejects this; measuring UTF-16 units would read eight, call the
    // rule satisfied, and let someone submit a password the backend refuses.
    const sevenCodePoints = '😀abcdef'
    expect(Array.from(sevenCodePoints)).toHaveLength(7)
    expect(sevenCodePoints).toHaveLength(8)
    expect(byId(sevenCodePoints, 'min_length')).toBe(false)
    expect(satisfiedIds(sevenCodePoints)).toEqual(['lowercase', 'special'])

    // The other direction: a value that clears the bar only because each
    // astral-plane character counts as one character rather than two.
    const eightCodePoints = `${'😀'.repeat(6)}aB`
    expect(Array.from(eightCodePoints)).toHaveLength(8)
    expect(byId(eightCodePoints, 'min_length')).toBe(true)
  })

  describe('non-ASCII input', () => {
    it('accepts uppercase, lowercase and digits outside ASCII, as the backend does', () => {
      // `str.isupper()` / `islower()` / `isdigit()` in `user.py` are
      // Unicode-aware, so `Ä` satisfies uppercase, `é` lowercase and `١`
      // (Arabic-Indic one, category Nd) a digit. `\p{Lu}` / `\p{Ll}` / `\p{Nd}`
      // are the matching Unicode properties — verified against the same inputs
      // with the backend's own `_rule_results`.
      expect(satisfiedIds('Äbcdefg1!')).toEqual([
        'min_length',
        'uppercase',
        'lowercase',
        'digit',
        'special',
      ])
      expect(byId('äbcdefg1!', 'uppercase')).toBe(false)
      expect(byId('äbcdefg1!', 'lowercase')).toBe(true)
      expect(byId('ABCDEFG١!', 'digit')).toBe(true)
    })

    it('treats an emoji as a special character and not as a letter or a digit', () => {
      // Python: not alpha, not digit → special. Same verdict here.
      expect(byId('abc😀defg', 'special')).toBe(true)
      expect(byId('abc😀defg', 'uppercase')).toBe(false)
      expect(byId('abc😀defg', 'lowercase')).toBe(true)
      expect(byId('abc😀defg', 'digit')).toBe(false)
    })

    it('KNOWN DIVERGENCE: rejects a superscript digit the backend would accept', () => {
      /*
       * DEFECT (reported, not fixed here — the source is not this suite's to
       * change). `user.py` gates the digit rule on `str.isdigit()`, which is
       * true for characters outside category Nd; `\p{Nd}` is exactly Nd. So for
       * `Ab²cdefg!` (U+00B2) the server reports every rule satisfied and
       * accepts the password, while this mirror reports "Digit — not met yet"
       * and the register form refuses to submit it.
       *
       * Checked directly against `backend/app/schemas/user.py::_rule_results`,
       * which returns `{min_len: True, up: True, lo: True, dig: True,
       * sp: True}` for that value.
       *
       * This test pins the current behaviour so the divergence is visible in the
       * suite rather than discovered by a user. Fixing either side changes the
       * result and the change has to be made deliberately.
       */
      const value = `Ab²cdefg!`

      expect(byId(value, 'digit')).toBe(false)
      // `!` supplies the special character on both sides, so the digit rule is
      // the only row where the two checklists disagree.
      expect(byId(value, 'special')).toBe(true)
      expect(satisfiedIds(value)).toEqual(['min_length', 'uppercase', 'lowercase', 'special'])

      // `½` (U+00BD) is numeric but not a digit in Python either, so it agrees.
      expect(byId('Ab½cdefg!', 'digit')).toBe(false)
    })
  })
})