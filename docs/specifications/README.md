# NEXUS — Phase Specifications

This directory holds the **authoritative specifications** for NEXUS Phases 3 through 9, exactly
as they were issued by the project owner.

These are the contracts the code is built to. When the implementation and a specification
disagree, the specification is what the work is measured against — and the disagreement should be
fixed in one direction or the other, never left ambiguous.

| Spec | Phase | Module | Status |
| --- | --- | --- | --- |
| [`phase-3-projects-tasks.md`](./phase-3-projects-tasks.md) | 3 | Projects, Tasks & Work Management | 🟡 Backend complete · frontend not started |
| [`phase-4-planner.md`](./phase-4-planner.md) | 4 | Intelligent Planner, Calendar & Scheduling Engine | 🔴 Not started |
| [`phase-5-knowledge.md`](./phase-5-knowledge.md) | 5 | Knowledge Base, Notes, Resources & Knowledge Graph | 🔴 Not started |
| [`phase-6-analytics.md`](./phase-6-analytics.md) | 6 | Analytics & Intelligence Data Engine | 🔴 Not started |
| [`phase-7-risk-recommendations.md`](./phase-7-risk-recommendations.md) | 7 | Risk Detection & Recommendation Engine | 🔴 Not started |
| [`phase-8-9-developer-learning-career.md`](phase-8-9-developer-learning-career.md) | 8 + 9 | Developer Intelligence + Learning & Career Intelligence | 🔴 Not started |

## Why these are stored rather than kept in chat

Several of these phases were issued across separate sessions and were, more than once, sent
against a codebase that had not yet reached the previous phase. Storing them means:

- the **dependency chain is explicit** — Phase 4's scheduling model requires Phase 3's
  `task_id`, `due_date` and `estimated_minutes`; Phase 6's analytics require Phase 4's work
  sessions; Phase 7's risks require Phase 6's metrics;
- the **acceptance criteria survive** a context reset, so a phase can be resumed without
  re-reading a conversation;
- **scope disputes are settled from a document**, not from recollection.

## Cross-phase dependencies

```text
Phase 3  Projects · Tasks · Tags · Events
   │      (task_id, due_date, estimated_minutes, dependencies, subtasks)
   ▼
Phase 4  Planner · Calendar · Work Sessions · Availability
   │      (scheduled start/end, actual vs planned duration, time-of-day)
   ▼
Phase 5  Knowledge Base · Notes · Concepts · Relationships
   │      (links knowledge to projects and tasks)
   ▼
Phase 6  Analytics & Intelligence Data Engine
   │      (aggregates every event stream above into metrics)
   ▼
Phase 7  Risk Detection & Recommendation Engine
          (consumes Phase 6 metrics + Phases 3–5 data)
```

Phases 8 and 9 branch from the trunk rather than following it:

```text
Phase 8  Developer Intelligence ──┐
                                   ├──> Phase 10 (ML training)
Phase 9  Learning & Career Int. ──┘
```

## Standing rules that apply to every phase

These recur in the specifications and are not restated in each file:

1. **No fake data.** Every number on screen traces to a database row. A metric that cannot be
   computed from insufficient data says so — "Not enough data yet" — rather than rendering `0%`.
2. **Deterministic before learned.** Every phase through 9 is rules and arithmetic, never an LLM
   and never a trained model. The deterministic engine stays as the fallback for cold-start users.
3. **Explainability is a requirement, not a nicety.** Every score states its formula. Every risk
   states why it exists.
4. **Ownership is enforced in the query.** Tenant scoping lives in the repository, never in a
   post-fetch check, and never in the frontend.
5. **Report only what was executed.** A test that was not run is not a passing test.