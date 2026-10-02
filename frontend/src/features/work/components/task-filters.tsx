import { useEffect, useState } from 'react'
import { ArrowDownUp, Search, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  PRIORITY_META,
  TASK_PRIORITIES,
  TASK_SORT_KEYS,
  TASK_SORT_LABELS,
  TASK_STATUSES,
  TASK_STATUS_META,
} from '@/types/work'
import type { TaskFilterValue, TaskSortKey, WorkTag } from '@/types/work'

import type { Project } from '@/types/work'

export interface TaskFiltersProps {
  value: TaskFilterValue
  onChange: (next: TaskFilterValue) => void
  /** Projects offered by the project filter and named on each row. */
  projects?: Project[]
  tags?: WorkTag[]
  /** Size of the filtered set on the server, shown so the bar states its effect. */
  resultCount?: number
  className?: string
}

const ALL = '__all__'
const SEARCH_DEBOUNCE_MS = 250

export function TaskFilters({
  value,
  onChange,
  projects = [],
  tags = [],
  resultCount,
  className,
}: TaskFiltersProps) {
  const tagIds = value.tag_ids ?? []
  const [search, setSearch] = useState(value.search ?? '')

  // Debounced, unlike every other control here: search is the only filter that
  // changes on every keystroke, and one request per character turns typing
  // "deploy" into five round trips and a list that flickers through prefixes
  // nobody meant to search. 250ms is long enough to swallow a word and short
  // enough that submitting still feels immediate.
  useEffect(() => {
    if (search === (value.search ?? '')) return
    const timer = setTimeout(() => onChange({ ...value, search: search || undefined }), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // Re-running per keystroke is the point; `value.search` is the only external
    // input the effect reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, value.search])
  const activeCount =
    (value.search ? 1 : 0) +
    (value.status ? 1 : 0) +
    (value.priority ? 1 : 0) +
    (value.project_id ? 1 : 0) +
    tagIds.length +
    (value.due_after ? 1 : 0) +
    (value.due_before ? 1 : 0)

  function update(patch: Partial<TaskFilterValue>) {
    onChange({ ...value, ...patch })
  }

  function toggleTag(id: string) {
    const next = tagIds.includes(id) ? tagIds.filter((existing) => existing !== id) : [...tagIds, id]
    update({ tag_ids: next.length ? next : undefined })
  }

  const resultLabel =
    resultCount === undefined
      ? 'Filtering'
      : `${resultCount} task${resultCount === 1 ? '' : 's'} match`

  return (
    <div className={cn('space-y-3 rounded-lg border border-border bg-card p-3', className)}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="app-form-field">
          <Label htmlFor="task-filter-search">Search</Label>
          <div className="relative">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="task-filter-search"
              type="search"
              className="pl-8"
              placeholder="Title or description"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-status">Status</Label>
          <Select
            id="task-filter-status"
            value={value.status ?? ALL}
            onChange={(event) =>
              update({ status: event.target.value === ALL ? undefined : (event.target.value as TaskFilterValue['status']) })
            }
          >
            <option value={ALL}>All statuses</option>
            {TASK_STATUSES.map((status) => (
              <option key={status} value={status}>
                {TASK_STATUS_META[status].label}
              </option>
            ))}
          </Select>
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-priority">Priority</Label>
          <Select
            id="task-filter-priority"
            value={value.priority ?? ALL}
            onChange={(event) =>
              update({ priority: event.target.value === ALL ? undefined : (event.target.value as TaskFilterValue['priority']) })
            }
          >
            <option value={ALL}>Any priority</option>
            {TASK_PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>
                {PRIORITY_META[priority].label}
              </option>
            ))}
          </Select>
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-project">Project</Label>
          <Select
            id="task-filter-project"
            value={value.project_id ?? ALL}
            onChange={(event) =>
              update({ project_id: event.target.value === ALL ? undefined : event.target.value })
            }
          >
            <option value={ALL}>All projects</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-due-after">Due from</Label>
          <Input
            id="task-filter-due-after"
            type="date"
            value={value.due_after ?? ''}
            onChange={(event) => update({ due_after: event.target.value || undefined })}
          />
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-due-before">Due before</Label>
          <Input
            id="task-filter-due-before"
            type="date"
            value={value.due_before ?? ''}
            onChange={(event) => update({ due_before: event.target.value || undefined })}
          />
        </div>

        <div className="app-form-field">
          <Label htmlFor="task-filter-sort">Sort</Label>
          <div className="flex gap-2">
            <Select
              id="task-filter-sort"
              value={value.sort ?? 'created_at'}
              onChange={(event) => update({ sort: event.target.value })}
            >
              {TASK_SORT_KEYS.map((key) => (
                <option key={key} value={key}>
                  {TASK_SORT_LABELS[key as TaskSortKey]}
                </option>
              ))}
            </Select>
            <Button
              variant="outline"
              size="icon"
              className="shrink-0"
              aria-label={value.order === 'desc' ? 'Sorted descending' : 'Sorted ascending'}
              title={value.order === 'desc' ? 'Descending' : 'Ascending'}
              onClick={() => update({ order: value.order === 'desc' ? 'asc' : 'desc' })}
            >
              <ArrowDownUp aria-hidden="true" />
            </Button>
          </div>
        </div>

        <div className="flex items-end justify-between gap-2">
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {resultLabel}
          </p>
          <Button
            variant="ghost"
            size="sm"
            disabled={activeCount === 0}
            onClick={() =>
              onChange({ sort: value.sort ?? 'created_at', order: value.order ?? 'desc' })
            }
          >
            <X aria-hidden="true" />
            Clear{activeCount ? ` (${activeCount})` : ''}
          </Button>
        </div>
      </div>

      {tags.length > 0 && (
        <fieldset className="space-y-1.5">
          <legend className="text-xs font-medium text-muted-foreground">
            Tags — a task must carry every tag selected
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {tags.map((tag) => {
              const active = tagIds.includes(tag.id)
              return (
                <Button
                  key={tag.id}
                  type="button"
                  size="sm"
                  variant={active ? 'default' : 'outline'}
                  aria-pressed={active}
                  onClick={() => toggleTag(tag.id)}
                >
                  {tag.name}
                </Button>
              )
            })}
          </div>
        </fieldset>
      )}
    </div>
  )
}