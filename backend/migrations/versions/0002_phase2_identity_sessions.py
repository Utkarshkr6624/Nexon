"""phase2_identity_sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-15 00:00:00

Explicit DDL for the Phase 2 account shape: the new columns on ``users`` plus the
``sessions``, ``password_reset_tokens`` and ``audit_logs`` tables.

Models are deliberately NOT imported here — as in ``0001`` — so that a later
change to ``app/models/`` cannot silently rewrite history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "full_name",
        new_column_name="display_name",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )

    # `username` is a NOT NULL handle and `users` is not empty once Phase 1 has
    # run, so the column cannot simply be added as NOT NULL: PostgreSQL has no
    # value to give the existing rows and the ALTER would fail.
    #
    # Two strategies were available:
    #
    #   1. add the column with a `server_default`, then drop the default;
    #   2. add it nullable, backfill, then `SET NOT NULL`.
    #
    # (1) is fewer statements, but it hands every existing row the *same*
    # literal, which collides as soon as a second row exists — so the unique
    # index below would fail and the migration could not be applied to any
    # database with more than one user. (2) is chosen: each backfilled value is
    # derived from the row itself, so the backfill is deterministic (re-running
    # the UPDATE is a no-op) and unique by construction.
    #
    # The value is the local part of the email truncated to 15 characters, an
    # underscore, then the first 16 hex digits of the row's UUID: 15 + 1 + 16 =
    # 32, exactly the VARCHAR(32) limit. The UUID suffix is what makes the handle
    # unique regardless of how emails collide or truncate; a collision would
    # require two distinct users whose UUIDs agree on their first 64 bits, and it
    # would surface loudly as a unique-index violation rather than silently merge
    # two accounts. These are placeholders — a user may claim a real handle later.
    op.add_column("users", sa.Column("username", sa.String(length=32), nullable=True))
    op.execute(
        sa.text(
            "UPDATE users "
            "SET username = left(split_part(email, '@', 1), 15) "
            "|| '_' || left(replace(id::text, '-', ''), 16)"
        )
    )
    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    op.add_column("users", sa.Column("avatar_url", sa.String(length=2048), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=16),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Matches the `ix_users_email` pattern from 0001: the ORM's
    # `unique=True, index=True` on the column makes SQLAlchemy fold uniqueness
    # into the index instead of emitting a separate UniqueConstraint.
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    # Two single-column indexes, and deliberately NO composite
    # (user_id, revoked_at). The repositories that matter are "list a user's live
    # sessions" (user_id = ? AND revoked_at IS NULL) and "revoke a session by id
    # for a user" (id = ? AND user_id = ?). The second is served by the primary
    # key plus `ix_sessions_user_id`. The first is only a partial win from a
    # composite index: a user has on the order of tens of sessions, so the index
    # filter already leaves very few heap pages to visit, while the extra column
    # would be rewritten on insert and again on every revoke. Two narrow indexes
    # are cheaper to maintain than one wider one at this cardinality.
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    # `token_hash` is the lookup key for "which session does this refresh token
    # belong to", which happens on every refresh request.
    op.create_index(
        op.f("ix_sessions_token_hash"), "sessions", ["token_hash"], unique=False
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        op.f("ix_password_reset_tokens_user_id"),
        "password_reset_tokens",
        ["user_id"],
        unique=False,
    )
    # Resolution is by digest: the raw token is never stored, so this index is the
    # only way to find the row a submitted reset token belongs to.
    op.create_index(
        op.f("ix_password_reset_tokens_token_hash"),
        "password_reset_tokens",
        ["token_hash"],
        unique=False,
    )

    # An audit row is immutable evidence: there is no `updated_at` and nothing
    # ever rewrites a past event, so a single `created_at` is the whole timeline.
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        # `metadata` is a SQLAlchemy `Base.metadata` attribute name, not a
        # PostgreSQL reserved word, so it is safe as a column name here. The ORM
        # model exposes it under a different Python attribute.
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # SET NULL, where the session and reset-token foreign keys CASCADE.
        # A session row is part of the account and is meaningless without it,
        # so deleting the account should take it; an audit row is evidence that
        # must outlive the account it describes, so deleting the account must
        # blank the reference rather than destroy the history. The column stays
        # nullable for the failed sign-in against an unknown address, which has
        # no user row to point at. The constraint is spelled out here to match
        # the ORM model — without it the model and the migration disagree and
        # `alembic check` reports drift forever.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"], unique=False
    )


def downgrade() -> None:
    # The child tables go first: they hold the only foreign keys pointing at
    # `users`, and dropping a column that a live FK depends on is refused by
    # PostgreSQL. Dropping a table takes its indexes with it, so the indexes
    # below are not listed again here.
    op.drop_table("audit_logs")
    op.drop_table("password_reset_tokens")
    op.drop_table("sessions")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.alter_column(
        "users",
        "display_name",
        new_column_name="full_name",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "role")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "username")
