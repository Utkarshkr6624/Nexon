import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Pencil, Plus, Trash2 } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ConfirmDialog } from '@/features/work/components/confirm-dialog'
import { EmptyWork } from '@/features/work/components/empty-work'
import { PriorityBadge } from '@/features/work/components/priority-badge'
import { ProjectFormDialog } from '@/features/work/components/project-form-dialog'
import { StatusBadge } from '@/features/work/components/status-badge'
import { WorkProgress } from '@/features/work/components/work-progress'
import { useDeleteProject, useProjectSummary, useProjects } from '@/features/work/hooks'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { Project } from '@/types/work'

const PAGE_LIMIT = 25

/** Date-only strings are parsed as UTC by `Date`, which shifts the day west of Greenwich. */
function formatDay(value: string | null): string {
  if (!value) return 'No target date'
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  const date = parts
    ? new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))
    : new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

/**
 * Counts and progress for one row.
 *
 * The list endpoint returns bare projects; the per-project summary is where the
 * counts live, so a row asks for its own. A failure here costs that row its
 * numbers rather than the whole list, so it degrades to a dash.
 */
function RowProgress({ projectId, name }: { projectId: string; name: string }) {
  const summary = useProjectSummary(projectId)

  if (summary.isPending) return <Skeleton className="h-8 w-36" />
  const data = summary.data
  if (!data) return <span className="text-xs text-muted-foreground">Counts unavailable</span>

  return (
    <div className="w-full sm:w-36">
      <WorkProgress value={data.progress_percent} label={`${name} completion`} />
      <p className="mt-1.5 text-xs text-muted-foreground">
        {data.completed_task_count} of {data.task_count} tasks
      </p>
    </div>
  )
}

function ProjectRowSkeleton() {
  return (
    <li className="flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:gap-6">
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-4 w-56 max-w-full" />
        <Skeleton className="h-3 w-full max-w-md" />
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-16" />
      </div>
      <Skeleton className="h-8 w-36" />
      <Skeleton className="h-8 w-16" />
    </li>
  )
}

function ProjectRow({
  project,
  onEdit,
  onDelete,
}: {
  project: Project
  onEdit: (project: Project) => void
  onDelete: (project: Project) => void
}) {
  return (
    <li className="flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:gap-6">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/projects/${project.id}`}
            className="truncate rounded-sm text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            {project.name}
          </Link>
          <StatusBadge status={project.status} kind="project" />
          <PriorityBadge priority={project.priority} />
        </div>
        {project.description ? (
          <p className="mt-1 line-clamp-1 text-sm text-muted-foreground">{project.description}</p>
        ) : (
          <p className="mt-1 text-sm italic text-muted-foreground">No description</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">Target: {formatDay(project.target_date)}</p>
      </div>

      <RowProgress projectId={project.id} name={project.name} />

      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => onEdit(project)}
          aria-label={`Edit ${project.name}`}
        >
          <Pencil aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-destructive"
          onClick={() => onDelete(project)}
          aria-label={`Delete ${project.name}`}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
    </li>
  )
}

export default function ProjectsPage() {
  const projects = useProjects({ limit: PAGE_LIMIT, offset: 0 })

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Project | undefined>(undefined)
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null)

  const remove = useDeleteProject()

  const items = projects.data?.items ?? []
  const total = projects.data?.meta.total ?? items.length

  function openCreate() {
    setEditing(undefined)
    setFormOpen(true)
  }

  function openEdit(project: Project) {
    setEditing(project)
    setFormOpen(true)
  }

  return (
    <div className="app-container py-6 lg:py-8">
      <PageHeader
        title="Projects"
        eyebrow="Work"
        description="Every effort you are running, and how far each one has got."
        actions={
          <Button type="button" onClick={openCreate}>
            <Plus aria-hidden="true" />
            New project
          </Button>
        }
      />

      <div className="mt-6" aria-busy={projects.isPending}>
        {projects.isPending ? (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {Array.from({ length: 5 }, (_, index) => (
              <ProjectRowSkeleton key={index} />
            ))}
          </ul>
        ) : projects.isError ? (
          <ErrorState error={toApiError(projects.error)} onRetry={() => void projects.refetch()} />
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-border">
            <EmptyWork
              kind="projects"
              action={
                <Button type="button" onClick={openCreate}>
                  <Plus aria-hidden="true" />
                  New project
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <ul className="divide-y divide-border rounded-lg border border-border">
              {items.map((project) => (
                <ProjectRow
                  key={project.id}
                  project={project}
                  onEdit={openEdit}
                  onDelete={setPendingDelete}
                />
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              Showing {items.length} of {total}
              {total > items.length ? ' projects — the rest are further down the sequence.' : ''}
            </p>
          </>
        )}
      </div>

      <ProjectFormDialog
        open={formOpen}
        project={editing}
        onOpenChange={(open) => {
          setFormOpen(open)
          if (!open) setEditing(undefined)
        }}
        onSaved={(saved) => {
          setFormOpen(false)
          setEditing(undefined)
          toast.success(editing ? 'Project updated' : 'Project created', saved.name)
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setPendingDelete(null)
        }}
        title="Delete this project?"
        description={
          pendingDelete
            ? `“${pendingDelete.name}” and the tasks inside it will be removed. This cannot be undone.`
            : ''
        }
        confirmLabel="Delete project"
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          if (!pendingDelete) return
          const name = pendingDelete.name
          remove.mutate(pendingDelete.id, {
            onSuccess: () => {
              setPendingDelete(null)
              toast.success('Project deleted', `“${name}” is gone.`)
            },
            onError: (cause) => {
              toast.error('Could not delete that project', toApiError(cause).message)
            },
          })
        }}
      />
    </div>
  )
}
