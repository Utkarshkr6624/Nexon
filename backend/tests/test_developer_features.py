"""Phase 10 preconditions: what a feature vector may claim, and what it may not.

Everything here is about the seam between "what NEXUS has measured" and "what a
later trainer will read as a fact about a person". The other developer test
files decide what the service stores and what it refuses; this one decides which
of those facts are allowed to become a **number in a matrix**.

Three groups, each for a defect that was invisible in a demo and wrong in
production:

* **A repository nobody has scanned must not produce a row.** A registered
  repository that has never been read publishes the same eight zeros as one that
  was scanned and genuinely recorded nothing. Inside a training matrix those two
  rows are the same row, and a model learns from it that a developer did nothing
  when the truth is that NEXUS never looked. The vector therefore omits an
  unmeasured repository and keeps a measured-and-empty one — the ``available``
  semantics of ``/learning``-style surfaces, expressed in a schema that has no
  ``available`` column.
* **Statement counts must not grow with the number of repositories.** A page
  that costs one query per repository is a page whose latency is a statement
  about how busy somebody has been, and both the project view and the branch
  growth metric grew exactly that way. Each is counted through a
  ``before_cursor_execute`` listener at two repository counts, because an
  assertion on the returned numbers passes just as happily on the slow
  implementation.
* **The configured allowlist is enforced on the scan, not only at
  registration.** A stored path is a string, not a promise about what it
  resolves to on the day it is read back, and a configuration that silently
  stopped restricting anything is worse than one that was never written.

House style, deliberately
--------------------------
Follows ``tests/test_developer_service.py``: every collaborator is the real one,
rows are read back through explicit column tuples rather than through the ORM,
and every figure is derived in the docstring of the test that asserts it rather
than recorded from a run. Repositories are built by driving the system ``git``
binary, because a mock proves the arguments this implementation *would* pass and
nothing about whether the scan stores what git actually reported.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ValidationError
from app.models.developer import GitCommit, GitRepository
from app.models.enums import GitScanStatus
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.project import ProjectRepository
from app.services.activity_service import ActivityService
from app.services.developer.service import DeveloperIntelligenceService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The window every test below is written against. Thirty days is the configured
#: default, and it is the length the metrics arithmetic in the docstrings is
#: derived from.
WINDOW = 30


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(
    session: AsyncSession, *, settings: Settings | None = None
) -> DeveloperIntelligenceService:
    """A developer service wired the way ``app.api.deps`` wires it.

    Args:
        session: The test session. Every repository is built on the same one.
        settings: Supplied only by the allowlist tests, which need a deployment
            whose ``developer_path_allowlist`` names a particular root.

    Returns:
        The service, with no collaborator left as ``None``.
    """
    return DeveloperIntelligenceService(
        repositories=DeveloperRepository(session),
        projects=ProjectRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly.

    ``register_user`` writes no activity events, which matters only where an
    event count is asserted; here it simply keeps the fixture to one row.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read the service performs, so a fixture commit placed relative to
    it falls inside the window the service computes.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Real git repositories
# ---------------------------------------------------------------------------

#: Identity and safety flags passed to every ``git`` call below.
#:
#: ``-c user.*`` so a commit never depends on the machine's global config,
#: ``-c core.autocrlf=false`` so the line counts are the ones the fixture wrote,
#: ``-c safe.directory=*`` so a ``tmp_path`` owned by another user on a build
#: agent is still readable, and ``-c commit.gpgsign=false`` so a machine with a
#: signing key configured does not fail the fixture. This is test setup, not a
#: product behaviour — the engine passes no ``-c`` flags at all.
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
    """Run one git command inside ``repo`` and return its stdout.

    ``when`` sets both the author and the committer date, which is how a commit
    is placed on a chosen day without sleeping. Both matter: the service windows
    are built from ``committed_at`` (the author date), and git's ``--since``
    filters on the committer date, so setting only one of them would make the
    engine's windowed reads disagree with the fixtures' arithmetic.

    Args:
        repo: The working directory. Must already exist.
        *args: The subcommand and its arguments, each a separate element.
        when: The instant to record on the commit, if any.

    Returns:
        The command's stdout, stripped.

    Raises:
        CalledProcessError: If git exits non-zero. A fixture that cannot build
            the repository it is about to scan should fail loudly rather than
            hand the test an empty scan.
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

    ``symbolic-ref HEAD`` is set explicitly rather than relying on
    ``init.defaultBranch``, which has been ``master`` and ``main`` in different
    git versions, so every assertion about the default branch describes git and
    not the machine's configuration.
    """
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo


def _commit(repo: Path, message: str, *, when: datetime, lines: int = 2) -> None:
    """Append lines to a tracked file and commit them at a fixed instant.

    The file is *appended* to rather than rewritten, so two commits in the same
    repository are never the same tree: a rewritten-identical file would leave
    git with nothing to commit and the fixture would fail in a way that has
    nothing to do with what it is measuring.

    Args:
        repo: The work tree.
        message: The commit subject.
        when: The instant recorded on the commit.
        lines: How many lines the commit adds. Every additions figure in this
            file is derived from this number rather than from a previous run.
    """
    path = repo / "app.py"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        existing + "".join(f"{message} line {index}\n" for index in range(lines)),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message, when=when)


async def _register(service: DeveloperIntelligenceService, owner: User, path: Path):
    """Register one work tree for ``owner`` and return the stored row.

    Args:
        service: The wired service.
        owner: The account registering it.
        path: The work tree to register.

    Returns:
        The ``RepositoryRead`` the service stored.
    """
    return await service.register_repository(owner=owner, local_path=str(path))


# ---------------------------------------------------------------------------
# Statement counting
# ---------------------------------------------------------------------------


async def _statements_during(engine, call):
    """Run ``call`` and return the SQL statements it issued, plus its result.

    A ``before_cursor_execute`` listener on the engine's synchronous face counts
    statements rather than rows, which is the only measure that can see an N+1:
    the rows a query returns are identical whether the repository was fetched
    with one aggregate or with one aggregate per repository, so an assertion on
    the answer alone passes on the implementation this file exists to catch.

    Args:
        engine: The async engine the test session is bound to.
        call: A zero-argument coroutine function to run.

    Returns:
        ``(statements, result)`` — the SQL text of each statement in order, and
        whatever ``call`` returned.
    """
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return statements, result


def _grouped_commit_statements(statements: list[str]) -> int:
    """How many of these statements aggregate ``git_commits`` in one group.

    Args:
        statements: The SQL text recorded for one call.

    Returns:
        The number of statements that both read ``git_commits`` and group the
        rows they return.
    """
    return sum(1 for sql in statements if "git_commits" in sql and "GROUP BY" in sql.upper())


# ---------------------------------------------------------------------------
# The Phase 10 precondition
# ---------------------------------------------------------------------------


async def test_only_a_repository_that_was_measured_gets_a_row_in_the_feature_vector(
    db_session: AsyncSession, engine, tmp_path: Path
) -> None:
    """An unmeasured repository is absent; a measured-and-empty one is all zeros.

    Four repositories are registered for one account:

    * ``never-scanned`` — registered, never read. Nobody knows what is in it.
    * ``scanned-empty`` — scanned, and the scan found no commits at all.
    * ``unreadable`` — registered, then its directory deleted, so the scan
      failed and its figures are as unknown as the first repository's.
    * ``with-history`` — scanned, with one commit two lines long four days ago.

    Expected figures, derived from the fixture rather than from a run: the
    vector carries exactly two repository rows. ``with-history`` reports
    ``commits_last_7d == 1`` and ``additions_7d == 2`` — one commit, two lines —
    and ``scanned-empty`` reports zeros with a null age and a null inactivity,
    because a scan ran and measured nothing. The other two have **no row at
    all**, which is the only honest thing the schema can say: every field of
    ``RepositoryFeatureVectorRead`` is non-nullable, so a row for them would
    carry the same eight zeros as ``scanned-empty`` and a trainer could not tell
    "this developer committed nothing" from "we never looked".

    The account row is the control and is deliberately *not* zero: the three
    unmeasured repositories contribute nothing to it, so the account reports the
    one commit that was genuinely recorded. A roll-up that treated "not measured"
    as "measured zero" would report ``0`` here and lose the one fact the
    database holds.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    now = await _db_now(db_session)

    never_scanned = _init(tmp_path, "never-scanned")
    scanned_empty = _init(tmp_path, "scanned-empty")
    unreadable = _init(tmp_path, "unreadable")
    with_history = _init(tmp_path, "with-history")

    unscanned_row = await _register(service, owner, never_scanned)
    empty_row = await _register(service, owner, scanned_empty)
    unreadable_row = await _register(service, owner, unreadable)
    busy_row = await _register(service, owner, with_history)

    _commit(with_history, "First", when=now - timedelta(days=4))
    await service.scan_repository(owner=owner, repository_id=empty_row.id)
    await service.scan_repository(owner=owner, repository_id=busy_row.id)
    shutil.rmtree(unreadable)
    failed = await service.scan_repository(owner=owner, repository_id=unreadable_row.id)
    assert failed.status == GitScanStatus.ERROR, "the fixture must have failed to scan"

    vector = await service.features(owner=owner, window_days=WINDOW)

    by_id = {row.repository_id: row for row in vector.repositories}
    assert set(by_id) == {empty_row.id, busy_row.id}
    assert unscanned_row.id not in by_id, "never scanned: no figures may be published"
    assert unreadable_row.id not in by_id, "last scan failed: the figures are stale"

    silent = by_id[empty_row.id]
    assert silent.commits_last_7d == 0
    assert silent.commits_last_30d == 0
    assert silent.active_days_7d == 0
    assert silent.files_changed_7d == 0
    assert silent.additions_7d == 0
    assert silent.deletions_7d == 0
    assert silent.repository_age_days is None
    assert silent.inactivity_days is None

    busy = by_id[busy_row.id]
    assert busy.commits_last_7d == 1
    assert busy.commits_last_30d == 1
    assert busy.active_days_7d == 1
    assert busy.files_changed_7d == 1
    assert busy.additions_7d == 2
    assert busy.deletions_7d == 0
    assert busy.repository_age_days == 4, "one commit exactly four days ago"

    assert vector.features.commits_last_7d == 1, (
        "the account row describes the record: the three repositories nobody "
        "measured contribute nothing, and the one measured commit is not lost"
    )
    assert vector.features.commits_last_30d == 1
    assert vector.features.repository_age_days == 4


async def test_the_feature_vector_still_reports_every_measured_repository(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The omission is about measurement, not about repositories.

    Three repositories are registered and all three are scanned. Every one of
    them appears, so a client can still join the vector against the repository
    list it already fetched — the vector drops rows only when the figures behind
    them were never established. A repository with a commit four days ago
    reports ``commits_last_7d == 1`` and one with none reports zeros.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    now = await _db_now(db_session)

    busy_repo = _init(tmp_path, "busy")
    quiet_repo = _init(tmp_path, "quiet")
    other_busy_repo = _init(tmp_path, "other-busy")
    rows = [
        await _register(service, owner, path) for path in (busy_repo, quiet_repo, other_busy_repo)
    ]
    _commit(busy_repo, "First", when=now - timedelta(days=4))
    _commit(other_busy_repo, "First", when=now - timedelta(days=9))
    for row in rows:
        await service.scan_repository(owner=owner, repository_id=row.id)

    vector = await service.features(owner=owner, window_days=WINDOW)

    by_id = {row.repository_id: row for row in vector.repositories}
    assert set(by_id) == {row.id for row in rows}
    assert by_id[rows[0].id].commits_last_7d == 1
    assert by_id[rows[2].id].commits_last_7d == 0, "nine days ago is outside the 7-day column"
    assert by_id[rows[2].id].commits_last_30d == 1
    assert by_id[rows[1].id].commits_last_7d == 0
    assert vector.features.commits_last_30d == 2


# ---------------------------------------------------------------------------
# Query counts that must not grow with the repository count
# ---------------------------------------------------------------------------


async def _account_with_repositories(
    session: AsyncSession, tmp_path: Path, *, count: int, username: str
) -> tuple[DeveloperIntelligenceService, User, uuid.UUID, list[uuid.UUID]]:
    """Register ``count`` scanned repositories for one account, with commits.

    Each repository gets two commits: one three days ago, which is inside the
    thirty-day window and puts the repository into the branch-growth scope, and
    one 45 days ago, which is outside it. The second commit is what makes the
    fixture's window arithmetic meaningful — with both inside the window,
    ``repository_growth`` would report every repository's ``main`` as new and the
    metric's value would stop distinguishing anything.

    Args:
        session: The test session.
        tmp_path: The directory the work trees are created under.
        count: How many repositories to register and scan.
        username: The account's username, so two scenarios in one test do not
            collide.

    Returns:
        ``(service, owner, project id, repository ids)``.
    """
    owner = await _owner(session, username)
    service = _service(session)
    seed = AnalyticsSeed(session, owner)
    project = await seed.project(name=f"{username}-project")
    now = await _db_now(session)

    identifiers: list[uuid.UUID] = []
    for index in range(count):
        repo = _init(tmp_path / username, f"repo-{index}")
        _commit(repo, f"Recent {index}", when=now - timedelta(days=3))
        _commit(repo, f"Older {index}", when=now - timedelta(days=45))
        row = await service.register_repository(
            owner=owner, local_path=str(repo), project_id=project.id
        )
        await service.scan_repository(owner=owner, repository_id=row.id)
        identifiers.append(row.id)
    return service, owner, project.id, identifiers


async def test_the_project_view_costs_the_same_at_one_and_at_five_repositories(
    db_session: AsyncSession, engine, tmp_path: Path
) -> None:
    """The project view reads every repository's history in one grouped statement.

    Two accounts, one project each: Ada with a single repository, Grace with
    five, both scanned and both carrying the same two commits apiece. The
    statement count is recorded through a ``before_cursor_execute`` listener for
    each call and must be **equal**, and exactly one of those statements may
    aggregate ``git_commits`` in a group — the single read that replaced the
    per-repository ``commit_totals`` loop.

    Expected figures: a project page issues the same statements either way. The
    five-repository case used to issue five more — one aggregate per repository,
    which is a project with fifteen repositories costing sixteen round trips to
    render. The answer is unchanged either way, so only the count distinguishes
    the two implementations, and the returned ``commit_count`` is asserted
    against the fixture to prove the grouped read still sums correctly.
    """
    now = await _db_now(db_session)
    one_service, one_owner, one_project, one_ids = await _account_with_repositories(
        db_session, tmp_path, count=1, username="one"
    )
    five_service, five_owner, five_project, five_ids = await _account_with_repositories(
        db_session, tmp_path, count=5, username="five"
    )

    one_statements, one_view = await _statements_during(
        engine, lambda: one_service.project_view(owner=one_owner, project_id=one_project)
    )
    five_statements, five_view = await _statements_during(
        engine, lambda: five_service.project_view(owner=five_owner, project_id=five_project)
    )

    assert one_view.commit_count == 2, "two commits in the single repository"
    assert five_view.commit_count == 10, "two commits in each of the five repositories"
    assert len(one_view.repositories) == 1
    assert len(five_view.repositories) == 5
    assert len(one_statements) == len(five_statements), (
        f"one repository cost {len(one_statements)} statements and five cost "
        f"{len(five_statements)}: {five_statements}"
    )
    assert _grouped_commit_statements(five_statements) == 1, (
        "one grouped aggregate for the whole project, not one per repository"
    )
    assert _grouped_commit_statements(one_statements) == 1
    # Both scenarios place their newest commit three days before the clock they
    # read, and the two scenarios read that clock a second apart, so the maxima
    # are compared as a range rather than for equality.
    for view in (one_view, five_view):
        age = abs((view.latest_commit_at - (now - timedelta(days=3))).total_seconds())
        assert age < 60, view.latest_commit_at
    assert set(one_ids) == {row.id for row in one_view.repositories}
    assert set(five_ids) == {row.id for row in five_view.repositories}


async def test_the_metrics_read_costs_the_same_at_one_and_at_five_repositories(
    db_session: AsyncSession, engine, tmp_path: Path
) -> None:
    """Branch growth is resolved for every repository in one statement, not N.

    The same two scenarios, measured on ``metrics()``. Branch first-seen is
    grouped ``(repository_id, branch)`` in the database and folded in memory,
    where it used to be one query per repository touched in the window — with a
    ceiling that tested the number of *names* found rather than the number of
    repositories visited, so it never bounded anything at all.

    Expected figures: the statement count is equal at one and at five
    repositories, and exactly one statement aggregates ``git_commits``. The
    metric's own figures are asserted as well, because a fix that made the read
    cheap by grouping ``main`` across repositories would return a different set
    of branch names — the case that actually distinguishes the two is the next
    test, where one repository's ``main`` really is new.
    """
    one_service, one_owner, _one_project, _one_ids = await _account_with_repositories(
        db_session, tmp_path, count=1, username="solo"
    )
    five_service, five_owner, _five_project, _five_ids = await _account_with_repositories(
        db_session, tmp_path, count=5, username="crowd"
    )

    one_statements, one_metrics = await _statements_during(
        engine, lambda: one_service.metrics(owner=one_owner, window_days=WINDOW)
    )
    five_statements, five_metrics = await _statements_during(
        engine, lambda: five_service.metrics(owner=five_owner, window_days=WINDOW)
    )

    one_by_key = {metric.key: metric for metric in one_metrics}
    five_by_key = {metric.key: metric for metric in five_metrics}
    assert one_by_key["commit_activity"].value == 1.0, "one commit inside the window"
    assert five_by_key["commit_activity"].value == 5.0, "one per repository, five repositories"
    assert one_by_key["repository_growth"].value == 0.0, "every main is 45 days old"
    assert five_by_key["repository_growth"].value == 0.0, (
        "five repositories, five mains, none of them new"
    )

    assert len(one_statements) == len(five_statements), (
        f"one repository cost {len(one_statements)} statements and five cost "
        f"{len(five_statements)}: {five_statements}"
    )
    assert _grouped_commit_statements(five_statements) == 1, (
        "one grouped branch read for every repository, not one per repository"
    )
    assert _grouped_commit_statements(one_statements) == 1


async def test_a_branch_first_recorded_inside_the_window_is_reported_once_per_repository(
    db_session: AsyncSession, engine, tmp_path: Path
) -> None:
    """Two repositories, two ``main`` branches, one of them new: growth says ``main``.

    The reason the grouping has to keep ``repository_id`` in it. Repository A
    has had ``main`` for 45 days; repository B was created three days ago and
    its ``main`` is therefore first recorded inside the thirty-day window.
    Resolved per repository — which is what the grouped read does — the name is
    growth. Grouped by name alone, ``main`` would carry the older of the two
    minima, sit outside the window, and the metric would report zero for a
    repository that demonstrably started this week.

    Expected figures: one commit in each repository, both inside the window;
    ``repository_growth`` is ``1.0`` for the account and for repository B, and
    ``0.0`` for A. The metric's figures are the behaviour being pinned; the
    statement count is the mechanism, and one grouped read covers both
    repositories.
    """
    owner = await _owner(db_session, "branches")
    service = _service(db_session)
    now = await _db_now(db_session)

    old_repo = _init(tmp_path, "old")
    new_repo = _init(tmp_path, "new")
    _commit(old_repo, "Long ago", when=now - timedelta(days=45))
    _commit(new_repo, "Brand new", when=now - timedelta(days=3))
    old_row = await _register(service, owner, old_repo)
    new_row = await _register(service, owner, new_repo)
    await service.scan_repository(owner=owner, repository_id=old_row.id)
    await service.scan_repository(owner=owner, repository_id=new_row.id)

    statements, metrics = await _statements_during(
        engine, lambda: service.metrics(owner=owner, window_days=WINDOW)
    )

    account = {metric.key: metric for metric in metrics}
    scoped_new = {
        metric.key: metric
        for metric in await service.metrics(
            owner=owner, window_days=WINDOW, repository_id=new_row.id
        )
    }
    scoped_old = {
        metric.key: metric
        for metric in await service.metrics(
            owner=owner, window_days=WINDOW, repository_id=old_row.id
        )
    }

    assert account["repository_growth"].value == 1.0, "one new branch name: main"
    assert scoped_new["repository_growth"].value == 1.0, (
        "the new repository's own main is growth; collapsing main across "
        "repositories would date it by the older repository and report 0.0"
    )
    assert scoped_old["repository_growth"].value == 0.0
    assert _grouped_commit_statements(statements) == 1, "two repositories, one grouped read"


# ---------------------------------------------------------------------------
# The configured allowlist, on the scan as well as at registration
# ---------------------------------------------------------------------------


async def test_a_scan_refuses_a_repository_the_configured_allowlist_excludes(
    db_session: AsyncSession, tmp_path: Path, make_settings
) -> None:
    """A registered path that the allowlist no longer permits is not read.

    The repository is registered while no allowlist is configured, which is the
    default and always permits anything. A second service is then built from a
    deployment whose ``developer_path_allowlist`` names a *different* directory,
    and the scan is issued through that one.

    Expected figures: the scan returns a row with ``status='error'`` carrying the
    engine's sentence about a directory outside the permitted roots, the
    repository's own row records the same failure, and not one commit row was
    written. Registration alone would have let this through, because the path was
    legitimate when it was written — the check has to run against what the stored
    string resolves to on the day it is read back.
    """
    owner = await _owner(db_session, "scanner")
    service = _service(db_session)
    repo = _init(tmp_path, "outside-the-root")
    _commit(repo, "Should never be read", when=await _db_now(db_session))
    registered = await _register(service, owner, repo)

    permitted = tmp_path / "permitted"
    permitted.mkdir()
    restricted = _service(
        db_session,
        settings=make_settings(DEVELOPER_PATH_ALLOWLIST=str(permitted)),
    )

    run = await restricted.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.ERROR
    assert "outside the directories" in (run.error or ""), run.error
    assert "should never be read" not in (run.error or "").lower()
    stored = await db_session.scalar(
        select(GitCommit).where(GitCommit.repository_id == registered.id).limit(1)
    )
    assert stored is None, "a refused scan writes nothing"
    row_status = await db_session.scalar(
        select(GitRepository.last_scan_status).where(GitRepository.id == registered.id)
    )
    assert row_status == GitScanStatus.ERROR.value


async def test_an_allowlist_that_resolves_to_nothing_denies_every_path(
    db_session: AsyncSession, tmp_path: Path, make_settings, monkeypatch
) -> None:
    """A configured allowlist whose entries all failed to parse permits nothing.

    The failure is simulated rather than provoked, for the same reason as in
    ``tests/test_developer_git.py``: an entry the platform can still resolve is
    hard to write portably, and what is under test is the branch that consumes
    the parse result, not the filesystem's. A repository registered before the
    setting existed is then scanned through a service configured with that
    unresolvable allowlist, and a fresh path is offered for registration.

    Expected figures: both are refused — the scan with an ``error`` run carrying
    the same sentence a path outside the roots gets, the registration with a
    ``ValidationError``. An allowlist that silently stopped restricting anything
    would be an operator's lock silently removed by one unusable line in a
    settings file.
    """
    owner = await _owner(db_session, "locked")
    service = _service(db_session)
    repo = _init(tmp_path, "registered-while-open")
    registered = await _register(service, owner, repo)

    original_resolve = Path.resolve

    def exploding_resolve(self: Path, *args, **kwargs):
        if "unresolvable-entry" in str(self):
            raise OSError("this entry could not be resolved")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", exploding_resolve)
    locked = _service(
        db_session,
        settings=make_settings(DEVELOPER_PATH_ALLOWLIST="unresolvable-entry"),
    )

    run = await locked.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.ERROR
    assert "outside the directories" in (run.error or ""), run.error

    fresh = _init(tmp_path, "offered-while-locked")
    with pytest.raises(ValidationError, match="outside the directories"):
        await locked.register_repository(owner=owner, local_path=str(fresh))


async def test_no_allowlist_configured_keeps_every_path_readable(
    db_session: AsyncSession, tmp_path: Path, make_settings
) -> None:
    """The fix is that the list is honoured when there is one, not that scans are restricted.

    A deployment with ``developer_path_allowlist`` left blank — the documented
    default for a local-first application running on the user's own machine —
    registers and scans a work tree under a directory it was never told about.
    The control matters in both directions: a fix that denied everything by
    default would pass the refusal tests above and break every existing
    installation.
    """
    owner = await _owner(db_session, "unlocked")
    service = _service(db_session, settings=make_settings(DEVELOPER_PATH_ALLOWLIST=""))
    repo = _init(tmp_path, "anywhere")
    _commit(repo, "Read me", when=await _db_now(db_session))

    registered = await _register(service, owner, repo)
    run = await service.scan_repository(owner=owner, repository_id=registered.id)

    assert run.status == GitScanStatus.OK, run.error
    assert run.commits_added == 1
    vector = await service.features(owner=owner, window_days=WINDOW)
    assert [row.repository_id for row in vector.repositories] == [registered.id]
    counts = await db_session.scalar(
        select(func.count(GitCommit.id)).where(GitCommit.repository_id == registered.id)
    )
    assert counts == 1
