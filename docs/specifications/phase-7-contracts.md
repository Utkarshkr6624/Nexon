# Phase 7 — Implementation Contracts

Internal working document. Not a deliverable. Every agent building Phase 7 reads
this first so the pieces fit together without a round of integration failures.

Phase 7 has already been written and verified:

- `app/models/enums.py` — `RiskType`, `RiskSeverity`, `RiskStatus`,
  `RecommendationType`, `RecommendationStatus`, `RecommendationPriority`,
  `EvidenceStrength`, their `validate_*` helpers, and ten new `ActivityEvent`
  members (`RISK_DETECTED` … `RECOMMENDATION_COMPLETED`).
- `app/models/risk.py` — `Risk`, `Recommendation`, `RiskEvaluation`.
- `migrations/versions/0007_phase7_intelligence.py` — explicit DDL, applied,
  with no model/migration drift.
- `app/services/risk/scoring.py` — pure formulas. **Done and verified against
  the brief's worked examples; do not change it without a reason.**

---

## 1. Scoring — `app/services/risk/scoring.py` (frozen)

Pure functions. No database, no clock, no request.

| Function | Signature |
| --- | --- |
| `deadline_risk` | `(*, remaining_minutes: int, available_minutes: int, deadline_in_hours: float \| None, priority: str = "medium", historical_completion_rate: float \| None = None, title: str = "") -> RiskResult` |
| `workload_risk` | `(*, scheduled_minutes: int, available_minutes: int \| None, window_label: str = "") -> RiskResult` |
| `estimation_risk` | `(*, pairs: Sequence[tuple[int, int]]) -> RiskResult` |
| `consistency_risk` | `(*, active_days: int, window_days: int, previous_active_days: int \| None, previous_window_days: int) -> RiskResult` |
| `project_risk` | `(*, overdue_tasks=0, blocked_tasks=0, days_to_deadline=None, remaining_tasks=0, required_velocity=None, recent_velocity=None, project_name="") -> RiskResult` |
| `scheduling_risk` | `(*, overlapping_sessions=0, outside_availability_sessions=0, sessions_after_deadline=0, longest_consecutive_run=0, window_label="") -> RiskResult` |
| `risk_severity_for` | `(score: int, thresholds=DEFAULT_SEVERITY_THRESHOLDS) -> RiskSeverity` |
| `evidence_strength_for` | `(sample_count: int, *, medium=10, high=30) -> EvidenceStrength` |

`RiskResult` carries `risk_type`, `score` (`None` when unavailable — **never `0`
for "cannot judge"**), `evidence: list[RiskEvidence]`, `evidence_strength`,
`available`, `reason_if_unavailable`, `metadata`, and a `severity` property that
delegates to `risk_severity_for`.

`RiskEvidence` is `(label: str, detail: str, contribution: float)`.

Verified behaviour, which the tests assert and the services must not contradict:

| Input | Score | Severity |
| --- | --- | --- |
| 300 min work, 120 min booked, due in 24 h | 60 | high |
| 240 min work, 120 min booked, 18 h left | 50 | high |
| 2280 min scheduled, 1800 min available | 53 | high |
| `(60,110) (90,150) (120,180)` | 83 (mean overrun 0.6667) | critical |
| 4 of 7 active days, previous 12 of 7 | 67 | high |
| any deadline already passed | 100 | critical |

Cold start returns `available=False` with `score=None`, never `0`: fewer than 3
estimation pairs, `available_minutes=None` for workload, no previous window for
consistency.

---

## 2. Repository — `app/repositories/risk.py`

One class, `RiskRepository(session)`. Ownership is a predicate in every
`WHERE`, never a post-filter.

| Method | Purpose |
| --- | --- |
| `list_risks(owner_id, *, statuses=None, risk_types=None, limit, offset)` | The Risk Center list. Order: severity desc, `detected_at` desc. |
| `get_risk(owner_id, risk_id) -> Risk \| None` | Owner-scoped single row. A foreign id returns `None` → the route 404s. |
| `find_live_risk(owner_id, *, risk_type, entity_type, entity_id)` | The dedup lookup. Returns the `active`/`acknowledged` row for this identity. |
| `upsert_risk(owner_id, *, risk_type, severity, score, title, description, evidence, evidence_strength, entity_type, entity_id, metadata) -> tuple[Risk, bool]` | `(row, created)`. **This is the idempotency anchor.** Insert-or-update by identity; `created=False` when an existing live row was updated in place. |
| `transition_risk(owner_id, risk_id, *, status, resolved_at=None, responded=None)` | Lifecycle. Validates the transition. |
| `list_stale_live_risks(owner_id, *, seen: set[tuple[...]], limit)` | Live risks **not** re-detected this run — the candidates for auto-resolution. |
| `count_by_severity(owner_id, *, statuses)` | `{severity: count}` for the Risk Center header. |
| `list_recommendations(owner_id, *, statuses=None, types=None, limit, offset)` | |
| `get_recommendation(owner_id, recommendation_id) -> Recommendation \| None` | |
| `find_open_recommendation(owner_id, *, recommendation_type, entity_type, entity_id)` | Dedup lookup over `new`/`viewed`. |
| `upsert_recommendation(...) -> tuple[Recommendation, bool]` | Same contract as `upsert_risk`. |
| `transition_recommendation(owner_id, recommendation_id, *, status, responded_at=None, expires_at=None)` | |
| `expire_recommendations_for_risks(owner_id, risk_ids) -> int` | Sets `expired` where the risk is gone. Returns the count. |
| `record_evaluation(owner_id, *, window_start, window_end, risks_found, risks_created, risks_updated, risks_resolved, by_severity, by_type, recommendations_created, duration_ms) -> RiskEvaluation` | One summary row per run. |
| `list_evaluations(owner_id, *, limit) -> list[RiskEvaluation]` | Newest first. |

**Deduplication contract.** `upsert_risk` must be safe under the partial unique
index `uq_risks_live_identity`. Use PostgreSQL `INSERT ... ON CONFLICT` against
a partial index via `postgresql_index_elements`/`index_where`, or a
select-then-write inside one transaction. Returning `created` matters: the
detection service emits `RISK_DETECTED` only on `created` and `RISK_UPDATED`
otherwise, and the evaluation summary counts them separately.

---

## 3. Schemas — `app/schemas/risk.py`, `app/schemas/recommendation.py`

Follow `app/schemas/analytics.py` exactly: pydantic v2 `BaseModel`, every field
with a `description=`, docstrings stating *why* a field is nullable rather than
just that it is.

`RiskRead`: `id`, `risk_type`, `severity`, `score`, `title`, `description`,
`evidence: list[RiskEvidenceRead]`, `evidence_strength`, `entity_type`,
`entity_id`, `status`, `detected_at`, `resolved_at`, `metadata`, plus
`recommendations: list[RecommendationSummaryRead]` (empty when none — the Risk
Center renders "No suggested action yet", which is a real state).

`RiskEvidenceRead`: `label`, `detail`, `contribution`.

`RiskListRead`: `items`, `total`, `limit`, `offset`, `by_severity: dict[str,int]`,
`summary` (a one-line count sentence for the header).

`RiskSummaryRead` (dashboard): `critical`, `high`, `medium`, `low`, `total`,
`needs_attention` (bool: anything `high` or worse).

`RecommendationRead`: `id`, `recommendation_type`, `priority`, `title`,
`description`, `reason`, `entity_type`, `entity_id`, `risk_id`, `status`,
`created_at`, `responded_at`, `expires_at`, `metadata`.

`RecommendationListRead`: `items`, `total`, `limit`, `offset`, `by_priority`.
Deliberately **no `summary` sentence**, unlike `RiskListRead`: the
recommendations screen composes its count line from `by_priority`, and a
field no consumer reads is a field nobody keeps correct. (An earlier draft
of this document listed one; the implementation does not ship it and the
TypeScript type was corrected to match.)

`EvaluationRead`: `evaluated_at`, `risks_found`, `risks_created`, `risks_updated`,
`risks_resolved`, `by_severity`, `by_type`, `recommendations_created`,
`duration_ms`, `window_start`, `window_end`, `evaluated: bool`,
`reason_if_not_evaluated`.

---

## 4. Detection service — `app/services/risk/detection.py`

`RiskDetectionService(metrics: AnalyticsRepository, tasks: TaskRepository,
projects: ProjectRepository, sessions: WorkSessionRepository, risks: RiskRepository,
activity: ActivityService | None, settings: Settings)`.

```python
async def evaluate(self, *, owner: User, today: date, window_days: int = 14) -> EvaluationRead
```

One pass, six detectors, then reconciliation:

1. **Gather.** Read analytics once — `analytics.overview`, `.workload`,
   `.deadlines`, `.estimation`, `.consistency`, `.focus`, `.project_analytics`,
   `.task_analytics`, `.time_distribution`. Do **not** re-derive any of these;
   Phase 6 computed them and the brief says reuse existing analytics services.
2. **Run the six detectors** from `scoring.py` over those reads plus the planner
   signals the overload needs (overlapping sessions, out-of-availability
   sessions, sessions scheduled after their task's due date, longest
   consecutive run).
3. **Drop the unavailable.** A `RiskResult` with `available=False` produces **no
   row and no event** — its `reason_if_unavailable` goes on the evaluation
   summary so the UI can say "not enough data" rather than silently showing
   nothing.
4. **Drop the zero.** A detector that scores exactly `0` is a *measurement* that
   nothing is wrong. Storing it would fill the Risk Center with "no risk" rows
   that exist only to be dismissed by the user. Record the measurement in the
   summary; do not persist it.
5. **Upsert** every surviving result through the repository. Emit
   `RISK_DETECTED` on `created`, `RISK_UPDATED` otherwise.
6. **Resolve.** Ask the repository for live risks not re-detected this run, and
   transition them to `resolved` with `resolved_at=now`. Emit `RISK_RESOLVED`.
   This is the brief's "if the underlying condition disappears, mark the risk as
   resolved" — without it the Risk Center only ever grows.
7. **Record the evaluation**, including `duration_ms`.

Then delegate to `RecommendationService.generate` for the pass's risks and add
`recommendations_created` to the same summary.

**Titles and descriptions must be neutral and factual.** "Three tasks are due in
the next two days with less time booked than they need" — never "you are
failing" or "falling behind badly". This is an explicit brief requirement and a
reviewer will check the strings.

---

## 5. Recommendation service — `app/services/risk/recommendation.py`

`RecommendationService(risks: RiskRepository, tasks: TaskRepository,
projects: ProjectRepository, activity: ActivityService | None)`.

```python
async def generate(self, *, owner: User, risks: Sequence[Risk]) -> list[Recommendation]
```

One rule per risk type, each producing at most one recommendation per entity:

| Rule | Fires when | Type | Text shape |
| --- | --- | --- | --- |
| Deadline gap | deadline risk, work unbooked | `BLOCK_TIME` | "Schedule another N before <due date>" |
| Deadline pressure, nothing unbooked | deadline risk, gap 0, still urgent | `REVIEW_DEADLINE` | "Check whether <task> is still achievable" |
| Workload over | workload risk | `REDUCE_WORKLOAD` | "Move about N of planned work to later dates" |
| Blocked | task blocked, or project has blocked tasks | `COMPLETE_BLOCKED_TASK` | "Resolve the blocked work in <project>" |
| Rescheduled repeatedly | ≥3 `TASK_RESCHEDULED` events on one task | `BREAK_DOWN_TASK` | "Break <task> into smaller pieces" |
| Estimation overrun | estimation risk available | `UPDATE_ESTIMATE` | "Recent tasks ran about N% over estimate" |
| Consistency drop | consistency risk | `REVIEW_PROJECT` | "Recorded activity is lower than the previous period" |
| Project risk | project risk | `REVIEW_PROJECT` | "Review the open signals on <project>" |

Every recommendation must carry **WHAT / WHY / RELATED DATA / SUGGESTED ACTION**
— `title` is WHAT, `description` is the suggested action, `reason` is WHY plus the
numbers. A recommendation with an empty `reason` must be impossible to construct.

`priority` is derived from the risk's `severity` — one source of truth.

Lifecycle methods: `accept`, `reject`, `complete`, `view`, each recording the
matching `ActivityEvent` and stamping `responded_at`.

---

## 6. API — `app/api/v1/risks.py`, `recommendations.py`, `intelligence.py`

```
GET    /api/v1/risks                     ?status=&risk_type=&limit=&offset=
GET    /api/v1/risks/{id}
POST   /api/v1/risks/{id}/acknowledge
POST   /api/v1/risks/{id}/dismiss
POST   /api/v1/risks/{id}/resolve
GET    /api/v1/risks/summary              compact counts for the dashboard
GET    /api/v1/recommendations            ?status=&recommendation_type=&limit=&offset=
GET    /api/v1/recommendations/{id}
POST   /api/v1/recommendations/{id}/accept
POST   /api/v1/recommendations/{id}/reject
POST   /api/v1/recommendations/{id}/complete
POST   /api/v1/recommendations/{id}/view
POST   /api/v1/intelligence/evaluate      ?window_days=
GET    /api/v1/intelligence/evaluations   recent run summaries
```

`GET /risks/summary` **must be declared before `/risks/{id}`** or FastAPI routes
`summary` into the path parameter. Registering them in that order is the whole
fix.

Every route requires authentication (401 anonymous) and the `analytics.read`
permission (403 without). A foreign id is **404, not 403** — same rule Phase 6
settled, see `AnalyticsService._owned_project`.

Wire dependencies in `app/api/deps.py` as `get_risk_service` /
`get_recommendation_service`, following `get_analytics_service` exactly, and
register all three routers in `app/api/v1/router.py`.

---

## 7. Frontend

- `src/types/risk.ts` — mirror the schemas.
- `src/services/risk.ts` — mirror `src/services/analytics.ts`: one exported
  function per endpoint, using `@/lib/api-client`.
- `src/features/risk/` — `hooks.ts`, `components/`. Reuse
  `MetricCard`, `EmptyAnalytics`, `ChartShell` from the analytics feature rather
  than rebuilding them.
- `src/pages/risk-center-page.tsx`, `src/pages/recommendations-page.tsx`.
- Register both in `src/routes/lazy-pages.ts` and `src/routes/router.tsx`, and
  add entries to `src/features/modules/catalog.ts` (the sidebar and command
  palette render from that registry alone).

**Severity must never be colour alone.** The brief requires this for dark/light
mode and the a11y section generally: every severity indicator carries an icon
*and* the severity word, exactly as `MetricCard`'s comparison does with its
arrow plus sentence.

**Language.** Neutral and factual throughout. No "failing", "unproductive",
"lazy", "at risk of burnout", or fake urgency.