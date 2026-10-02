import { useEffect, useMemo, useState } from 'react'
import { Link2, Plus, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import {
  useConcepts,
  useCreateLink,
  useDeleteLink,
  useNotes,
  useOutboundLinks,
  useResources,
} from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { cn } from '@/lib/utils'
import { KNOWLEDGE_ENTITY_TYPES, KNOWLEDGE_ENTITY_META, KNOWLEDGE_LINK_TYPES, LINK_TYPE_META } from '@/types/knowledge'
import type {
  KnowledgeEntityType,
  KnowledgeLinkType,
  UUIDString,
} from '@/types/knowledge'

import { EmptyKnowledge } from './empty-knowledge'
import { EntityTypeBadge, LinkTypeBadge } from './knowledge-badges'

export interface LinkEditorProps {
  /** The object links leave from. Fixed: this editor creates *outbound* edges. */
  sourceType: KnowledgeEntityType
  sourceId: UUIDString
  onOpenEntity?: (type: KnowledgeEntityType, id: UUIDString) => void
  className?: string
}

interface Target {
  id: UUIDString
  label: string
}

/** Bounded target search. Each kind is one bounded query server-side, so the
 *  picker pages rather than asking for "everything" — and the filter below is
 *  client-side over that page, which is honest about being a filter. */
const TARGET_PAGE = 50
const FILTER_DEBOUNCE_MS = 200

export function LinkEditor({
  sourceType,
  sourceId,
  onOpenEntity,
  className,
}: LinkEditorProps) {
  const [targetType, setTargetType] = useState<KnowledgeEntityType>('note')
  const [linkType, setLinkType] = useState<KnowledgeLinkType>('references')
  const [term, setTerm] = useState('')
  const [filter, setFilter] = useState('')
  const [targetId, setTargetId] = useState<UUIDString | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => setFilter(term.trim()), FILTER_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [term])

  const notes = useNotes({ limit: TARGET_PAGE, search: filter || undefined })
  const concepts = useConcepts({ limit: TARGET_PAGE, search: filter || undefined })
  const resources = useResources({ limit: TARGET_PAGE, search: filter || undefined })

  const candidates = useMemo<Target[]>(() => {
    const rows =
      targetType === 'note'
        ? (notes.data?.items ?? []).map((note) => ({ id: note.id, label: note.title }))
        : targetType === 'concept'
          ? (concepts.data?.items ?? []).map((concept) => ({ id: concept.id, label: concept.name }))
          : (resources.data?.items ?? []).map((resource) => ({ id: resource.id, label: resource.title }))
    return rows.filter((row) => row.id !== sourceId)
  }, [targetType, notes.data, concepts.data, resources.data, sourceId])

  const outbound = useOutboundLinks({ source_type: sourceType, source_id: sourceId })
  const createLink = useCreateLink()
  const deleteLink = useDeleteLink()

  const targetQuery = targetType === 'note' ? notes : targetType === 'concept' ? concepts : resources

  return (
    <div className={cn('space-y-4', className)}>
      <div className="space-y-3 rounded-lg border border-border p-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="app-form-field">
            <label htmlFor="link-target-type" className="text-xs font-medium text-foreground">
              Link to a
            </label>
            <Select
              id="link-target-type"
              value={targetType}
              onChange={(event) => {
                setTargetType(event.target.value as KnowledgeEntityType)
                // A selection made under one kind cannot survive a switch to
                // another, so the chosen target is dropped rather than silently
                // pointing the new edge at an id from the old list.
                setTargetId(null)
              }}
            >
              {KNOWLEDGE_ENTITY_TYPES.map((type) => (
                <option key={type} value={type}>
                  {KNOWLEDGE_ENTITY_META[type].label}
                </option>
              ))}
            </Select>
          </div>

          <div className="app-form-field">
            <label htmlFor="link-type" className="text-xs font-medium text-foreground">
              Relationship
            </label>
            <Select
              id="link-type"
              value={linkType}
              onChange={(event) => setLinkType(event.target.value as KnowledgeLinkType)}
            >
              {KNOWLEDGE_LINK_TYPES.map((type) => (
                <option key={type} value={type}>
                  {LINK_TYPE_META[type].label}
                </option>
              ))}
            </Select>
            <p className="app-form-hint">{LINK_TYPE_META[linkType].description}</p>
          </div>
        </div>

        <div className="app-form-field">
          <label htmlFor="link-target-search" className="text-xs font-medium text-foreground">
            Find a {KNOWLEDGE_ENTITY_META[targetType].label.toLowerCase()}
          </label>
          <Input
            id="link-target-search"
            value={term}
            placeholder="Type to search"
            onChange={(event) => setTerm(event.target.value)}
          />
        </div>

        {targetQuery.isPending ? (
          <LoadingState compact label="Searching" />
        ) : candidates.length === 0 ? (
          <p className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
            {filter
              ? `No ${KNOWLEDGE_ENTITY_META[targetType].label.toLowerCase()} matches “${filter}”.`
              : `No ${KNOWLEDGE_ENTITY_META[targetType].label.toLowerCase()} available yet.`}
          </p>
        ) : (
          <ul className="max-h-48 space-y-1 overflow-y-auto" aria-label="Link targets">
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <button
                  type="button"
                  onClick={() => setTargetId(candidate.id)}
                  aria-pressed={targetId === candidate.id}
                  className={cn(
                    'block w-full truncate rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors',
                    targetId === candidate.id
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border hover:border-primary/50',
                  )}
                >
                  {candidate.label}
                </button>
              </li>
            ))}
          </ul>
        )}

        <Button
          type="button"
          size="sm"
          disabled={!targetId || createLink.isPending}
          onClick={async () => {
            if (!targetId) return
            try {
              await createLink.mutateAsync({
                source_type: sourceType,
                source_id: sourceId,
                target_type: targetType,
                target_id: targetId,
                link_type: linkType,
              })
              toast.success('Link created', LINK_TYPE_META[linkType].label)
              setTargetId(null)
            } catch (cause) {
              toast.error('Could not create that link', toApiError(cause).message)
            }
          }}
        >
          <Plus aria-hidden="true" className="size-3.5" />
          {createLink.isPending ? 'Linking…' : 'Create link'}
        </Button>
      </div>

      <div className="space-y-2">
        <h3 className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          Links out
        </h3>

        {outbound.isPending ? (
          <LoadingState compact label="Loading links" />
        ) : outbound.error ? (
          <ErrorState
            compact
            error={toApiError(outbound.error)}
            onRetry={() => void outbound.refetch()}
            title="The links could not be loaded"
          />
        ) : (outbound.data?.items.length ?? 0) === 0 ? (
          <EmptyKnowledge kind="links" compact />
        ) : (
          <ul className="space-y-1.5">
            {outbound.data?.items.map((link) => (
              <li
                key={link.id}
                className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <LinkTypeBadge type={link.link_type} size="sm" />
                  <EntityTypeBadge type={link.target_type} size="sm" />
                  <button
                    type="button"
                    onClick={() => onOpenEntity?.(link.target_type, link.target_id)}
                    className="truncate text-sm text-foreground hover:underline"
                  >
                    {link.target_id.slice(0, 8)}…
                  </button>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                  title="Remove this link"
                  onClick={async () => {
                    try {
                      await deleteLink.mutateAsync(link.id)
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
            ))}
          </ul>
        )}

        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Link2 aria-hidden="true" className="size-3" />
          Backlinks to this object are on the backlinks panel.
        </p>
      </div>
    </div>
  )
}
