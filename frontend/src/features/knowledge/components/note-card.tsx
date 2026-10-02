import { History } from 'lucide-react'

import { useTags } from '@/features/work/hooks'
import { cn } from '@/lib/utils'
import { formatRelative } from '@/types/knowledge'
import type { Note } from '@/types/knowledge'

import { NoteStatusBadge } from './knowledge-badges'

export interface NoteCardProps {
  note: Note
  onOpen?: (note: Note) => void
  /** Single-line density, for list rows rather than the index grid. */
  compact?: boolean
  selected?: boolean
  className?: string
}

const MAX_TAG_CHIPS = 3

/**
 * A note in an index: title, status, tags, when it last changed and how much
 * history stands behind it. `revision_count` is read from the list response
 * rather than fetched per card — N cards would otherwise cost N extra requests
 * for a number the list already carries.
 */
export function NoteCard({ note, onOpen, compact = false, selected, className }: NoteCardProps) {
  // Tag ids arrive without names, so the card resolves them from the shared tag
  // query — one cached request for the whole list, not one per card.
  const { data: tagPage } = useTags()
  const tagNames = (tagPage?.items ?? [])
    .filter((tag) => note.tag_ids.includes(tag.id))
    .map((tag) => tag.name)

  return (
    <article
      className={cn(
        'group rounded-lg border bg-card transition-colors',
        selected ? 'border-primary' : 'border-border hover:border-primary/50',
        compact ? 'flex items-center gap-3 px-3 py-2' : 'space-y-2 p-3',
        className,
      )}
    >
      <div className={cn('min-w-0 flex-1', compact && 'flex items-center gap-3')}>
        <div className="min-w-0 flex-1">
          {onOpen ? (
            <button
              type="button"
              onClick={() => onOpen(note)}
              className="block w-full truncate text-left text-sm font-medium text-foreground hover:underline focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {note.title}
            </button>
          ) : (
            <p className="truncate text-sm font-medium text-foreground">{note.title}</p>
          )}

          {!compact && note.summary && (
            <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {note.summary}
            </p>
          )}

          <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span title={note.updated_at}>
              Updated {formatRelative(note.updated_at)}
            </span>
            {note.revision_count > 0 && (
              <span className="flex items-center gap-1" title="Stored revisions">
                <History aria-hidden="true" className="size-3" />
                {note.revision_count}
              </span>
            )}
          </p>

          {!compact && tagNames.length > 0 && (
            <ul className="mt-1 flex flex-wrap gap-1">
              {tagNames.slice(0, MAX_TAG_CHIPS).map((name) => (
                <li
                  key={name}
                  className="rounded border border-border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                >
                  {name}
                </li>
              ))}
              {tagNames.length > MAX_TAG_CHIPS && (
                <li className="px-1 py-0.5 text-[11px] text-muted-foreground">
                  +{tagNames.length - MAX_TAG_CHIPS}
                </li>
              )}
            </ul>
          )}
        </div>

        <div className={cn('flex items-center gap-2', compact && 'shrink-0')}>
          <NoteStatusBadge status={note.status} size="sm" />
        </div>
      </div>
    </article>
  )
}
