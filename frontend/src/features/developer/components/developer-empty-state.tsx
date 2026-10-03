import type { ReactNode } from 'react'
import {
  Activity,
  CircleAlert,
  Filter,
  GitBranch,
  GitCommitVertical,
  Languages,
  ScanLine,
  type LucideIcon,
} from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { NOT_ENOUGH_DATA_TITLE } from '@/features/developer/components/developer-vocabulary'
import { cn } from '@/lib/utils'

/**
 * The "nothing to show" states for the developer surface.
 *
 * **Every description says why the region is empty *and* what fills it.** A bare
 * "No commits" reads as a broken page; "no commits, and commits appear when a
 * scan reads the repository" reads as an answer. That is the rule `EmptyState`
 * documents and this module is where it is discharged — one variant per region,
 * each with its own sentence, because a missing commit history and a missing
 * branch list are filled by completely different things.
 *
 * **`EmptyAnalytics` is deliberately not reused, and the reason is the title.**
 * That component says "Not enough activity yet", which is the right claim for an
 * analytics panel where an empty chart means a measurement could not be made
 * *from task data*. This surface reads git, and its empty states are mostly
 * about *nothing having been recorded yet* — so the shared, contract-specified
 * wording "Not enough data yet." is used instead. What is reused is the layout:
 * `EmptyState` is the same primitive underneath, so the two surfaces cannot
 * drift apart visually.
 *
 * Two variants are not about insufficiency at all and say so with their own
 * titles. `repositories` is a cold-start state: nothing has been registered, and
 * a title reading "Not enough data yet" would imply the account was missing
 * something rather than having not started. `filtered` is the opposite of empty
 * — the records exist and the filter is what hides them — and conflating the two
 * is exactly how a filtered list comes to read as an empty account.
 *
 * The tone throughout is factual. Nothing here congratulates an empty state or
 * alarms at one: an empty commit timeline is an ordinary state for a repository
 * that has not been scanned yet.
 */
export type DeveloperEmptyVariant =
  | 'repositories'
  | 'commits'
  | 'metrics'
  | 'activity'
  | 'languages'
  | 'branches'
  | 'scanRuns'
  | 'filtered'

interface DeveloperEmptyCopy {
  icon: LucideIcon
  title: string
  description: string
}

const COPY: Record<DeveloperEmptyVariant, DeveloperEmptyCopy> = {
  repositories: {
    icon: ScanLine,
    title: 'No repositories registered yet',
    description:
      'Registering a local git work tree is what starts this. NEXUS reads the path with ' +
      'the git CLI on the machine it runs on — there is no hosted service and no account ' +
      'to connect — and everything it shows is read from what that scan records.',
  },
  commits: {
    icon: GitCommitVertical,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'The timeline is built from commits a scan read out of a registered repository. ' +
      'Register a work tree and run its first scan, and every commit it finds appears here.',
  },
  metrics: {
    icon: Activity,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'Each metric is computed from recorded commits and changed lines. Once a scan has ' +
      'read at least one commit, every metric below shows a value or the specific reason ' +
      'it could not be computed.',
  },
  activity: {
    icon: Activity,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'The series buckets commits day by day, week by week or month by month across the ' +
      'window. Every bucket in the range is plotted, including the empty ones, so a quiet ' +
      'stretch shows as zero commits rather than as a gap.',
  },
  languages: {
    icon: Languages,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'Languages are counted from the file extensions git tracks. A repository whose ' +
      'files use extensions outside the recognised set contributes nothing here — there is ' +
      'no "other" bucket, because that would be a category, not a language.',
  },
  branches: {
    icon: GitBranch,
    title: NOT_ENOUGH_DATA_TITLE,
    description:
      'Branches are read from the repository at scan time. A repository with no commits ' +
      'has no branches to list yet, and one on a detached HEAD still has them.',
  },
  scanRuns: {
    icon: CircleAlert,
    title: 'No scan has been run yet',
    description:
      'Every attempt to read a repository is recorded whatever its outcome. Run a scan and ' +
      'the run appears here with what it discovered, or with the sentence explaining why it ' +
      'could not read the directory.',
  },
  filtered: {
    icon: Filter,
    title: 'Nothing matches this filter',
    description:
      'The repositories exist and have been scanned; none of them match what is selected. ' +
      'Clearing the filter shows them again.',
  },
}

export interface DeveloperEmptyStateProps {
  variant: DeveloperEmptyVariant
  /** Replaces the built-in description — the backend's own reason, verbatim. */
  reason?: string | null
  action?: ReactNode
  compact?: boolean
  className?: string
}

/**
 * One "nothing here yet" state, with the reason it is nothing here.
 *
 * `reason` wins over the built-in copy whenever the backend supplied one, for the
 * same reason `EmptyAnalytics` renders it verbatim: it names the specific thing
 * that was missing, which is the part a generic sentence cannot carry.
 */
export function DeveloperEmptyState({
  variant,
  reason,
  action,
  compact = false,
  className,
}: DeveloperEmptyStateProps) {
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

export interface DeveloperStaleNoticeProps {
  /** True while a background refetch is in flight behind figures already on screen. */
  isStale: boolean
  /** What is being refetched, e.g. "the commit timeline". */
  subject?: string
  className?: string
}

/**
 * The "these figures are from the previous read" line.
 *
 * **It renders nothing at all when `isStale` is false** — not a hidden element,
 * not an empty string — so a settled page has no residue from a state it has
 * already left. While a refetch is in flight, the figures beneath it are real
 * and were real a moment ago; saying so is what stops the control from being
 * read as "these numbers are broken".
 *
 * `aria-live="polite"` so a screen reader hears the settling rather than being
 * interrupted mid-sentence by the change itself.
 */
export function DeveloperStaleNotice({
  isStale,
  subject = 'these figures',
  className,
}: DeveloperStaleNoticeProps) {
  if (!isStale) return null

  return (
    <p
      aria-live="polite"
      className={cn(
        'flex items-center gap-1.5 text-xs text-muted-foreground',
        className,
      )}
    >
      <Activity aria-hidden="true" className="size-3 animate-pulse" />
      Refreshing {subject}. The values shown are from the last completed read.
    </p>
  )
}