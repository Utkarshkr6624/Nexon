import type { ReactNode } from 'react'
import { ClipboardList, Columns3, ListFilter, SearchX } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'

export type EmptyWorkKind = 'tasks' | 'matches' | 'search' | 'board' | 'projects'

const COPY: Record<EmptyWorkKind, { icon: LucideIcon; title: string; description: string }> = {
  tasks: {
    icon: ClipboardList,
    title: 'No tasks yet',
    description:
      'Tasks live inside a project. Create one to start filling this list, or clear the filters to see everything you own.',
  },
  matches: {
    icon: SearchX,
    title: 'No tasks match these filters',
    description:
      'The filters are narrowing the list to nothing. Widen or clear one of them to see your work again.',
  },
  search: {
    icon: SearchX,
    title: 'Nothing found',
    description: 'No result for this search. Try a shorter or different term.',
  },
  board: {
    icon: Columns3,
    title: 'Nothing to place on the board',
    description: 'No task matches the current filters, so there are no columns to fill.',
  },
  projects: {
    icon: ListFilter,
    title: 'No projects',
    description: 'A task has to belong to a project, so the project comes first.',
  },
}

/**
 * Distinguishes "you have nothing" from "your filters match nothing" — the two
 * need different next moves, and collapsing them makes an empty list read as a
 * bug.
 */
export function EmptyWork({ kind, action }: { kind: EmptyWorkKind; action?: ReactNode }) {
  const copy = COPY[kind]
  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      description={copy.description}
      action={action}
      className="rounded-lg border border-dashed border-border"
    />
  )
}