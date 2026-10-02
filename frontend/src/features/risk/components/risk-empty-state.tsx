import type { ReactNode } from 'react'
import { Filter, Lightbulb, ShieldCheck } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'

/**
 * The three "there is nothing here" states for the risk surface.
 *
 * **`EmptyAnalytics` is not reused, and the reason matters.** That component is
 * the right answer for an analytics surface, where an empty chart means *the
 * measurement could not be made* — there were no completed tasks, so there is
 * no completion rate. Its title says exactly that: "Not enough activity yet".
 * A Risk Center with nothing in it is the opposite claim. The engine ran, read
 * the recorded data across all six detectors and found no condition worth
 * reporting, and the brief asks that to be stated as a result, not as a gap.
 * Printing the analytics copy here would tell a new user their account is
 * missing something it is not missing, which is both wrong and the more
 * alarming of the two sentences.
 *
 * What is reused is the layout — `EmptyState` itself, the same primitive
 * `EmptyAnalytics` renders — so the two surfaces cannot drift apart visually.
 * Only the words are this surface's own, because only the words are a different
 * claim.
 *
 * **Every sentence says why the surface is empty and what fills it**, which is
 * the rule `EmptyState` documents. "No risks" alone reads as a broken page;
 * "no risks, and here is what the engine looks at" reads as an answer.
 *
 * The tone throughout is neutral and factual. A quiet screen is described as
 * quiet: no congratulation the data did not earn, and no warning the data does
 * not support.
 */

export type RiskEmptyVariant = 'risks' | 'recommendations' | 'filtered'

const COPY: Record<
  RiskEmptyVariant,
  { icon: typeof ShieldCheck; title: string; description: string }
> = {
  risks: {
    icon: ShieldCheck,
    title: 'No significant risk detected yet',
    description:
      'The engine checks deadlines, workload, estimates, project signals and recorded activity on every run, and reports a finding only when the recorded data shows a gap. Finding nothing is a normal result — it means the data so far does not point at a condition worth acting on.',
  },
  recommendations: {
    icon: Lightbulb,
    title: 'No suggestions open',
    description:
      'Suggestions are raised from a detected risk, one per condition, and they close when the condition closes. Nothing here means no rule currently has something to propose.',
  },
  filtered: {
    icon: Filter,
    title: 'Nothing matches this filter',
    description:
      'The records exist, but none of them match what is selected. Clearing the filter shows them again.',
  },
}

export interface RiskEmptyStateProps {
  /**
   * `risks` and `recommendations` are the "nothing has been found" states;
   * `filtered` is the "something exists, just not here" state and says so,
   * because conflating the two is how a filtered list reads as an empty
   * account.
   */
  variant?: RiskEmptyVariant
  /** Replaces the built-in description — e.g. the backend's own reason. */
  reason?: string | null
  action?: ReactNode
  compact?: boolean
  className?: string
}

export function RiskEmptyState({
  variant = 'risks',
  reason,
  action,
  compact = false,
  className,
}: RiskEmptyStateProps) {
  const copy = COPY[variant]
  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      description={reason && reason.trim().length > 0 ? reason : copy.description}
      action={action}
      compact={compact}
      className={className}
    />
  )
}
