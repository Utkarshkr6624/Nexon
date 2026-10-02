import type { ReactNode } from 'react'
import {
  Activity,
  BrainCircuit,
  CalendarClock,
  Clock,
  FolderKanban,
  GraduationCap,
  ListTodo,
  type LucideIcon,
  Sparkles,
} from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { cn } from '@/lib/utils'

/**
 * The metrics this surface can be empty about.
 *
 * Each one gets its own icon and its own sentence because "Not enough activity
 * yet" alone tells a reader nothing about *what* to do: a missing focus score
 * and a missing deadline rate are filled by completely different behaviour.
 */
export type AnalyticsMetricKey =
  | 'productivity'
  | 'consistency'
  | 'focus'
  | 'deadlines'
  | 'estimation'
  | 'workload'
  | 'time'
  | 'projects'
  | 'tasks'
  | 'learning'
  | 'knowledge'
  | 'trend'
  | 'heatmap'
  | 'overview'

interface MetricEmptyCopy {
  icon: LucideIcon
  /** What fills it, in one sentence. */
  description: string
}

const COPY: Record<AnalyticsMetricKey, MetricEmptyCopy> = {
  productivity: {
    icon: Sparkles,
    description:
      'The score weighs four things at once: completed work, deadlines met, focus held and estimates kept. Complete or schedule a task and it can be computed.',
  },
  consistency: {
    icon: Activity,
    description:
      'Recorded activity on at least part of the window is what this counts. A single completed task or work session is enough to start the streak.',
  },
  focus: {
    icon: Sparkles,
    description:
      'This score is built from work sessions, and needs at least one planned session run to completion. Nothing is inferred about attention.',
  },
  deadlines: {
    icon: CalendarClock,
    description:
      'Give a task a due date and finish it — the rate is on-time over everything finished. Until then there is nothing to be on time about.',
  },
  estimation: {
    icon: Clock,
    description:
      'Only tasks carrying both an estimate and tracked time are compared. Record an estimate on a task, then run a session against it.',
  },
  workload: {
    icon: ListTodo,
    description:
      'Workload is planned time against the hours you declared available. Add tasks to the plan, and declare availability in the Planner.',
  },
  time: {
    icon: Clock,
    description:
      'Time is read from finished work sessions in the window. Run or complete one and the breakdown appears here.',
  },
  projects: {
    icon: FolderKanban,
    description:
      'A project appears here once it has tasks or work sessions in the window. There is no figure to show for a project with neither.',
  },
  tasks: {
    icon: ListTodo,
    description:
      'Task throughput comes from recorded tasks: created, completed, blocked and overdue. Create your first task to start the series.',
  },
  learning: {
    icon: GraduationCap,
    description:
      'Learning activity is measured from calendar events typed "study" and from recorded knowledge events. Neither has happened in this window.',
  },
  knowledge: {
    icon: BrainCircuit,
    description:
      'Counts come from notes, concepts, resources and links written in the window. Writing or linking something puts this on screen.',
  },
  trend: {
    icon: Activity,
    description:
      'A trend needs recorded days to plot. Each point is a day something happened, so an unrecorded stretch is a gap rather than a zero.',
  },
  heatmap: {
    icon: Activity,
    description: 'No day in this window has recorded activity yet.',
  },
  overview: {
    icon: Activity,
    description:
      'Every figure on this tab is computed from recorded tasks, sessions and knowledge events. There is nothing recorded in this window yet.',
  },
}

export interface EmptyAnalyticsProps {
  metric: AnalyticsMetricKey
  /**
   * The backend's `reason_if_unavailable`, rendered verbatim when present. It is
   * the authority on why a number is absent; the copy above is only the
   * fallback for the surfaces that have no such field.
   */
  reason?: string | null
  action?: ReactNode
  compact?: boolean
  className?: string
}

/**
 * The single "not enough activity yet" state.
 *
 * **`reason` is rendered word for word.** The backend wrote it to say what it
 * looked for and did not find, and paraphrasing that into a generic sentence
 * throws away the most useful part — which specific ingredient was missing.
 */
export function EmptyAnalytics({
  metric,
  reason,
  action,
  compact = true,
  className,
}: EmptyAnalyticsProps) {
  const copy = COPY[metric]
  return (
    <EmptyState
      icon={copy.icon}
      title="Not enough activity yet"
      description={reason && reason.trim().length > 0 ? reason : copy.description}
      action={action}
      compact={compact}
      className={cn('h-full', className)}
    />
  )
}