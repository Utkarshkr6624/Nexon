import { Check, Circle } from 'lucide-react'
import type { JSX } from 'react'

import { cn } from '@/lib/utils'
import type { RuleResult } from '../password-rules'

export interface PasswordRulesChecklistProps {
  results: RuleResult[]
  className?: string
}

/**
 * The live policy checklist under a password field.
 *
 * The icon *changes shape* between a check and a hollow circle, and not only
 * its colour: colour alone is invisible to anyone with a colour vision
 * deficiency, to a screen in sunlight, and to a person reading the list in
 * greyscale. The shape change is the accessible signal; the success and muted
 * tokens only reinforce it. The `sr-only` suffix carries the same state
 * non-visually, so the list is not two contradictory channels.
 */
export function PasswordRulesChecklist({
  results,
  className,
}: PasswordRulesChecklistProps): JSX.Element {
  return (
    <ul className={cn('flex flex-col gap-0.5', className)}>
      {results.map((result) => (
        <li
          key={result.id}
          className={cn(
            'flex items-center gap-2 text-xs leading-5 transition-colors duration-150 ease-out',
            result.satisfied ? 'text-success' : 'text-muted-foreground',
          )}
        >
          {result.satisfied ? (
            <Check className="size-3.5 shrink-0" strokeWidth={2.5} aria-hidden="true" />
          ) : (
            <Circle className="size-3.5 shrink-0 opacity-60" strokeWidth={1.75} aria-hidden="true" />
          )}
          <span>
            {result.label}
            <span className="sr-only">{result.satisfied ? ' — met' : ' — not met yet'}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}
