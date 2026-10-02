# Phase 7 — Risk Detection & Recommendation Engine

> **Status: 🔴 Not started.** Requires Phase 6 (real metrics) — building risk rules on absent
> analytics is exactly what this phase forbids.

---

## PREAMBLE

Before implementing anything, inspect the COMPLETE current repository and understand what was
actually built in Phases 1–6. Do not assume previous architecture exactly matches the original
plans.

### 🚨 MANDATORY PRE-PHASE BUG CHECK

**DO NOT immediately start Phase 7.** First perform a complete regression and health check.

**Frontend:** TypeScript errors, build errors, broken imports, broken routes, console errors,
authentication, protected routes, dashboard, projects, tasks, Kanban, planner, calendar, work
sessions, knowledge base, notes, editor, knowledge graph, analytics, charts, date filters,
responsive behavior, dark/light mode, loading states, error states.

**Backend:** startup, imports, authentication, authorization, validation, exception handling,
project APIs, task APIs, planner APIs, calendar APIs, knowledge APIs, analytics APIs, event system.

**Database:** migrations, foreign keys, indexes, constraints, relationships, analytics data, event
data.

**Integration:** React → FastAPI, authentication, projects/tasks, planner/calendar, work sessions,
knowledge, analytics, date/time handling.

If bugs are found: 1. Document them. 2. Fix them. 3. Re-test them. 4. Only then begin Phase 7.

**Do NOT build the risk engine on top of known broken analytics.** If no meaningful bugs are
found, explicitly state that the pre-phase regression check passed.

### PHASE 7 OBJECTIVE

Build the NEXUS Risk Detection and Recommendation Engine. The engine should continuously analyze
the user's existing data and identify: deadline risks, workload risks, project risks, task risks,
scheduling risks, estimation risks, consistency risks.

Then produce: explainable risk alerts, recommended actions, priority suggestions, scheduling
suggestions, workload adjustments.

**IMPORTANT: Phase 7 is NOT the ML phase. Do NOT train machine-learning models here. Do NOT use an
LLM to determine risk.** The first version must be **deterministic, explainable, testable and
data-driven**. Phase 10 will later introduce trained ML models.

### CORE ARCHITECTURE

```text
Raw Data → Analytics Engine → Risk Detection Engine → Risk Objects
        → Recommendation Engine → Recommendations → React UI
```

Later: ML Predictions → Risk Engine → Recommendations.
And: Risk/Recommendation Data → Ollama → Natural-language explanation.
**Do NOT mix these responsibilities.**

### RISK MODEL

Create a proper Risk entity. Fields: `id`, `user_id`, `risk_type`, `severity`, `score`, `title`,
`description`, `entity_type`, `entity_id`, `detected_at`, `status`, `resolved_at`, `metadata`.

| Risk types | Severity | Status |
| --- | --- | --- |
| `DEADLINE`, `WORKLOAD`, `PROJECT`, `TASK`, `SCHEDULING`, `ESTIMATION`, `CONSISTENCY` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | `ACTIVE`, `ACKNOWLEDGED`, `RESOLVED`, `DISMISSED` |

**Do not scatter these values throughout the frontend.**

### RISK SCORE

Create a transparent 0–100 risk score. **Document the formula for every risk type. Do NOT pretend
these scores are scientifically validated. They are NEXUS-derived engineering metrics.**

```text
 0–24  LOW
25–49  MEDIUM
50–74  HIGH
75–100 CRITICAL
```

**Use configurable thresholds.**

### DEADLINE RISK

Detect when a task/project is likely to miss its deadline based on deterministic signals.
Possible inputs: deadline distance, remaining estimated work, historical completion rate, current
workload, task priority, task status, scheduled work, available time.

```text
Task:            Complete AI Assignment
Deadline:        Tomorrow
Remaining estimate: 5 hours
Available time:  2 hours

Risk: HIGH

Explain: "Estimated remaining work exceeds currently available time by 3 hours."
```

**Do not merely display "Risk = 82". Explain WHY.**

### PROJECT RISK

Signals: many overdue tasks, declining velocity, approaching deadline, increasing workload,
blocked tasks, large remaining workload, low completion rate.

```text
PROJECT AT RISK — NEXUS

Reasons:
  • 2 overdue tasks
  • 3 blocked tasks
  • Deadline in 4 days
  • Required velocity exceeds recent velocity
```

### WORKLOAD RISK

```text
Available: 30 hours
Scheduled: 38 hours
Workload:  127%

Risk: HIGH
Recommendation: "Move approximately 8 hours of planned work to later dates."
```

**Do not automatically reschedule anything without user confirmation.**

### SCHEDULING RISK

Detect overlapping sessions, work outside availability, unrealistic schedules, too many
consecutive work sessions, insufficient breaks where appropriate, tasks scheduled after
deadlines.

Keep the rules transparent. **Do not make medical or psychological claims about breaks or
fatigue.**

### ESTIMATION RISK

Use historical estimation accuracy.

```text
Estimated 60 → Actual 110
Estimated 90 → Actual 150
Estimated 120 → Actual 180

Detect a systematic underestimation pattern.

Recommendation: "Recent tasks have taken approximately 60% longer than estimates.
Consider increasing estimates for similar tasks."
```

**Do not diagnose the user.**

### CONSISTENCY RISK

Detect changes in activity.

```text
Previous 4 weeks: average 12 active days
Current period:     4 active days
```

Create a low/medium/high risk depending on the defined formula. **Be careful: do NOT label the
user as lazy, unmotivated, unhealthy, etc. Only describe recorded behavior.**

### RECOMMENDATION MODEL

Create a Recommendation entity. Fields: `id`, `user_id`, `recommendation_type`, `priority`, `title`,
`description`, `reason`, `entity_type`, `entity_id`, `created_at`, `expires_at`, `status`,
`metadata`.

**Statuses:** `NEW`, `VIEWED`, `ACCEPTED`, `REJECTED`, `COMPLETED`, `EXPIRED`.

### RECOMMENDATION TYPES

`RESCHEDULE_TASK`, `BREAK_DOWN_TASK`, `REDUCE_WORKLOAD`, `START_TASK`, `PRIORITIZE_TASK`,
`REVIEW_DEADLINE`, `UPDATE_ESTIMATE`, `BLOCK_TIME`, `COMPLETE_BLOCKED_TASK`, `REVIEW_PROJECT`.

**Do not automatically perform destructive actions.**

### RECOMMENDATION ENGINE

Build rules that generate recommendations.

```text
IF deadline approaching AND remaining work > available time
THEN recommend rescheduling / increasing available work time.

IF task repeatedly rescheduled
THEN recommend breaking task into subtasks.

IF project has many blocked tasks
THEN recommend reviewing blocked dependencies.

IF task estimate repeatedly underestimates actual duration
THEN recommend increasing future estimates.
```

**Every recommendation must contain: WHAT, WHY, RELATED DATA, SUGGESTED ACTION.**

### EXPLAINABILITY

Every risk and recommendation must be explainable.

```text
⚠ Deadline Risk — HIGH

"Finish AI Assignment"

Why:
  • Deadline is 18 hours away
  • Estimated remaining work: 4 hours
  • Available scheduled time: 2 hours
  • Recent completion rate: 72%

Suggested action: "Schedule an additional 2 hours before the deadline."
```

**Do not produce unexplained scores.**

### RECOMMENDATION CONFIDENCE

If useful, expose a confidence/strength indicator. **Do NOT call it ML confidence.** Use
something like `Evidence strength: LOW / MEDIUM / HIGH`, based on how much reliable data exists.

```text
Only 2 historical tasks  → Evidence strength: LOW
120 historical tasks     → Evidence strength: HIGH
```

This is important because new users won't have enough data.

### NEW USER / COLD START

The system must work for users with little data. **Do NOT generate strong conclusions from
insufficient data.** Use "No significant risk detected yet." or "Not enough historical data to
estimate your typical task duration." **Do not fabricate history.**

### RISK LIFECYCLE

Risks have lifecycle states: Detected, Active, Acknowledged, Resolved, Dismissed. If the
underlying condition disappears, mark the risk as resolved where appropriate.
**Avoid creating duplicate risk records every time the detection engine runs.**

### RISK DEDUPLICATION

The same underlying risk should not generate hundreds of identical records. Use a sensible
deduplication strategy based on user, risk type, entity, active state. **Update an existing active
risk where appropriate.**

### RISK DETECTION SERVICE

Create a centralized risk detection service.

```text
RiskDetectionService
  detect_deadline_risks()
  detect_project_risks()
  detect_workload_risks()
  detect_scheduling_risks()
  detect_estimation_risks()
  detect_consistency_risks()
```

**Do not put risk calculations inside API route handlers.**

### RECOMMENDATION SERVICE

Create a separate recommendation service. It should consume analytics, risks, planner data, tasks
and projects, and produce recommendations. **Keep the architecture modular so Phase 10/11 ML
models can later contribute predictions.**

### RISK SNAPSHOTS

Where appropriate, store risk evaluations over time. This will later allow analysis of risk
trend, risk resolution, prediction accuracy and recommendation effectiveness. **Do not create
excessive duplicate snapshots.**

### RECOMMENDATION FEEDBACK

This is important for future ML. When a recommendation is Accepted / Rejected / Completed /
Ignored, **record the outcome.**

```text
Recommendation: "Schedule 2 additional hours"  →  User: Accepted
Later:                                          →  Task: Completed
```

This gives us future training data for recommendation models.

### RISK DASHBOARD

Create a dedicated Risk Center.

```text
RISK CENTER

🔴 2 Critical
🟠 4 High
🟡 3 Medium
```

Then list active risks. Each risk shows: Title, Severity, Score, Reason, Affected entity, Detected
time, Recommended action.

### RECOMMENDATION CENTER

Create a Recommendations page.

```text
TODAY'S RECOMMENDATIONS

1. Prioritize: Complete AI Assignment
   Reason: Deadline tomorrow and remaining work exceeds scheduled time.
   [View Task] [Accept] [Dismiss]

2. Reschedule: Database Refactoring
   Reason: Current schedule exceeds today's available time by 90 minutes.
   [Review Schedule]
```

### DASHBOARD INTEGRATION

Add a compact section to the main dashboard: Risks, Recommendations. **Do not overwhelm the
dashboard.**

```text
⚠ 2 items need attention

High:   AI Assignment deadline risk
Medium: NEXUS workload above available capacity

[View Risk Center]
```

### PROJECT INTEGRATION

Project pages should show Project Health, Active Risks, Recommendations.

```text
PROJECT HEALTH — Risk: HIGH

Reasons:
  • 2 overdue tasks
  • Deadline in 5 days

Recommendations: Review blocked tasks
```

### TASK INTEGRATION

Task pages should show relevant risk information (Deadline Risk, HIGH, Reason, Suggested action).

### PLANNER INTEGRATION

Risk engine should consume planner information:

| Condition | Risk |
| --- | --- |
| Scheduled time > available time | Workload risk |
| Task scheduled after deadline | Scheduling risk |
| Task has insufficient work blocks | Deadline risk |

### EVENT TRACKING

```text
RISK_DETECTED   RISK_UPDATED   RISK_RESOLVED
RISK_ACKNOWLEDGED   RISK_DISMISSED
RECOMMENDATION_CREATED   RECOMMENDATION_VIEWED
RECOMMENDATION_ACCEPTED  RECOMMENDATION_REJECTED  RECOMMENDATION_COMPLETED
```

These events will later help train ML models.

### API

```text
GET  /api/v1/risks
GET  /api/v1/risks/{id}
POST /api/v1/risks/{id}/acknowledge
POST /api/v1/risks/{id}/dismiss
POST /api/v1/risks/{id}/resolve

GET  /api/v1/recommendations
GET  /api/v1/recommendations/{id}
POST /api/v1/recommendations/{id}/accept
POST /api/v1/recommendations/{id}/reject
POST /api/v1/recommendations/{id}/complete

POST /api/v1/intelligence/evaluate
```

The evaluate endpoint/service should run the relevant deterministic detection logic.
**Do not run extremely expensive full-system evaluation on every page load.**

### EVALUATION STRATEGY

Create a clean mechanism to evaluate intelligence. Possible: manual refresh, event-triggered
evaluation, scheduled evaluation foundation. For this phase, **do not introduce complex
background infrastructure unless already available. Create the service architecture so
background processing can be added later.**

### UI/UX — VERY IMPORTANT

This should feel like an intelligent product, not a list of warnings. Use severity hierarchy,
excellent typography, meaningful icons, clear explanations, expandable details, subtle
transitions, polished cards, action buttons, confirmation dialogs, skeleton states, empty states.

**Do NOT use:** flashing red screens, excessive warning colors, fear-based language, fake
urgency, meaningless animations. The UI should be calm and informative.

### RISK LANGUAGE

Use neutral, factual language.

| Good | Bad |
| --- | --- |
| "Deadline risk is HIGH because estimated remaining work exceeds available scheduled time." | "You are failing." |
| — | "You are extremely unproductive." |

**Never make psychological or medical conclusions.**

### DARK/LIGHT MODE

Ensure risk indicators remain readable in both themes. **Do not rely exclusively on red/orange/
yellow. Use icons, labels, severity text, patterns where appropriate.**

### PERFORMANCE

Risk evaluation should not require loading the entire database into memory. Use efficient
queries. **Reuse existing analytics services. Avoid N+1 queries. Do not run duplicate calculations
unnecessarily.**

### TESTING

Create deterministic test datasets. Test deadline risk, project risk, workload risk, scheduling
risk, estimation risk, consistency risk, recommendation generation, recommendation
acceptance/rejection, risk resolution, risk deduplication, cold-start users, ownership.

**Test that User A cannot access User B's risks/recommendations.**

### MATHEMATICAL CORRECTNESS

Manually verify known cases.

```text
Available time: 2h   Remaining work: 5h   →  Expected workload gap: 3h
Estimated: 100 minutes   Actual: 150 minutes   →  Estimation error: 50%
```

**Verify the recommendation logic. Do not trust visual output alone.**

### FUTURE ML COMPATIBILITY

Design the system so Phase 10/11 can replace or augment deterministic rules.

```text
Historical Data → ML Model → Prediction → Risk Engine → Recommendation Engine
```

**The deterministic engine should remain as a fallback when ML has insufficient data. This is
extremely important for cold-start users.**

### 🚨 MANDATORY FINAL REGRESSION CHECK

After Phase 7 implementation, **STOP.** Run a complete regression check.

**Frontend:** TypeScript, build, console errors, authentication, dashboard, projects, tasks,
planner, calendar, knowledge, analytics, risk center, recommendation center, responsive UI,
dark/light mode.
**Backend:** startup, imports, risk APIs, recommendation APIs, analytics integration, authorization,
validation, exception handling.
**Database:** migrations, relationships, indexes, constraints, event records, risk records,
recommendation records.
**Integration:** analytics → risk engine, risk engine → recommendations, task → risk, project →
risk, planner → risk, frontend → backend.

Run ALL tests from Phases 1–6. **If ANY regression appears, FIX IT.** Then re-run the affected
tests. **Do not simply report a fixable regression.**

### QUALITY BAR

Ask: "Can I explain why every risk exists?" If not, fix it. "Can the user understand what action
to take?" If not, improve the recommendation. "Does this work without machine learning?" **It
MUST.** "Will this architecture allow ML later?" **It MUST.**

### DO NOT IMPLEMENT PHASE 8

**Do NOT build Developer Intelligence yet. Phase 8 will focus on analyzing local Git repositories
and developer activity. Stop after Phase 7 is fully implemented, tested and stable.**