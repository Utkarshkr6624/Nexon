/**
 * A coarse strength indicator for the password field.
 *
 * This is a UI affordance that helps someone while they type — it is not an
 * entropy estimator and it carries no cryptographic authority whatsoever. It
 * does not read a dictionary, does not count bits, and does not know about
 * reuse across sites. A "strong" reading here means the value clears the
 * policy and is not obviously a repeated run of one character; nothing more.
 * The server decides what is actually accepted.
 *
 * The score is deliberately explainable rather than clever: one point per
 * satisfied policy rule, plus small bonuses for length and variety, minus a
 * penalty for a repeated run. Every term is something a person can check by
 * looking at the field, which is the only property worth having in a meter.
 */

import { PASSWORD_MIN_LENGTH, PASSWORD_RULES, evaluatePasswordRules } from './password-rules'

export type StrengthLevel = 'weak' | 'fair' | 'good' | 'strong'

export interface StrengthResult {
  /** 0-10. Drives the meter width; the level is what gets shown as text. */
  score: number
  level: StrengthLevel
  /** How many policy rules are currently met, and how many exist. */
  satisfied: number
  total: number
  /** The next thing worth fixing, or null when nothing is left to suggest. */
  nextHint: string | null
}

/** One point per satisfied rule, so the policy is most of the score. */
const RULE_POINTS = 5
/** Extra characters past the policy minimum, one point every step. */
const LENGTH_BONUS_MAX = 3
const LENGTH_BONUS_STEP = 4
/** Distinct characters, not distinct character classes: the policy already fixes
 * the classes, so the only variety left to reward is a value that is not a
 * handful of symbols rearranged. */
const VARIETY_BONUS_MAX = 2
const VARIETY_BONUS_STEP = 10
/** A value in which one character makes up this share of the whole is a run. */
const REPETITION_SHARE = 0.4
const REPETITION_MIN_LENGTH = 6

export const MAX_STRENGTH_SCORE = RULE_POINTS + LENGTH_BONUS_MAX + VARIETY_BONUS_MAX

/** Highest level first, so the first threshold the score clears wins. */
const LEVEL_THRESHOLDS: readonly { readonly min: number; readonly level: StrengthLevel }[] = [
  { min: 8, level: 'strong' },
  { min: 5, level: 'good' },
  { min: 3, level: 'fair' },
  { min: 0, level: 'weak' },
]

const LEVEL_ORDER: readonly StrengthLevel[] = ['weak', 'fair', 'good', 'strong']

const VARIETY_TIP = 'Longer is harder to guess — a passphrase beats a short word.'

/**
 * Token classes, not raw colours, so the meter follows the active theme in
 * both light and dark. These are plain class strings rather than a `cva` map
 * because they are values in a record, not a component variant.
 */
export const STRENGTH_TOKENS: Record<StrengthLevel, { bar: string; text: string; label: string }> =
  {
    weak: { bar: 'bg-destructive', text: 'text-destructive', label: 'Weak' },
    fair: { bar: 'bg-warning', text: 'text-warning', label: 'Fair' },
    good: { bar: 'bg-primary', text: 'text-primary', label: 'Good' },
    strong: { bar: 'bg-success', text: 'text-success', label: 'Strong' },
  }

function varietyBonus(value: string): number {
  const distinct = new Set(value).size
  if (distinct >= VARIETY_BONUS_STEP * VARIETY_BONUS_MAX) return VARIETY_BONUS_MAX
  if (distinct >= VARIETY_BONUS_STEP) return 1
  return 0
}

/** One point off for `aaaaaa` and friends. Absent in `Passw0rd!`, present in every run. */
function repetitionPenalty(value: string): number {
  const length = Array.from(value).length
  if (length < REPETITION_MIN_LENGTH) return 0

  const counts = new Map<string, number>()
  let most = 0
  for (const char of value) {
    const next = (counts.get(char) ?? 0) + 1
    counts.set(char, next)
    if (next > most) most = next
  }

  return most / length >= REPETITION_SHARE ? 1 : 0
}

function levelFor(score: number): StrengthLevel {
  for (const threshold of LEVEL_THRESHOLDS) {
    if (score >= threshold.min) return threshold.level
  }
  return 'weak'
}

/**
 * The highest level the policy can back. Length and variety can carry a short
 * value over a threshold, but a meter that says "strong" about a password the
 * server will reject is worse than no meter at all — the bonuses only get to
 * colour a reading, never to raise one.
 */
function policyCap(satisfied: number, total: number): StrengthLevel {
  if (satisfied >= total) return 'strong'
  return total - satisfied <= 1 ? 'good' : 'fair'
}

function capped(level: StrengthLevel, cap: StrengthLevel): StrengthLevel {
  const index = Math.min(LEVEL_ORDER.indexOf(level), LEVEL_ORDER.indexOf(cap))
  return LEVEL_ORDER[index] ?? 'weak'
}

/** The first unmet rule's requirement, or a tip once the policy is satisfied. */
function nextHint(level: StrengthLevel, results: ReturnType<typeof evaluatePasswordRules>): string | null {
  const unmet = results.find((result) => !result.satisfied)
  if (unmet) {
    const rule = PASSWORD_RULES.find((candidate) => candidate.id === unmet.id)
    return rule?.hint ?? unmet.label
  }
  return level === 'strong' ? null : VARIETY_TIP
}

/** Score a candidate password for display only. Pure and cheap enough to run per keystroke. */
export function scorePassword(value: string): StrengthResult {
  const results = evaluatePasswordRules(value)
  const satisfied = results.filter((result) => result.satisfied).length
  const total = results.length

  const length = Array.from(value).length
  const beyondPolicy = Math.max(0, length - PASSWORD_MIN_LENGTH)

  const raw =
    satisfied +
    Math.min(LENGTH_BONUS_MAX, Math.floor(beyondPolicy / LENGTH_BONUS_STEP)) +
    varietyBonus(value) -
    repetitionPenalty(value)

  const score = Math.max(0, Math.min(MAX_STRENGTH_SCORE, raw))
  const level = capped(levelFor(score), policyCap(satisfied, total))

  return {
    score,
    level,
    satisfied,
    total: results.length,
    nextHint: nextHint(level, results),
  }
}
