# Phase 6 — Analytics & Intelligence Data Engine

> **Status: 🔴 Not started.** Requires Phase 4 (work sessions, actual durations) and Phase 5
> (knowledge interactions) to have real data to aggregate.

---

## PREAMBLE

Before implementing anything, inspect the COMPLETE current repository and understand what was
actually built in Phases 1–5. Do not assume previous architecture exactly matches the original
plans.

### 🚨 MANDATORY PRE-PHASE BUG CHECK

**DO NOT immediately start analytics.** First perform a complete regression check.

**Frontend:** TypeScript errors, build errors, broken imports, broken routes, console errors,
authentication, protected routes, dashboard, projects, tasks, Kanban, planner, calendar, work
sessions, knowledge base, notes, editor, knowledge graph, search, filtering, autosave, dark/light
mode, responsive behavior, loading states, error states.

**Backend:** startup, imports, authentication, authorization, validation, exception handling,
projects APIs, tasks APIs, planner APIs, calendar APIs, work-session APIs, knowledge APIs, search
APIs.

**Database:** migrations, foreign keys, indexes, relationships, constraints, orphan records,
analytics data, event data.

**Integration:** React → FastAPI, authentication, project/task integration, task/calendar
integration, planner, knowledge/project relationships, knowledge/task relationships.

If bugs are found: 1. Document them. 2. Fix them. 3. Re-test them. 4. Only then begin Phase 6.

**Do NOT build analytics on top of known broken data pipelines.** If the system is healthy,
explicitly state that the pre-phase regression check passed.

### PHASE 6 OBJECTIVE

Build the NEXUS Analytics and Intelligence Data Engine. The objective is to transform raw
application activity into reliable, explainable metrics.

NEXUS should be able to answer: How productive was I this week? How many tasks did I complete?
How much time did I actually work? Which projects consume most of my time? How consistent am I?
How often do I miss deadlines? How accurate are my time estimates? Which days are most
productive? How much planned work actually gets completed? How often do I reschedule tasks? Which
projects are slowing down? How has my workload changed? How much time do I spend learning? Which
knowledge areas am I actively using?

**DO NOT use an LLM to calculate these metrics. The analytics engine must use deterministic
calculations from real database data.**

### CORE PRINCIPLE

Separate RAW DATA from DERIVED METRICS from INSIGHTS.

```text
Raw application events
↓
Event processing
↓
Metric calculations
↓
Daily/weekly/monthly aggregates
↓
Analytics API
↓
React dashboards
```

Later: Analytics → ML models → Predictions. And: Analytics → Ollama → natural-language
explanation. **Do NOT mix these responsibilities.**

### EVENT SYSTEM

Strengthen the event/activity architecture from previous phases. All important user actions
should generate structured events.

```text
USER_LOGIN
PROJECT_CREATED   PROJECT_COMPLETED
TASK_CREATED      TASK_STARTED    TASK_COMPLETED
TASK_REOPENED     TASK_RESCHEDULED   TASK_BLOCKED
WORK_SESSION_STARTED    WORK_SESSION_COMPLETED
CALENDAR_EVENT_CREATED
NOTE_CREATED      NOTE_VIEWED    NOTE_UPDATED
KNOWLEDGE_LINK_CREATED
PLANNER_SUGGESTION_ACCEPTED   PLANNER_SUGGESTION_REJECTED
```

Use a consistent event schema. Possible fields: `id`, `user_id`, `event_type`, `entity_type`,
`entity_id`, `timestamp`, `metadata`.
**Do not store sensitive information unnecessarily.**

### EVENT IDEMPOTENCY

Where appropriate, prevent duplicate event processing. **Analytics must not double-count the
same activity.** Think carefully about retries, repeated API calls, duplicate submissions. **Do
not over-engineer this, but avoid obvious duplicate counting.**

### ANALYTICS DATA MODEL

Create appropriate models/tables for derived analytics. Possible entities: `daily_metrics`,
`weekly_metrics`, `monthly_metrics`, `project_metrics`, `task_metrics`,
`productivity_snapshots`. Adapt to the actual architecture. **Do NOT create redundant tables for
every possible metric. Use a clean metric model.**

### DAILY METRICS

Calculate daily metrics: total tasks, tasks completed, tasks created, tasks overdue, tasks
cancelled, planned work minutes, actual work minutes, completed work minutes, number of work
sessions, average session duration, number of rescheduled tasks, number of blocked tasks, projects
worked on, knowledge interactions. **These must be derived from real data.**

### WEEKLY METRICS

Calculate: tasks completed, tasks created, completion rate, overdue rate, total work hours, average
daily work hours, average session length, active projects, project distribution, rescheduling rate,
deadline adherence, estimated vs actual duration.

Compare with previous periods where appropriate.

```text
This week:      Tasks completed: 27
Previous week:  Tasks completed: 22
Change:                        +22.7%
```

**Always calculate the percentage correctly.**

### MONTHLY METRICS

Calculate monthly trends: completed tasks, total work hours, project activity, deadline adherence,
consistency, learning activity, knowledge activity. Allow users to compare months.

### PRODUCTIVITY SCORE

**IMPORTANT: Do NOT create a meaningless arbitrary number. Define exactly how it is calculated.**

The score may incorporate: task completion, deadline adherence, planned vs actual work,
consistency, focused work sessions, overdue workload.

```text
Productivity Score = weighted combination of:
  completion rate, deadline adherence, consistency, focus time
Clamped to 0–100
```

**The weights must be configurable in code/configuration rather than scattered throughout the
application. Document the formula. Show users the factors contributing to the score.**

```text
Productivity Score
78

Contributors:
  Completion     +24
  Consistency    +19
  Deadline Rate  +18
  Focus Time     +17
```

**Do not pretend the score is scientifically validated. Label it as a NEXUS-derived metric.**

### CONSISTENCY SCORE

Create a transparent consistency metric. Consider days active, regularity of work, streaks,
variation in activity. **Again: document the formula. Do not make arbitrary claims.**

### FOCUS SCORE

Create a focus metric based on actual work-session data. Possible inputs: average uninterrupted
session, number of interruptions/reschedules where available, completed planned sessions, focused
minutes.

**Do NOT claim to measure human concentration directly. Call it something like "NEXUS Focus Score"
and explain that it is derived from recorded work-session behavior.**

### DEADLINE ADHERENCE

Calculate completed before deadline, completed after deadline, still overdue.

```text
Completed on time: 18
Completed late:     3
Rate:            85.7%
```

### ESTIMATION ACCURACY

This is an important metric for future ML. Compare `estimated_minutes` vs `actual_minutes`.
Calculate absolute error, percentage error, bias.

```text
Estimated: 60 min
Actual:    80 min
Error:    +20 min
```

Across many tasks: average estimation error, median estimation error, underestimation rate,
overestimation rate. **This data will later train the Task Duration Prediction model.**

### PROJECT ANALYTICS

Every project should have analytics: total tasks, completed tasks, remaining tasks, overdue tasks,
completion rate, total work time, average task duration, estimated vs actual, task velocity, recent
activity.

Project timeline: `Week 1 — 12 tasks completed`, `Week 2 — 18`, `Week 3 — 21`. **Use real data.**

### PROJECT VELOCITY

Calculate project velocity — tasks completed per week, and optionally completed estimated
minutes per week. **Do not invent Agile methodology claims. Clearly define what NEXUS means by
velocity.**

### WORKLOAD ANALYTICS

Calculate open tasks, high-priority tasks, overdue tasks, scheduled work, available time, workload
ratio.

```text
Available: 40 hours
Scheduled: 34 hours
Workload:  85%
```

Show workload trends over time.

### TIME DISTRIBUTION

Show where time goes.

```text
NEXUS     ████████████ 32%
DSA       ████████     21%
React     ██████       15%
AI        █████        12%
Other                    20%
```

Allow filtering by project, task, category, date range.

### LEARNING ANALYTICS FOUNDATION

Use existing knowledge/planner/work-session information where available. Track study sessions,
study time, knowledge interactions, learning-related tasks.
**Do NOT build the complete Learning Intelligence module yet. Just expose useful analytics.**

### KNOWLEDGE ANALYTICS

Show notes created, notes updated, concepts created, resources added, knowledge viewed, most-used
tags, most active knowledge areas.

**Again: do not infer intelligence or mastery. Only report recorded activity.**

### TIME SERIES ENGINE

Build reusable backend logic for time-series metrics. Queries should support daily, weekly,
monthly and arbitrary date ranges. **Avoid writing separate duplicated calculation code for every
dashboard. Create reusable analytics services.**

### DATE RANGE FILTERS

Frontend should support Today, 7 days, 30 days, 90 days, This month, Last month, Custom range.
Changing the date range should update analytics. **Do not reload unrelated parts of the
application unnecessarily.**

### ANALYTICS API

```text
GET /api/v1/analytics/overview
GET /api/v1/analytics/productivity
GET /api/v1/analytics/workload
GET /api/v1/analytics/time
GET /api/v1/analytics/projects
GET /api/v1/analytics/tasks
GET /api/v1/analytics/deadlines
GET /api/v1/analytics/learning
GET /api/v1/analytics/knowledge
GET /api/v1/analytics/trends
```

Use query parameters for `start_date`, `end_date`, `project_id`, `category`.
**Avoid dozens of unnecessary endpoints if a clean aggregation endpoint is more appropriate.**

### CACHING / AGGREGATION

Analytics should not repeatedly scan millions of raw events for every dashboard request.

```text
raw events → daily aggregates → weekly/monthly calculations
```

If background jobs are not yet available, implement a service that can generate/recalculate
aggregates safely. **Do not introduce Redis/background workers unnecessarily in this phase if they
aren't already available. Prepare the architecture for them later.**

### ANALYTICS REFRESH

Provide a clear mechanism to calculate metrics, refresh aggregates, detect stale analytics. Show
appropriate UI states: `Calculating...`, `Updated just now`, `Last updated 5 minutes ago`.
**Do not silently show stale numbers without indication.**

### DASHBOARD

Transform the main NEXUS dashboard into a real intelligence dashboard: Productivity Score, Tasks
Completed, Work Time, Deadline Adherence, Current Workload — then Activity trend, Task completion
trend, Time distribution, Project performance, Upcoming deadlines, Recent activity.
**Keep it visually clean. Do not create a dashboard consisting of 20 tiny cards.**

### ANALYTICS PAGE

Create a dedicated Analytics section. Suggested structure: Overview, Productivity, Time, Projects,
Tasks, Deadlines, Learning, Knowledge. Use tabs or navigation.

### CHARTS

Use the existing charting system or an appropriate library. Create meaningful visualizations:

| Chart | Data |
| --- | --- |
| Line | Productivity over time |
| Bar | Tasks completed per day |
| Area/line | Work hours over time |
| Donut/pie | Time distribution |
| Bar | Project activity |
| Calendar heatmap | Activity consistency |

**Avoid charts that don't communicate useful information.** Every chart needs: title, date
range/context, legend where necessary, empty state, tooltip, accessible labels where practical.

### ANALYTICS EXPLANATIONS

Every important metric should have an explanation.

```text
Productivity Score
78
"Derived from task completion, deadline adherence, consistency and recorded focus sessions."
```

**Do NOT imply medical/scientific validity.**

### COMPARISON

Allow comparison with previous periods. Show absolute change and percentage change.
**Handle zero/empty previous periods safely. Never display `NaN`, `Infinity` or `undefined%`.**

### DATA QUALITY

Analytics must handle: no data, partial data, missing work sessions, deleted entities, cancelled
tasks, incomplete tasks, timezone boundaries. **Never crash because there is no activity.**

```text
"Not enough activity yet"     ← correct
"0% productivity"             ← wrong
```

Use "Not enough activity yet" instead of "0% productivity" when the metric cannot meaningfully be
calculated.

### ML DATA PREPARATION

**This is extremely important. Phase 6 should prepare the dataset architecture for Phase 10 ML
training. Create clean feature extraction services.**

Potential future features: task priority, task age, estimated duration, actual duration, deadline
distance, reschedule count, project workload, historical completion rate, user activity, work
session duration, time of day, day of week, project velocity, overdue count.

**DO NOT train the ML models yet. But make sure these values can be extracted consistently.**

### FEATURE SNAPSHOTS

If appropriate, create a feature snapshot mechanism — `user_id`, `task_id`, `snapshot_date`,
`priority`, `estimated_minutes`, `deadline_distance`, `open_task_count`, `recent_completion_rate`,
`recent_work_minutes`. This will make Phase 10 training much easier.
**Do not prematurely create thousands of redundant snapshots. Design this carefully.**

### DATA EXPORT

Create a basic analytics export mechanism. Allow users to export their own analytics data as CSV.
Potential datasets: daily metrics, task performance, work sessions. This will also help inspect
and validate the eventual ML dataset.

### PRIVACY

All analytics must be user-scoped. **Never allow one user to retrieve another user's metrics. Do
not expose raw internal events unnecessarily.**

### UI/UX — EXTREMELY IMPORTANT

This should look like a serious analytics product. Pay particular attention to: dashboard
hierarchy, charts, date filters, tooltips, metric cards, responsive layouts, loading skeletons,
empty states, comparison indicators, drill-down interactions, dark/light mode.

**Do not use random colors for every chart. Use a coherent visual system. Do not overcrowd the
page. Users should understand the most important information within seconds.**

### RESPONSIVE ANALYTICS

**Desktop:** rich multi-chart dashboard. **Tablet:** reduced chart density. **Mobile:** stack
charts vertically. **Do not create horizontal scrolling disasters.**

### ACCESSIBILITY

Charts should have text context. **Do not communicate important information through color alone.**
Metric changes should use ↑/↓, text, percentage along with color where useful.

### TESTING

Daily calculations, weekly calculations, monthly calculations, productivity score, consistency
score, focus score, deadline adherence, estimation accuracy, project metrics, workload
calculations, time distribution, date range filtering, empty datasets, zero division, timezone
boundaries, user isolation, CSV export.

**Use known fixed test datasets where possible so calculations can be verified exactly.**

```text
If: 10 tasks, 8 completed
Then: Completion rate must equal 80%
```

**Do not rely only on visual tests.**

### ANALYTICS CORRECTNESS

This is critical. Before declaring the phase complete, manually verify several metrics using known
data. Database: 10 tasks, 8 completed, 2 overdue → expected completion rate 80%, overdue rate 20%.
Create similar test cases. **Analytics must be mathematically correct.**

### 🚨 MANDATORY FINAL REGRESSION CHECK

After implementing Phase 6, **STOP.** Run a complete regression check.

**Frontend:** TypeScript, build, console errors, authentication, dashboard, projects, tasks,
planner, calendar, knowledge, analytics, charts, filters, responsive UI, dark/light mode.
**Backend:** startup, imports, analytics APIs, authentication, authorization, validation, exception
handling.
**Database:** migrations, relationships, indexes, constraints, aggregation data.
**Integration:** frontend → backend, analytics calculations, date ranges, project/task analytics,
planner/work-session data, knowledge analytics.

Run ALL tests from Phases 1–5. If any regression appears, **FIX IT.** Then re-run affected tests.

### QUALITY BAR

Ask: "Can a user actually understand their behavior from this dashboard?" If not, improve the
information hierarchy. Ask: "Can every important number be traced back to real database data?" If
not, fix it. Ask: "Can I explain exactly how each score is calculated?" If not, document/fix the
formula. **No fake intelligence. No fake metrics. No hardcoded analytics.**

### DO NOT IMPLEMENT PHASE 7

**Do NOT build the Risk Detection and Recommendation Engine yet. Phase 7 will consume the
analytics created here. Stop after Phase 6 is fully implemented, tested and stable.**