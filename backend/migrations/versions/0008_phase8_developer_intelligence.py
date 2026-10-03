"""phase8_developer_intelligence

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-01 00:00:00

Explicit DDL for Phase 8: ``git_repositories``, ``git_commits``,
``git_branches`` and ``git_scan_runs``.

Models are deliberately NOT imported here — as in ``0001`` through ``0007`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

What this migration creates, and what it pointedly does not
------------------------------------------------------------
Phase 8 analyses **local git repositories**, and the governing rule of the whole
phase is that a commit timestamp is evidence rather than a verdict. Git records
that a commit object carries an author date; it does not record how long anyone
worked. So there is no ``hours``, ``minutes_spent``, ``effort`` or ``focus``
column below, and no table here could be summed into one. The counts are line
changes git counted from diffs and the number of commits — changes and events,
never time.

* **No ``git_commit_files`` table.** ``git log --numstat`` reports per-file
  counts and this schema stores only their sum. A per-file row table would grow
  with the size of the repositories (millions of rows for a mature codebase) to
  answer questions Phase 8 does not ask.
* **No blame, no per-line churn.** Line-level history is the most reliable way
  to turn recorded facts into judgements about individual people, which the
  phase forbids.
* **No remote-tracking branches.** Only ``refs/heads`` is recorded; a
  ``refs/remotes/*`` mirror is somebody else's history and would double-count.
* **No ``metadata`` JSONB.** Phase 7's risk tables carry one because a
  detection run's raw inputs cannot be re-derived. Git inputs can be — the
  repository is still on disk and the next scan reads it again — so a blob here
  would be a second copy that drifts from the first.

The constraints, and why each earns its place
---------------------------------------------
* ``uq_git_repositories_owner_path`` — one registration per (account, path), so
  the same directory cannot end up with two rows whose commit counts disagree.
  It is deliberately *not* global: the path is a local directory on the
  developer's own machine, and two accounts may each legitimately watch it.
* ``uq_git_commits_repo_hash`` — the idempotency anchor. A re-scan of an
  unchanged repository returns the same hashes, and without this the dashboard's
  commit counts would double every time the user pressed the scan button. It is
  a *full* unique constraint rather than Phase 7's partial one because a commit
  is not an episode: the same commit is the same commit forever, and there is no
  "resolved" version of one.
* ``uq_git_branches_repo_name`` — the same anchor for branch names.
* ``ck_git_repositories_counts_non_negative``,
  ``ck_git_commits_counts_non_negative`` and
  ``ck_git_scan_runs_counts_non_negative`` — line counts come from git's diff
  parser, and a negative line count is not a thin measurement but a broken one.
  A zero is legitimate and stored (a binary file has no countable lines); the
  constraint refuses only the value that cannot mean anything, so it cannot
  reject a legitimate scan.

Why two tables have no ``updated_at``
--------------------------------------
``git_commits`` and ``git_scan_runs`` are records of things that happened, and a
commit object and a finished scan attempt are both immutable: git will not
change a commit, and re-reading a scan does not revise when it ran.
:class:`~app.db.base.TimestampMixin` stamps ``updated_at`` on every UPDATE, so
putting it here would assert a revision that does not exist.
``git_repositories`` and ``git_branches`` *are* revised — a repository's
counters move on each scan, a branch's head moves on each commit — so those two
carry the pair.

Why ``project_id`` is SET NULL
------------------------------
The recorded history outlives the project. Deleting a project in NEXUS must not
delete the commits and branches that were observed against it, exactly as
``activity_events`` keeps the history of a deleted project. The reverse would
make a removal quietly erase evidence, which is the failure the brief's "avoid
excessive duplication" concern is really about on the other side.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # git_repositories
    # ------------------------------------------------------------------
    op.create_table(
        "git_repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # A user-facing label, not a sentence. Renamed freely; the path below is
        # not, which is why the two are separate columns at all.
        sa.Column("name", sa.String(length=200), nullable=False),
        # The RESOLVED absolute path, as the path validator produced it. A
        # relative path is never stored, so it cannot later resolve elsewhere
        # when a different working directory runs the scan. Text rather than a
        # bounded String because a deep POSIX path outgrows a reasonable limit.
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Nullable, not defaulted to a placeholder: "Python" and "no tracked
        # file we recognise" are different answers, and "" would be a third.
        sa.Column("primary_language", sa.String(length=64), nullable=True),
        # SET NULL, not CASCADE: the observed history outlives the project it
        # was linked to. Deleting a project must not delete the commits that
        # were recorded against it.
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        # Nullable: a detached HEAD is a normal state, not an error, and git
        # reports it as the literal string "HEAD" — which must not be stored as
        # though it were a branch name.
        sa.Column("current_branch", sa.String(length=255), nullable=True),
        sa.Column("default_branch", sa.String(length=255), nullable=True),
        # Counts of what the LAST SCAN saw, not of everything that ever existed.
        # A branch deleted on disk leaves this table but keeps its commits, which
        # are the record of what was observed.
        sa.Column("branch_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("commit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Null for a repository with no commits at all. A fresh `git init` is a
        # valid registration and 0 would claim a zero-length history rather than
        # the absence of one.
        sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "working_tree_dirty", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_status", sa.String(length=16), server_default="ok", nullable=True),
        # A human sentence, never a traceback and never an absolute path from
        # inside the user's home directory.
        sa.Column("last_scan_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "local_path", name="uq_git_repositories_owner_path"),
        sa.CheckConstraint(
            "commit_count >= 0 AND branch_count >= 0",
            name="ck_git_repositories_counts_non_negative",
        ),
    )
    # The account probe every Phase 8 read starts with.
    op.create_index("ix_git_repositories_user_id", "git_repositories", ["user_id"], unique=False)
    # "My active repositories", the default list. `ix_git_repositories_user_id`
    # above cannot serve it: that index carries no predicate on `is_active`.
    op.create_index(
        "ix_git_repositories_owner_active",
        "git_repositories",
        ["user_id", "is_active"],
        unique=False,
    )
    # `GET /developer/projects/{project_id}` — the repositories linked to one
    # project. A nullable FK column that is not the leading column of any other
    # index here, so it needs its own.
    op.create_index(
        "ix_git_repositories_project_id", "git_repositories", ["project_id"], unique=False
    )

    # ------------------------------------------------------------------
    # git_commits
    # ------------------------------------------------------------------
    op.create_table(
        "git_commits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 64, not 40: `git rev-parse` returns a full SHA-256 object name on a
        # repository using the newer object format, and a 40-wide column would
        # truncate it.
        sa.Column("commit_hash", sa.String(length=64), nullable=False),
        # Display abbreviation only. Carried rather than re-derived so the width
        # is this schema's choice rather than whatever `%h` emitted.
        sa.Column("short_hash", sa.String(length=12), nullable=False),
        # The author date git recorded, in UTC. Never a measure of time spent.
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        # Subject line only. The body is not stored: messages carry ticket ids
        # and the occasional pasted credential, and none of it is needed to
        # answer "how much changed".
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(length=200), nullable=True),
        sa.Column("author_email", sa.String(length=320), nullable=True),
        # Zero is a real answer — a binary file has no countable lines — and is
        # stored rather than dropped, so the commit count and the file count
        # always describe the same set of commits.
        sa.Column("additions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deletions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("files_changed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Best-effort attribution from the branch head that currently contains
        # this commit; null when no branch does or when the scan resolved none.
        # Never a guess.
        sa.Column("branch", sa.String(length=255), nullable=True),
        # `created_at` alone, deliberately. An observed commit is immutable, and
        # an `updated_at` here would assert a revision git cannot make.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["git_repositories.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "commit_hash", name="uq_git_commits_repo_hash"),
        sa.CheckConstraint(
            "additions >= 0 AND deletions >= 0 AND files_changed >= 0",
            name="ck_git_commits_counts_non_negative",
        ),
    )
    op.create_index("ix_git_commits_user_id", "git_commits", ["user_id"], unique=False)
    # "This repository's history, newest first" and the same read narrowed to a
    # window — the per-repository timeline route.
    op.create_index(
        "ix_git_commits_repo_committed",
        "git_commits",
        ["repository_id", "committed_at"],
        unique=False,
    )
    # The same read across every repository: the account-wide timeline and every
    # metric not scoped to one repository. A separate index because the leading
    # column differs, so the composite above cannot serve a `user_id` probe.
    op.create_index(
        "ix_git_commits_user_committed",
        "git_commits",
        ["user_id", "committed_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # git_branches
    # ------------------------------------------------------------------
    op.create_table(
        "git_branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("head_commit_hash", sa.String(length=64), nullable=True),
        sa.Column("last_committed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["repository_id"], ["git_repositories.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "name", name="uq_git_branches_repo_name"),
    )
    op.create_index("ix_git_branches_user_id", "git_branches", ["user_id"], unique=False)
    # Serves the probe for one repository's branches and the CASCADE that fires
    # when that repository is deleted.
    op.create_index("ix_git_branches_repo_id", "git_branches", ["repository_id"], unique=False)

    # ------------------------------------------------------------------
    # git_scan_runs
    # ------------------------------------------------------------------
    op.create_table(
        "git_scan_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        # There is no `pending` member. A scan is a synchronous request, so a row
        # is only ever written once the attempt has already finished; a row
        # claiming to be in progress could outlive the request that started it.
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column(
            "commits_discovered", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("commits_added", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "branches_discovered", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        # Wall-clock duration of the CLI call. It is the only signal that a
        # repository has become expensive to read, and it is explicitly not a
        # measure of anybody's work.
        sa.Column("duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # A human sentence for a failed scan. Never a traceback. Failed attempts
        # get rows too: that is what lets a repository whose directory has
        # since been moved still explain itself.
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["git_repositories.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "commits_discovered >= 0 AND commits_added >= 0 "
            "AND branches_discovered >= 0 AND duration_ms >= 0",
            name="ck_git_scan_runs_counts_non_negative",
        ),
    )
    op.create_index("ix_git_scan_runs_user_id", "git_scan_runs", ["user_id"], unique=False)
    # The scan history panel: "when was this repository read, and how did each
    # read go?", newest first.
    op.create_index(
        "ix_git_scan_runs_repo_scanned",
        "git_scan_runs",
        ["repository_id", "scanned_at"],
        unique=False,
    )


def downgrade() -> None:
    # Reverse dependency order, and the order is load-bearing rather than
    # alphabetical: the three child tables all carry a CASCADE foreign key onto
    # `git_repositories`, so dropping it last is what keeps the reversal legal.
    # Within the children nothing references anything, so the order among them is
    # for readability — commits, then branches, then the scan runs that observed
    # both.
    op.drop_table("git_scan_runs")
    op.drop_table("git_branches")
    op.drop_table("git_commits")
    # `git_repositories.project_id` is SET NULL rather than CASCADE, so this is
    # also the statement that leaves `projects` untouched — dropping the table
    # removes the reference without touching the row it pointed at.
    op.drop_table("git_repositories")