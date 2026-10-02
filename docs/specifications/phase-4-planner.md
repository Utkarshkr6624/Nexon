# Phase 4 — Intelligent Planner, Calendar & Scheduling Engine

> **Status: 🔴 Not started.** Requires Phase 3 (`task_id`, `due_date`, `estimated_minutes`,
> task dependencies, subtasks).

---

## PREAMBLE

Before implementing anything, inspect the COMPLETE current repository and understand what was
actually built in Phases 1–3.

Do not assume previous architecture exactly matches the original plans.

### 🚨 MANDATORY PRE-PHASE BUG CHECK

**DO NOT immediately start implementing Phase 4.** First perform a complete regression and health
check of the existing application.

**Frontend:** TypeScript errors, build errors, broken imports, broken routes, console errors,
authentication flow, protected routes, project pages, task pages, Kanban board, dialogs, forms,
filtering, search, dark/light mode, responsive layouts, loading states, error states.

**Backend:** startup errors, Python import errors, FastAPI startup errors, broken dependencies,
incorrect API routes, authentication problems, authorization problems, validation problems,
exception handling problems.

**Database:** migration problems, schema inconsistencies, missing relationships, incorrect
constraints, broken indexes.

**Integration:** frontend → backend communication, authentication flow, API error handling, session
handling.

If bugs are found: 1. Document them. 2. Fix them. 3. Re-test them. 4. Only then start Phase 4.

**Do NOT build Phase 4 on top of known broken functionality.** If everything is healthy, explicitly
state that the pre-phase regression check passed.

### PHASE 4 OBJECTIVE

Build a complete planning and scheduling system. NEXUS should allow a user to:

* view their schedule
* create calendar events
* schedule tasks
* plan work sessions
* manage deadlines
* visualize workload
* identify scheduling conflicts
* estimate available time
* organize their day/week/month
* generate an intelligent schedule

**This should NOT be a simple calendar CRUD application.** Build the foundation for the future
recommendation and ML systems.

### CORE CONCEPTS

NEXUS should distinguish between:

1. **Tasks**
2. **Calendar Events**
3. **Work Sessions**
4. **Deadlines**
5. **Availability**
6. **Planning Blocks**

A task is work that needs to be completed. A calendar event is something occupying a time period.
A work session is a planned period dedicated to a task. A deadline is a time constraint. A
planning block is reserved time for a particular activity.

**Do not merge these concepts into one database table.**

### CALENDAR

Implement calendar functionality. Views: `DAY`, `WEEK`, `MONTH`. If practical, also provide
`AGENDA`.

Users should be able to:

* create event
* edit event
* delete event
* move event
* resize event where appropriate
* set start time
* set end time
* add title
* add description
* associate event with project
* associate event with task
* set location/textual context
* mark event as completed where appropriate

**Do not implement external Google/Outlook calendar integration. This is a local NEXUS calendar.**

### CALENDAR EVENT MODEL

Create a proper database model. Possible fields: `id`, `user_id`, `title`, `description`,
`start_time`, `end_time`, `all_day`, `event_type`, `project_id`, `task_id`, `created_at`,
`updated_at`.

**Use appropriate timezone-aware timestamps. Do not use naive timestamps if the architecture can
avoid them.**

### EVENT TYPES

`WORK`, `STUDY`, `MEETING`, `PERSONAL`, `BREAK`, `DEADLINE`, `OTHER`. **Use a clean extensible
representation.**

### PLANNING SESSIONS

Allow users to schedule a task into a specific time window.

```text
Task:              Implement authentication
Estimated duration: 120 minutes

Schedule:
  Monday  18:00–20:00
```

Create a relationship between the task and the planning/work session. Track planned duration,
actual duration, start time, end time, completion status.

This data will later be used for analytics, ML training, productivity analysis and task-duration
prediction.

### TIME TRACKING

Allow users to start/stop work sessions.

```text
Task: Build API

[ Start Session ]

Started: 18:42
Duration: 01:37:21

[ Stop Session ]
```

Store the actual session. **Do not attempt to implement invasive background activity tracking.
Only track sessions explicitly started through NEXUS.**

### AVAILABILITY

Allow users to define their typical availability.

```text
Monday     09:00–13:00, 15:00–20:00
Tuesday    10:00–18:00
```

Store recurring availability rules. Users should be able to edit these settings. This will later
allow the scheduling engine to determine when tasks can be placed.

### SCHEDULING ENGINE

**Build a deterministic scheduling engine. Do NOT use an LLM for scheduling logic.** The backend
should calculate schedules based on explicit rules.

**Inputs:** task priority, task deadline, estimated duration, task dependencies, existing
calendar events, user availability, project importance, task status.

**Output:** suggested work sessions.

```text
Task:      Complete DSA assignment
Estimated: 120 minutes
Deadline:  Tomorrow

Suggested:
  Today      19:00–20:00
  Tomorrow   09:00–10:00
```

### SCHEDULING RULES

The initial scheduling engine should consider:

1. Hard calendar conflicts
2. User availability
3. Task deadline
4. Task priority
5. Estimated duration
6. Task dependencies
7. Existing planned work
8. Reasonable working blocks

**Do NOT attempt to make the system "AI" yet. Keep the algorithm deterministic and
explainable.**

### CONFLICT DETECTION

Detect calendar conflicts, overlapping work sessions, task scheduled outside availability,
deadline conflicts, dependency conflicts.

```text
EVENT    18:00–20:00   Study DSA
ATTEMPT  19:00–21:00   Project Work

RESULT   CONFLICT
```

Show a clear explanation.

### OVERLOAD DETECTION

```text
Available: 6 hours
Scheduled: 8 hours

RESULT: OVERLOADED

Scheduled:  8h
Available:  6h
Overload:   2h
```

This will later become an input to the risk engine.

### DAILY PLANNER

Create a polished daily planning page.

```text
TODAY

09:00  ────────────────  DSA           1h 30m
11:00  ────────────────  React         2h
14:00  ────────────────  Lunch / Break
15:00  ────────────────  NEXUS         2h
18:00  ────────────────  AI Study      1h
```

Include completion state, current task, upcoming task, overdue task, available time, workload
indicator.

### WEEKLY PLANNER

Display Monday through Sunday. Allow users to create work sessions, move sessions, inspect task
information, see deadlines, identify overloaded days. **Make the week view visually clean.**

### MONTH VIEW

Show deadlines, important events, project milestones, scheduled work, overloaded days. **Do not
overcrowd the calendar. Use visual hierarchy.**

### AGENDA VIEW

```text
TODAY

09:00   DSA Study            90 min
11:00   React Project        120 min
15:00   NEXUS                120 min

TOMORROW

10:00   AI Assignment        90 min
```

**This should be excellent on smaller screens.**

### TASK → CALENDAR INTEGRATION

Tasks should be schedulable directly from the task UI.

```text
Task card:  Complete AI Assignment    [ Schedule ]
```

Clicking it opens a scheduling interface. User selects date, start time, duration. Then NEXUS
creates the work session.

### DEADLINE VISUALIZATION

Make deadlines visible across the planner. Due today / due tomorrow / due this week / normal
upcoming. **Do not rely only on color. Use text/icons as well.**

### QUICK ADD

```text
"+ Add"  →  Task | Event | Work Session | Deadline
```

Make this interaction fast. Keyboard shortcuts may be implemented if appropriate.

### PLANNER DASHBOARD

```text
TODAY

Available   6h 00m
Scheduled   4h 30m
Free        1h 30m
Workload    75%
```

**This data must come from the backend. Do not hardcode dashboard numbers.**

### UI/UX — EXTREMELY IMPORTANT

This phase is heavily UI-oriented. Pay particular attention to: calendar interactions,
drag/drop where practical, hover states, selected states, keyboard navigation, dialogs, time
pickers, date pickers, empty states, loading skeletons, tooltips, responsive layouts, mobile agenda
view, dark/light mode.

**Avoid:** generic calendar templates, excessive colors, huge cards, unnecessary gradients,
clutter, tiny unreadable text, inconsistent spacing.

**The calendar should remain understandable even when many events exist.**

### RESPONSIVE DESIGN

**Desktop:** full calendar experience. **Tablet:** reduce density intelligently. **Mobile:** prefer
an agenda/day-oriented experience rather than trying to squeeze a desktop week calendar into a
tiny screen.

### DATABASE

Add appropriate models for `calendar_events`, `work_sessions`, `availability_rules`,
`planning_blocks` if needed.

Use foreign keys to users, projects, tasks. Use proper indexes for `user_id`, `start_time`,
`end_time`, `task_id`, `project_id`. **Avoid unnecessary indexes.**

### API

```text
GET    /api/v1/calendar/events
POST   /api/v1/calendar/events
GET    /api/v1/calendar/events/{id}
PATCH  /api/v1/calendar/events/{id}
DELETE /api/v1/calendar/events/{id}

GET    /api/v1/planner/day
GET    /api/v1/planner/week
GET    /api/v1/planner/month

POST   /api/v1/work-sessions
PATCH  /api/v1/work-sessions/{id}
POST   /api/v1/work-sessions/{id}/start
POST   /api/v1/work-sessions/{id}/stop

GET    /api/v1/availability
PUT    /api/v1/availability

POST   /api/v1/planner/suggestions
```

Adapt endpoint naming to the existing architecture where appropriate.

### PERFORMANCE

**Do not fetch the entire calendar history. Query only the required date ranges.** For example,
week view: `start = Monday 00:00`, `end = Sunday 23:59`. Use server-side filtering. Avoid N+1
queries.

### EVENT TRACKING

Record important planner events.

```text
CALENDAR_EVENT_CREATED    CALENDAR_EVENT_UPDATED    CALENDAR_EVENT_DELETED
WORK_SESSION_STARTED      WORK_SESSION_COMPLETED
TASK_SCHEDULED            TASK_RESCHEDULED
PLANNER_SUGGESTION_ACCEPTED    PLANNER_SUGGESTION_REJECTED
```

These events will later become training/analytics data.

### ML DATA FOUNDATION

**Do NOT train ML models in Phase 4.** But ensure the data collected here is useful for future ML.
Capture: estimated task duration, actual task duration, scheduled start, actual start, scheduled
end, actual end, task priority, deadline distance, number of reschedules, completion status,
project, task type, work session duration.

This will eventually allow training: Task Duration Prediction, Deadline Risk Prediction, Schedule
Optimization, Productivity Prediction.

### TESTING

Calendar event creation, calendar event ownership, overlapping events, work session creation,
starting work session, stopping work session, task scheduling, availability rules, conflict
detection, overload detection, scheduling suggestions, authorization.

**Test that users cannot access another user's calendar/events/work sessions.**

Test important date/time edge cases.

### TIMEZONE & DATE/TIME SAFETY

**Be extremely careful with date/time handling.** Use timezone-aware backend timestamps. Avoid
mixing naive and aware datetime objects. Clearly define how timestamps are stored and converted.
The UI should display times consistently. **Do not silently shift user events due to timezone
mistakes.**

### 🚨 MANDATORY FINAL REGRESSION CHECK

After Phase 4 implementation, stop and perform another complete regression check.

**Frontend:** TypeScript, build, routes, console errors, planner UI, calendar views, dialogs,
forms, task scheduling, responsive behavior, dark/light mode.
**Backend:** startup, imports, planner APIs, calendar APIs, authorization, validation, exception
handling.
**Database:** migrations, foreign keys, indexes, relationships, constraints.
**Integration:** authentication, project/task integration, task → calendar scheduling,
frontend → backend, date/time correctness.

Run the existing tests from Phases 1–3 again. If any regression is discovered, **FIX IT.** Then
re-run the affected tests. **Do not simply report the bug if it can reasonably be fixed now.**

### QUALITY REQUIREMENT

Before finishing, inspect the actual planner UI and ask: "Would this feel natural if I used it
every day?" If not, improve it. **Do not settle for technically functional but unpleasant UX.**

### DO NOT IMPLEMENT PHASE 5

**Do NOT build the Knowledge Base yet. Stop after Phase 4 is fully implemented, tested and
stable.**