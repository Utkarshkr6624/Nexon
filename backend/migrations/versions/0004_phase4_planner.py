"""phase4_planner

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-08 00:00:00

Explicit DDL for the Phase 4 planner schema: ``calendar_events``,
``work_sessions`` and ``availability_rules``.

Models are deliberately NOT imported here — as in ``0001`` through ``0003`` — so
that a later change to ``app/models/`` cannot silently rewrite history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "event_type",
            sa.String(length=16),
            server_default=sa.text("'other'"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        # TIME WITH TIME ZONE, deliberately. Every stored instant is UTC; a
        # caller may write any offset and PostgreSQL normalises it. A `timestamp
        # without time zone` here would store a wall-clock reading whose meaning
        # depends on the reader, which is precisely the silent shift the spec
        # forbids.
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        # Display hint only. The span above stays the truth, so a month query
        # needs no special case for all-day rows.
        sa.Column("all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
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
        # CASCADE throughout: an event exists for its owner, and for the project
        # or task it was about. An event that outlives either is a booking for
        # nothing — unlike `activity_events`, whose rows are the record.
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        # A zero-length or inverted span is not a short meeting: conflict
        # detection computes `min(end) - max(start)`, which goes negative; the
        # overload report subtracts it from the day total; a month view cannot
        # render it. The Python check that would catch it lives in the request
        # process, while rows also arrive from imports and scripts, so the
        # invariant is total here. Strict `>`, because a block of no length
        # reserves no time and would otherwise let a caller claim a slot it does
        # not occupy.
        sa.CheckConstraint(
            "ends_at > starts_at",
            name="ck_calendar_events_ends_after_starts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_calendar_events_owner_id"),
        "calendar_events",
        ["owner_id"],
        unique=False,
    )
    # Serves the week and month planner views: `owner_id = ? AND starts_at < ?`
    # with an overlap term on the end. The single-column owner index above can
    # only serve the first term, so PostgreSQL would fetch every event the user
    # has ever had — a table that only grows, since past events are not pruned —
    # and filter in the heap. With the composite, the range resolves inside the
    # index and rows already arrive in `starts_at` order, which is the order the
    # day view renders in anyway. `ends_at` is omitted on purpose: a btree cannot
    # use a second range term, so it would widen the index without narrowing any
    # scan.
    op.create_index(
        op.f("ix_calendar_events_owner_starts_at"),
        "calendar_events",
        ["owner_id", "starts_at"],
        unique=False,
    )
    # Serves the `?project_id=` and `?task_id=` filters on the event list and the
    # "what is booked around this task" question the task panel asks. Both ids
    # are known before the query runs, so a single column is the whole index.
    op.create_index(
        op.f("ix_calendar_events_project_id"),
        "calendar_events",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_events_task_id"),
        "calendar_events",
        ["task_id"],
        unique=False,
    )
    # `event_type` deliberately gets no index: seven values installation-wide is
    # far too low a selectivity for PostgreSQL to ever choose it over a scan, and
    # the planner never filters on it.

    op.create_table(
        "work_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable on purpose: a session may be a deliberate unassigned focus
        # block, and a project can be worked on without picking a task. Only the
        # scheduler's proposals always carry a task.
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        # Written once at stop from actual_start/actual_end and rounded once, so
        # it is never accumulated in Python across sessions. NOT NULL with a zero
        # default means a planned-but-unstarted session has a duration of zero
        # rather than NULL and the arithmetic never has to null-check.
        sa.Column("actual_minutes", sa.Integer(), server_default=sa.text("'0'"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'planned'"),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # Same argument as the event check above: an inverted or zero-length
        # session contributes negative minutes to the overload figure and breaks
        # every interval-overlap test the scheduler makes.
        sa.CheckConstraint(
            "scheduled_end > scheduled_start",
            name="ck_work_sessions_scheduled_end_after_start",
        ),
        # `actual_end` is written by a stop request that may arrive seconds or
        # days late, and a negative duration would silently subtract from the
        # task's tracked time.
        sa.CheckConstraint(
            "actual_end IS NULL OR actual_start IS NULL OR actual_end > actual_start",
            name="ck_work_sessions_actual_end_after_start",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_work_sessions_owner_id"),
        "work_sessions",
        ["owner_id"],
        unique=False,
    )
    # Serves the planner range queries — day, week, and the `?from=&to=` filter
    # on `GET /work-sessions` — which are `owner_id = ? AND scheduled_start < ?`
    # with an overlap term on the end. Same argument, and the same deliberate
    # omission of the second range column, as the event index above.
    op.create_index(
        op.f("ix_work_sessions_owner_scheduled_start"),
        "work_sessions",
        ["owner_id", "scheduled_start"],
        unique=False,
    )
    # "All the time spent on this task" (task detail render and the tracked-time
    # total) and the project effort roll-up. Both ids are known before the query.
    op.create_index(
        op.f("ix_work_sessions_task_id"), "work_sessions", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_work_sessions_project_id"),
        "work_sessions",
        ["project_id"],
        unique=False,
    )
    # `status` gets no index: four values installation-wide, and the one query
    # that filters on it is already served by the owner + start composite.

    op.create_table(
        "availability_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 0=Monday .. 6=Sunday, matching Python's `date.weekday()` rather than
        # PostgreSQL's `DOW` (0=Sunday). Python is where these rows become
        # concrete instants, so Python's convention is the one that must not need
        # translating at the boundary.
        sa.Column("weekday", sa.Integer(), nullable=False),
        # `TIME WITHOUT TIME ZONE`, deliberately. "09:00 to 17:00, Mondays" is
        # not an instant: it has no date, so it has no UTC offset. Stored as a
        # timestamp it would take whatever offset the writer's zone had, freeze
        # the rule to it, and drift twice a year under DST. The wall-clock
        # reading is kept verbatim; the date and the zone are supplied when the
        # rule is turned into instants.
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        # `PUT /availability` replaces the whole set, so two windows for one
        # weekday starting at the same minute are a bug rather than a mergeable
        # state — and "which one wins" has no answer the conflict detector or the
        # scheduler could implement consistently.
        sa.UniqueConstraint(
            "owner_id",
            "weekday",
            "starts_at",
            name="uq_availability_rules_owner_weekday_start",
        ),
        # An out-of-range weekday matches no date, so it is a row that silently
        # does nothing. Refused here rather than in the service because the
        # writer may be an import in another process.
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_availability_rules_weekday_range",
        ),
        # Both sides carry no zone, so `>` is a plain wall-clock comparison. A
        # window ending at or before its start covers no time and would make the
        # day's available minutes negative.
        sa.CheckConstraint(
            "ends_at > starts_at",
            name="ck_availability_rules_ends_after_starts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # The unique constraint above already indexes `owner_id` as its leading
    # column; this narrower index is the one PostgreSQL picks for
    # `WHERE owner_id = ? ORDER BY weekday, starts_at`, the query behind every
    # planner view.
    op.create_index(
        op.f("ix_availability_rules_owner_id"),
        "availability_rules",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    # Strict reverse dependency order. Both `calendar_events` and `work_sessions`
    # reference `tasks` and `projects`, and `availability_rules` references
    # only `users`, so the two child-of-work tables go first. Dropping a table
    # takes its indexes and constraints with it, so none of the `create_index`
    # calls above are undone individually.
    op.drop_table("availability_rules")
    op.drop_table("work_sessions")
    op.drop_table("calendar_events")
