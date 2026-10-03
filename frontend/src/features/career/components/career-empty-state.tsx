import type { ReactNode } from 'react'
import {
  Award,
  Contact,
  FileStack,
  Filter,
  Images,
  type LucideIcon,
  UserRound,
} from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/career/components/career-vocabulary'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'

/**
 * The "nothing to show" states for the career surface.
 *
 * **Every description says why the region is empty *and* what fills it**, and
 * here that rule has an extra edge: the only thing that can fill a career
 * surface is something the user supplies. So no empty copy here promises that
 * NEXUS will find a qualification, infer a job title or draft a summary — each
 * sentence names the action the *person* takes. A page that said "your
 * achievements will appear here once NEXUS finds them" would be the exact
 * fabrication the phase forbids.
 *
 * Two variants are cold-start states rather than insufficiency states and say so
 * with their own titles: `profile` is "no profile exists yet", which is a 404 on
 * the read and the target of the upsert, and `records` is an account that has not
 * listed anything. A title reading "Not enough data yet" would imply the account
 * was missing something rather than having not started.
 *
 * `filtered` is the opposite of empty — the records exist and the filter is what
 * hides them — and conflating the two is how a filtered list comes to read as an
 * empty profile.
 */
export type CareerEmptyVariant =
  | 'profile'
  | 'records'
  | 'evidence'
  | 'skills'
  | 'development'
  | 'summary'
  | 'filtered'

interface CareerEmptyCopy {
  icon: LucideIcon
  title: string
  description: string
}

const COPY: Record<CareerEmptyVariant, CareerEmptyCopy> = {
  profile: {
    icon: Contact,
    title: 'No career profile yet',
    description:
      'A profile is entirely your own: a target role, a domain, a one-line headline and any ' +
      'links you want on it. Nothing on it is written for you — NEXUS has no opinion about what ' +
      'you are aiming at and will not guess at one.',
  },
  records: {
    icon: FileStack,
    title: 'No education, experience or certifications listed yet',
    description:
      'These are the dated records a profile is made of: where you studied, the roles you have ' +
      'held, the certifications you hold. You add them, with the dates you know, and NEXUS stores ' +
      'them exactly as given.',
  },
  evidence: {
    icon: Images,
    title: 'No evidence added yet',
    description:
      'Evidence is what you want to point at: a project, a feature, a repository’s recorded ' +
      'activity, a certification or an achievement. Add one with a date, and it appears in the ' +
      'timeline grouped by kind.',
  },
  skills: {
    icon: UserRound,
    title: 'No skills to show yet',
    description:
      'This panel reads the skills you have tracked. Add one on the learning page with the level ' +
      'you claim for it, and it appears here with that level and where it came from.',
  },
  development: {
    icon: Award,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'This panel lists skills where you have set a target above the current level and there is ' +
      'little recorded evidence behind them. With no such skills it has nothing to say — which is ' +
      'not the same as saying every skill is well evidenced.',
  },
  summary: {
    icon: FileStack,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'The counts above are read from the records on this profile. They stay at nothing until ' +
      'there is at least one, because a dashboard of zeroes reads as a measurement and is not one.',
  },
  filtered: {
    icon: Filter,
    title: 'Nothing matches this filter',
    description: 'These records exist; none of them match what is selected. Clearing the filter shows them again.',
  },
}

export interface CareerEmptyStateProps {
  variant: CareerEmptyVariant
  /** Replaces the built-in description — the backend's own reason, verbatim. */
  reason?: string | null
  action?: ReactNode
  compact?: boolean
  className?: string
}

/**
 * One "nothing here yet" state, with the reason it is nothing here.
 *
 * `reason` wins over the built-in copy whenever the backend supplied one: it
 * names the specific thing that was missing, which is the part a generic
 * sentence cannot carry.
 */
export function CareerEmptyState({
  variant,
  reason,
  action,
  compact = false,
  className,
}: CareerEmptyStateProps) {
  const copy = COPY[variant]
  const useReason = typeof reason === 'string' && reason.trim().length > 0

  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      description={useReason ? reason : copy.description}
      action={action}
      compact={compact}
      className={className}
    />
  )
}

export interface CareerRegionErrorProps {
  error: ApiError
  onRetry?: () => void
  /** What failed, in a sentence the page can complete: "the career profile". */
  subject: string
  compact?: boolean
  className?: string
}

/**
 * The failed-read state for one region.
 *
 * `subject` is per-region on purpose: a profile that failed to load and an
 * evidence timeline that failed to load are different failures, and a reader
 * told "the career data could not be loaded" learns nothing about what to retry.
 * `ErrorState` renders the backend's user-safe message and the request id, and
 * never a stack trace — this supplies only the title.
 */
export function CareerRegionError({
  error,
  onRetry,
  subject,
  compact = false,
  className,
}: CareerRegionErrorProps) {
  return (
    <ErrorState
      error={error}
      onRetry={onRetry}
      title={`${subject} could not be loaded`}
      compact={compact}
      className={className}
    />
  )
}

export interface CareerStaleNoticeProps {
  /** True while a background refetch is in flight behind figures already on screen. */
  isStale: boolean
  /** What is being refetched, e.g. "the evidence timeline". */
  subject?: string
  className?: string
}

/**
 * The "these figures are from the previous read" line.
 *
 * **It renders nothing at all when `isStale` is false** — not a hidden element,
 * not an empty string — so a settled page has no residue from a state it has
 * already left. `aria-live="polite"` so a screen reader hears the settling rather
 * than being interrupted mid-sentence by the change itself.
 */
export function CareerStaleNotice({
  isStale,
  subject = 'these figures',
  className,
}: CareerStaleNoticeProps) {
  if (!isStale) return null

  return (
    <p aria-live="polite" className={cn('flex items-center gap-1.5 text-xs text-muted-foreground', className)}>
      <Contact aria-hidden="true" className="size-3 animate-pulse" />
      Refreshing {subject}. The values shown are from the last completed read.
    </p>
  )
}