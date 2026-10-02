# Phase 3 — Projects, Tasks & Work Management

> **Status: 🟡 Backend complete and verified · frontend not started.**
> Backend: 11 tables, migration `0003`, 40 endpoints, 617 tests passing against PostgreSQL.
> `src/pages/projects-page.tsx` and `src/pages/tasks-page.tsx` are still 6-line placeholders.

---

## Objective

Build the complete NEXUS project and task management system. This becomes the core
work-management layer that later powers analytics, productivity metrics, risk detection,
recommendations, planner, machine learning and AI insights. **The data model must be designed
carefully — do not build a superficial CRUD task list.**

## Project Management

Fields: `id`, `owner_id`, `name`, `description`, `status`, `priority`, `start_date`,
`target_date`, `completed_at`, `created_at`, `updated_at`.

| Statuses | Priorities |
| --- | --- |
| `PLANNED`, `ACTIVE`, `ON_HOLD`, `COMPLETED`, `ARCHIVED` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |

Use enums or an appropriate database representation. **Do not hardcode these values throughout
the frontend.**

Operations: create, view, edit, archive, restore, delete where appropriate.

### Project dashboard

Show completion percentage, task count, completed tasks, remaining tasks, overdue tasks, priority
distribution, upcoming deadlines, recent activity.

```text
PROJECT HEALTH

CampusPulse

████████████████░░░░ 82%

Tasks       42
Completed   34
Remaining    8
Overdue      2

Target Date   October 24
Status        ACTIVE
```

## Task Management

Fields: `id`, `project_id`, `owner_id`, `title`, `description`, `status`, `priority`,
`due_date`, `start_date`, `estimated_minutes`, `actual_minutes`, `completed_at`, `created_at`,
`updated_at`.

| Statuses | Priorities |
| --- | --- |
| `TODO`, `IN_PROGRESS`, `BLOCKED`, `COMPLETED`, `CANCELLED` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |

Operations: create, view, edit, complete, reopen, delete, change status, change priority, set
deadline, set estimated duration, track actual duration.

**Every important action must be recorded as an event for future analytics.**

## Task Dependencies

```text
Task A: Design database
Task B: Implement API
        └── depends on Task A
```

Prevent impossible dependency states. **Do not allow a task to depend on itself.** Prevent
circular dependencies if practical. The architecture must support dependency graphs later.

## Subtasks

```text
Project: NEXUS
Task:    Implement Authentication
  ├── Create user model
  ├── Create login endpoint
  ├── Implement JWT
  ├── Create login UI
  └── Add tests
```

Show parent/subtask relationships clearly in the UI.

## Tags

`frontend`, `backend`, `AI`, `ML`, `DSA`, `urgent`, `college`. Users filter by tags.
**Avoid unnecessary complexity in the first implementation.**

## Project Dashboard UI

Header (name, description, status, priority, target date), then **Overview / Tasks / Timeline /
Activity** tabs. Include useful statistics. **Do not fill the screen with meaningless cards.
Information hierarchy matters.**

## Task UI

At minimum a **List view** and a **Board/Kanban view** (`TODO` / `IN PROGRESS` / `BLOCKED` /
`COMPLETED`), with status changes through the UI. Use drag-and-drop if it can be implemented
cleanly with existing dependencies. **Do not introduce unnecessary dependencies merely for
drag-and-drop.**

## Task Creation UX

Use a dialog/drawer/page appropriately. Fields: title, description, project, priority, status,
start date, due date, estimated duration, tags, dependencies. **Validation must be clear. Do not
overwhelm the user. Use sensible defaults.**

## Filtering & Sorting

Tasks support search, status filter, priority filter, project filter, tag filter, due-date filter.
Sorting: priority, due date, created date, updated date, estimated duration.

## Bulk Operations

Select multiple tasks, then change status / change priority / delete / archive.
**Do not implement dangerous bulk deletion without confirmation.**

## Overdue System

A task is overdue when `current time > due date` **and** the task is not completed or cancelled.
Show it consistently across task list, project dashboard, dashboard and analytics foundation.
**Do not rely only on frontend calculations — the backend should provide authoritative state.**

## Activity / Event System

```text
PROJECT_CREATED   PROJECT_UPDATED   PROJECT_COMPLETED
TASK_CREATED      TASK_UPDATED      TASK_STARTED
TASK_COMPLETED    TASK_REOPENED     TASK_BLOCKED
TASK_PRIORITY_CHANGED   TASK_DUE_DATE_CHANGED
```

Store useful metadata. **Do not store passwords, tokens or sensitive information.**

This event data will later power analytics, ML, recommendations and the AI Assistant.

## Ownership & Authorization

Every project and task must belong to a user. **Users must NEVER be able to access another user's
projects or tasks simply by changing an ID in the URL or API request.** Check ownership
server-side:

```text
GET /projects/123     ←  must verify project 123 belongs to the caller
```

**Do not rely on frontend restrictions.**

## Database Design

Entities: `projects`, `tasks`, `task_dependencies`, `tags`, `task_tags`, `project_tags`,
`activity_events`, and `project_members` if the architecture supports collaboration later.

Use foreign keys and appropriate cascading behaviour. **Avoid accidental cascade deletion of
important historical analytics/event data.** Think carefully about relationships before
implementing them.

## API

```text
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
DELETE /api/v1/projects/{id}
GET    /api/v1/projects/{id}/tasks

GET    /api/v1/tasks
POST   /api/v1/tasks
GET    /api/v1/tasks/{id}
PATCH  /api/v1/tasks/{id}
DELETE /api/v1/tasks/{id}
```

Plus endpoints for dependencies, tags and activity. **Use proper pagination where appropriate.
Do not return enormous unbounded datasets.**

## Frontend State

Use the existing React Query / TanStack Query architecture. Handle loading, success, error,
empty, stale and retry. **Do not put all server state into Zustand — keep server state and client
state separate.**

## Search

Project name, task title, task description where appropriate, tags. Keep the architecture ready
for the much larger global search system in Phase 13.

## Dashboard Integration

Replace the Phase 1/2 dashboard placeholder with a useful initial dashboard: active projects,
open tasks, completed tasks, overdue tasks, upcoming deadlines, recent activity. Keep it clean.
**Do not attempt advanced analytics yet.**

## UI/UX — Very Important

Smooth but subtle transitions, excellent spacing, clear typography, consistent icons, responsive
layouts, polished dialogs, confirmation dialogs, useful tooltips, skeleton loading, empty states,
error states, toast feedback, keyboard-friendly controls, accessible forms.

The Kanban board must be visually excellent. Project pages must feel distinct from generic CRUD
pages. Use visual hierarchy to distinguish Critical / High / Medium / Low. **Do not rely only on
colours.** Ensure both light and dark themes look intentional.

## Performance

**Do not fetch every task in the database whenever the dashboard loads.** Use pagination, filtered
queries, proper indexes and server-side filtering. Avoid N+1 queries. Think about how this system
could eventually contain thousands of tasks.

## Testing

Project creation, project ownership, project updates, task creation, task ownership, task
completion, task reopening, overdue detection, task dependencies, self-dependency rejection,
authorization, filtering, pagination, important API validation.

**Test that User A cannot access User B's project/task.**

## Quality Bar

> "Would I be comfortable showing this application to a senior software engineer?"

Do not hide broken functionality behind placeholder UI. **Do not fake data unless it is
explicitly seed/demo data.**

## Out of Scope

Do not implement Phase 4 (intelligent planner/calendar) yet. Stop after Phase 3 is stable.