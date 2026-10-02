import { useState } from 'react'
import { History, RotateCcw } from 'lucide-react'

import { ConfirmDialog } from '@/features/work/components/confirm-dialog'
import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { Button } from '@/components/ui/button'
import { useNoteRevisions, useRestoreRevision } from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { cn } from '@/lib/utils'
import { formatKnowledgeDate, formatRelative } from '@/types/knowledge'
import type { NoteRevision, UUIDString } from '@/types/knowledge'

import { EmptyKnowledge } from './empty-knowledge'

export interface RevisionListProps {
  noteId: UUIDString
  /** The revision being restored, confirmed before the write. */
  className?: string
}

/** A revision is a full copy, so a preview of the first lines is honest about
 *  what will be restored without pretending to show a diff. */
function RevisionPreview({ revision }: { revision: NoteRevision }) {
  const firstLine = revision.content
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line !== '')

  return (
    <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
      {firstLine ?? 'Empty body'}
    </p>
  )
}

/**
 * History, newest first, with a restore behind a confirmation.
 *
 * A restore is not a revert of the history: the backend writes a *new*
 * revision before overwriting the note, so the action is itself undoable. The
 * copy says so, because "restore" otherwise reads as destructive.
 */
export function RevisionList({ noteId, className }: RevisionListProps) {
  const { data, isPending, error, refetch } = useNoteRevisions(noteId)
  const restore = useRestoreRevision()
  const [pending, setPending] = useState<NoteRevision | null>(null)

  if (isPending) {
    return <LoadingState compact label="Loading history" />
  }

  if (error) {
    return (
      <ErrorState
        compact
        error={toApiError(error)}
        onRetry={() => void refetch()}
        title="The history could not be loaded"
      />
    )
  }

  const revisions = data?.items ?? []
  if (revisions.length === 0) {
    return <EmptyKnowledge kind="revisions" compact />
  }

  return (
    <div className={cn('space-y-2', className)}>
      <ol className="space-y-2">
        {revisions.map((revision, index) => (
          <li
            key={revision.id}
            className="rounded-md border border-border px-3 py-2"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-sm text-foreground">
                  <History aria-hidden="true" className="size-3.5 text-muted-foreground" />
                  <span className="truncate">{revision.title}</span>
                  {index === 0 && (
                    <span className="text-[11px] text-muted-foreground">· newest</span>
                  )}
                </p>
                <p
                  className="text-xs text-muted-foreground"
                  title={formatKnowledgeDate(revision.created_at)}
                >
                  {formatRelative(revision.created_at)}
                </p>
                <RevisionPreview revision={revision} />
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPending(revision)}
              >
                <RotateCcw aria-hidden="true" className="size-3.5" />
                Restore
              </Button>
            </div>
          </li>
        ))}
      </ol>

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => !open && setPending(null)}
        title="Restore this version?"
        description="The note is set back to the text, title and summary stored in this revision. The current text is kept as a new revision first, so this can itself be undone."
        confirmLabel="Restore version"
        pending={restore.isPending}
        onConfirm={async () => {
          if (!pending) return
          try {
            await restore.mutateAsync({ id: noteId, revisionId: pending.id })
            toast.success('Version restored', 'The previous text is still in the history.')
            setPending(null)
          } catch (cause) {
            toast.error('Could not restore that version', toApiError(cause).message)
          }
        }}
      />
    </div>
  )
}
