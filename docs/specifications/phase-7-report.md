# Phase 7 — Final Report: Risk Detection & Recommendation Engine

Companion to [`phase-7-risk-recommendations.md`](./phase-7-risk-recommendations.md),
which is the brief. This is the record of what was built, what was found broken,
and what was actually executed.

`phase-7-contracts.md` in this directory is the internal working contract the
implementation was built against in parallel. It is superseded by this document
where the two disagree, and one disagreement it caused is recorded below.

---

## 1. Pre-phase regression check

**Passed.** Run fresh at the start of Phase 7: **884 backend tests green**
(617 from Phases 1–5 plus the 267 Phase 6 analytics tests), and on the frontend
361 tests across 32 files with `tsc`, `eslint` and `vite build` clean.

No bugs were found in the Phase 1–6 system, so nothing was fixed before Phase 7
began. The analytics engine Phase 7 consumes was healthy.

---

## 2. Bugs found and fixed during Phase 7

Every one of these was found by a Phase 7 test or by review of code Phase 7
wrote. None existed before this phase except where noted.

1. **`recommendations.recommendation_type` was `VARCHAR(16)`; `complete_blocked_task` is 21 characters.** One of the eight recommendation rules fired correctly and then died at the database with `StringDataRightTruncation`. The width had been derived from the longest *risk* enum member (`acknowledged`, 12) and reused for a different, longer vocabulary. The two vocabularies now have separately named widths.
2. **The null-identity upsert path silently dropped `metadata`.** `metadata` is reserved on a declarative class, so the models declare the column as `metadata_`. The Core insert path used the column name; the ORM path (`Risk(**values)` / `setattr`) used the same name and therefore set a stray attribute SQLAlchemy never writes. **Every account-level risk — workload, consistency and estimation, three of seven detectors — stored `{}`**, and three recommendation rules read that metadata and could never fire. Fixed with one `_to_orm_attributes` translation at the boundary.
3. **`risk_severity_for`'s contract was broken by `AliasChoices` ordering.** The schemas declared `AliasChoices("metadata", "metadata_")`, so `model_validate(row)` resolved `"metadata"` first via `getattr` and returned SQLAlchemy's `MetaData` object instead of a dict. Any caller doing the obvious `RiskRead.model_validate(row)` got a 500.
4. **`app.services.risk` listed `recommendation_rules` in `__all__` without importing it**, so `from app.services.risk import *` raised `AttributeError`.
5. **A percentage was passed where a fraction was expected.** `DeadlineAdherenceRead.adherence_rate` is a percentage; `deadline_risk` clamps to 0–1. Every adherence at or above 50% clamped to exactly 1.0, so an account finishing 100% on time and one finishing 55% received the identical multiplier — the completion-rate input did nothing across the whole range where it would have mattered most.
6. **`from_status` on every recommendation lifecycle event equalled the target status.** The service read the row, then transitioned through the same identity-mapped instance, whose attributes the `UPDATE ... RETURNING` (running with `populate_existing=True`) had already overwritten. "Accepted from viewed" and "accepted without being read" — different rows in a future training set — became indistinguishable. Fixed by capturing the status as a string before the write.
7. **A project with one unfinished task always produced a stored score-1 risk.** `0.10 × 0.05 × 100` is exactly 0.5, which binary floating point renders as `0.5000000000000001`, which rounds to 1. That defeated the engine's own "a measured zero writes nothing" rule for the common case. Fixed by snapping to six decimals before rounding.
8. **A repeat of a risk's own terminal status answered 200, not 409.** The repository's transition table deliberately permits `resolved → resolved` so the detection sweep can re-resolve without raising; the router inherited that and told a user who clicked "dismiss" on an already-dismissed risk that it worked. The two callers genuinely want different answers, so the distinction was made in the router, which already holds the row.
9. **Two different 404 messages for the same condition** on the recommendation surface — one for `GET`, a different one from the service for the four transitions. Both were free of existence leaks, but the same condition answering in two sentences on two routes of one resource is a contract a client cannot hold. Unified.
10. **The Phase 7 frontend had an accessibility defect:** while a transition was in flight, `Spinner`'s sr-only "Loading" was folded into the button's accessible name, so "Acknowledge" became "LoadingAcknowledge" — the control renamed itself at the exact moment a screen-reader user was waiting on it. The busy state now lives on the button as `aria-busy`.
11. **`RiskType.TASK` was unreachable.** The brief requires task risks; the enum had the member and two recommendation rules mapped onto it, but no detector emitted one. A seventh detector now exists for blocked and repeatedly-rescheduled tasks, and both rules fire end to end.
12. **`RecommendationListRead.summary` was declared in TypeScript but never sent** by the backend. The contracts document listed it; the implementation deliberately shipped without it. The type was corrected and the contracts document amended — recorded here because the document was the source of the disagreement.

---

## 3. Risk architecture

```text
Phase 6 analytics (already computed, never re-derived)
  ↓  one grouped read per signal
RiskDetectionService.evaluate
  ↓  seven pure scoring functions
RiskResult { score, severity, evidence[], evidence_strength, metadata }
  ↓  upsert by identity, or dropped if unavailable / zero
risks table
  ↓
RecommendationService.generate → recommendations table
```

**Three tables**, and each shape is argued at its definition:

- **`risks`** — one row per *live condition*, not one per evaluation. Deduplication is a **partial unique index**:
  `CREATE UNIQUE INDEX uq_risks_live_identity ON risks (user_id, risk_type, entity_type, entity_id) WHERE status IN ('active', 'acknowledged')`.
  Partial rather than plain so a risk that resolves and legitimately returns is recordable as a new observation instead of colliding with its own history. `entity_type` is in the key because task ids and project ids share one uuid space — without it, "task 7 at risk" would swallow "project 7 at risk". `entity_id` is nullable, and nulls are distinct in a btree index, which is what lets several account-level risks of one type coexist.
- **`recommendations`** — the same shape over the *open* statuses (`new`, `viewed`). A rejected suggestion is deliberately re-raisable: the user declined and the condition did not change, so re-raising would be nagging.
- **`risk_evaluations`** — **one summary row per run**, not one per risk per run. Bounded by (evaluations) rather than (evaluations × risks). Per-risk resolution timing is already recoverable from `risks.detected_at`/`resolved_at`, so storing it twice would create a second answer that could disagree.

**No risk-score history table**, deliberately. A score is a pure function of the analytics it reads, so a stored time series is a second answer that could disagree with the dashboard's. The Phase 10 training set is re-derivable by re-running detection over a past window.

### Every risk is explainable by construction

A `RiskResult` carries `evidence` — an ordered list of `(label, detail, contribution)` — and `metadata`, the raw inputs. The detection service cannot persist a risk with no evidence, because it cannot build one without calling a scoring function that always produces the list. `score` is `None` when a detector could not judge, and such a result produces **no row and no event**: its reason travels on the evaluation summary instead, so the UI can distinguish "no risk" from "not enough data".

---

## 4. Risk formulas

All pure functions in `backend/app/services/risk/scoring.py`, no I/O, documented at
the definition. Verified against the brief's worked examples:

| Input | Score | Severity |
| --- | --- | --- |
| 5 h work, 2 h booked, due in 24 h | **60** | high |
| 4 h work, 2 h booked, 18 h left | **50** | high |
| 38 h scheduled, 30 h available (127%) | **53** | high |
| `(60,110) (90,150) (120,180)` — mean overrun 0.6667 | **83** | critical |
| 4 active days of 7, previous 12 of 7 | **67** | high |
| Any deadline already passed | **100** | critical |

| Detector | Formula |
| --- | --- |
| **Deadline** | `100 × gap_ratio × urgency × priority_k × completion_k`, where `gap_ratio` is the *unbooked fraction of remaining work* (so a 30-minute task with nothing booked scores the same as a 30-hour one), `urgency` is 1.0 / 0.7 / 0.4 / 0.15 by proximity, `priority_k` is ±10%, and `completion_k` adjusts for how reliably the user finishes what they plan. |
| **Workload** | `100 × max(0, ratio − 1) / 0.5`. Exactly zero at or below 100%; clamps to 100 at 200%. |
| **Estimation** | `100 × max(0, mean(overrun)) / 0.8` over `(estimated, actual)` pairs with a positive estimate. Only *over*-run raises the score — finishing early is not a risk. |
| **Consistency** | `100 × clamp((previous_rate − current_rate) / previous_rate)`, each rate per-day so unequal windows compare fairly. |
| **Project** | `100 ×` a weighted sum of five normalised signals: overdue 0.30, blocked 0.25, deadline 0.20, velocity 0.15, remaining 0.10. Overdue leads because it is the only signal that is already a fact rather than a projection. |
| **Scheduling** | `100 ×` overlap 0.35, out-of-availability 0.25, work-after-deadline 0.25, longest unbroken run 0.15. |
| **Task** | `100 ×` blocked 0.60, rescheduled-at-least-3× 0.40. Both binary. |

**Severity is derived, never chosen.** `risk_severity_for` is the only thing that
maps a score to a band, so a score and its severity cannot disagree. The ladder
is configurable and validated on construction.

### Two deliberate restraints

**On the consecutive-sessions signal.** The brief asks for "too many consecutive work sessions" and forbids psychological claims about fatigue. So the detector reports the *number* — "the longest run of back-to-back sessions was 11" — and concludes nothing beyond the plan containing an unbroken run. A test asserts the evidence line contains none of `rest`, `fatigue`, `burnout`, `tired`, `wellbeing`.

**On "evidence strength".** The brief asks for a confidence indicator and forbids calling it ML confidence. It is the sample count, banded `LOW`/`MEDIUM`/`HIGH` at 10 and 30. The name is the point: calling it confidence would be the misrepresentation.

---

## 5. Recommendation architecture

Eight rules, each a small named method returning a `RecommendationDraft` or
`None`. `generate` walks the risks, dedupes through the repository, and returns
only what it **created** — which is what keeps `recommendations_created` honest.

`RecommendationDraft.__post_init__` **rejects a `reason` containing no digit.**
The brief's failure mode is a bare imperative that renders perfectly; making it
impossible to construct at the point where a reviewer can see it is stronger than
hoping the copy stays good.

`priority` is derived from the risk's `severity`. No rule takes a `priority`
argument, so none *can* choose one.

Every `RecommendationType` names something a **person** does. There is no member
meaning "the system rescheduled your task for you", which makes the brief's "do
not automatically reschedule without confirmation" structural rather than a rule
someone has to remember at each call site.

| Rule | Fires when | Type |
| --- | --- | --- |
| Deadline gap | work unbooked before a deadline | `BLOCK_TIME` |
| Deadline pressure, nothing unbooked | urgent, gap 0 | `REVIEW_DEADLINE` |
| Workload over | scheduled exceeds declared capacity | `REDUCE_WORKLOAD` |
| Blocked | blocked task, or blocked project | `COMPLETE_BLOCKED_TASK` |
| Repeatedly rescheduled | ≥ 3 `TASK_RESCHEDULED` events | `BREAK_DOWN_TASK` |
| Estimation overrun | pattern available | `UPDATE_ESTIMATE` |
| Consistency drop | activity lower than the previous period | `REVIEW_PROJECT` |
| Project risk | any project signal firing | `REVIEW_PROJECT` |

---

## 6. Database changes

`migrations/versions/0007_phase7_intelligence.py` — explicit DDL, no model
import, as in 0001–0006. Creates `risks`, `recommendations`, `risk_evaluations`
with two partial unique indexes (the deduplication mechanism), the CHECK
constraints, and the `risk_evaluations` unique constraint that makes a retried
insert safe. Verified applied, downgraded and re-applied, with
`alembic` reporting no drift against `Base.metadata` on live PostgreSQL.

`tests/test_migration_ddl.py` was extended in three places this phase: the
migration chain, the new indexes, and — the non-obvious one — **the index regex**.
It stopped at the closing paren, so *every partial index would have been silently
excluded from the model/migration comparison*. An index nobody checks is an index
that can drift, which is the exact failure that module exists to prevent.

---

## 7. API endpoints (14)

```
GET  /api/v1/risks                      GET  /api/v1/recommendations
GET  /api/v1/risks/summary              GET  /api/v1/recommendations/{id}
GET  /api/v1/risks/{id}                 POST /api/v1/recommendations/{id}/accept
POST /api/v1/risks/{id}/acknowledge     POST /api/v1/recommendations/{id}/reject
POST /api/v1/risks/{id}/dismiss         POST /api/v1/recommendations/{id}/complete
POST /api/v1/risks/{id}/resolve         POST /api/v1/recommendations/{id}/view
POST /api/v1/intelligence/evaluate      GET  /api/v1/intelligence/evaluations
```

`GET /risks/summary` is declared **before** `/risks/{id}` on purpose: FastAPI
matches in registration order, and the reverse order binds the literal string
`summary` to the path parameter. A test asserts the summary handler wins by
checking for a field only it returns.

All routes require authentication (401) and `analytics.read` (403). A foreign id
is **404, not 403** — the Phase 6 rule, pinned in both directions.

---

## 8. UI

**Risk Center** (`/risks`) — the four severity tiles, each an icon *and* a word,
each a link into a filtered list; the risk list with title, severity, score
meter, the evidence under "Why", affected entity, detected time and the suggested
action; acknowledge / resolve / dismiss acting on the returned row rather than a
local guess; loading skeletons, a working retry on failure, and an empty state
that reads **"No significant risk detected yet"**.

**Recommendations** (`/recommendations`) — grouped by priority, each heading
carrying an icon and the word, each card showing WHAT / WHY / the action, with
accept / complete / not-for-me offered only where legal from the current status.

**Dashboard** — one compact strip: a sentence, a band tally that omits empty
bands, the three most severe findings, and a link to the Risk Center. No new
visual language, and no urgency the data does not support.

Severity is never conveyed by colour alone anywhere in the feature.

---

## 9. Event tracking

Ten new `ActivityEvent` members: `RISK_DETECTED`, `RISK_UPDATED`,
`RISK_RESOLVED`, `RISK_ACKNOWLEDGED`, `RISK_DISMISSED`, and
`RECOMMENDATION_CREATED` / `_VIEWED` / `_ACCEPTED` / `_REJECTED` / `_COMPLETED`.
They are written to `activity_events` rather than only to a status column because
the column says what a row is *now* and the feed says *when* and *in what order* —
which is what a future classifier needs. `from_status` on a lifecycle event is
the training label that says whether a suggestion was read before it was answered.

---

## 10. Cold start

Every detector returns `available=False` with `score=None` and a human reason
when it cannot judge — never a fabricated 0:

- fewer than 3 estimation pairs → "Not enough historical data to estimate your typical task duration."
- no availability rules → "No availability is configured, so there is no capacity to compare scheduled work against."
- no earlier window → "There is no earlier period with recorded activity to compare against."

None of these produce a row or an event. The reasons travel on the evaluation
summary, so a new user's Risk Center says *"No significant risk detected yet"*
and the evaluation history explains *why* nothing was asserted. `evidence_strength`
is `LOW` whenever the sample is thin, so a two-task pattern is reported but never
presented as firm.

---

## 11. Tests

| Suite | Tests |
| --- | --- |
| Backend, **whole suite** | **1170 passing** |
| Backend, Phase 1–6 | 902 passing, behaviour unchanged |
| Backend, Phase 7 `test_risk_scoring.py` | 76 — **runs with no database** |
| Backend, Phase 7 `test_risk_detection.py` | 24 |
| Backend, Phase 7 `test_risk_api.py` | 75 |
| Backend, Phase 7 `test_risk_recommendation.py` | 93 |
| Frontend, **whole suite** | **422 passing** across 35 files |
| Frontend, Phase 1–6 | 361 passing |
| Frontend, Phase 7 `risk-components.test.tsx` | 40 |
| Frontend, Phase 7 `risk-center-page.test.tsx` | 11 |
| Frontend, Phase 7 `recommendations-page.test.tsx` | 10 |

268 backend and 61 frontend tests added by this phase — **329 in total**. `test_risk_scoring.py`
runs without PostgreSQL — verified by pointing `TEST_DATABASE_URL` at a dead port
and watching it still pass.

Every expected value is hand-derived from the formulas and asserted exactly. A
property-style grid asserts across 386 detector outputs that
`result.severity` always agrees with `risk_severity_for(result.score)`.

---

## 12. Final regression

| Gate | Result |
| --- | --- |
| `pytest tests/ -q` | **1170 passed** in 545 s, 0 failed |
| `ruff check app/ tests/` | clean |
| `ruff format --check app/ tests/` | clean, 135 files |
| `vitest run` | **422 passed** across 35 files |
| `tsc -b --noEmit` | clean |
| `eslint src/` | clean |
| `vite build` | succeeds, Phase 7 pages in separate lazy chunks |

**No `xfail` or `skip` markers remain** in the Phase 7 suite.

---

## 13. Known limitations

- **Evaluation is triggered by a request**, not a schedule. `POST
  /intelligence/evaluate` is manual, which is what the brief asks for absent
  background infrastructure. The service boundary is already the seam a worker
  would take; only the caller changes.
- **A risk that goes away is resolved on the next evaluation**, not the moment the
  condition clears. Until then the Risk Center shows a stale finding.
- ~~**Risk Center band filtering is client-side**~~ — **fixed.** `GET /risks`
  takes a `severity` parameter, served as a third equality column on
  `ix_risks_owner_status_severity`, so no migration was needed. `total` and
  `by_severity` now describe the filtered set, the tiles cannot disagree with the
  list, and the pager works under every filter combination.
- **There is no `/risks/{id}` detail route**, so the cards link to the underlying
  task or project rather than to a risk detail page.
- ~~**`EvaluationRead.evaluated` cannot be `false`**~~ — **fixed.**
  `scheduling_risk` now takes `sessions_considered` and declines when the plan it
  walked held no sessions. The gate is the session count rather than its own four
  output counts, because availability gated on a detector's own output would let
  it silence itself by miscounting. An account that has recorded nothing now
  answers "not enough data to assess this yet" instead of a confident empty report.
- **Write routes are gated by `analytics.read`.** There is no `analytics.write`
  permission in the map, and the contract said "every route". Deliberate, but it
  is worth an explicit decision.

---

## 14. Commands

```bash
# Backend
cd backend
./.venv/Scripts/python.exe -m pytest tests/ -q                 # full suite
./.venv/Scripts/python.exe -m pytest tests/ -q -k risk        # Phase 7 only
./.venv/Scripts/python.exe -m ruff check app/ tests/
./.venv/Scripts/python.exe -m ruff format --check app/ tests/

# Frontend
cd frontend
npx vitest run
npx vitest run src/features/risk src/pages/risk-center-page.test.tsx \
                src/pages/recommendations-page.test.tsx
npx tsc -b --noEmit
npx eslint src/
npx vite build
```

> **Do not run two pytest sessions against the same database.**
> `truncated_database` issues `TRUNCATE ... RESTART IDENTITY CASCADE` before
> every test, so parallel sessions destroy each other's rows. This cost real time
> during this phase, so `tests/conftest.py` now **enforces** it: a session takes a
> PostgreSQL advisory lock named after the test database and holds it for its
> whole run, and a second session on the same database aborts with an actionable
> message instead of corrupting the first. To run in parallel deliberately, point
> the second at its own database with `TEST_DATABASE_URL`.

---

## 15. Starting point for Phase 8

- **`RiskResult` is the interface Phase 10 replaces or augments.** A model's job
  is to produce the same shape — score, evidence, sample count — from data the
  deterministic rules cannot see. It plugs in at `RiskDetectionService.evaluate`,
  and the cold-start fallback is already the behaviour: the rules run, and the
  model's contribution is weighted by how much data backs it.
- **`feature_snapshot()` (Phase 6) and `risk_evaluations` (Phase 7) together are
  the training set**: the former is per-task features, the latter is when risk
  moved. Linking them needs a timestamp join, and `risk_evaluations.window_start`
  is the anchor.
- **Do not re-derive analytics in a risk rule.** `WorkloadRead` already answers
  the 30h/38h example in the brief; the temptation will be to compute the ratio
  again inside a rule, and two answers that can disagree is the failure mode this
  schema was designed to avoid.
- **`evidence_strength` is the honesty gate.** A model should be allowed to
  override a score only where its evidence strength beats the rule's, and that
  comparison needs no new machinery — both already speak `LOW`/`MEDIUM`/`HIGH`.