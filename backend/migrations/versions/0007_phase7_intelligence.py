"""phase7_intelligence

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-01 00:00:00

Explicit DDL for the Phase 7 risk and recommendation engine: ``risks``,
``recommendations`` and ``risk_evaluations``.

Models are deliberately NOT imported here — as in ``0001`` through ``0006`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

What this migration creates, and what it pointedly does not
------------------------------------------------------------
The Phase 7 brief asks for risk objects, recommendations, and "risk snapshots
where appropriate", with an explicit warning against creating excessive
duplicate snapshots. That warning shapes the whole schema, so it is worth
restating here where a future reader will hit the question:

* **One risk row per live condition, not one per evaluation.** The two
  ``uq_*_identity`` indexes below are *partial* unique indexes. They are the
  mechanism behind the brief's "the same underlying risk should not generate
  hundreds of identical records", and they are partial rather than plain
  precisely so that a risk which resolved and then legitimately returned is
  recordable as a new observation instead of colliding with its own history.

* **No per-risk score history table.** A risk score is a pure function of the
  analytics it reads, so a stored time series of scores would be a second answer
  that could disagree with the one the dashboard is currently showing. The
  training set Phase 10 wants is re-derivable by re-running detection over a
  past window, and ``app.services.analytics.service.AnalyticsService`` already
  extracts its half of it on demand.

* **One summary row per evaluation run, not one per risk per run.** ``risks``
  carries the durable per-risk record; ``risk_evaluations`` carries only the
  counts, the timing and the window a run reasoned over. That makes the table
  bounded by (evaluations) rather than (evaluations x risks) — a table that
  grows when you look at the page, instead of one that grows when you have work
  to do.

Why ``entity_type`` is part of the deduplication key
----------------------------------------------------
The obvious key is ``(user_id, risk_type, entity_id)``. It is wrong. Task ids
and project ids are uuids from the same space, so "task 7 is at risk" and
"project 7 is at risk" would collide, and the second detection would silently
swallow the first — the user would see one risk where there are two conditions
of different kinds. ``entity_type`` disambiguates them.

The key is also deliberately four columns wide and *not* including ``score``:
including the score would make every re-detection a new row, which is exactly
the duplication the index exists to prevent. A changed score updates the
existing row; only a changed *identity* is a new risk.

Why ``entity_id`` is nullable
------------------------------
The workload and consistency detectors both produce risks that are about the
account rather than about a row — "your next seven days are overcommitted" has
no task id. PostgreSQL treats NULLs as distinct in a unique btree index, so
several distinct account-level risks of the same type can coexist, which is the
behaviour we want: "workload risk" and "workload risk for next week" are
different conditions that happen to share a type.

The constraints, and why each earns its place
---------------------------------------------
* ``ck_risks_score_range`` — a score outside 0-100 could not be banded, so a row
  carrying one would have no severity and the Risk Center could not sort it.
* ``ck_risks_terminal_has_timestamp`` — the reverse direction of the lifecycle
  is what actually bites: ``resolved_at`` set on a risk that is still ``active``
  means two queries disagree about whether it is open, and "how long was this
  open" silently answers with a wrong number.
* ``ck_recommendations_priority_known`` — ``priority`` is not derived from a
  CHECK on a band the way ``risks.severity`` is, because a recommendation may
  come from a rule that fires below the risk threshold. Restricting it to the
  four known words stops a typo from producing a priority no sort can order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # risks
    # ------------------------------------------------------------------
    op.create_table(
        "risks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_type", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), server_default="low", nullable=False),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # The ordered "why". Stored rather than reconstructed because the data it
        # was computed from has been rebuilt since; without it a stored risk
        # could not explain itself, which is the one thing it must always do.
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("evidence_strength", sa.String(length=16), server_default="low", nullable=False),
        # Nullable together: an account-level risk (workload, consistency) has
        # no row to point at.
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
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
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_risks_score_range",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR status IN ('resolved', 'dismissed')",
            name="ck_risks_terminal_has_timestamp",
        ),
    )
    # THE DEDUPLICATION ANCHOR. A partial unique index rather than a table-level
    # constraint because "at most one LIVE risk per condition" is the actual
    # invariant: a resolved risk must be able to return as a new observation.
    op.create_index(
        "uq_risks_live_identity",
        "risks",
        ["user_id", "risk_type", "entity_type", "entity_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'acknowledged')"),
    )
    # The Risk Center's default query is "my live risks, worst first". The
    # partial unique index above cannot serve it: that index is keyed for the
    # equality probe of the dedup insert, not for ordering a list.
    op.create_index(
        "ix_risks_owner_status_severity",
        "risks",
        ["user_id", "status", "severity"],
        unique=False,
    )
    op.create_index("ix_risks_user_id", "risks", ["user_id"], unique=False)
    # "What did I find most recently?" for the dashboard's newest-first list.
    op.create_index("ix_risks_detected_at", "risks", ["detected_at"], unique=False)

    # ------------------------------------------------------------------
    # recommendations
    # ------------------------------------------------------------------
    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 32, not 16: `complete_blocked_task` is 21 characters. The risk
        # columns are 16 because their longest member is `acknowledged` (12);
        # sharing one width across both vocabularies truncated the longest
        # recommendation type at insert time.
        sa.Column("recommendation_type", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), server_default="medium", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        # SET NULL, not CASCADE: deleting a risk must not delete the record of
        # the user having acted on its recommendation. That record is the
        # training label Phase 10 wants, so losing it would lose the whole reason
        # this table keeps a link back at all.
        sa.Column("risk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="new", nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
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
        sa.ForeignKeyConstraint(["risk_id"], ["risks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "priority IN ('critical', 'high', 'medium', 'low')",
            name="ck_recommendations_priority_known",
        ),
    )
    # Same shape as the risks index, over the *open* statuses. A rejected
    # recommendation is re-raisable on purpose: the user declined a suggestion
    # and the underlying condition did not change, so re-raising the identical
    # suggestion would be nagging rather than noticing.
    op.create_index(
        "uq_recommendations_open_identity",
        "recommendations",
        ["user_id", "recommendation_type", "entity_type", "entity_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('new', 'viewed')"),
    )
    op.create_index(
        "ix_recommendations_owner_status_priority",
        "recommendations",
        ["user_id", "status", "priority"],
        unique=False,
    )
    op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"], unique=False)

    # ------------------------------------------------------------------
    # risk_evaluations
    # ------------------------------------------------------------------
    op.create_table(
        "risk_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Not a business key. It exists so two evaluations landing in the same
        # microsecond cannot be mistaken for one, which is what makes a retried
        # insert safe without turning a timestamp into an identity.
        sa.Column("run_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risks_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risks_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risks_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risks_resolved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("by_severity", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("by_type", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("recommendations_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        # The window this run reasoned over, so a snapshot is interpretable
        # without also having to reconstruct which range produced it.
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "evaluated_at",
            "run_token",
            name="uq_risk_evaluations_run",
        ),
    )
    # "My risk history, newest first" — the one read this table has. The unique
    # constraint's leading `user_id` already indexes this table for an owner
    # probe, but nothing orders it by time, so this index earns its place.
    op.create_index(
        "ix_risk_evaluations_owner_evaluated",
        "risk_evaluations",
        ["user_id", "evaluated_at"],
        unique=False,
    )
    op.create_index(
        "ix_risk_evaluations_user_id",
        "risk_evaluations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    # `recommendations.risk_id` is SET NULL rather than CASCADE, so dropping
    # `risks` would leave recommendation rows referring to nothing. They are
    # dropped first for that reason — an ordering the reverse migration must
    # keep, and the reason a reader should not reorder these two statements.
    op.drop_table("risk_evaluations")
    op.drop_table("recommendations")
    op.drop_table("risks")