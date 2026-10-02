import { CornerDownRight, Link2, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { Button } from '@/components/ui/button'
import {
  useBacklinks,
  useConcepts,
  useDeleteLink,
  useNotes,
  useResources,
} from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { cn } from '@/lib/utils'
import { KNOWLEDGE_ENTITY_META } from '@/types/knowledge'
import type { KnowledgeEntityType, UUIDString } from '@/types/knowledge'

import { EmptyKnowledge } from './empty-knowledge'
import { EntityTypeBadge, LinkTypeBadge } from './knowledge-badges'

export interface BacklinksPanelProps {
  entityType: KnowledgeEntityType
  entityId: UUIDString
  /** Navigates to the referring object. Without it the row is still readable. */
  onOpenEntity?: (type: KnowledgeEntityType, id: UUIDString) => void
  className?: string
}

/** How many rows of each kind to pull when resolving a source's label. The
 *  backend caps a page at 100 and the backlink page is itself bounded, so this
 *  resolves the overwhelming majority without a per-id endpoint (there is no
 *  per-id link source route to ask). Anything unresolved degrades to its id. */
const LABEL_LOOKUP_LIMIT = 100

/**
 * "Referenced by …" — the direction that makes this a knowledge base rather
 * than a notes app, and the direction a note is far more often pointed *from*.
 *
 * A backlink carries an id, not a title, and there is no route that resolves an
 * edge's source into its row, so labels come from the three list queries the
 * surrounding page already runs. Unresolved sources still render, with their
 * type and a truncated id — a row that names its object is better than a panel
 * that quietly omits half of what points here.
 */
export function BacklinksPanel({
  entityType,
  entityId,
  onOpenEntity,
  className,
}: BacklinksPanelProps) {
  const { data, isPending, error, refetch } = useBacklinks(entityType, entityId)
  const { data: notes } = useNotes({ limit: LABEL_LOOKUP_LIMIT })
  const { data: concepts } = useConcepts({ limit: LABEL_LOOKUP_LIMIT })
  const { data: resources } = useResources({ limit: LABEL_LOOKUP_LIMIT })
  const removeLink = useDeleteLink()

  const labels = new Map<string, string>()
  for (const note of notes?.items ?? []) labels.set(`note:${note.id}`, note.title)
  for (const concept of concepts?.items ?? []) labels.set(`concept:${concept.id}`, concept.name)
  for (const resource of resources?.items ?? []) labels.set(`resource:${resource.id}`, resource.title)

  if (isPending) {
    return <LoadingState compact label="Finding what references this" />
  }

  if (error) {
    return (
      <ErrorState
        compact
        error={toApiError(error)}
        onRetry={() => void refetch()}
        title="The backlinks could not be loaded"
      />
    )
  }

  const backlinks = data?.items ?? []
  const total = data?.meta.total ?? 0

  if (backlinks.length === 0) {
    return <EmptyKnowledge kind="backlinks" compact />
  }

  return (
    <div className={cn('space-y-2', className)}>
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CornerDownRight aria-hidden="true" className="size-3.5" />
        Referenced by {total} {total === 1 ? 'object' : 'objects'}
      </p>

      <ul className="space-y-1.5">
        {backlinks.map((link) => {
          const label = labels.get(`${link.source_type}:${link.source_id}`)
          return (
            <li
              key={link.id}
              className="flex items-start justify-between gap-2 rounded-md border border-border px-2.5 py-1.5"
            >
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <LinkTypeBadge type={link.link_type} size="sm" />
                  <EntityTypeBadge type={link.source_type} size="sm" />
                </div>
                {onOpenEntity ? (
                  <button
                    type="button"
                    onClick={() => onOpenEntity(link.source_type, link.source_id)}
                    className="block max-w-full truncate text-left text-sm text-foreground hover:underline focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {label ?? `${link.source_id.slice(0, 8)}…`}
                  </button>
                ) : (
                  <p className="truncate text-sm text-foreground">
                    {label ?? `${link.source_id.slice(0, 8)}…`}
                  </p>
                )}
                {!label && (
                  <p className="text-[11px] text-muted-foreground">
                    {KNOWLEDGE_ENTITY_META[link.source_type].label} outside the loaded page
                  </p>
                )}
              </div>

              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                title="Remove this link"
                onClick={async () => {
                  try {
                    await removeLink.mutateAsync(link.id)
                    toast.success('Link removed')
                  } catch (cause) {
                    toast.error('Could not remove that link', toApiError(cause).message)
                  }
                }}
              >
                <Trash2 aria-hidden="true" className="size-3.5" />
                <span className="sr-only">Remove link</span>
              </Button>
            </li>
          )
        })}
      </ul>

      {total > backlinks.length && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Link2 aria-hidden="true" className="size-3" />
          Showing {backlinks.length} of {total}.
        </p>
      )}
    </div>
  )
}
