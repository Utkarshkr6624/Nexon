import type { ReactNode } from 'react'
import {
  Bookmark,
  FolderTree,
  Lightbulb,
  Link2,
  Notebook,
  SearchX,
  Share2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'

export type EmptyKnowledgeKind =
  | 'notes'
  | 'matches'
  | 'search'
  | 'concepts'
  | 'resources'
  | 'bookmarks'
  | 'documents'
  | 'categories'
  | 'backlinks'
  | 'links'
  | 'revisions'
  | 'graph'

const COPY: Record<EmptyKnowledgeKind, { icon: LucideIcon; title: string; description: string }> =
  {
    notes: {
      icon: Notebook,
      title: 'No notes yet',
      description:
        'A note is a Markdown document with links into concepts, resources and other notes. Write the first one.',
    },
    matches: {
      icon: SearchX,
      title: 'No notes match these filters',
      description:
        'The status filter or the search term is narrowing the list to nothing. Clear one of them to see your notes again.',
    },
    search: {
      icon: SearchX,
      title: 'Nothing found',
      description: 'No note, concept, resource or bookmark matches this term. Try a shorter one.',
    },
    concepts: {
      icon: Lightbulb,
      title: 'No concepts yet',
      description:
        'Concepts are the named ideas notes explain and resources support — the vocabulary your knowledge base is organised around.',
    },
    resources: {
      icon: Share2,
      title: 'No resources yet',
      description:
        'Resources are the external things worth citing: articles, papers, videos and repositories you want a note to point at.',
    },
    bookmarks: {
      icon: Bookmark,
      title: 'No bookmarks yet',
      description:
        'Bookmarks are links you saved to come back to. They keep the address and the title, so they stay readable offline.',
    },
    documents: {
      icon: FolderTree,
      title: 'No documents yet',
      description:
        'Documents are file metadata a note can cite as its source. Nothing has been registered here yet.',
    },
    categories: {
      icon: FolderTree,
      title: 'No categories yet',
      description: 'Categories nest to group things by area. Create a top-level one to start.',
    },
    backlinks: {
      icon: Link2,
      title: 'Nothing references this yet',
      description:
        'When another note links here, it appears in this list. That is the direction a note is usually pointed from, so it fills last.',
    },
    links: {
      icon: Link2,
      title: 'This links to nothing yet',
      description:
        'Link this object to a concept, a resource or another note to put it in the graph.',
    },
    revisions: {
      icon: Notebook,
      title: 'No earlier versions',
      description:
        'A revision is written when a note is edited. Once there is history, the oldest entries can be restored from here.',
    },
    graph: {
      icon: Link2,
      title: 'The graph is empty',
      description:
        'The graph is drawn from the links between your notes, concepts and resources. Create one, then link two objects together.',
    },
  }

/**
 * Distinguishes "you have nothing" from "your filters match nothing" — the two
 * need different next moves, and collapsing them makes an empty list read as a
 * bug rather than as a state.
 */
export function EmptyKnowledge({
  kind,
  action,
  className,
  compact = false,
}: {
  kind: EmptyKnowledgeKind
  action?: ReactNode
  className?: string
  /** Inline variant for a panel that is already framed by its container. */
  compact?: boolean
}) {
  const copy = COPY[kind]
  return (
    <EmptyState
      icon={copy.icon}
      title={copy.title}
      description={copy.description}
      action={action}
      compact={compact}
      className={className ?? (compact ? undefined : 'rounded-lg border border-dashed border-border')}
    />
  )
}
