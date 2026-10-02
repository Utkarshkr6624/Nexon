import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Archive, Check, Eye, Pencil, RotateCcw, Tag, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import {
  BacklinksPanel,
  LinkEditor,
  NoteEditor,
  NoteStatusBadge,
  RevisionList,
} from '@/features/knowledge/components'
import { useDeleteNote, useNote, useNoteTransition } from '@/features/knowledge/hooks'
import { ConfirmDialog } from '@/features/work/components'
import { useTags } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import {
  MAX_PAGE_SIZE,
  NOTE_STATUS_META,
  formatKnowledgeDateTime,
  formatRelative,
  renderMarkdown,
} from '@/types/knowledge'
import type { KnowledgeEntityType, UUIDString } from '@/types/knowledge'

/**
 * The reading measure. ~68 characters is the width at which a line of body text
 * can be taken in one saccade; past about 75 the return sweep starts losing the
 * start of the next line, which is the failure that makes long-form UI tiring.
 *
 * Applied to the prose column only. The rail beside it is allowed to be wider,
 * because a list of titles is scanned, not read.
 */
const READING_MEASURE = 'max-w-[68ch]'

/**
 * Typographic rhythm for rendered Markdown, expressed as arbitrary variants so
 * the reading surface does not depend on a typography plugin. The scale is
 * deliberately shallow: body 1.0625rem on a 1.75 line height, headings that
 * step up rather than jump, and spacing between blocks rather than inside them.
 */
const PROSE = [
  'text-[1.0625rem] leading-[1.75] text-foreground',
  '[&_h1]:mt-10 [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:tracking-tight',
  '[&_h2]:mt-9 [&_h2]:mb-3 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:tracking-tight',
  '[&_h3]:mt-7 [&_h3]:mb-2 [&_h3]:text-lg [&_h3]:font-semibold',
  '[&_h4]:mt-6 [&_h4]:mb-2 [&_h4]:text-base [&_h4]:font-semibold',
  '[&_h5]:mt-5 [&_h5]:text-sm [&_h5]:font-semibold [&_h5]:uppercase [&_h5]:tracking-wide',
  '[&_h6]:mt-5 [&_h6]:text-sm [&_h6]:font-semibold [&_h6]:text-muted-foreground',
  '[&_p]:my-4 [&_p:first-of-type]:mt-0',
  '[&_ul]:my-4 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-6',
  '[&_ol]:my-4 [&_ol]:list-decimal [&_ol]:space-y-1.5 [&_ol]:pl-6',
  '[&_li]:pl-1 [&_li::marker]:text-muted-foreground',
  '[&_blockquote]:my-5 [&_blockquote]:border-l-2 [&_blockquote]:border-primary/40 [&_blockquote]:pl-4 [&_blockquote]:italic [&_blockquote]:text-muted-foreground',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.9em]',
  '[&_pre]:my-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-muted/60 [&_pre]:p-4',
  '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-sm [&_pre_code]:leading-relaxed',
  '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2 [&_a]:decoration-primary/40 hover:[&_a]:decoration-primary',
  '[&_hr]:my-8 [&_hr]:border-border',
  '[&_strong]:font-semibold',
  '[&_del]:text-muted-foreground',
].join(' ')

function DetailSkeleton() {
  return (
    <div className="app-container space-y-6 py-6">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-8 w-2/3 max-w-full" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-10/12" />
        </div>
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  )
}

function openEntity(navigate: (to: string) => void) {
  return (type: KnowledgeEntityType, id: UUIDString) => {
    if (type === 'note') navigate(`/knowledge/notes/${id}`)
    else if (type === 'concept') navigate(`/knowledge/concepts/${id}`)
    // Resources have no detail route in this phase, so they open the list.
    else navigate('/knowledge?tab=resources')
  }
}

export default function NoteDetailPage() {
  const { noteId } = useParams<{ noteId: string }>()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const note = useNote(noteId)
  const transition = useNoteTransition()
  const remove = useDeleteNote()
  const { data: tagPage } = useTags({ limit: MAX_PAGE_SIZE })

  const tagNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const tag of tagPage?.items ?? []) map.set(tag.id, tag.name)
    return map
  }, [tagPage])

  const body = useMemo(
    () => (note.data ? renderMarkdown(note.data.content) : ''),
    [note.data],
  )

  if (note.isPending) return <DetailSkeleton />

  if (note.isError) {
    return (
      <div className="app-container space-y-4 py-6">
        <Button variant="ghost" size="sm" onClick={() => navigate('/knowledge?tab=notes')}>
          <ArrowLeft aria-hidden="true" />
          Back to notes
        </Button>
        <ErrorState error={toApiError(note.error)} onRetry={() => void note.refetch()} />
      </div>
    )
  }

  const current = note.data
  const meta = NOTE_STATUS_META[current.status]

  async function run(transitionTo: 'publish' | 'archive' | 'restore') {
    if (!noteId) return
    try {
      const saved = await transition.mutateAsync({ id: noteId, transition: transitionTo })
      toast.success(
        transitionTo === 'publish' ? 'Note published' : transitionTo === 'archive' ? 'Note archived' : 'Note restored',
        `${saved.title} is now ${NOTE_STATUS_META[saved.status].label.toLowerCase()}.`,
      )
    } catch (cause) {
      toast.error('That change was refused', toApiError(cause).message)
    }
  }

  return (
    <div className="app-container space-y-6 py-6">
      <PageHeader
        eyebrow={
          <Link
            to="/knowledge?tab=notes"
            className="flex items-center gap-1.5 hover:text-foreground"
          >
            <ArrowLeft aria-hidden="true" className="size-3.5" />
            Knowledge
          </Link>
        }
        title={current.title}
        badges={<NoteStatusBadge status={current.status} />}
        description={`${meta.description} Last changed ${formatRelative(current.updated_at)} · created ${formatKnowledgeDateTime(current.created_at)} · ${current.revision_count} ${current.revision_count === 1 ? 'revision' : 'revisions'}.`}
        actions={
          <>
            {!editing ? (
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Pencil aria-hidden="true" />
                Edit
              </Button>
            ) : (
              <Button variant="outline" onClick={() => setEditing(false)}>
                <Eye aria-hidden="true" />
                Done editing
              </Button>
            )}

            {current.status === 'draft' && (
              <Button variant="outline" disabled={transition.isPending} onClick={() => void run('publish')}>
                <Check aria-hidden="true" />
                Publish
              </Button>
            )}
            {current.status !== 'archived' ? (
              <Button
                variant="outline"
                disabled={transition.isPending}
                onClick={() => void run('archive')}
              >
                <Archive aria-hidden="true" />
                Archive
              </Button>
            ) : (
              <Button
                variant="outline"
                disabled={transition.isPending}
                onClick={() => void run('restore')}
              >
                <RotateCcw aria-hidden="true" />
                Restore
              </Button>
            )}
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

      {current.status === 'archived' && (
        <Alert variant="warning">
          <AlertIcon />
          <AlertTitle>This note is archived</AlertTitle>
          <AlertDescription>
            It keeps its revisions and its links, and nothing new can point at it as a target.
            Restoring returns it as a draft — publishing is a separate act you perform yourself.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className="min-w-0">
          {editing ? (
            <NoteEditor note={current} className={READING_MEASURE} />
          ) : (
            <div className={READING_MEASURE}>
              {current.summary ? (
                <p className="mb-8 border-l-2 border-primary/40 pl-4 text-lg leading-relaxed text-muted-foreground">
                  {current.summary}
                </p>
              ) : null}

              {current.content.trim() === '' ? (
                <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
                  <p className="text-sm font-medium text-foreground">This note has no body yet</p>
                  <p className="mx-auto mt-1 max-w-sm text-sm leading-relaxed text-muted-foreground">
                    The summary above is all there is. Editing adds a Markdown body with a live
                    preview, and every change is saved as you type.
                  </p>
                  <Button className="mt-4" onClick={() => setEditing(true)}>
                    <Pencil aria-hidden="true" />
                    Start writing
                  </Button>
                </div>
              ) : (
                <div className={PROSE} dangerouslySetInnerHTML={{ __html: body }} />
              )}
            </div>
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
              {current.tag_ids.length === 0 ? (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  No tags on this note yet.
                </p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {current.tag_ids.map((id) => (
                    <li key={id}>
                      <Badge variant="secondary">{tagNames.get(id) ?? 'Unknown tag'}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Related knowledge</CardTitle>
            </CardHeader>
            <CardContent>
              <LinkEditor
                sourceType="note"
                sourceId={current.id}
                onOpenEntity={openEntity(navigate)}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Referenced by</CardTitle>
            </CardHeader>
            <CardContent>
              <BacklinksPanel
                entityType="note"
                entityId={current.id}
                onOpenEntity={openEntity(navigate)}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Connected work</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Projects and tasks can be linked to a note as <code>uses</code> and{' '}
                <code>requires</code>. The entity vocabulary this backend version serves is notes,
                concepts and resources, so those two endpoints are not resolvable yet — the
                relationship types exist and nothing else does.
              </p>
            </CardContent>
          </Card>

          <Separator />

          <div>
            <h2 className="mb-2 text-sm font-medium text-foreground">History</h2>
            <RevisionList noteId={current.id} />
          </div>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this note?"
        description={`"${current.title}", its ${current.revision_count} revisions and every link touching it are removed. The activity history is kept. Archiving is the reversible answer.`}
        confirmLabel="Delete note"
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          if (!noteId) return
          const title = current.title
          remove
            .mutateAsync(noteId)
            .then(() => {
              toast.success('Note deleted', title)
              navigate('/knowledge?tab=notes')
            })
            .catch((cause: unknown) => {
              toast.error('Could not delete the note', toApiError(cause).message)
            })
        }}
      />
    </div>
  )
}