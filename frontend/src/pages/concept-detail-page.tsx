import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Pencil, Tag, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  BacklinksPanel,
  ConceptFormDialog,
  EmptyKnowledge,
  LinkEditor,
} from '@/features/knowledge/components'
import { useConcepts, useDeleteConcept, useOutboundLinks } from '@/features/knowledge/hooks'
import { ConfirmDialog } from '@/features/work/components'
import { useTags } from '@/features/work/hooks'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import {
  MAX_PAGE_SIZE,
  formatKnowledgeDateTime,
  formatRelative,
  renderMarkdown,
} from '@/types/knowledge'
import type { KnowledgeEntityType, KnowledgeLink, UUIDString } from '@/types/knowledge'

/** Same measure and rhythm as the note reader — one reading surface, not two. */
const READING_MEASURE = 'max-w-[68ch]'

const PROSE = [
  'text-[1.0625rem] leading-[1.75] text-foreground',
  '[&_p]:my-4 [&_p:first-of-type]:mt-0',
  '[&_ul]:my-4 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-6',
  '[&_ol]:my-4 [&_ol]:list-decimal [&_ol]:space-y-1.5 [&_ol]:pl-6',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.9em]',
  '[&_pre]:my-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted/60 [&_pre]:p-4',
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
  '[&_strong]:font-semibold',
].join(' ')

function DetailSkeleton() {
  return (
    <div className="app-container space-y-6 py-6">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-8 w-1/2 max-w-full" />
      <Skeleton className="h-40 w-full max-w-2xl" />
    </div>
  )
}

interface RelatedRow {
  id: UUIDString
  label: string
  to: string
  linkType: string
}

/**
 * One section of "what this links to", grouped by kind.
 *
 * A concept is the vocabulary its neighbours hang off, so the three kinds are
 * listed separately rather than merged: "the notes that explain this" and "the
 * resources that support it" are different questions, and interleaving them
 * would answer neither. Rows that could not be resolved to a title still appear
 * with their id — a named row beats a silently shortened list.
 */
function RelatedSection({
  title,
  rows,
  emptyText,
}: {
  title: string
  rows: RelatedRow[]
  emptyText: string
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-xs leading-relaxed text-muted-foreground">{emptyText}</p>
        ) : (
          <ul className="space-y-1.5">
            {rows.map((row) => (
              <li key={row.id} className="flex items-baseline gap-2 text-sm">
                <Link to={row.to} className="min-w-0 flex-1 truncate hover:underline">
                  {row.label}
                </Link>
                <Badge variant="secondary" className="shrink-0 text-[10px]">
                  {row.linkType.replace(/_/g, ' ')}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

export default function ConceptDetailPage() {
  const { conceptId } = useParams<{ conceptId: string }>()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  /**
   * `GET /knowledge/concepts/{id}` does not exist — the router exposes only the
   * collection. So the concept is resolved out of the caller's own list rather
   * than fetched by id, and a concept outside the fetched window says so
   * instead of rendering as "not found".
   */
  const list = useConcepts({ limit: MAX_PAGE_SIZE, sort: 'name', order: 'asc' })
  const concept = useMemo(
    () => list.data?.items.find((item) => item.id === conceptId) ?? null,
    [list.data, conceptId],
  )

  const outbound = useOutboundLinks(
    conceptId ? { source_type: 'concept', source_id: conceptId } : undefined,
  )
  const { data: tagPage } = useTags({ limit: MAX_PAGE_SIZE })
  const remove = useDeleteConcept()

  // Labels for outbound edges. There is no route that resolves an edge's target
  // into its row, so the names come from the collections this page can read.
  const conceptLabels = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of list.data?.items ?? []) map.set(item.id, item.name)
    return map
  }, [list.data])

  const tagNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const tag of tagPage?.items ?? []) map.set(tag.id, tag.name)
    return map
  }, [tagPage])

  const grouped = useMemo(() => {
    const rows: Record<KnowledgeEntityType, RelatedRow[]> = { concept: [], note: [], resource: [] }
    for (const edge of (outbound.data?.items ?? []) as KnowledgeLink[]) {
      const id = edge.target_id
      const isConcept = edge.target_type === 'concept'
      const isNote = edge.target_type === 'note'
      rows[edge.target_type].push({
        id,
        label: isConcept
          ? (conceptLabels.get(id) ?? `Concept ${id.slice(0, 8)}`)
          : `${edge.target_type} ${id.slice(0, 8)}`,
        to: isConcept
          ? `/knowledge/concepts/${id}`
          : isNote
            ? `/knowledge/notes/${id}`
            : '/knowledge?tab=resources',
        linkType: edge.link_type,
      })
    }
    return rows
  }, [outbound.data, conceptLabels])

  if (list.isPending) return <DetailSkeleton />

  if (list.isError) {
    return (
      <div className="app-container space-y-4 py-6">
        <Button variant="ghost" size="sm" onClick={() => navigate('/knowledge?tab=concepts')}>
          <ArrowLeft aria-hidden="true" />
          Back to concepts
        </Button>
        <ErrorState error={toApiError(list.error)} onRetry={() => void list.refetch()} />
      </div>
    )
  }

  if (!concept) {
    const beyondWindow = (list.data?.meta.total ?? 0) > list.data?.items.length
    return (
      <div className="app-container space-y-4 py-6">
        <Button variant="ghost" size="sm" onClick={() => navigate('/knowledge?tab=concepts')}>
          <ArrowLeft aria-hidden="true" />
          Back to concepts
        </Button>
        <EmptyKnowledge
          kind="concepts"
          action={
            <Button onClick={() => navigate('/knowledge?tab=concepts')}>Browse concepts</Button>
          }
        />
        {beyondWindow ? (
          <p className="mx-auto max-w-md text-center text-xs leading-relaxed text-muted-foreground">
            This id is not among the first {list.data?.items.length} concepts by name — the list
            endpoint is the only way to read a concept, and a page holds at most {MAX_PAGE_SIZE}.
          </p>
        ) : null}
      </div>
    )
  }

  const description = concept.description?.trim() ?? ''

  return (
    <div className="app-container space-y-6 py-6">
      <PageHeader
        eyebrow={
          <Link
            to="/knowledge?tab=concepts"
            className="flex items-center gap-1.5 hover:text-foreground"
          >
            <ArrowLeft aria-hidden="true" className="size-3.5" />
            Knowledge
          </Link>
        }
        title={concept.name}
        description={`A named idea. Last changed ${formatRelative(concept.updated_at)} · created ${formatKnowledgeDateTime(concept.created_at)}.`}
        actions={
          <>
            <Button variant="outline" onClick={() => setEditing(true)}>
              <Pencil aria-hidden="true" />
              Rename or edit
            </Button>
            <Button
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 aria-hidden="true" />
              Delete
            </Button>
          </>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className={cn('min-w-0', READING_MEASURE)}>
          <h2 className="sr-only">Description</h2>
          {description === '' ? (
            <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
              <p className="text-sm font-medium text-foreground">No description yet</p>
              <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-muted-foreground">
                A concept is the note you take before writing the notes themselves. Give it one
                line and the graph stops being a pile of dots.
              </p>
              <Button className="mt-4" onClick={() => setEditing(true)}>
                <Pencil aria-hidden="true" />
                Describe this concept
              </Button>
            </div>
          ) : (
            <div
              className={PROSE}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(description) }}
            />
          )}
        </article>

        <aside className="min-w-0 space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Tag aria-hidden="true" className="size-4 text-muted-foreground" />
                Tags
              </CardTitle>
            </CardHeader>
            <CardContent>
              {concept.tag_ids.length === 0 ? (
                <p className="text-xs leading-relaxed text-muted-foreground">No tags yet.</p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {concept.tag_ids.map((id) => (
                    <li key={id}>
                      <Badge variant="secondary">{tagNames.get(id) ?? 'Unknown tag'}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <RelatedSection
            title="Related concepts"
            rows={grouped.concept}
            emptyText="No concept points at another yet."
          />
          <RelatedSection
            title="Notes that explain this"
            rows={grouped.note}
            emptyText="No note explains this concept yet."
          />
          <RelatedSection
            title="Resources that support this"
            rows={grouped.resource}
            emptyText="No resource supports this concept yet."
          />

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Referenced by</CardTitle>
            </CardHeader>
            <CardContent>
              <BacklinksPanel
                entityType="concept"
                entityId={concept.id}
                onOpenEntity={(type, id) => {
                  if (type === 'note') navigate(`/knowledge/notes/${id}`)
                  else if (type === 'concept') navigate(`/knowledge/concepts/${id}`)
                }}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Links</CardTitle>
            </CardHeader>
            <CardContent>
              <LinkEditor
                sourceType="concept"
                sourceId={concept.id}
                onOpenEntity={(type, id) => {
                  if (type === 'note') navigate(`/knowledge/notes/${id}`)
                  else if (type === 'concept') navigate(`/knowledge/concepts/${id}`)
                }}
              />
            </CardContent>
          </Card>
        </aside>
      </div>

      <ConceptFormDialog
        open={editing}
        onOpenChange={setEditing}
        concept={concept}
        onSaved={(saved) => {
          toast.success('Concept updated', saved.name)
          setEditing(false)
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this concept?"
        description={`"${concept.name}" and every edge touching it are removed. The notes that referenced it are not. Archiving does not apply to concepts — a name you no longer use is deleted.`}
        confirmLabel="Delete concept"
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          const name = concept.name
          remove
            .mutateAsync(concept.id)
            .then(() => {
              toast.success('Concept deleted', name)
              navigate('/knowledge?tab=concepts')
            })
            .catch((cause: unknown) => {
              toast.error('Could not delete the concept', toApiError(cause).message)
            })
        }}
      />
    </div>
  )
}