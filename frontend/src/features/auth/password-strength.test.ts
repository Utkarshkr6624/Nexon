import { describe, expect, it } from 'vitest'

import { evaluatePasswordRules } from '@/features/auth/password-rules'
import {
  MAX_STRENGTH_SCORE,
  STRENGTH_TOKENS,
  scorePassword,
} from '@/features/auth/password-strength'
import type { StrengthLevel } from '@/features/auth/password-strength'

/**
 * The strength meter behind the password field.
 *
 * Every expectation below is a hand-computed score, not a recorded output: the
 * model is five policy points, up to three for length, up to two for variety,
 * minus one for a repeated run. Writing the arithmetic out is what makes a
 * change to the weights a deliberate decision rather than a quiet shift in
 * what "Good" means.
 */

/** Policy satisfied, plus `bonus` for length/variety, minus `penalty`. */
function score(satisfied: number, bonus = 0, penalty = 0): number {
  return satisfied + bonus - penalty
}

const VARIETY_TIP = 'Longer is harder to guess — a passphrase beats a short word.'

describe('password strength meter', () => {
  it('scores an empty field at zero, as weak, and says what to fix first', () => {
    const result = scorePassword('')

    expect(result.score).toBe(0)
    expect(result.level).toBe('weak')
    expect(result.satisfied).toBe(0)
    expect(result.total).toBe(5)
    // An empty input is not a weak password, but the meter is hidden until the
    // first keystroke — and when it is shown it must not claim there is nothing
    // left to suggest.
    expect(result.nextHint).not.toBeNull()
    expect(result.nextHint).toBe('At least 8 characters')
  })

  it('walks the band boundaries at the thresholds the levels are cut at', () => {
    // One point below the fair threshold: 3 rules met, nothing bonus, and the
    // repeated-run penalty takes it to 2.
    const belowFair = scorePassword('Aaaaaaaa')
    expect(belowFair.score).toBe(score(3, 0, 1))
    expect(belowFair.score).toBe(2)
    expect(belowFair.level).toBe('weak')

    // Exactly on it: same rule count, no repetition, so 3.
    const atFair = scorePassword('abcdefgA')
    expect(atFair.score).toBe(score(3))
    expect(atFair.score).toBe(3)
    expect(atFair.level).toBe('fair')

    // Fair, one point short of good.
    const fair = scorePassword('aA1abcde')
    expect(fair.score).toBe(score(4))
    expect(fair.score).toBe(4)
    expect(fair.level).toBe('fair')

    // Exactly on the good threshold.
    const atGood = scorePassword('aA1!aA1!')
    expect(atGood.score).toBe(score(5))
    expect(atGood.score).toBe(5)
    expect(atGood.level).toBe('good')

    // Good, one point short of strong: 5 policy + 1 length + 1 variety.
    const good = scorePassword('abcdefgA1!xy')
    expect(good.score).toBe(score(5, 2))
    expect(good.score).toBe(7)
    expect(good.level).toBe('good')

    // Exactly on the strong threshold.
    const atStrong = scorePassword('Str0ng!passphrase!')
    expect(atStrong.score).toBe(score(5, 3))
    expect(atStrong.score).toBe(8)
    expect(atStrong.level).toBe('strong')
  })

  it('never lets a bonus call a password strong when the policy is unmet', () => {
    // Long and varied enough to clear the raw threshold on its own, but still
    // missing a digit. A meter that says "Strong" about a password the server
    // will reject is worse than no meter.
    const long = scorePassword('abcdefgA!xyzabcdefgA!xyz')

    expect(long.score).toBeGreaterThanOrEqual(8)
    expect(long.satisfied).toBe(4)
    expect(long.level).not.toBe('strong')
    expect(long.level).toBe('good')

    // One rule short of the whole policy, on a long value: capped at good.
    expect(scorePassword('aA!bcdefghijklmnop').satisfied).toBe(4)
    expect(scorePassword('aA!bcdefghijklmnop').score).toBe(score(5, 2))
    expect(scorePassword('aA!bcdefghijklmnop').level).toBe('good')

    // Two or more short, on a long value of the same shape: capped at fair,
    // even though the raw score alone would have read "good".
    expect(scorePassword('ab!cdefghijklmnopq').satisfied).toBe(3)
    expect(scorePassword('ab!cdefghijklmnopq').score).toBe(score(6))
    expect(scorePassword('ab!cdefghijklmnopq').level).toBe('fair')
  })

  it('reports the policy outcome alongside the level', () => {
    const partial = scorePassword('abc')
    expect(partial.satisfied).toBe(1)
    expect(partial.total).toBe(5)
    expect(evaluatePasswordRules('abc').filter((rule) => rule.satisfied)).toHaveLength(
      partial.satisfied,
    )

    const compliant = scorePassword('Passw0rd!')
    expect(compliant.satisfied).toBe(compliant.total)
    expect(evaluatePasswordRules('Passw0rd!').every((rule) => rule.satisfied)).toBe(true)
    // Compliant but plainly breakable: "Good", and the tip points at length
    // rather than at a rule already met.
    expect(compliant.level).toBe('good')
    expect(compliant.nextHint).toBe(VARIETY_TIP)
  })

  it('suggests the first unmet rule, in policy order, and stops at strong', () => {
    expect(scorePassword('aB1!aaa').nextHint).toBe('At least 8 characters')
    expect(scorePassword('aaaaaaaa').nextHint).toBe('An uppercase letter (e.g. A, Z)')
    expect(scorePassword('ABCDEFGH').nextHint).toBe('A lowercase letter (e.g. a, z)')
    expect(scorePassword('abcdeFGh').nextHint).toBe('A number (e.g. 0, 9)')
    expect(scorePassword('abcdefG1').nextHint).toBe('A symbol or space (e.g. ! @ #)')

    // Nothing left to suggest: a strong reading on a compliant password.
    const strong = scorePassword('Str0ng!passphrase!')
    expect(strong.level).toBe('strong')
    expect(strong.nextHint).toBeNull()
  })

  it('penalises a repeated run and never reports a negative score', () => {
    const run = scorePassword('aaaaaaaa')
    expect(run.satisfied).toBe(2)
    expect(run.score).toBe(score(2, 0, 1))
    expect(run.level).toBe('weak')

    const varied = scorePassword('aA1!aA1!')
    expect(varied.score).toBeGreaterThan(run.score)

    // A value so poor the penalty would go negative without the floor.
    const tiny = scorePassword('a')
    expect(tiny.score).toBeGreaterThanOrEqual(0)
    expect(tiny.level).toBe('weak')
  })

  it('clamps the score to the meter maximum', () => {
    expect(MAX_STRENGTH_SCORE).toBe(10)

    // 21 distinct characters, no run: 5 policy + 3 length + 2 variety.
    const maximal = scorePassword('aB1!cD2@eF3#gH4$iV%jK')
    expect(maximal.score).toBe(score(5, 5))
    expect(maximal.score).toBe(MAX_STRENGTH_SCORE)
    expect(maximal.level).toBe('strong')
  })

  it('gives every strength level a token entry with a distinct text label', () => {
    expect(Object.keys(STRENGTH_TOKENS).sort()).toEqual(['fair', 'good', 'strong', 'weak'])

    const labels = new Set<string>()
    for (const level of Object.keys(STRENGTH_TOKENS) as StrengthLevel[]) {
      const token = STRENGTH_TOKENS[level]
      // The meter must never rely on colour alone, so a bar class, a text class
      // and a human label all travel together.
      expect(token.bar.trim()).not.toBe('')
      expect(token.text.trim()).not.toBe('')
      expect(token.label.trim()).not.toBe('')
      labels.add(token.label)
    }
    expect(labels.size).toBe(4)
  })

  it('produces only levels that have a token entry, across a spread of inputs', () => {
    const corpus = [
      '',
      'a',
      'aaaaaaaa',
      'Aaaaaaaa',
      'abcdefgA',
      'aA1abcde',
      'aA1!aA1!',
      'Passw0rd!',
      'abcdefgA1!xy',
      'Str0ng!passphrase!',
      'aB1!cD2@eF3#gH4$i',
      'Ab²cdefg!',
      'Äbcdefg1!',
    ]

    const seen = new Set<StrengthLevel>()
    for (const value of corpus) {
      const result = scorePassword(value)
      seen.add(result.level)
      expect(result.score).toBeGreaterThanOrEqual(0)
      expect(result.score).toBeLessThanOrEqual(MAX_STRENGTH_SCORE)
      expect(STRENGTH_TOKENS[result.level]).toBeDefined()
    }

    // Guards the table above: if the level union grew, this fails until
    // STRENGTH_TOKENS is extended to cover it.
    expect([...seen].sort()).toEqual(['fair', 'good', 'strong', 'weak'])
  })
})