import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Archive, ArchiveRestore, CheckCircle2, FolderX, Pencil, Plus } from 'lucide-react'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { PageHeader } from '@/components/feedback/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ActivityFeed } from '@/features/work/components/activity-feed'
import { EmptyWork } from '@/features/work/components/empty-work'
import { PriorityBadge } from '@/features/work/components/priority-badge'
import { ProjectFormDialog } from '@/features/work/components/project-form-dialog'
import { StatusBadge } from '@/features/work/components/status-badge'
import { TaskCard } from '@/features/work/components/task-card'
import { TaskDialog } from '@/features/work/components/task-dialog'
import { WorkProgress } from '@/features/work/components/work-progress'
import {
  useProject,
  useProjectActivity,
  useProjectSummary,
  useProjectTasks,
  useProjectTransition,
  useTaskTransition,
} from '@/features/work/hooks'
import { cn } from '@/lib/utils'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import { PRIORITY_META, TASK_STATUS_META, TASK_STATUS_ORDER } from '@/types/work'
import type { Project, Task, TaskPriority, TaskStatus } from '@/types/work'

/** `limit` above 100 is a 422, so the widest legal page covers the whole project. */
const TASK_PAGE_LIMIT = 100
const ACTIVITY_PAGE_LIMIT = 25

const TABS = ['overview', 'tasks', 'timeline', 'activity'] as const
type Tab = (typeof TABS)[number]
const DEFAULT_TAB: Tab = 'overview'

const TONE_BAR: Record<string, string> = {
  neutral: 'bg-muted-foreground/40',
  info: 'bg-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-destructive',
}

const PRIORITIES = Object.keys(PRIORITY_META) as TaskPriority[]

function isTab(value: string | null): value is Tab {
  return TABS.includes(value as Tab)
}

/** Date-only strings are parsed as UTC by `Date`, which shifts the day west of Greenwich. */
function formatDay(value: string | null): string {
  if (!value) return '—'
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

function StatTile({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: 'success' | 'destructive'
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardDescription className="text-xs uppercase tracking-[0.1em]">{label}</CardDescription>
      </CardHeader>
      <CardContent>
        <p
          className={cn(
            'text-2xl font-semibold tabular-nums text-foreground',
            tone === 'success' && 'text-success',
            tone === 'destructive' && 'text-destructive',
          )}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  )
}

function OverviewTab({ projectId }: { projectId: string }) {
  const summaryQuery = useProjectSummary(projectId)
  const tasksQuery = useProjectTasks(projectId, { limit: TASK_PAGE_LIMIT, offset: 0 })

  const summary = summaryQuery.data
  const tasks = useMemo<Task[]>(() => tasksQuery.data?.items ?? [], [tasksQuery.data])
  const loading = summaryQuery.isPending || tasksQuery.isPending

  // Counts come from the backend's own summary; the task list is the fallback
  // for the one number the summary does not carry (overdue).
  const total = summary?.task_count ?? tasks.length
  const completed =
    summary?.completed_task_count ?? tasks.filter((task) => task.status === 'completed').length
  const remaining = Math.max(total - completed, 0)
  const percent =
    summary?.progress_percent ?? (total > 0 ? Math.round((completed / total) * 1000) / 10 : 0)
  // `is_overdue` is the backend's answer. It is read here, never recomputed.
  const overdue = tasks.filter((task) => task.is_overdue).length

  const byPriority = useMemo(
    () =>
      PRIORITIES.map((priority) => {
        const count = tasks.filter((task) => task.priority === priority).length
        return {
          priority,
          count,
          percent: tasks.length > 0 ? (count / tasks.length) * 100 : 0,
        }
      }).filter((entry) => entry.count > 0),
    [tasks],
  )

  const deadlines = useMemo(
    () =>
      tasks
        .filter((task) => task.due_date !== null)
        .sort((a, b) => (a.due_date ?? '').localeCompare(b.due_date ?? ''))
        .slice(0, 6),
    [tasks],
  )

  const distributionLabel =
    byPriority.length > 0
      ? `Priority mix: ${byPriority
          .map((entry) => `${entry.count} ${PRIORITY_META[entry.priority].label.toLowerCase()}`)
          .join(', ')}`
      : 'Priority mix: no tasks yet'

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Health</CardTitle>
          <CardDescription>
            Completion is the backend's count of finished tasks over total tasks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <Skeleton className="h-8 w-full" />
          ) : (
            <>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="text-muted-foreground">Completion</span>
                <span className="font-medium tabular-nums text-foreground">{percent}%</span>
              </div>
              <WorkProgress value={percent} label="Project completion" />
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }, (_, index) => (
            <Card key={index}>
              <CardHeader className="pb-3">
                <CardDescription>
                  <Skeleton className="h-3 w-20" />
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Skeleton className="h-7 w-12" />
              </CardContent>
            </Card>
          ))
        ) : (
          <>
            <StatTile label="Tasks" value={total} />
            <StatTile label="Completed" value={completed} tone="success" />
            <StatTile label="Remaining" value={remaining} />
            <StatTile label="Overdue" value={overdue} tone={overdue > 0 ? 'destructive' : undefined} />
          </>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Priority distribution</CardTitle>
          <CardDescription>How the {tasks.length} tasks in this project are weighted.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {loading ? (
            <Skeleton className="h-8 w-full" />
          ) : byPriority.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No tasks yet, so there is nothing to distribute.
            </p>
          ) : (
            <>
              <div
                role="img"
                aria-label={distributionLabel}
                className="flex h-2 overflow-hidden rounded-full bg-secondary"
              >
                {byPriority.map((entry) => (
                  <div
                    key={entry.priority}
                    className={cn('h-full', TONE_BAR[PRIORITY_META[entry.priority].tone])}
                    style={{ width: `${entry.percent}%` }}
                  />
                ))}
              </div>
              <ul className="flex flex-wrap gap-x-4 gap-y-1">
                {byPriority.map((entry) => (
                  <li key={entry.priority} className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span
                      aria-hidden="true"
                      className={cn(
                        'size-2 rounded-full',
                        TONE_BAR[PRIORITY_META[entry.priority].tone],
                      )}
                    />
                    {PRIORITY_META[entry.priority].label}
                    <span className="tabular-nums text-foreground">{entry.count}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Upcoming deadlines</CardTitle>
          <CardDescription>The next tasks with a due date, soonest first.</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }, (_, index) => (
                <Skeleton key={index} className="h-8 w-full" />
              ))}
            </div>
          ) : tasksQuery.isError ? (
            <ErrorState
              error={toApiError(tasksQuery.error)}
              compact
              onRetry={() => void tasksQuery.refetch()}
            />
          ) : deadlines.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing in this project has a due date yet.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {deadlines.map((task) => (
                <li key={task.id} className="flex items-center gap-3 py-2.5">
                  <span className="w-28 shrink-0 text-xs tabular-nums text-muted-foreground">
                    {formatDay(task.due_date)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">{task.title}</span>
                  {task.is_overdue ? <Badge variant="destructive">Overdue</Badge> : null}
                  <StatusBadge status={task.status} kind="task" />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function TasksTab({ project }: { project: Project }) {
  const tasksQuery = useProjectTasks(project.id, { limit: TASK_PAGE_LIMIT, offset: 0 })
  const transition = useTaskTransition()

  const [status, setStatus] = useState<'all' | TaskStatus>('all')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Task | undefined>(undefined)

  const all = useMemo<Task[]>(() => tasksQuery.data?.items ?? [], [tasksQuery.data])
  const tasks = status === 'all' ? all : all.filter((task) => task.status === status)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          Status
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value as 'all' | TaskStatus)}
            className="w-48"
            aria-label="Filter tasks by status"
          >
            <option value="all">All statuses</option>
            {TASK_STATUS_ORDER.map((value) => (
              <option key={value} value={value}>
                {TASK_STATUS_META[value].label}
              </option>
            ))}
          </Select>
        </label>
        <Button
          type="button"
          onClick={() => {
            setEditing(undefined)
            setDialogOpen(true)
          }}
        >
          <Plus aria-hidden="true" />
          New task
        </Button>
      </div>

      {tasksQuery.isPending ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-20 w-full rounded-lg" />
          ))}
        </div>
      ) : tasksQuery.isError ? (
        <ErrorState error={toApiError(tasksQuery.error)} onRetry={() => void tasksQuery.refetch()} />
      ) : tasks.length === 0 ? (
        <div className="rounded-lg border border-border">
          <EmptyWork kind={all.length === 0 ? 'tasks' : 'search'} />
        </div>
      ) : (
        <ul className="space-y-3">
          {tasks.map((task) => (
            <li key={task.id}>
              <TaskCard
                task={task}
                onOpen={() => {
                  setEditing(task)
                  setDialogOpen(true)
                }}
                onComplete={() =>
                  transition.mutate(
                    { id: task.id, status: 'completed' },
                    {
                      onSuccess: () => toast.success('Task completed', task.title),
                      onError: (cause: Error) =>
                        toast.error('Could not complete that task', toApiError(cause).message),
                    },
                  )
                }
                onReopen={() =>
                  transition.mutate(
                    { id: task.id, status: 'reopened' },
                    {
                      onSuccess: () => toast.success('Task reopened', task.title),
                      onError: (cause: Error) =>
                        toast.error('Could not reopen that task', toApiError(cause).message),
                    },
                  )
                }
              />
            </li>
          ))}
        </ul>
      )}

      <TaskDialog
        open={dialogOpen}
        task={editing}
        projects={[project]}
        defaultProjectId={project.id}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) setEditing(undefined)
        }}
        onSaved={(saved) => {
          setDialogOpen(false)
          setEditing(undefined)
          toast.success(editing ? 'Task updated' : 'Task created', saved.title)
        }}
      />
    </div>
  )
}

function TimelineTab({ projectId }: { projectId: string }) {
  const tasksQuery = useProjectTasks(projectId, { limit: TASK_PAGE_LIMIT, offset: 0 })

  const entries = useMemo(
    () =>
      (tasksQuery.data?.items ?? [])
        .map((task) => ({ id: task.id, task, day: task.due_date ?? task.start_date }))
        .filter((entry) => entry.day !== null)
        .sort((a, b) => (a.day ?? '').localeCompare(b.day ?? '')),
    [tasksQuery.data],
  )

  if (tasksQuery.isPending) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (tasksQuery.isError) {
    return <ErrorState error={toApiError(tasksQuery.error)} onRetry={() => void tasksQuery.refetch()} />
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-border">
        <EmptyWork kind="tasks" />
      </div>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Timeline</CardTitle>
        <CardDescription>
          Every task with a date, earliest first. A due date wins over a start date.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="relative space-y-4 border-l border-border pl-6">
          {entries.map(({ id, task, day }) => (
            <li key={id} className="relative">
              <span
                aria-hidden="true"
                className={cn(
                  'absolute -left-[27px] top-1.5 size-2.5 rounded-full ring-4 ring-card',
                  task.is_overdue ? 'bg-destructive' : 'bg-primary',
                )}
              />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs tabular-nums text-muted-foreground">{formatDay(day)}</span>
                <StatusBadge status={task.status} kind="task" />
                <PriorityBadge priority={task.priority} />
                {task.is_overdue ? <Badge variant="destructive">Overdue</Badge> : null}
              </div>
              <p className="mt-1 text-sm text-foreground">{task.title}</p>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

function ActivityTab({ projectId }: { projectId: string }) {
  const activity = useProjectActivity(projectId, { limit: ACTIVITY_PAGE_LIMIT, offset: 0 })
  const items = activity.data?.items ?? []

  if (activity.isPending) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    )
  }

  if (activity.isError) {
    return <ErrorState error={toApiError(activity.error)} onRetry={() => void activity.refetch()} />
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
        <CardDescription>
          Everything that has happened in this project, most recent first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ActivityFeed items={items} emptyMessage="Nothing has happened in this project yet." />
      </CardContent>
    </Card>
  )
}

function ProjectDetailSkeleton() {
  return (
    <div className="app-container space-y-6 py-6" aria-busy="true">
      <div className="space-y-3 border-b border-border pb-6">
        <Skeleton className="h-6 w-64 max-w-full" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <Skeleton className="h-10 w-full max-w-md" />
      <Skeleton className="h-10 w-72 max-w-full" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  )
}

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [formOpen, setFormOpen] = useState(false)

  const requested = searchParams.get('tab')
  const tab: Tab = isTab(requested) ? requested : DEFAULT_TAB

  const projectQuery = useProject(projectId)
  const summaryQuery = useProjectSummary(projectId)
  const projectTransition = useProjectTransition()

  function runTransition(
    transition: 'complete' | 'archive' | 'restore',
    success: string,
    failure: string,
  ): void {
    if (!projectId) return
    projectTransition.mutate(
      { id: projectId, transition },
      {
        onSuccess: () => toast.success(success),
        onError: (cause: Error) => toast.error(failure, toApiError(cause).message),
      },
    )
  }

  if (projectQuery.isPending) return <ProjectDetailSkeleton />

  if (projectQuery.isError) {
    const error = toApiError(projectQuery.error)
    // A 404 means the project is gone or belongs to another account. That is an
    // answer, not a failure, so it gets its own screen rather than a retry.
    if (error.isNotFound) {
      return (
        <div className="app-container py-6 lg:py-8">
          <EmptyState
            icon={FolderX}
            title="That project is not here"
            description="It may have been deleted, or it may belong to another account. Both answer the same way, so there is nothing further to check."
            action={
              <Button asChild variant="outline">
                <Link to="/projects">Back to projects</Link>
              </Button>
            }
          />
        </div>
      )
    }
    return (
      <div className="app-container py-6 lg:py-8">
        <ErrorState error={error} onRetry={() => void projectQuery.refetch()} />
      </div>
    )
  }

  const project = projectQuery.data

  return (
    <div className="app-container py-6 lg:py-8">
      <PageHeader
        title={project.name}
        eyebrow={
          <Link to="/projects" className="hover:text-foreground">
            Projects
          </Link>
        }
        badges={
          <>
            <StatusBadge status={project.status} kind="project" />
            <PriorityBadge priority={project.priority} />
          </>
        }
        description={project.description ?? undefined}
        actions={
          <>
            <Button type="button" variant="outline" onClick={() => setFormOpen(true)}>
              <Pencil aria-hidden="true" />
              Edit
            </Button>
            {project.status === 'archived' ? (
              <Button
                type="button"
                variant="outline"
                disabled={projectTransition.isPending}
                onClick={() =>
                  runTransition('restore', 'Project restored', 'Could not restore the project')
                }
              >
                {projectTransition.isPending ? <Spinner size="sm" /> : <ArchiveRestore aria-hidden="true" />}
                Restore
              </Button>
            ) : (
              <>
                {project.status !== 'completed' ? (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={projectTransition.isPending}
                    onClick={() =>
                      runTransition('complete', 'Project completed', 'Could not complete the project')
                    }
                  >
                    {projectTransition.isPending ? <Spinner size="sm" /> : <CheckCircle2 aria-hidden="true" />}
                    Complete
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="outline"
                  disabled={projectTransition.isPending}
                  onClick={() =>
                    runTransition('archive', 'Project archived', 'Could not archive the project')
                  }
                >
                  {projectTransition.isPending ? <Spinner size="sm" /> : <Archive aria-hidden="true" />}
                  Archive
                </Button>
              </>
            )}
          </>
        }
      />

      <div className="mt-6 space-y-1.5">
        {summaryQuery.isPending ? (
          <Skeleton className="h-10 w-full max-w-md" />
        ) : summaryQuery.data ? (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-3 text-sm">
              <span className="text-muted-foreground">
                Target date {formatDay(project.target_date)}
              </span>
              <span className="font-medium tabular-nums text-foreground">
                {summaryQuery.data.completed_task_count} of {summaryQuery.data.task_count} tasks done
              </span>
            </div>
            <WorkProgress value={summaryQuery.data.progress_percent} label={`${project.name} completion`} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Target date {formatDay(project.target_date)} · progress unavailable
          </p>
        )}
      </div>

      <Tabs
        value={tab}
        onValueChange={(next) => {
          if (next === DEFAULT_TAB) setSearchParams({})
          else setSearchParams({ tab: next })
        }}
        className="mt-6"
      >
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList className="w-max" aria-label="Project sections">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="tasks">Tasks</TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="overview">
          <OverviewTab projectId={project.id} />
        </TabsContent>
        <TabsContent value="tasks">
          <TasksTab project={project} />
        </TabsContent>
        <TabsContent value="timeline">
          <TimelineTab projectId={project.id} />
        </TabsContent>
        <TabsContent value="activity">
          <ActivityTab projectId={project.id} />
        </TabsContent>
      </Tabs>

      <ProjectFormDialog
        open={formOpen}
        project={project}
        onOpenChange={setFormOpen}
        onSaved={(saved) => {
          setFormOpen(false)
          toast.success('Project updated', saved.name)
        }}
      />
    </div>
  )
}
