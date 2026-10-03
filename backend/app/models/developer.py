"""Phase 8 developer intelligence: what the local git repositories recorded.

Four tables, and the argument for each is the same one the whole phase rests
on: **a commit timestamp is evidence, not a verdict.** Git records that a
commit object carries an author date. It does not record how long anyone
worked, whether they were focused, or whether the hour before it was spent on
this repository at all. So nothing here is named ``hours``, ``effort``,
``productivity`` or ``focus``, and no column here could be summed into one —
``git_commits`` stores *changes* (additions, deletions, files), which git
counts from diffs, and :mod:`app.services.developer.metrics` is written to say
"18 commits were recorded this week" rather than anything about a person.

**One registered repository per path, per account.** ``uq_git_repositories_owner_path``
is deliberately *not* global. The path is a local directory on the developer's
own machine, and two accounts on the same machine may each want to watch it —
one watching it for a personal project and one for a client. A global unique
index on ``local_path`` would make the second registration a conflict about
something that is not actually contended. The pairing that must be unique is
"this account, this path", because registering it twice would give the same
directory two rows whose commit counts would then disagree with each other.

**Commits are immutable and deduplicated by ``(repository_id, commit_hash)``.
** That pair is the whole re-scan story: ``POST /developer/repositories/{id}/scan``
runs the git CLI again, and on an unchanged repository git returns the same
commits it returned last time. If the identity were the row id, a re-scan would
insert a second copy of every commit and the commit counts on the dashboard
would double each time the user pressed the button. ``uq_git_commits_repo_hash``
makes the re-scan an upsert instead, which is why
``uq_git_commits_repo_hash`` is a *full* unique constraint and not the partial
one Phase 7 used for risks — a commit is not an episode, and the same commit
is the same commit forever.

**The commit's ``branch`` is best-effort and may be null.** Attribution comes
from asking which branch head currently contains the commit, which is true for
a while and then stops being true after a rebase or a fast-forward that moved
history. Storing a guess would put a wrong word in a factual column, so the
column is nullable and the engine is required to leave it null rather than
invent one. A commit with no counted lines is stored with ``0``, never dropped:
"this commit touched a binary file" is a fact, and a scan that silently
omitted it would make the file counts disagree with the commit counts.

**``git_scan_runs`` is append-only and has no ``updated_at``.** A scan run is a
fact about a moment — this attempt, these counts, this duration — and an
``onupdate`` column on it would assert that the moment is still being revised.
The same reasoning gives ``git_commits.created_at`` without ``updated_at``: an
observed commit does not change. Only ``git_repositories`` and ``git_branches``
carry :class:`~app.db.base.TimestampMixin`, because those two *are* revised —
a repository's counters move on every scan and a branch's head hash moves on
every commit.

**A failed scan is a row, not a gap.** ``git_repositories.last_scan_error``
holds a human sentence and ``git_scan_runs.error`` holds the same thing, so a
repository whose directory has since been deleted still answers "the last scan
could not read this repository" instead of looking like one that has never been
read. Nothing in this module can raise at write time for that reason: the scan
service catches the failure and stores it.

What is deliberately absent
---------------------------
* **No per-commit file rows.** ``git log --numstat`` reports changed-line
  counts, and NEXUS stores the counts. A ``git_commit_files`` table would grow
  with the size of the repositories — tens of millions of rows for a mature
  codebase — to answer questions Phase 8 does not ask, and the brief's "avoid
  excessive duplication" applies to it more than to anything else here.
* **No blame and no line-level churn.** ``git blame`` is a per-line rewrite of
  history that is expensive, ambiguous across renames, and says less than it
  appears to about a person. It is the single most reliable way to turn recorded
  facts into judgements about individuals, which this phase forbids.
* **No working-time, focus or effort column of any kind**, for the reason the
  module opens with. There is no honest value such a column could hold.
* **No ``metadata`` JSONB.** Phase 7's risk tables carry one because a
  detection run's raw inputs cannot be re-derived. Git inputs *can* be: the
  repository is still on disk and the next scan reads it again. A JSONB blob
  here would be a second copy of data the CLI hands over for free, and it would
  drift from it the moment someone committed.
* **No ``relationship()`` anywhere in this module.** Every read is written as
  an explicit owner-scoped statement in :mod:`app.repositories.developer`,
  because the lazy load a relationship invites is exactly the read that forgets
  to filter on ``user_id``.

Enum storage follows the house rule from :mod:`app.models.enums`: a status is
``Mapped[str]`` in a ``String(n)`` and is validated in Python, never a
SQLAlchemy ``Enum`` and never a native PostgreSQL enum type.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import GitScanStatus

__all__ = [
    "DEFAULT_GIT_SCAN_STATUS",
    "GitBranch",
    "GitCommit",
    "GitRepository",
    "GitScanRun",
]

#: What a repository carries before its first scan. ``ok`` rather than a third
#: ``unknown`` member because the contract fixes the enum at two values and a
#: repository that has never been scanned is distinguished by
#: ``last_scanned_at IS NULL`` — which is a fact — rather than by a status word
#: that would be a guess.
DEFAULT_GIT_SCAN_STATUS = GitScanStatus.OK.value

#: Sized for a ``GitScanStatus`` member. The longest is ``error`` (5), so 16
#: leaves room for the next member without a migration, and matches the width
#: the risk tables use for their small vocabularies.
_MAX_GIT_SCAN_STATUS_LENGTH = 16
#: A repository name is a label the user typed — a directory name, a team name,
#: a repository name from the forge. It is not a sentence.
_MAX_REPOSITORY_NAME_LENGTH = 200
#: Branch names are filesystem-shaped: the longest well-known branch name
#: (``dependabot/npm_and_yarn/...``) runs well past a hundred characters, and
#: 255 is the widest a label is allowed to be on every platform git supports.
_MAX_BRANCH_NAME_LENGTH = 255
#: 64 is a full SHA-1 and a full SHA-256 (``git rev-parse`` can return either
#: depending on the repository's object format), so the column holds both rather
#: than assuming the older one.
_MAX_COMMIT_HASH_LENGTH = 64
#: Display only. ``git log --pretty=%h`` chooses the abbreviation length from
#: the repository's own object count, so this is a ceiling on what NEXUS shows,
#: not a claim that git produced exactly this many characters.
_MAX_SHORT_HASH_LENGTH = 12
#: An author name is a person's display name; an address is an RFC 5321 local
#: part at its practical maximum. Both are stored as recorded and neither is
#: ever shown to another account, because every read is owner-scoped.
_MAX_AUTHOR_NAME_LENGTH = 200
_MAX_AUTHOR_EMAIL_LENGTH = 320
#: The primary-language vocabulary is the static extension map in the git
#: engine. Its longest member is ``TypeScript`` (10); 64 leaves room to add one
#: without a migration and cannot store a language nobody mapped.
_MAX_LANGUAGE_LENGTH = 64


class GitRepository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One registered local repository, and what its last scan found.

    The row is a *registration plus a snapshot*: the user-declared part (name,
    description, path, optional project) and the machine-read part (branch,
    counts, commit range, working-tree state). Keeping both on one row is what
    lets the repository list render without a join, and the split is visible in
    the comments rather than in the schema.
    """

    __tablename__ = "git_repositories"

    __table_args__ = (
        # One row per (account, path). Not a global unique index: a path is a
        # local directory and two accounts may each watch it legitimately. See
        # the module docstring.
        UniqueConstraint("user_id", "local_path", name="uq_git_repositories_owner_path"),
        Index("ix_git_repositories_user_id", "user_id"),
        # "My active repositories", the default list. `ix_git_repositories_user_id`
        # cannot serve it because it carries no ordering or filter on `is_active`.
        Index("ix_git_repositories_owner_active", "user_id", "is_active"),
        Index("ix_git_repositories_project_id", "project_id"),
        CheckConstraint(
            "commit_count >= 0 AND branch_count >= 0",
            name="ck_git_repositories_counts_non_negative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The user's label for it, defaulting to the directory name. Never
    #: machine-derived afterwards: renaming the directory on disk must not
    #: rename it here, because the row would then describe a path that no longer
    #: matches the one the scan reads.
    name: Mapped[str] = mapped_column(String(_MAX_REPOSITORY_NAME_LENGTH), nullable=False)
    #: The *resolved absolute* path, stored exactly as the path validator
    #: produced it. A relative path is never stored, so it cannot later resolve
    #: to somewhere else when a different working directory runs the scan.
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Most common tracked-file extension, from the engine's static map.
    #: Nullable rather than defaulted to a placeholder, because "Python" and
    #: "no file we recognise" are different answers and "" would be the third.
    primary_language: Mapped[str | None] = mapped_column(
        String(_MAX_LANGUAGE_LENGTH), nullable=True
    )
    #: The project this repository belongs to, when the user linked one.
    #: ``ON DELETE SET NULL`` because the recorded history outlives the
    #: project: deleting a project must not delete the commits it described.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: False hides the repository from the dashboard without deleting its
    #: commits. A deactivated repository is a statement about *now*, so it is a
    #: column rather than a soft-delete flag on ``updated_at``.
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    #: Null when HEAD is detached, which is a normal state and not an error —
    #: hence nullable rather than the literal string git prints for it.
    current_branch: Mapped[str | None] = mapped_column(
        String(_MAX_BRANCH_NAME_LENGTH), nullable=True
    )
    #: ``refs/remotes/origin/HEAD`` when the clone has one, else ``HEAD``'s
    #: branch. Nullable for a repository that has never resolved one.
    default_branch: Mapped[str | None] = mapped_column(
        String(_MAX_BRANCH_NAME_LENGTH), nullable=True
    )
    #: Counts of what the last scan saw, not of what has ever existed: a branch
    #: or commit removed from disk disappears from the repository's own list but
    #: stays in :class:`GitCommit`, which is the record of what was observed.
    branch_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    commit_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Null for a repository with no commits at all — a fresh ``git init`` is a
    #: valid registration, and 0 would claim a history of zero length rather
    #: than the absence of one.
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Whether the last scan found uncommitted changes. Recorded because it is
    #: the only thing in the schema that distinguishes "the user is mid-change"
    #: from "the repository is at rest" — and it is recorded as a *state at the
    #: last scan*, never as time spent.
    working_tree_dirty: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    #: When the last scan ran, and how it went. Null ``last_scanned_at`` means
    #: never, which is a fact the UI states; ``last_scan_error`` is the human
    #: sentence from the failed attempt, never a traceback.
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_status: Mapped[str | None] = mapped_column(
        String(_MAX_GIT_SCAN_STATUS_LENGTH),
        server_default=DEFAULT_GIT_SCAN_STATUS,
        nullable=True,
    )
    last_scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class GitCommit(UUIDPrimaryKeyMixin, Base):
    """One commit object as the git CLI reported it.

    ``created_at`` is the row's own insertion time and the only timestamp
    here. The commit's own instant lives in ``committed_at``, which is a fact
    about the repository; keeping the two apart is what stops "when did NEXUS
    see this" from being reported as "when did this happen", which differ by
    however long the repository was unregistered.
    """

    __tablename__ = "git_commits"

    __table_args__ = (
        # THE IDEMPOTENCY ANCHOR. A re-scan of an unchanged repository returns
        # the same hashes, and this constraint is what turns that into an update
        # rather than a second copy of the same history.
        UniqueConstraint("repository_id", "commit_hash", name="uq_git_commits_repo_hash"),
        Index("ix_git_commits_user_id", "user_id"),
        # "This repository's history, newest first" and the same read narrowed
        # to a window — the timeline route and every per-repository metric.
        Index("ix_git_commits_repo_committed", "repository_id", "committed_at"),
        # The same read across every repository, which is the account-wide
        # timeline and every metric that is not scoped to one repository. It is
        # a separate index because the leading column differs: the composite
        # above cannot serve a `user_id` probe.
        Index("ix_git_commits_user_committed", "user_id", "committed_at"),
        CheckConstraint(
            "additions >= 0 AND deletions >= 0 AND files_changed >= 0",
            name="ck_git_commits_counts_non_negative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("git_repositories.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: The full object name. 64 characters so it holds a SHA-256 object name as
    #: well as a SHA-1 one.
    commit_hash: Mapped[str] = mapped_column(String(_MAX_COMMIT_HASH_LENGTH), nullable=False)
    #: Display abbreviation. Carried alongside the full hash so a commit list
    #: never has to re-derive it, and so the column's width is this module's
    #: choice rather than whatever `git log --pretty=%h` happened to emit.
    short_hash: Mapped[str] = mapped_column(String(_MAX_SHORT_HASH_LENGTH), nullable=False)
    #: The author date git recorded, in UTC. Never a "time spent" and never
    #: compared against a working-hours baseline.
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The subject line only. The body is deliberately not stored: commit
    #: messages carry ticket ids, credentials pasted in haste and paragraphs of
    #: context, and none of that is needed to answer "how much changed".
    message: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(_MAX_AUTHOR_NAME_LENGTH), nullable=True)
    author_email: Mapped[str | None] = mapped_column(
        String(_MAX_AUTHOR_EMAIL_LENGTH), nullable=True
    )

    #: Line changes git counted from the diff. ``0`` is a real answer — a merge
    #: is filtered out of the log, and a binary file has no countable lines — and
    #: is stored rather than dropped, so the commit count and the file count
    #: always describe the same set of commits.
    additions: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    files_changed: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Best-effort attribution, from the branch head that currently contains
    #: this commit. Null when no branch contains it (a detached commit) or when
    #: the scan could not resolve one. Never a guess.
    branch: Mapped[str | None] = mapped_column(String(_MAX_BRANCH_NAME_LENGTH), nullable=True)

    #: Deliberately not ``TimestampMixin``: an observed commit is immutable, and
    #: an ``updated_at`` here would assert otherwise.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class GitBranch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One branch the last scan saw under ``refs/heads``.

    Remote branches are not stored. ``refs/remotes/*`` is a mirror of somebody
    else's repository, and counting it would let the user's own commit count
    double every time they added a remote-tracking branch.
    """

    __tablename__ = "git_branches"

    __table_args__ = (
        # Same idempotency anchor as commits, for the same reason: a re-scan
        # sees the same branch names and must not create a second row for one.
        UniqueConstraint("repository_id", "name", name="uq_git_branches_repo_name"),
        Index("ix_git_branches_user_id", "user_id"),
        # The cascade from `git_repositories` is not indexed by any composite
        # index here, so this is the index that makes "delete this repository"
        # cheap as well as the probe for one repository's branches.
        Index("ix_git_branches_repo_id", "repository_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("git_repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(_MAX_BRANCH_NAME_LENGTH), nullable=False)
    #: Exactly one branch per repository has this set, and that is a fact about
    #: git rather than something this table can enforce — a detached HEAD is a
    #: repository with none.
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    #: The branch head's object name. Null when the scan could not read it; the
    #: branch exists in the listing either way.
    head_commit_hash: Mapped[str | None] = mapped_column(
        String(_MAX_COMMIT_HASH_LENGTH), nullable=True
    )
    #: The committer date git reported for the head commit, or null if it was
    #: unreadable. Null is honest; 0 is not.
    last_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GitScanRun(UUIDPrimaryKeyMixin, Base):
    """One attempt to read a repository from disk, and how it went.

    Append-only, and therefore the only one of the four tables with neither an
    ``updated_at`` nor a lifecycle: a scan that has already finished is a fact
    about a moment, and revising it would be a lie. Failed attempts are stored
    as rows too — that is the whole reason this table exists alongside
    ``last_scan_error``, and it is why a repository whose directory has since
    been moved can still explain itself.
    """

    __tablename__ = "git_scan_runs"

    __table_args__ = (
        Index("ix_git_scan_runs_user_id", "user_id"),
        # "When was this repository last read, and how did each read go?" —
        # the scan history panel.
        Index("ix_git_scan_runs_repo_scanned", "repository_id", "scanned_at"),
        CheckConstraint(
            "commits_discovered >= 0 AND commits_added >= 0 "
            "AND branches_discovered >= 0 AND duration_ms >= 0",
            name="ck_git_scan_runs_counts_non_negative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("git_repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: ``ok`` or ``error``, validated in Python. A row is only ever written
    #: after the attempt has finished, so there is deliberately no ``pending``
    #: member: a scan is a synchronous request, and a row that claimed to be in
    #: progress could outlive the request that started it.
    status: Mapped[str] = mapped_column(
        String(_MAX_GIT_SCAN_STATUS_LENGTH),
        server_default=DEFAULT_GIT_SCAN_STATUS,
        nullable=False,
    )
    #: What git returned, and how much of it was new. The two differ on exactly
    #: the re-scan of an unchanged repository, and ``commits_added`` is what
    #: tells the two apart.
    commits_discovered: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    commits_added: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    branches_discovered: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Wall-clock duration of the CLI call. Recorded because it is the only
    #: signal that a repository has become expensive to read; it is explicitly
    #: *not* a measure of anybody's work.
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: A human sentence for a failed scan — what git said, sanitised, or why the
    #: path could not be read. Never a traceback, never an absolute path from
    #: inside the user's home directory.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When the attempt finished. Distinct from ``created_at``, which is when
    #: the row was written; on a slow scan the two differ by the write itself,
    #: and it is ``scanned_at`` that belongs on a timeline.
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
