# Phase 9 — Learning + Career Intelligence: Frozen Contracts

> **Status: 🔴 frozen.** Every agent in the Phase 9 swarm codes against this file. Phase 8's
> contract is at `phase-8-developer-contracts.md` and its code is already in the tree. If an
> implementation needs a change here, it stops and reports rather than inventing its own shape.

---

## 0. Governing rules (from the brief, and not negotiable)

1. **Levels are the user's, or visibly derived.** NEXUS never pretends to know how good
   someone is at something. A `current_level` is either `level_source='user_defined'` — the
   person set it — or `system_estimate`, and every estimate names the evidence it came from.
   Never: *"You are not good at Machine Learning."* Instead: *"Your current self-assessed
   level is 2/5. NEXUS recorded 6 related learning activities in the last 30 days."*
2. **Do not invent qualifications.** A career profile is entirely user-controlled. NEXUS
   never writes a certification, employer or date the user did not supply.
3. **Commits are not task completion.** Learning evidence drawn from repositories, tasks and
   notes stays labelled as what it is. *"6 commits touched Python files in this repository"*
   is true; *"6 Python tasks completed"* is a different claim and is not made.
4. **Evidence is traceable.** Every learning activity names where it came from.
5. **Server-side ownership, always.** Never trust a user id from the client. Another account's
   goal, skill, activity, profile or evidence is a **404, never a 403**.
6. **No ML.** §9.11 produces features. It does not train, load, serve or register anything.

---

## 1. Enumerations — `app/models/enums.py`

Five new `StrEnum`s, each with a docstring arguing *why the value exists* in the house style,
each with a matching `validate_*` helper added to `__all__`:

```python
class LearningGoalStatus(StrEnum):
    """Where a learning goal sits in its own life.

    ``ARCHIVED`` is separate from ``COMPLETED`` because a finished goal the user
    still wants as a record and a goal they have dismissed are different facts,
    and an archived goal must not count as incomplete work anywhere.
    """
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SkillLevelSource(StrEnum):
    """Who is allowed to claim a number for a skill level.

    This is the single most important honesty control in Phase 9. A level the
    user typed is a claim they are making and NEXUS merely records; a level
    NEXUS derived is an inference it must be able to show its working for.
    """
    USER_DEFINED = "user_defined"
    SYSTEM_ESTIMATE = "system_estimate"


class LearningActivityType(StrEnum):
    """What kind of event counts as evidence that something was learned.

    Each member is a fact NEXUS can point at a record for. None of them implies
    understanding — ``RESOURCE_VIEWED`` in particular records that a page was
    opened, which is the weakest of these and is weighted as such.
    """
    STUDY_SESSION = "study_session"
    TASK_COMPLETED = "task_completed"
    NOTE_CREATED = "note_created"
    RESOURCE_VIEWED = "resource_viewed"
    CONCEPT_LEARNED = "concept_learned"
    PROJECT_COMPLETED = "project_completed"
    CODING_ACTIVITY = "coding_activity"


class CareerEvidenceType(StrEnum):
    """A thing worth putting in front of someone who is deciding about you."""
    PROJECT_COMPLETED = "project_completed"
    FEATURE_SHIPPED = "feature_shipped"
    REPOSITORY_ACTIVITY = "repository_activity"
    SKILL_ACTIVITY = "skill_activity"
    LEARNING_MILESTONE = "learning_milestone"
    CERTIFICATION = "certification"
    ACHIEVEMENT = "achievement"


class CareerRecordKind(StrEnum):
    """A line on the career profile that is a record rather than an achievement."""
    EDUCATION = "education"
    EXPERIENCE = "experience"
    CERTIFICATION = "certification"
```

**`LearningGoal.priority` reuses the existing `ProjectPriority`.** It carries the same four
values with the same meaning, so a second near-identical vocabulary would only give the two
tables a way to disagree.

`ActivityEvent` gains ten members under a `# -- Phase 9 (Learning & Career) ---` banner:
`LEARNING_GOAL_CREATED`, `LEARNING_GOAL_UPDATED`, `LEARNING_GOAL_COMPLETED`,
`LEARNING_SESSION_RECORDED`, `SKILL_CREATED`, `SKILL_UPDATED`, `SKILL_ACTIVITY_RECORDED`,
`CAREER_PROFILE_UPDATED`, `CAREER_EVIDENCE_ADDED`, `CAREER_EVIDENCE_UPDATED`.

---

## 2. Schema — migration `0009`

Revision `"0009"`, `down_revision = "0008"`, filename `0009_phase9_learning_career.py`,
explicit DDL, **no `app.models` import**, `postgresql.JSONB(astext_type=sa.Text())`,
`server_default=sa.text("now()")` for timestamps, check constraints inside `create_table`,
indexes outside, every `op.create_index` passing `unique=False` explicitly.

### 2.1 `learning_goals`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | `UUIDPrimaryKeyMixin` |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `title` | `String(200)` | not null |
| `description` | `Text` | nullable |
| `target_skill_id` | `UUID` FK `skills.id` `ON DELETE SET NULL` | nullable — a goal can name a topic before the skill exists |
| `target_topic` | `String(200)` | nullable — the free-text form of the same idea |
| `target_date` | `Date` | nullable |
| `priority` | `String(16)` server_default `'medium'` | not null |
| `status` | `String(16)` server_default `'not_started'` | not null |
| `progress` | `Integer` server_default `"0"` | not null |
| `estimated_effort_minutes` | `Integer` nullable | the user's own estimate, not NEXUS's |
| `project_id` | `UUID` FK `projects.id` `ON DELETE SET NULL` | nullable |
| `note_id` | `UUID` FK `notes.id` `ON DELETE SET NULL` | nullable — the knowledge relationship |
| `completed_at` | `DateTime(timezone=True)` | nullable |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (
    Index("ix_learning_goals_user_id", "user_id"),
    Index("ix_learning_goals_owner_status", "user_id", "status"),
    Index("ix_learning_goals_owner_target_date", "user_id", "target_date"),
    CheckConstraint("progress >= 0 AND progress <= 100", name="ck_learning_goals_progress_range"),
    CheckConstraint(
        "completed_at IS NULL OR status = 'completed'",
        name="ck_learning_goals_completed_has_terminal_status",
    ),
)
```

### 2.2 `skills`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `name` | `String(120)` | not null |
| `category` | `String(64)` | nullable — `language`, `framework`, `domain`, `practice` are suggestions, not a closed set |
| `description` | `Text` | nullable |
| `current_level` | `Integer` server_default `"1"` | not null — **the user's, or a labelled estimate** |
| `target_level` | `Integer` server_default `"3"` | not null |
| `level_source` | `String(24)` server_default `'user_defined'` | not null |
| `confidence` | `Integer` server_default `"0"` | not null — 0–100, how much evidence backs an estimate |
| `evidence_count` | `Integer` server_default `"0"` | not null |
| `last_activity_at` | `DateTime(timezone=True)` | nullable |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (
    UniqueConstraint("user_id", "name", name="uq_skills_owner_name"),
    Index("ix_skills_user_id", "user_id"),
    CheckConstraint(
        "current_level >= 1 AND current_level <= 5", name="ck_skills_current_level_range"
    ),
    CheckConstraint(
        "target_level >= 1 AND target_level <= 5", name="ck_skills_target_level_range"
    ),
    CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_skills_confidence_range"),
)
```

Levels are 1–5 throughout. Five is enough to be useful and few enough that the difference
between 3 and 4 means something.

### 2.3 `learning_activities`

Append-only: `UUIDPrimaryKeyMixin, Base` — **no `updated_at`**, because a recorded activity is
a fact about a moment.

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `skill_id` | `UUID` FK `skills.id` `ON DELETE CASCADE` | nullable — a study session may name no skill yet |
| `goal_id` | `UUID` FK `learning_goals.id` `ON DELETE SET NULL` | nullable — the trail outlives the goal |
| `activity_type` | `String(32)` | not null |
| `title` | `String(200)` | not null |
| `description` | `Text` | nullable |
| `occurred_at` | `DateTime(timezone=True)` server_default `now()` | not null |
| `duration_minutes` | `Integer` nullable | null when the activity is an event rather than a span |
| `source_type` | `String(32)` nullable | `manual`, `task`, `note`, `project`, `repository` |
| `source_id` | `UUID` nullable | the record it came from |

```python
__table_args__ = (
    Index("ix_learning_activities_user_id", "user_id"),
    Index("ix_learning_activities_user_occurred", "user_id", "occurred_at"),
    Index("ix_learning_activities_skill_id", "skill_id"),
    Index("ix_learning_activities_goal_id", "goal_id"),
    CheckConstraint(
        "duration_minutes IS NULL OR duration_minutes >= 0",
        name="ck_learning_activities_duration_non_negative",
    ),
)
```

`source_type` + `source_id` is a polymorphic pair, exactly as `risks.entity_type` /
`entity_id` already is in this codebase. Nulls do not collide in a btree index, so a goal with
no linked source and one with a linked source are both representable.

### 2.4 `career_profiles`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null, **unique** — one profile per account |
| `target_role` | `String(200)` | nullable |
| `target_domain` | `String(120)` | nullable |
| `headline` | `String(200)` | nullable |
| `summary` | `Text` | nullable — the user's own words, never generated |
| `location` | `String(200)` | nullable |
| `links` | `JSONB` server_default `"[]"` `default=list` | not null — portfolio URLs, user-supplied |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (Index("ix_career_profiles_user_id", "user_id"),)
```

The unique constraint on `user_id` is what makes `PUT /career/profile` an upsert rather than a
second profile appearing.

### 2.5 `career_experience`

Education, work experience and certifications — the dated records a profile is made of.

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `kind` | `String(24)` | not null |
| `title` | `String(200)` | not null |
| `organisation` | `String(200)` | nullable |
| `started_on` | `Date` | nullable |
| `ended_on` | `Date` | nullable — null means current |
| `description` | `Text` | nullable |
| `url` | `String(500)` | nullable |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (
    Index("ix_career_experience_user_id", "user_id"),
    CheckConstraint(
        "ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on",
        name="ck_career_experience_dates_in_order",
    ),
)
```

### 2.6 `career_evidence`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `evidence_type` | `String(32)` | not null |
| `title` | `String(200)` | not null |
| `description` | `Text` | nullable |
| `occurred_on` | `Date` | not null |
| `project_id` | `UUID` FK `projects.id` `ON DELETE SET NULL` | nullable |
| `skill_id` | `UUID` FK `skills.id` `ON DELETE SET NULL` | nullable |
| `repository_id` | `UUID` FK `git_repositories.id` `ON DELETE SET NULL` | nullable |
| `source` | `String(64)` server_default `'manual'` | not null — `manual` or the subsystem it was derived from |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (
    UniqueConstraint(
        "user_id", "evidence_type", "source", "project_id", "skill_id", "repository_id",
        name="uq_career_evidence_source_identity",
    ),
    Index("ix_career_evidence_user_id", "user_id"),
    Index("ix_career_evidence_user_occurred", "user_id", "occurred_on"),
)
```

**Nulls do not collide in a btree unique index**, so several manually-added `ACHIEVEMENT`
rows coexist while a project-derived one cannot be inserted twice. That is the whole
deduplication mechanism, and it is the same trick `uq_risks_live_identity` already uses.

### 2.7 Registration checklist

- `app/models/__init__.py` — imports + flat alphabetised `__all__`.
- `app/models/enums.py` — the five enums, five validators, ten `ActivityEvent` members.
- `tests/test_migration_ddl.py` — append `"migrations.versions.0009_phase9_learning_career"` to
  `MIGRATION_MODULES`, add `"0009": "0008"` to its down-revision map and `"0009"` to its walk
  list and head assertion, add the FK-per-table counts for the six new tables, and add the new
  indexes to the model-match parametrisation.
- `tests/test_migrations.py` — same three edits (`"0009"` into the walk list at ~line 48, the
  map at ~line 69, and `head_revision() == "0009"` at ~line 77).

---

## 3. Configuration — `app/core/config.py`

Appended additively, each with a `#:` comment, prefixed `learning_` / `career_`:

```
learning_default_window_days: int = 30
learning_max_window_days: int = 366
learning_max_goals: int = 200
learning_max_skills: int = 100
learning_min_evidence_for_estimate: int = 3     # below this an estimate is refused
career_max_evidence: int = 500
career_stale_inactive_days: int = 21           # when a target skill counts as dormant
```

---

## 4. Skill gap — pure, no model stores it

`app/services/learning/gaps.py`, pure functions. **A gap is computed on read, never stored** —
the same argument `models/analytics.py` makes about weekly and monthly metrics: a stored copy
would be a second answer that could disagree with the dashboard.

```python
@dataclass(frozen=True, slots=True)
class SkillGap:
    skill_id: uuid.UUID | None
    skill_name: str
    target_level: int
    current_level: int
    level_source: SkillLevelSource
    gap: int                      # max(0, target - current); 0 is a real measurement
    evidence_count: int
    evidence_last_30d: int
    days_since_last_activity: int | None
    available: bool
    reason_if_unavailable: str | None
    explanation: str              # must name the levels AND the evidence count
```

- `current_level` comes from `Skill.current_level`, and `level_source` decides whether it may
  be described as *self-assessed* or as a *system estimate*.
- `evidence_last_30d` counts `learning_activities` rows for that skill in the window.
- **The explanation must contain a digit** and must read as a fact, e.g.
  *"Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning activities in the
  last 30 days."* — enforced the way `RecommendationDraft.__post_init__` rejects a reason with
  no digit in it.
- A skill with no target set above its current level reports `gap=0, available=True`. A skill
  with nothing recorded at all reports `available=False` with a reason. **A measured zero gap
  and an unmeasured one are different answers.**
- When `learning_min_evidence_for_estimate` is not met, no estimate is offered: the response
  says so rather than inventing a level.

---

## 5. HTTP API

Two routers, `prefix="/learning"` and `prefix="/career"`, both reusing
`Permission.ANALYTICS_READ` (the Phase 7/8 precedent). **No new `Permission` member** —
`tests/test_permissions.py` asserts the full member set.

Literal sub-paths (`/summary`, `/metrics`, `/gaps`, `/features`, `/activity`) are declared
**before** `/{id}` paths, because Starlette matches in declaration order.

### `/learning`

| method | path |
|---|---|
| `GET` | `/learning/summary` |
| `GET` | `/learning/metrics` |
| `GET` | `/learning/gaps` |
| `GET` | `/learning/activity` |
| `GET` | `/learning/features` |
| `GET` `POST` | `/learning/goals` |
| `GET` `PATCH` `DELETE` | `/learning/goals/{goal_id}` |
| `POST` | `/learning/goals/{goal_id}/complete` |
| `GET` `POST` | `/learning/skills` |
| `GET` `PATCH` `DELETE` | `/learning/skills/{skill_id}` |
| `GET` `POST` | `/learning/activities` |

### `/career`

| method | path |
|---|---|
| `GET` | `/career/summary` |
| `GET` `PUT` | `/career/profile` |
| `GET` `POST` | `/career/experience` |
| `PATCH` `DELETE` | `/career/experience/{experience_id}` |
| `GET` `POST` | `/career/evidence` |
| `PATCH` `DELETE` | `/career/evidence/{evidence_id}` |

`PUT /career/profile` upserts on the unique `user_id`. Completing a goal sets
`completed_at` and emits `LEARNING_GOAL_COMPLETED`. Recording an activity bumps
`Skill.evidence_count` / `last_activity_at` and emits `LEARNING_SESSION_RECORDED`.

### 9.9 Learning recommendations

Extends `app/services/risk/recommendation.py` with new `RecommendationType` members and rules,
following the existing `recommendation_rules` registry pattern rather than a parallel path. Two
rules minimum, both deterministic, each carrying what / why / evidence / suggested action:

- a goal with a deadline approaching and low recorded progress;
- a target skill inactive beyond `career_stale_inactive_days`.

**No ML.** No scoring model, no learned ranking.

---

## 6. ML-ready features

`GET /learning/features` and `GET /career/features` return named numbers under a
`schema_version`, exactly as Phase 8's `developer_features.v1` does — so a Phase 10 trainer
knows what each column meant:

```
learning_features.v1 : sessions_last_7d, sessions_last_30d, learning_minutes, goal_progress,
                       goal_deadline_distance_days, completion_rate, learning_consistency,
                       skill_activity_frequency
career_features.v1   : projects_completed, project_activity, repositories,
                       relevant_skill_evidence, learning_activity, portfolio_evidence_count
```

A figure that could not be computed is `null`, **never 0** — `0` would be a claim about a
repository with no commits, when the truth is that it has never been scanned.

No model, no training, no inference, no registry.

---

## 7. Frontend

`/learning` and `/career` **already exist as routes, sidebar entries and command-palette
items** — each is a 6-line `ModulePage` stub. Replace the bodies; add no new nav entries and do
not touch `sidebar.tsx` or `command-palette.tsx`.

New `src/types/learning.ts`, `src/services/learning.ts`, `src/features/learning/**` and
`src/features/career/**`, mirroring the Phase 8 structure: every nullable is `T | null` and
required; a figure that could not be computed is `null`, never `0`; every formatter takes
`number | null` and returns `—`; empty copy explains why it is empty; insufficient data reads
**"Not enough data yet."**; charts go through `LazyChart`; reuse `ChartShell`, `ChartTooltip`,
`TrendChart`, `AnalyticsBarChart`, `MetricCard`, `ScoreCard` from `features/analytics` rather
than re-implementing.

Update the `/learning` and `/career` entries in `features/modules/catalog.ts` to describe what
shipped, keeping `to`, `label`, `icon` and `keywords` unchanged.

---

## 8. Phase 10 boundary

Phase 9 produces features. It does not train, load, serve or register a model. No Kaggle, no
MLflow, no inference endpoint, no Ollama, no voice assistant.
