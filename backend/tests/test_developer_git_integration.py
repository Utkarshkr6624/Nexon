"""Phase 8 against **real git repositories**: the five shapes that break a scanner.

``tests/test_developer_git.py`` covers the engine with the ``git`` CLI on PATH and
this file covers it again from the other end, through
:meth:`~app.services.developer.service.DeveloperIntelligenceService.scan_repository`
— but that is not the reason for the duplication. The reason is that a mocked
subprocess only proves *this* implementation passed *these* arguments. It cannot
prove that git's ``--pretty=format:%H%x09%h%x09%aI...`` output parses into the
stored columns, that ``--numstat`` counts the lines a fixture actually wrote, or
that a checkout performed from a detached HEAD is a normal state rather than an
exception. Every repository below is created for real, in ``tmp_path``, by the
``git`` binary, and every expected figure is derived from what the fixture wrote
into the work tree.

The five shapes
---------------
Each of these is a state a developer's machine is genuinely in, and each one has a
different correct answer — which is the point of putting them side by side:

``empty``
    A directory that was just ``git init``ed. ``git log`` exits non-zero here, so
    an engine that treated a non-zero exit as a broken repository would tell a
    user their brand-new project does not exist. It is a **successful** scan that
    records nothing.
``single commit``
    The smallest repository with any history, and the one where every stored
    column has a value that came from git rather than from a default.
``detached HEAD``
    ``git rev-parse --abbrev-ref HEAD`` prints the literal string ``HEAD``. That
    is a *name*, and storing it would put a branch on the repository that does not
    exist. ``None`` is the answer, and it is not an error.
``deleted after registration``
    The directory the user registered is gone by the time they press scan. The
    scan must produce a row with a human sentence and **must not raise**.
``no recent activity``
    A repository with real history that has been quiet for months. Its summary
    says so honestly — ``has_data`` is true, the in-window figures are measured
    zeros, and the one metric with a zero denominator declines rather than
    inventing a growth rate.

House style
-----------
``pytestmark = pytest.mark.integration``, a hand-wired ``_service()`` helper
mirroring ``app.api.deps``, and every stored row read back through an **explicit
column tuple** rather than through an ORM entity: this session wrote those rows,
so an entity read would return whatever the identity map cached and an idempotency
assertion would pass for the wrong reason. Instants come from ``_db_now`` — the
database's ``now()`` — because the service resolves every window from
``func.now()`` and a fixture anchored on the host clock would land in a different
day than the window the service computes.

One caveat, stated once here rather than in five docstrings
-----------------------------------------------------------
``commit_count`` on a repository row is what the **last scan** observed, and the
scan is incremental. A first scan of a repository with two commits sets it to 2; a
re-scan with ``--since=<latest_commit_at>`` transfers only the commit git admits
on that boundary instant and would set the counter from that. The service
therefore accumulates (``repository.commit_count + inserted``), and every
assertion below is made against the value the first scan wrote.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.developer import GitBranch, GitCommit, GitRepository, GitScanRun
from app.models.enums import ActivityEvent, GitScanStatus
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.project import ProjectRepository
from app.services.activity_service import ActivityService
from app.services.developer.metrics import NOT_ENOUGH_DATA
from app.services.developer.service import DeveloperIntelligenceService
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The window the quiet-repository test reports against. Thirty days is the
#: configured default, and the two commits in that fixture sit 100 and 120 days
#: back, so every in-window figure is a measured zero rather than a filtered one.
WINDOW = 30


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> DeveloperIntelligenceService:
    """A developer service wired the way ``app.api.deps`` will wire it.

    The activity sink is the real one: the dirty-working-tree test asserts on a
    ``FILE_ACTIVITY_DETECTED`` row, and a ``None`` sink would make it vacuous.
    """
    return DeveloperIntelligenceService(
        repositories=DeveloperRepository(session),
        projects=ProjectRepository(session),
        activity=ActivityService(ActivityRepository(session)),
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly; no activity rows of its own."""
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock as an aware UTC instant — the same read the service does."""
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Building real repositories
# ---------------------------------------------------------------------------

#: Identity and safety flags for every ``git`` call below.
#:
#: ``-c user.*`` so a commit never depends on the machine's global config;
#: ``-c core.autocrlf=false`` so the line counts git reports are the line counts
#: the fixture wrote rather than the ones a Windows checkout rewrote;
#: ``-c commit.gpgsign=false`` so a developer machine with a signing key cannot
#: make this suite fail on a passphrase prompt; and ``-c safe.directory=*`` so a
#: ``tmp_path`` owned by another account on a build agent is still readable. This
#: is fixture setup, not product behaviour: the engine under test passes no ``-c``
#: flags at all.
_GIT_FLAGS: tuple[str, ...] = (
    "-c",
    "safe.directory=*",
    "-c",
    "user.name=Ada Lovelace",
    "-c",
    "user.email=ada@nexus.test",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "commit.gpgsign=false",
)


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    """Run one real git command inside ``repo`` and return its stdout.

    ``when`` sets both the author and the committer date, which is how a commit is
    placed on a chosen day without the suite sleeping: git records whatever the
    environment tells it, and every assertion below is about the instants the
    repository recorded rather than about the wall clock.

    Args:
        repo: The working directory. Must already exist.
        *args: The subcommand and its arguments, each a separate element — no
            shell, for the same reason the engine itself builds no command string.
        when: The instant to record on a commit, if any.

    Returns:
        The command's stdout, stripped.

    Raises:
        CalledProcessError: If git exits non-zero. A fixture that cannot build the
            repository it is about to register should fail at the setup line rather
            than hand the test an empty snapshot and a passing assertion.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when.isoformat()
        env["GIT_COMMITTER_DATE"] = when.isoformat()
    completed = subprocess.run(  # noqa: S603 — test fixture, argv is fixed
        ["git", *_GIT_FLAGS, *args],  # noqa: S607 — the fixture resolves git on PATH
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init(root: Path, name: str) -> Path:
    """Create and return an initialised, commit-less work tree called ``name``.

    ``HEAD`` is pointed at ``refs/heads/main`` explicitly rather than relying on
    ``init.defaultBranch``: git's default branch name has been ``master`` and
    ``main`` across versions and configurations, and every assertion about
    ``current_branch`` and ``is_default`` below would otherwise be a statement
    about whichever git happens to be installed.
    """
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo


def _commit(
    repo: Path,
    filename: str,
    message: str,
    *,
    when: datetime,
    body: str = "one\n",
) -> str:
    r"""Write ``body`` to ``filename``, commit it, and return the full hash.

    ``body`` is written with an explicit newline so the file's line count is the
    number of lines the fixture asked for on every platform; the default
    text-mode translation would store ``\\r\\n`` on Windows and the derived
    ``additions`` figures would then be describing a different file.

    Args:
        repo: The work tree.
        filename: Path relative to the repository root.
        message: The commit subject. Only the subject is ever stored.
        when: The instant git should record on the commit.
        body: The whole new content of the file.

    Returns:
        The full commit hash.
    """
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    _git(repo, "add", filename, when=when)
    _git(repo, "commit", "--quiet", "--no-gpg-sign", "-m", message, when=when)
    return _git(repo, "rev-parse", "HEAD")


def _force_rmtree(path: Path) -> None:
    """Delete ``path`` and everything under it, read-only files included.

    Git marks its object files read-only on Windows so a stray tool cannot corrupt
    the object store, and that makes a plain ``shutil.rmtree`` fail with
    ``PermissionError`` on exactly the deletion the broken-repository fixture
    needs. The retry clears the read-only bit first; ``onexc`` rather than the
    removed ``onerror`` because the suite runs on Python 3.13.
    """

    def _retry(function, target, _exc_info) -> None:
        with contextlib.suppress(OSError):
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=_retry)


# ---------------------------------------------------------------------------
# Reading stored rows back
# ---------------------------------------------------------------------------

_REPOSITORY_COLUMNS = (
    GitRepository.id,
    GitRepository.commit_count,
    GitRepository.branch_count,
    GitRepository.current_branch,
    GitRepository.default_branch,
    GitRepository.first_commit_at,
    GitRepository.latest_commit_at,
    GitRepository.primary_language,
    GitRepository.working_tree_dirty,
    GitRepository.last_scanned_at,
    GitRepository.last_scan_status,
    GitRepository.last_scan_error,
)

_COMMIT_COLUMNS = (
    GitCommit.commit_hash,
    GitCommit.short_hash,
    GitCommit.committed_at,
    GitCommit.message,
    GitCommit.author_name,
    GitCommit.author_email,
    GitCommit.additions,
    GitCommit.deletions,
    GitCommit.files_changed,
    GitCommit.branch,
)

_BRANCH_COLUMNS = (
    GitBranch.name,
    GitBranch.is_current,
    GitBranch.is_default,
    GitBranch.head_commit_hash,
)


async def _repository(session: AsyncSession, repository_id: uuid.UUID) -> dict[str, object]:
    """The one stored repository row, as plain mappings."""
    result = await session.execute(
        select(*_REPOSITORY_COLUMNS).where(GitRepository.id == repository_id)
    )
    row = result.one_or_none()
    assert row is not None, f"no stored repository with id {repository_id}"
    return dict(row._mapping)


async def _commits(
    session: AsyncSession, owner_id: uuid.UUID, repository_id: uuid.UUID
) -> list[dict[str, object]]:
    """That repository's stored commits, newest first."""
    result = await session.execute(
        select(*_COMMIT_COLUMNS)
        .where(GitCommit.user_id == owner_id, GitCommit.repository_id == repository_id)
        .order_by(GitCommit.committed_at.desc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _branches(
    session: AsyncSession, owner_id: uuid.UUID, repository_id: uuid.UUID
) -> list[dict[str, object]]:
    """That repository's stored branches, by name."""
    result = await session.execute(
        select(*_BRANCH_COLUMNS)
        .where(GitBranch.user_id == owner_id, GitBranch.repository_id == repository_id)
        .order_by(GitBranch.name.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _scan_runs(
    session: AsyncSession, owner_id: uuid.UUID, repository_id: uuid.UUID
) -> list[dict[str, object]]:
    """That repository's stored scan runs, oldest first."""
    result = await session.execute(
        select(
            GitScanRun.status,
            GitScanRun.commits_discovered,
            GitScanRun.commits_added,
            GitScanRun.branches_discovered,
            GitScanRun.error,
        )
        .where(GitScanRun.user_id == owner_id, GitScanRun.repository_id == repository_id)
        .order_by(GitScanRun.scanned_at.asc(), GitScanRun.id.asc())
    )
    return [
        {
            "status": row[0],
            "commits_discovered": row[1],
            "commits_added": row[2],
            "branches_discovered": row[3],
            "error": row[4],
        }
        for row in result.all()
    ]


async def _event_metadata(
    session: AsyncSession, owner_id: uuid.UUID, event_type: ActivityEvent
) -> list[dict[str, object]]:
    """The metadata of every history row of one type this account has."""
    result = await session.execute(
        select(ActivityLog.metadata_).where(
            ActivityLog.user_id == owner_id, ActivityLog.event_type == event_type.value
        )
    )
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# (a) An empty repository is a repository
# ---------------------------------------------------------------------------


async def test_a_repository_that_was_only_just_initialised_scans_cleanly_and_records_nothing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """``git init``, no commits: a successful scan that observes nothing.

    This is the very first thing a user does with the feature, and it is where
    ``git log`` exits non-zero — ``HEAD`` does not resolve, so there is no history
    to read. Treating that non-zero exit as a broken repository would register a
    directory and then tell the owner their new project is damaged.

    Every column that depends on history is therefore **null or zero**, never a
    fabricated figure: ``current_branch`` is null because HEAD is an unborn symref
    and ``rev-parse --abbrev-ref HEAD`` cannot name a branch, and
    ``first_commit_at``, ``latest_commit_at`` and ``primary_language`` are null
    because there is no history and nothing is tracked. ``default_branch`` is the
    one non-null column, and it is honest: ``symbolic-ref HEAD`` resolves to
    ``refs/heads/main`` on an unborn branch, and ``main`` is where the first commit
    will land. (``RepositoryRead.default_branch`` documents "null for a repository
    with no commits"; that is not what the engine produces, and the engine's
    answer is the better one — noted here rather than worked around.)

    The feature vector is asserted here too because this is the only fixture that
    produces a repository with genuinely no commits: ``repository_age_days`` and
    ``inactivity_days`` are null there, and 0 would claim it was created today.

    The eight metrics are checked on the same fixture. Seven are measured zeros —
    "no commits were recorded in this window" is a true sentence — and
    ``recent_momentum`` declines, because its denominator is the seven days before
    the last seven and an account with no commits has nothing to divide.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "brand-new")

    registered = await service.register_repository(owner=owner, local_path=str(repo))
    run = await service.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.OK.value
    assert run.commits_discovered == 0
    assert run.commits_added == 0
    assert run.branches_discovered == 0
    assert run.error is None
    assert run.duration_ms >= 0

    stored = await _repository(db_session, registered.id)
    assert stored["commit_count"] == 0
    assert stored["branch_count"] == 0
    # HEAD is an *unborn* symref here, so `rev-parse --abbrev-ref HEAD` fails and
    # there is no branch to name. `symbolic-ref HEAD`, by contrast, succeeds and
    # reports `refs/heads/main` — the branch the first commit will land on, which
    # is a true answer even with nothing on it.
    assert stored["current_branch"] is None
    assert stored["default_branch"] == "main"
    assert stored["first_commit_at"] is None
    assert stored["latest_commit_at"] is None
    assert stored["primary_language"] is None
    assert stored["working_tree_dirty"] is False
    assert stored["last_scan_status"] == GitScanStatus.OK.value
    assert stored["last_scan_error"] is None
    assert stored["last_scanned_at"] is not None

    assert await _commits(db_session, owner.id, registered.id) == []
    assert await _branches(db_session, owner.id, registered.id) == []
    assert [row["status"] for row in await _scan_runs(db_session, owner.id, registered.id)] == [
        GitScanStatus.OK.value
    ]

    vector = await service.features(owner=owner, window_days=WINDOW)
    only = vector.repositories[0]
    assert only.repository_id == registered.id
    assert only.commits_last_7d == 0
    assert only.commits_last_30d == 0
    assert only.repository_age_days is None
    assert only.inactivity_days is None

    metrics = {
        metric.key: metric for metric in await service.metrics(owner=owner, window_days=WINDOW)
    }
    for key in (
        "commit_activity",
        "repository_activity",
        "change_volume",
        "active_days",
        "consistency",
        "repository_growth",
        "maintenance_activity",
    ):
        assert metrics[key].value == 0.0, key
        assert metrics[key].available is True, key
    assert metrics["recent_momentum"].available is False
    assert metrics["recent_momentum"].value is None
    assert metrics["recent_momentum"].reason_if_unavailable == NOT_ENOUGH_DATA


# ---------------------------------------------------------------------------
# (b) The smallest repository with any history
# ---------------------------------------------------------------------------


async def test_a_single_commit_repository_is_stored_with_the_line_counts_git_reported(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """One commit adding a three-line file: 3 additions, 0 deletions, 1 file changed.

    Every figure is git's, and every one is derived from what the fixture wrote:
    ``app.py`` is created with the three lines ``one``, ``two``, ``three``, so
    ``--numstat`` reports ``3`` in the insertions cell, nothing in the deletions
    cell, and one file. Nothing here is inferred by NEXUS — a reader who doubted
    the row could run ``git show --numstat`` and get the same answer.

    The identity columns are asserted as *relations* rather than as literals: the
    full hash is 40 hex characters and the stored abbreviation is a prefix of it,
    which is the whole contract of a display hash, and a literal would have to be
    re-copied every time git changed its abbreviation length.

    ``committed_at`` is asserted to within a second of the instant the fixture
    asked for. Git stores timestamps at second resolution, so the stored value is
    the fixture's instant truncated — a fixture cannot demand the microseconds the
    repository does not have.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "single")
    service_now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))

    moment = service_now - timedelta(days=2)
    expected_hash = _commit(repo, "app.py", "first commit", when=moment, body="one\ntwo\nthree\n")
    run = await service.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.OK.value
    assert run.commits_discovered == 1
    assert run.commits_added == 1
    assert run.branches_discovered == 1
    assert run.error is None

    commits = await _commits(db_session, owner.id, registered.id)
    assert len(commits) == 1
    stored = commits[0]
    assert stored["commit_hash"] == expected_hash
    assert len(stored["commit_hash"]) == 40
    assert stored["short_hash"]
    assert stored["commit_hash"].startswith(stored["short_hash"])
    assert stored["message"] == "first commit"
    assert stored["author_name"] == "Ada Lovelace"
    assert stored["author_email"] == "ada@nexus.test"
    assert (stored["additions"], stored["deletions"], stored["files_changed"]) == (3, 0, 1)
    assert abs((stored["committed_at"] - moment).total_seconds()) < 1

    stored_repository = await _repository(db_session, registered.id)
    assert stored_repository["commit_count"] == 1
    assert stored_repository["branch_count"] == 1
    assert stored_repository["current_branch"] == "main"
    assert stored_repository["default_branch"] == "main"
    assert stored_repository["primary_language"] == "Python"
    assert stored_repository["working_tree_dirty"] is False


# ---------------------------------------------------------------------------
# (c) A detached HEAD is a state, not a failure
# ---------------------------------------------------------------------------


async def test_a_detached_head_is_recorded_as_no_current_branch_rather_than_as_the_string_head(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """``rev-parse --abbrev-ref HEAD`` prints ``HEAD``; the row stores null.

    A detached HEAD — a commit checked out by hash, which is what bisecting, a
    bisect-abort and half of every hotfix workflow leave behind — makes git print
    the literal string ``HEAD``. That is a placeholder, not a branch: no ref by
    that name exists, so storing it would put a branch on the repository that a
    user could not switch to and that no ``for-each-ref refs/heads`` would ever
    return. The stored answer is null, which is the truth.

    Three things are asserted because they could each have gone the other way:

    * the **scan succeeds** — a detached HEAD is a normal state and
      ``status`` stays ``ok``;
    * ``current_branch`` is **null** on the repository row, and no branch row has
      ``is_current`` set, because HEAD points at no branch at all;
    * ``default_branch`` is **null too**, and so is every ``is_default``. Both come
      from ``symbolic-ref``, which cannot resolve anything while HEAD is a raw
      commit rather than a symref. That is the truthful answer — git genuinely does
      not know what the default branch is from a detached checkout — and it is the
      shape of absence the rest of this phase uses rather than a guess at ``main``.

    The two branches are still listed and the two commits are still stored: a
    detached working tree says nothing about the refs the repository has.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "detached")
    now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\n")
    _commit(repo, "app.py", "second", when=now - timedelta(days=2), body="one\ntwo\n")
    _git(repo, "branch", "feature")
    _git(repo, "checkout", "--quiet", "--detach", "HEAD")

    run = await service.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.OK.value
    assert run.error is None
    assert run.commits_discovered == 2
    assert run.commits_added == 2
    assert run.branches_discovered == 2

    stored = await _repository(db_session, registered.id)
    assert stored["current_branch"] is None
    assert stored["default_branch"] is None
    assert stored["commit_count"] == 2
    assert stored["branch_count"] == 2
    assert stored["last_scan_error"] is None

    branches = {row["name"]: row for row in await _branches(db_session, owner.id, registered.id)}
    assert set(branches) == {"main", "feature"}
    assert all(row["is_current"] is False for row in branches.values())
    assert all(row["is_default"] is False for row in branches.values())

    commits = await _commits(db_session, owner.id, registered.id)
    assert len(commits) == 2
    assert all(row["branch"] in {"main", "feature", None} for row in commits)


# ---------------------------------------------------------------------------
# (d) The repository is gone by the time the user presses scan
# ---------------------------------------------------------------------------


async def test_a_repository_whose_directory_was_deleted_after_registration_scans_as_an_error_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Directory removed between registration and scan: a sentence, and no exception.

    This is the shape the brief's "a broken repository must never break NEXUS" rule
    is about. The user registered a directory, moved or deleted it, and pressed the
    scan button. The alternative to a row is an exception, and an exception here
    is one unlucky ``POST /scan`` away from a 500 that takes the page down.

    Four claims, asserted separately because a scan service can fail any of them
    on its own:

    * the scan **returns** a ``status='error'`` run rather than raising;
    * the **sentence** names what happened, is a single line, and carries no
      traceback — git's diagnostics are written for a terminal, and one is not a
      user-facing response;
    * the sentence does **not** contain the server operator's home directory.
      Absolute paths are left readable on purpose (a user needs to know which
      directory failed), but the operator's home was never mentioned by the user
      and it carries their username, so it is collapsed to ``~``;
    * the **earlier history survives** — two commits, one branch and a
      ``commit_count`` of 2 are all still stored, and the counters do not move,
      because a failed scan observed nothing and has no reason to forget.

    The scan history is the reason the failed attempt is a *row* rather than only a
    message on the repository: a directory that was broken for a week and then
    fixed produces a run of errors followed by the run that worked, and "this was
    unreadable until Tuesday" is a different statement from "this is unreadable".
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "vanishing")
    now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))
    identifier = registered.id
    _commit(repo, "app.py", "first", when=now - timedelta(days=3), body="one\n")
    _commit(repo, "app.py", "second", when=now - timedelta(days=2), body="one\ntwo\n")
    first = await service.scan_repository(owner=owner, repository_id=identifier)
    assert first.status == GitScanStatus.OK.value

    _force_rmtree(repo)
    run = await service.scan_repository(owner=owner, repository_id=identifier)

    assert run.status == GitScanStatus.ERROR.value
    assert isinstance(run.error, str) and run.error
    assert "\n" not in run.error
    assert "Traceback" not in run.error
    assert "No directory was found at" in run.error
    assert str(Path.home()) not in run.error
    assert run.commits_discovered == 0
    assert run.commits_added == 0
    assert run.branches_discovered == 0

    stored = await _repository(db_session, identifier)
    assert stored["last_scan_status"] == GitScanStatus.ERROR.value
    assert stored["last_scan_error"] == run.error
    assert stored["commit_count"] == 2
    assert stored["branch_count"] == 1
    assert len(await _commits(db_session, owner.id, identifier)) == 2
    assert len(await _branches(db_session, owner.id, identifier)) == 1

    runs = await _scan_runs(db_session, owner.id, identifier)
    assert [row["status"] for row in runs] == [
        GitScanStatus.OK.value,
        GitScanStatus.ERROR.value,
    ]
    assert runs[1]["error"] == run.error
    history = await service.scan_history(owner=owner, repository_id=identifier)
    assert [row.status for row in history] == [
        GitScanStatus.ERROR.value,
        GitScanStatus.OK.value,
    ]

    scans = await _event_metadata(db_session, owner.id, ActivityEvent.REPOSITORY_SCANNED)
    assert len(scans) == 2
    assert sum(1 for meta in scans if meta["status"] == GitScanStatus.ERROR.value) == 1


# ---------------------------------------------------------------------------
# (e) A repository that has been quiet for months
# ---------------------------------------------------------------------------


async def test_a_repository_with_no_recent_activity_reports_a_measured_zero_and_declines_momentum(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Commits 120 and 100 days old: history is real, the window is empty, momentum declines.

    The fixture places two commits on days ``-120`` and ``-100`` and asks for a
    30-day window. The two facts that have to stay apart are exactly the two the
    contracts care about:

    * **``has_data`` is true.** The account has 2 recorded commits; the repository
      row carries ``commit_count == 2``. Nothing here is a cold start, and a
      dashboard that called it one would tell a user with a year of history that
      they had never committed.
    * **every in-window figure is a measured zero**, not an absence:
      ``commits_in_window``, ``active_days``, ``change_volume`` and
      ``repositories_touched`` are all 0 *with* ``available=True`` on the metrics
      side, because "no commits were recorded in the last 30 days" is a true
      sentence about a real window.

    ``recent_momentum`` is the one that declines, and it declines *because* the
    denominator is empty: it compares the last seven days against the seven before,
    and both bands are empty here, so ``0/0`` would be a number about a comparison
    nobody can make and ``N/0`` would be infinity — which a chart renders as
    infinite growth. That is the one place in Phase 8 that could produce a
    confidently wrong growth claim, and ``available=False`` with a reason is the
    answer.

    ``repository_growth`` is a measured **zero** rather than a decline, and the
    difference is worth stating: a growth figure is a count of branches first
    recorded inside the window, and none was — the branches' earliest commits are
    100 and 120 days old. Nothing is missing here; the answer is 0.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "dormant")
    now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))
    _commit(repo, "app.py", "old", when=now - timedelta(days=120), body="one\n")
    _commit(repo, "app.py", "older", when=now - timedelta(days=100), body="one\ntwo\n")

    run = await service.scan_repository(owner=owner, repository_id=registered.id)
    assert run.status == GitScanStatus.OK.value
    assert run.commits_discovered == 2
    assert run.commits_added == 2

    stored = await _repository(db_session, registered.id)
    assert stored["commit_count"] == 2
    assert stored["latest_commit_at"] is not None
    assert (stored["latest_commit_at"] - now).days <= -100

    summary = await service.summary(owner=owner, window_days=WINDOW)
    assert summary.has_data is True
    assert summary.commit_count == 2
    assert summary.commits_in_window == 0
    assert summary.active_days == 0
    assert summary.change_volume == 0
    assert summary.repositories_touched == 0
    assert summary.window_days == WINDOW
    assert summary.summary == (
        "0 commits were recorded across 0 repositories in the last 30 days, changing 0 lines."
    )

    metrics = {
        metric.key: metric for metric in await service.metrics(owner=owner, window_days=WINDOW)
    }
    for key in (
        "commit_activity",
        "repository_activity",
        "change_volume",
        "active_days",
        "consistency",
        "repository_growth",
        "maintenance_activity",
    ):
        assert metrics[key].value == 0.0, key
        assert metrics[key].available is True, key
        assert metrics[key].reason_if_unavailable is None, key
    momentum = metrics["recent_momentum"]
    assert momentum.available is False
    assert momentum.value is None
    assert momentum.reason_if_unavailable == NOT_ENOUGH_DATA

    series = await service.read_activity(owner=owner, window_days=WINDOW, granularity="day")
    assert series.total_commits == 0
    assert all(bucket.commits == 0 for bucket in series.buckets)

    vector = await service.features(owner=owner, window_days=WINDOW)
    assert vector.features.commits_last_7d == 0
    assert vector.features.commits_last_30d == 0
    assert isinstance(vector.features.inactivity_days, int)
    assert vector.features.inactivity_days >= 100


# ---------------------------------------------------------------------------
# (f) A dirty working tree is a state, not a judgement
# ---------------------------------------------------------------------------


async def test_an_untracked_file_makes_the_working_tree_dirty_and_records_one_file_activity_event(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """One uncommitted file: ``working_tree_dirty`` true, and one event carrying a count.

    ``git status --porcelain`` reports the file and the scan counts the lines, so
    ``working_tree_dirty`` is true and the single ``FILE_ACTIVITY_DETECTED`` row
    carries ``pending_changes: 1``.

    The count and not the content is the whole design. A porcelain line says what
    *kind* of change it is, and every interpretation of that a scanner could write
    — "you have been working on this for three days", "this is half-finished" —
    would be a claim about a person's uncommitted work that the record does not
    support. A commit timestamp cannot say how long anyone worked, and neither can
    a file's modification time; what the record supports is *how many changes were
    pending when the scan ran*, and that is exactly what is stored.

    The clean case is asserted in the same fixture's setup: the repository was
    committed immediately before the untracked file was written, so the ``true``
    is a change of state between two scans of the same repository and not a
    property of the repository all along.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "in-progress")
    now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))
    _commit(repo, "app.py", "first", when=now - timedelta(days=2), body="one\n")
    clean = await service.scan_repository(owner=owner, repository_id=registered.id)
    assert clean.status == GitScanStatus.OK.value
    assert (await _repository(db_session, registered.id))["working_tree_dirty"] is False
    assert await _event_metadata(db_session, owner.id, ActivityEvent.FILE_ACTIVITY_DETECTED) == []

    (repo / "notes.txt").write_text("uncommitted\n", encoding="utf-8", newline="\n")
    dirty = await service.scan_repository(owner=owner, repository_id=registered.id)

    assert dirty.status == GitScanStatus.OK.value
    assert dirty.error is None
    stored = await _repository(db_session, registered.id)
    assert stored["working_tree_dirty"] is True
    assert stored["commit_count"] == 1
    assert len(await _commits(db_session, owner.id, registered.id)) == 1

    events = await _event_metadata(db_session, owner.id, ActivityEvent.FILE_ACTIVITY_DETECTED)
    assert events == [
        {"repository_id": str(registered.id), "pending_changes": 1},
    ]


# ---------------------------------------------------------------------------
# (g) A repository spanning several languages
# ---------------------------------------------------------------------------


async def test_a_repository_spanning_several_languages_reports_the_most_common_one(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Three Python files, one TypeScript, one Markdown: ``Python``, and never ``Other``.

    The fixture commits five files in one commit — ``mod/one.py``, ``mod/two.py``,
    ``mod/three.py``, ``ui/view.ts`` and ``README.md`` — each holding one line. The
    distribution git's file list feeds through the engine's static extension map is
    therefore Python 3, TypeScript 1, Markdown 1, and ``primary_language`` is the
    most common: **Python**. It is a fact about tracked files, read from
    ``git ls-files`` rather than from the working tree, so an untracked scratch
    file cannot move it.

    The negative half matters as much as the positive one. An extension the map
    does not know is **not** counted at all, rather than bucketed into an ``Other``
    row: "Other" is a number nobody can act on and it reads on the dashboard as
    though it were a language. So the commit count and the file count are asserted
    separately — one commit touching **5** files, and ``files_changed == 5`` —
    which is what pins "every tracked file is in the commit count" independently of
    "every tracked file is in a language bucket".

    The additions figure is derived too: five files of one line each is 5
    insertions and 0 deletions.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    repo = _init(tmp_path, "polyglot")
    now = await _db_now(db_session)
    registered = await service.register_repository(owner=owner, local_path=str(repo))

    for name in ("mod/one.py", "mod/two.py", "mod/three.py", "ui/view.ts", "README.md"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("one\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--all")
    _git(
        repo,
        "commit",
        "--quiet",
        "--no-gpg-sign",
        "-m",
        "initial import",
        when=now - timedelta(days=4),
    )

    run = await service.scan_repository(owner=owner, repository_id=registered.id)
    assert run.status == GitScanStatus.OK.value
    assert run.commits_discovered == 1

    stored = await _repository(db_session, registered.id)
    assert stored["primary_language"] == "Python"
    assert stored["primary_language"] != "Other"

    commits = await _commits(db_session, owner.id, registered.id)
    assert len(commits) == 1
    assert commits[0]["files_changed"] == 5
    assert commits[0]["additions"] == 5
    assert commits[0]["deletions"] == 0
