"""phase3_projects_tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-01 00:00:00

Explicit DDL for the Phase 3 work-management schema: ``projects``, ``tasks``,
``task_dependencies``, ``tags``, the ``task_tags`` and ``project_tags``
association tables, and ``activity_events``.

Models are deliberately NOT imported here — as in ``0001`` and ``0002`` — so that
a later change to ``app/models/`` cannot silently rewrite history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("priority", sa.String(length=16), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    # CASCADE: a project's whole purpose is to be its owner's. Nobody has work
    # that outlives the account holding it.
    #
    # One single-column index, deliberately. The two real queries are "this
    # user's projects" and "this project's tasks" (which is an index on
    # `tasks.project_id`, not this table). `status` and `priority` have three and
    # four distinct values installation-wide, so an index on either is one
    # PostgreSQL will never select over a scan, while costing an entry per
    # insert.
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'todo'"), nullable=False),
        sa.Column("priority", sa.String(length=16), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("actual_minutes", sa.Integer(), server_default=sa.text("'0'"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("position", sa.Integer(), server_default=sa.text("'0'"), nullable=False),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["tasks.id"], ondelete="CASCADE"),
        # A task that is its own parent is a one-node cycle. It is exactly as
        # easy to write by accident as a self-dependency — a bug that copies the
        # current task's id into the "new subtask" form — and every subtask tree
        # walk then has to defend against it. The defence cannot live in the
        # model, because the write can come from an import or a script in another
        # process, so it is a constraint here. Named so a violation names itself.
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_tasks_parent_not_self",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Serves the Kanban board, which reads a whole project's tasks in one query
    # and groups them by status on the client. No `(project_id, position)`
    # composite: the board receives every row for the project regardless, so an
    # ordering index cannot reduce the row count — it would only replace a
    # client-side sort the application already pays for.
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)
    # Serves the unqualified "all my tasks" listing. On its own it cannot do the
    # dashboard's work — see `ix_tasks_owner_status_due` below — but it is the
    # right entry point for a query with no status or date term, and the column
    # contract asks for it.
    op.create_index(op.f("ix_tasks_owner_id"), "tasks", ["owner_id"], unique=False)
    # Serves "the subtasks of this task", read on every board expansion and by
    # the parent's roll-up of child completion.
    op.create_index(op.f("ix_tasks_parent_id"), "tasks", ["parent_id"], unique=False)
    # The one composite on this table, and it exists for two named queries:
    #
    #   1. the dashboard's "my tasks in these statuses due in this window,
    #      soonest first";
    #   2. the overdue probe —
    #      `owner_id = ? AND status <> 'completed' AND due_date < today`.
    #
    # Both are an equality on `owner_id`, an equality or a short IN list on
    # `status`, and a *range* on `due_date`. `ix_tasks_owner_id` can serve only
    # the first term: PostgreSQL heap-fetches the user's entire backlog — which
    # never shrinks, because a completed task keeps its row — and filters the
    # other two columns afterwards. With this index the range is resolved inside
    # the index, one scan per status value, rows already in `due_date` order, no
    # final sort, and only qualifying rows reach the heap.
    #
    # `status` and `due_date` therefore get no standalone indexes either: five
    # and "a date" are far too few distinct values for one to ever be chosen on
    # its own, and the task list's filters on them are always combined with a
    # selective term anyway.
    op.create_index(
        op.f("ix_tasks_owner_status_due"),
        "tasks",
        ["owner_id", "status", "due_date"],
        unique=False,
    )

    op.create_table(
        "task_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depends_on_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_id"], ["tasks.id"], ondelete="CASCADE"),
        # The same edge declared twice is a duplicate every traversal walks twice
        # and that "what blocks this?" answers twice. Named, because the pair is
        # also the index that serves the "what does this task wait on" lookup.
        sa.UniqueConstraint("task_id", "depends_on_id", name="uq_task_dependencies_task_pair"),
        # Self-dependency is not a dependency but a deadlock: the task is
        # permanently incomplete and simultaneously unable to start, and every
        # readiness calculation over the graph has to special-case it to avoid
        # looping. It is also trivial to write by accident, and the Python check
        # that would catch it is not in the process doing the write — an import,
        # a bulk script, a future service. So it is impossible here.
        #
        # This rules out only the direct cycle. A longer cycle (A blocks B blocks
        # C blocks A) remains possible and is left to the service layer, because
        # forbidding it in the database would cost a recursive trigger on every
        # insert to prevent something a depth-first walk catches for free.
        sa.CheckConstraint(
            "task_id <> depends_on_id",
            name="ck_task_dependencies_no_self_dependency",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Two directions, two questions, two indexes. `task_id` answers "what is this
    # task waiting on", asked on every board render; `depends_on_id` answers "what
    # does this task block", asked by the dependency panel. The unique constraint
    # above gives an index on `(task_id, depends_on_id)` but it cannot answer a
    # query keyed on the second column.
    op.create_index(
        op.f("ix_task_dependencies_task_id"), "task_dependencies", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_dependencies_depends_on_id"),
        "task_dependencies",
        ["depends_on_id"],
        unique=False,
    )

    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=48), nullable=False),
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
        # Tags are PER USER. Two accounts may both have "blocked"; neither may
        # have two. A global namespace would make one user's label another user's
        # row and would need a permission layer to undo what the schema already
        # decided. Stated as a constraint rather than folded into `ix_tags_user_id`
        # — which `users.email` does — because the indexed column here is the
        # owner, which is not unique.
        sa.UniqueConstraint("user_id", "name", name="uq_tags_user_id_name"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tags_user_id"), "tags", ["user_id"], unique=False)

    # Association tables: composite primary key on the pair, so re-applying a tag
    # is an idempotent no-op rather than a duplicate every "this task's tags"
    # query lists twice.
    #
    # No index on `tag_id` alone, on purpose. The tag filter is always applied
    # together with a task-side term, so the plan runs the other way: walk
    # `ix_tasks_project_id` (or `ix_tasks_owner_status_due`) and probe this table
    # by the `(task_id, tag_id)` prefix the primary key already provides.
    op.create_table(
        "task_tags",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
    )
    op.create_table(
        "project_tags",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "tag_id"),
    )

    # An activity row is a record of something that happened; the row it points
    # at is only an address. Every foreign key here is SET NULL, not CASCADE,
    # which is the deliberate opposite of every other Phase 3 table: work is
    # deletable, and a cascading delete would take the record of that deletion
    # with it — the feed would develop holes exactly where the history matters
    # most, with nothing a user could see to explain them. Blank instead, and the
    # entry still says "this user, in this project, on this date, completed
    # something". The columns are nullable for the same reason
    # `audit_logs.user_id` is: an event may be unattributable to an account, and
    # that has to be expressible.
    op.create_table(
        "activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        # `metadata` is a SQLAlchemy `Base.metadata` attribute name, not a
        # PostgreSQL reserved word, so it is safe as a column name. The ORM model
        # exposes it under a different Python attribute (`metadata_`); the same
        # idiom and the same reason as `audit_logs`.
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # No `updated_at`, and no mixin: an activity row is written once and
        # never rewritten, so an edit timestamp would be a lie. This table is read
        # far more often than it is written, so an `onupdate` that fired on a
        # stray edit would corrupt the one thing readers trust about it.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # `project_id` and `task_id` serve the two history views the UI asks for —
    # "what happened to this project" and "what happened to this task" — both
    # narrowed to a project or task the caller already knows, both read
    # newest-first and paginated. A `(project_id, created_at)` composite would
    # replace a top-N heapsort over that bounded set with a maintained index on a
    # table that only ever grows; the sort is the cheaper of the two here, which
    # is the opposite of the conclusion reached for `tasks`, where the row set is
    # unbounded and the dashboard query has to *exclude* most of it.
    op.create_index(
        op.f("ix_activity_events_project_id"), "activity_events", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_activity_events_task_id"), "activity_events", ["task_id"], unique=False
    )
    op.create_index(op.f("ix_activity_events_user_id"), "activity_events", ["user_id"], unique=False)
    # Weakest of the four — fifteen distinct values is low selectivity — but it
    # is the only way to answer "every completion ever recorded" without a full
    # scan. The table is append-only, so the maintenance cost is one index entry
    # per insert and nothing else.
    op.create_index(
        op.f("ix_activity_events_event_type"), "activity_events", ["event_type"], unique=False
    )


def downgrade() -> None:
    # Strict reverse dependency order. Every table that references another has to
    # go first, or PostgreSQL refuses the DROP because a live foreign key still
    # points at the table being removed. `activity_events` is the only table that
    # references three others, so it leads; `projects` is referenced by `tasks`
    # and comes last.
    #
    # Dropping a table takes its indexes and constraints with it, so none of the
    # `create_index` calls above are undone individually.
    op.drop_table("activity_events")
    op.drop_table("project_tags")
    op.drop_table("task_tags")
    op.drop_table("task_dependencies")
    op.drop_table("tags")
    op.drop_table("tasks")
    op.drop_table("projects")