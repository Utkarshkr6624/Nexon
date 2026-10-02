import { useState } from 'react'
import { CalendarClock, Flag, ListTodo, Plus, Timer } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { CalendarEventDialog, type EventFormDefaults } from '@/features/planner/components/calendar-event-dialog'
import { WorkSessionDialog } from '@/features/planner/components/work-session-dialog'
import { TaskDialog } from '@/features/work/components'
import { cn } from '@/lib/utils'
import type { CalendarEvent, WorkSession } from '@/types/planner'
import type { Project, Task } from '@/types/work'

type Choice = 'task' | 'event' | 'session' | 'deadline'

export interface QuickAddProps {
  projects: Project[]
  tasks: Task[]
  /** The zone the opened dialogs read their wall-clock values in. */
  timeZone: string
  /** Seeds the windows when the planner was opened from a specific day. */
  defaults?: EventFormDefaults
  defaultProjectId?: string
  onEventSaved?: (event: CalendarEvent) => void
  onSessionSaved?: (session: WorkSession) => void
  className?: string
}

/** The next whole hour, as instants. The dialog renders them in `timeZone`. */
function nextHour(): { starts_at: string; ends_at: string } {
  const now = new Date()
  now.setMinutes(0, 0, 0)
  return { starts_at: now.toISOString(), ends_at: new Date(now.getTime() + 60 * 60_000).toISOString() }
}

/**
 * "+ Add", and the four things it can mean.
 *
 * **A menu rather than four buttons**, because three of the four open a dialog
 * whose first fields are nearly identical and the fourth would push the control
 * out of the header. It is a Radix menu, so arrow keys and Escape already work —
 * there is no keyboard behaviour here to get wrong.
 *
 * "Task" hands off to the Phase 3 {@link TaskDialog} rather than growing a
 * second task form: a task created from the planner has to be the same task the
 * work surface shows. "Deadline" opens the event dialog with the type preset —
 * the calendar already carries that distinction, and a separate deadline record
 * would be a second rule to re-derive from a title at read time.
 */
export function QuickAdd({
  projects,
  tasks,
  timeZone,
  defaults,
  defaultProjectId,
  onEventSaved,
  onSessionSaved,
  className,
}: QuickAddProps) {
  const [choice, setChoice] = useState<Choice | null>(null)

  const seeded = defaults?.starts_at && defaults?.ends_at ? defaults : { ...nextHour(), ...defaults }
  const isOpen = (target: Choice) => choice === target
  const close = () => setChoice(null)
  const toggle = (target: Choice) => (open: boolean) => (open ? setChoice(target) : close())

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button>
            <Plus aria-hidden="true" />
            Add
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>Add to the planner</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setChoice('task')}>
            <ListTodo aria-hidden="true" />
            Task
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setChoice('event')}>
            <CalendarClock aria-hidden="true" />
            Event
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setChoice('session')}>
            <Timer aria-hidden="true" />
            Work session
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setChoice('deadline')}>
            <Flag aria-hidden="true" />
            Deadline
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <TaskDialog
        open={isOpen('task')}
        onOpenChange={toggle('task')}
        projects={projects}
        defaultProjectId={defaultProjectId}
      />

      <CalendarEventDialog
        open={isOpen('event')}
        onOpenChange={toggle('event')}
        projects={projects}
        tasks={tasks}
        timeZone={timeZone}
        defaults={seeded}
        onSaved={onEventSaved}
      />

      <CalendarEventDialog
        open={isOpen('deadline')}
        onOpenChange={toggle('deadline')}
        projects={projects}
        tasks={tasks}
        timeZone={timeZone}
        defaults={{ ...seeded, event_type: 'deadline' }}
        onSaved={onEventSaved}
      />

      <WorkSessionDialog
        open={isOpen('session')}
        onOpenChange={toggle('session')}
        projects={projects}
        tasks={tasks}
        timeZone={timeZone}
        defaults={{
          starts_at: seeded.starts_at,
          ends_at: seeded.ends_at,
          project_id: defaults?.project_id,
        }}
        onSaved={onSessionSaved}
      />
    </div>
  )
}