"""phase9_learning_career

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-01 00:00:00

Explicit DDL for Phase 9: ``skills``, ``learning_goals``, ``learning_activities``,
``career_profiles``, ``career_experience`` and ``career_evidence``.

Models are deliberately NOT imported here — as in ``0001`` through ``0008`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

What this migration creates, and what it pointedly does not
------------------------------------------------------------
Phase 9 is the first phase whose subject is a *person's self-description* rather
than their activity, and the governing rule of the phase is that NEXUS may never
be the author of it. Every column below that reads like a judgement is either a
number the user typed or a counter over rows the user created. There is no
column here that NEXUS fills in on its own to describe how good somebody is.

* **No ``assessed_level``, ``score``, ``proficiency`` or ``rating`` column.**
  :class:`~app.models.learning.Skill` has ``current_level`` and
  ``level_source``, and the source is the whole point: a level the user typed
  is a claim they are making, and one NEXUS derived is an inference it must be
  able to show its working for. Storing the verdict without the provenance would
  make the second case impossible to audit, which is the failure this phase
  exists to prevent.
* **No stored ``gap``.** A skill gap is computed on read
  (:mod:`app.services.learning.gaps`), for the same reason
  ``app/models/analytics.py`` keeps weekly and monthly metrics derived: a
  stored copy is a second answer that can silently disagree with the dashboard
  the user is actually looking at.
* **No generated biography, no ``highlights``, no ``strengths`` column.**
  Career content is entirely user-supplied. A column NEXUS could fill with a
  summary of somebody's career is a column whose contents would eventually be
  read as NEXUS vouching for them, and nothing here is written by NEXUS.
* **No ``hours_spent``, ``effort_actual`` or ``completion`` on an activity.**
  ``duration_minutes`` is the user's own account of one session, is nullable,
  and is null for the activity types that are events rather than spans. An
  activity that NEXUS derived from a repository carries its own ``source_type``
  so the reader is told it was derived rather than recorded — *"6 commits touched
  Python files in this repository"* is true, *"6 Python tasks completed"* is a
  different claim and there is nowhere in this schema to store the second one.
* **No ML columns.** ``GET /learning/features`` produces named numbers; nothing
  here stores a model, a coefficient or a version of one.

The one non-obvious ordering decision in this file
-------------------------------------------------
``skills`` is created **before** ``learning_goals`` even though the contract
numbers the goal table first. ``learning_goals.target_skill_id`` and
``career_evidence.skill_id`` carry foreign keys onto ``skills.id``, and
PostgreSQL resolves a ``REFERENCES`` clause when the statement runs, so a table
that does not exist yet is a hard error rather than a deferred check. The same
argument fixes ``learning_goals`` ahead of ``learning_activities`` and
``skills`` ahead of ``career_evidence``. Numbering in the contract is prose
order; this is dependency order.

The constraints, and why each earns its place
---------------------------------------------
* ``uq_skills_owner_name`` — one skill row per (account, name). Not global: two
  people may both have a skill called "Testing", and the skill list is filtered
  on owner before a name is ever resolved, so a global name would only make the
  second account's skill a conflict it cannot resolve without reading somebody
  else's vocabulary.
* **``career_profiles.user_id`` is UNIQUE and unnamed.** This is the only
  uniqueness in the phase that is not a named constraint, and it is the only
  one that is *not* the lookup path. It is what turns ``PUT /career/profile``
  into an upsert instead of letting a second profile row appear for one
  account. It is separate from ``ix_career_profiles_user_id`` — which exists to
  serve the owner probe — because the model declares them that way: a bare
  ``unique=True`` on the column plus a named plain ``Index``. Folding them into
  one object the way ``users.email`` does in ``0001`` would be a different
  schema, and ``alembic check`` would say so.
* ``uq_career_evidence_source_identity`` — the deduplication anchor, and a
  *full* unique constraint over
  ``(user_id, evidence_type, source, project_id, skill_id, repository_id)``.
  PostgreSQL treats NULLs as distinct in a btree unique index, which is exactly
  the behaviour wanted here: several manually-added ``ACHIEVEMENT`` rows (all
  three FKs null) coexist, while a project-derived one cannot be inserted twice.
  This is the same trick ``uq_risks_live_identity`` uses in Phase 7, except that
  one is a partial index because a resolved risk is still a row worth keeping;
  here there is no such second state.
* ``ck_skills_current_level_range`` / ``ck_skills_target_level_range`` — levels
  are 1-5 throughout. Five is enough to be useful and few enough that the
  difference between 3 and 4 means something; a wider scale would invite
  precision the evidence does not support.
* ``ck_skills_confidence_range`` — confidence is 0-100 and is the answer to
  "how much evidence backs this estimate", so it is bounded rather than open. A
  stored confidence of 250 is not enthusiasm, it is a broken write.
* ``ck_learning_goals_progress_range`` — a percentage. The 0 and 100 bounds are
  where a percentage stops meaning one.
* ``ck_learning_goals_completed_has_terminal_status`` — a goal cannot carry a
  completion timestamp while claiming to be in progress or paused. The reverse
  (a completed goal with no timestamp) is deliberately permitted, because the
  timestamp records when *the user* marked it done and a bulk import may not
  know that.
* ``ck_career_experience_dates_in_order`` — ``ended_on`` before ``started_on`` is
  a typo, not a fact. Either end may be null: an open-ended role or an undated
  one is a normal record, and nulls here are an absence of measurement, not a
  zero.
* ``ck_learning_activities_duration_non_negative`` — a negative duration is not a
  thin measurement but a broken one. Null is legitimate and stored: an activity
  can be an event ("read a chapter") rather than a span.

Why ``learning_activities`` has no ``updated_at``
-------------------------------------------------
It is append-only. A recorded activity is a fact about a moment, and
:class:`~app.db.base.TimestampMixin` stamps ``updated_at`` on every UPDATE, so
putting it here would assert a revision that does not exist — the same argument
that puts no ``updated_at`` on ``git_commits`` in ``0008``. Correcting a
mistaken activity means recording the correction, not rewriting the past.

Every foreign key here is SET NULL or CASCADE, and which is which is a product
decision rather than a default
------------------------------------------------------------------
``user_id`` cascades on all six tables: a row nobody can reach is a row nothing
can read. ``learning_activities.skill_id`` cascades too, because an activity
that names a deleted skill is an activity with no subject. But every *context*
reference — a goal's project, a goal's note, an activity's goal, evidence's
project / skill / repository — is SET NULL, so that deleting a project or a
skill leaves the recorded trail standing. Evidence that vanishes with the thing
it describes cannot be evidence of anything, which is the same reasoning that
puts SET NULL on ``activity_events`` in Phase 3.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # skills
    # ------------------------------------------------------------------
    # First, deliberately: three later tables carry a foreign key onto
    # `skills.id`, and PostgreSQL resolves a REFERENCES clause as the
    # statement runs. See the module docstring.
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # What the user calls this skill. Bounded because a skill name is a
        # label in a list, not a paragraph.
        sa.Column("name", sa.String(length=120), nullable=False),
        # `language`, `framework`, `domain`, `practice` are suggestions, not a
        # closed set: a column too narrow for the next category would be a
        # migration, and this vocabulary is the user's to extend.
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # The user's, or a labelled estimate. `level_source` below is what
        # makes the difference legible; `current_level` alone would be a
        # verdict with no provenance.
        sa.Column("current_level", sa.Integer(), server_default=sa.text("1"), nullable=False),
        # Defaulted to 3, not null: "I want to get better at this" is a goal
        # expressed by having a target at all, and a null target would make
        # every such skill read as unmeasured.
        sa.Column("target_level", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "level_source",
            sa.String(length=24),
            server_default="user_defined",
            nullable=False,
        ),
        # 0-100: how much evidence backs an estimate. A stored 0 on a
        # `user_defined` level is not "no confidence", it is "nothing to
        # weigh", which is why the field exists at all.
        sa.Column("confidence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # A counter over rows the user created, not a judgement.
        sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Null for a skill with no recorded activity. Zero would claim a
        # measurement of the skill's idleness, which is not the same fact.
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One skill row per (account, name); two people may each have a
        # "Testing" skill and neither is a conflict.
        sa.UniqueConstraint("user_id", "name", name="uq_skills_owner_name"),
        sa.CheckConstraint(
            "current_level >= 1 AND current_level <= 5",
            name="ck_skills_current_level_range",
        ),
        sa.CheckConstraint(
            "target_level >= 1 AND target_level <= 5",
            name="ck_skills_target_level_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_skills_confidence_range",
        ),
    )
    # The account probe every Phase 9 read of a skill starts with.
    op.create_index("ix_skills_user_id", "skills", ["user_id"], unique=False)

    # ------------------------------------------------------------------
    # learning_goals
    # ------------------------------------------------------------------
    op.create_table(
        "learning_goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # SET NULL, not CASCADE: a goal can name a topic before the skill
        # exists, and losing the skill row must not delete what the user was
        # working towards.
        sa.Column("target_skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The free-text form of the same idea, for exactly the case above —
        # "I want to learn Rust" before anyone has added a Rust skill row.
        sa.Column("target_topic", sa.String(length=200), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        # Reuses `ProjectPriority` in the model layer rather than a second
        # near-identical vocabulary: the same four values with the same
        # meaning, so the two tables have no way to disagree.
        sa.Column("priority", sa.String(length=16), server_default="medium", nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="not_started",
            nullable=False,
        ),
        # The user's own percentage. 0 is a real answer — a goal nobody has
        # started — and is stored rather than left null.
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # The user's estimate, never NEXUS's. Null means they have not said.
        sa.Column("estimated_effort_minutes", sa.Integer(), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The knowledge relationship: a goal may hang off a note, and the
        # note's deletion must not delete the goal.
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
        # When the user marked it done. Nullable for the same reason the
        # constraint below allows a completed goal without it.
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_learning_goals_progress_range",
        ),
        # The one direction that is refused: a timestamp claiming completion on
        # a goal that says it is still in progress. The reverse is allowed,
        # because the timestamp records when the user said so.
        sa.CheckConstraint(
            "completed_at IS NULL OR status = 'completed'",
            name="ck_learning_goals_completed_has_terminal_status",
        ),
    )
    op.create_index("ix_learning_goals_user_id", "learning_goals", ["user_id"], unique=False)
    # "My active goals", the default list and the recommendation rule's probe.
    # The bare `user_id` index above cannot serve it: it carries no predicate
    # or ordering on `status`.
    op.create_index(
        "ix_learning_goals_owner_status",
        "learning_goals",
        ["user_id", "status"],
        unique=False,
    )
    # The deadline rule — goals approaching a target date, soonest first — and
    # the feature set's `goal_deadline_distance_days`.
    op.create_index(
        "ix_learning_goals_owner_target_date",
        "learning_goals",
        ["user_id", "target_date"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # learning_activities
    # ------------------------------------------------------------------
    op.create_table(
        "learning_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # CASCADE, unlike every context reference in this migration: an
        # activity that names a deleted skill is an activity with no subject.
        # The learning record survives — a study session may name no skill at
        # all — but an activity whose only subject is gone is not evidence of
        # anything.
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        # SET NULL: the trail outlives the goal it was recorded against.
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("activity_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Null when the activity is an event rather than a span — reading a
        # page is not something with a duration. An absence of measurement,
        # never a zero-minute session.
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        # The polymorphic pair, exactly as `risks.entity_type` / `entity_id`
        # already is: `manual`, `task`, `note`, `project`, `repository`, with
        # `source_id` naming the record it came from. Not a foreign key on
        # purpose — five tables cannot all be the target of one constraint,
        # and the service is what validates the pairing.
        sa.Column("source_type", sa.String(length=32), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        # `created_at` alone, deliberately. A recorded activity is a fact
        # about a moment and `TimestampMixin` would stamp `updated_at` on
        # every UPDATE, asserting a revision that does not exist.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes >= 0",
            name="ck_learning_activities_duration_non_negative",
        ),
    )
    op.create_index(
        "ix_learning_activities_user_id", "learning_activities", ["user_id"], unique=False
    )
    # Every windowed read in the phase: the timeline, every metric window, and
    # the 7d/30d feature columns. A bare `user_id` index cannot serve it — that
    # one carries no ordering on `occurred_at`.
    op.create_index(
        "ix_learning_activities_user_occurred",
        "learning_activities",
        ["user_id", "occurred_at"],
        unique=False,
    )
    # The evidence count behind a skill's `evidence_count` and the per-skill
    # gap evidence figure.
    op.create_index(
        "ix_learning_activities_skill_id", "learning_activities", ["skill_id"], unique=False
    )
    # "What was recorded against this goal", and the SET NULL that fires when
    # the goal is deleted.
    op.create_index(
        "ix_learning_activities_goal_id", "learning_activities", ["goal_id"], unique=False
    )

    # ------------------------------------------------------------------
    # career_profiles
    # ------------------------------------------------------------------
    op.create_table(
        "career_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_role", sa.String(length=200), nullable=True),
        sa.Column("target_domain", sa.String(length=120), nullable=True),
        sa.Column("headline", sa.String(length=200), nullable=True),
        # The user's own words. Never generated, never summarised by NEXUS,
        # and never rewritten — there is nowhere in this schema for a second
        # copy that could drift from it.
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        # Portfolio URLs, user-supplied. A JSONB array rather than a
        # ``career_links`` table: the links have no dates, no metadata and no
        # references of their own, so a table would be six columns of
        # surrogate key to store strings.
        sa.Column(
            "links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One profile per account, and it is a bare constraint rather than a
        # unique index: the model declares it with `unique=True` on the column
        # and carries `ix_career_profiles_user_id` as a separate plain Index, so
        # the rule and the lookup path are two objects here. Unnamed on purpose
        # — the model does not name it either, and PostgreSQL's generated name
        # (`career_profiles_user_id_key`) is the one `Base.metadata` reflects
        # back, so naming it here would be drift `alembic check` would report.
        sa.UniqueConstraint("user_id"),
    )
    # The owner probe. Deliberately NOT unique: the uniqueness above is a
    # separate constraint, and folding the two together (the
    # `unique=True, index=True` idiom `users.email` uses) would be a different
    # schema than the one the model declares.
    op.create_index("ix_career_profiles_user_id", "career_profiles", ["user_id"], unique=False)

    # ------------------------------------------------------------------
    # career_experience
    # ------------------------------------------------------------------
    op.create_table(
        "career_experience",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # `education`, `experience`, `certification`. A single table with a
        # discriminator rather than three: they share every column, they are
        # read as one ordered list, and none of the three carries a field the
        # others need.
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("organisation", sa.String(length=200), nullable=True),
        # Both ends nullable: an undated entry is a normal record, and a null
        # `ended_on` means *current* rather than *unknown*.
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        # The user's own description of the entry. Nothing here is inferred
        # from a repository, a commit or an employment record NEXUS does not
        # have — career content is entirely user-supplied.
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # `ended_on` before `started_on` is a typo, not a fact. The guard is
        # disjunctive on purpose: either end may legitimately be null, and a
        # constraint that rejected those would refuse honest records.
        sa.CheckConstraint(
            "ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on",
            name="ck_career_experience_dates_in_order",
        ),
    )
    op.create_index(
        "ix_career_experience_user_id", "career_experience", ["user_id"], unique=False
    )

    # ------------------------------------------------------------------
    # career_evidence
    # ------------------------------------------------------------------
    op.create_table(
        "career_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # A date, not a timestamp: "shipped in March" is the fact being
        # recorded, and a time of day would be a precision nobody supplied.
        sa.Column("occurred_on", sa.Date(), nullable=False),
        # Every context reference is SET NULL. Evidence that disappears along
        # with the project it was recorded against is not evidence of
        # anything, and deleting a repository must not quietly delete the
        # user's record that they shipped something.
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=True),
        # `manual`, or the subsystem the row was derived from. This is the
        # column that keeps a derived row labelled as derived — a
        # `repository` row says where it came from rather than claiming to be
        # something the user asserted.
        sa.Column("source", sa.String(length=64), server_default="manual", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["repository_id"], ["git_repositories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # THE DEDUPLICATION ANCHOR, and the whole mechanism. PostgreSQL treats
        # NULLs as distinct in a btree unique index, so several manually-added
        # ACHIEVEMENT rows (all three FKs null) coexist while a project-derived
        # one cannot be inserted twice. The same trick `uq_risks_live_identity`
        # uses in Phase 7, but a full constraint rather than a partial index
        # because unlike a risk there is no "resolved" state for evidence.
        sa.UniqueConstraint(
            "user_id",
            "evidence_type",
            "source",
            "project_id",
            "skill_id",
            "repository_id",
            name="uq_career_evidence_source_identity",
        ),
    )
    op.create_index("ix_career_evidence_user_id", "career_evidence", ["user_id"], unique=False)
    # The evidence list ordered newest first, and the windowed read behind the
    # career feature columns.
    op.create_index(
        "ix_career_evidence_user_occurred",
        "career_evidence",
        ["user_id", "occurred_on"],
        unique=False,
    )


def downgrade() -> None:
    # Reverse dependency order, and the order is load-bearing rather than
    # alphabetical: `learning_activities` holds foreign keys onto both `skills`
    # and `learning_goals`, and `career_evidence` holds one onto `skills`, so
    # `skills` is dropped last — that is what keeps the reversal legal at every
    # intermediate step. Within the independent career tables nothing
    # references anything else, so the order among `career_evidence`,
    # `career_experience` and `career_profiles` is for readability, most
    # specific first.
    #
    # No index is dropped explicitly: every index below belongs to a table
    # being dropped in this same statement, and PostgreSQL removes it with the
    # table. Naming them again would only give `DROP INDEX` a chance to fail on
    # something the table drop had already made irrelevant.
    op.drop_table("career_evidence")
    op.drop_table("career_experience")
    op.drop_table("career_profiles")
    op.drop_table("learning_activities")
    op.drop_table("learning_goals")
    op.drop_table("skills")
