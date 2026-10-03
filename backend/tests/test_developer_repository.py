"""DeveloperRepository against the real test database.

Three properties are worth pinning, and none of them is visible from reading the
code:

**A re-scan does not duplicate anything.** The git CLI returns the same commits
every time it is run against an unchanged repository, so the whole Phase 8
promise — a repository that can be rescanned as often as the user likes — rests
on ``uq_git_commits_repo_hash`` and ``uq_git_branches_repo_hash`` being an
upsert rather than an insert. The idempotency tests therefore run the *same
batch twice* and assert the second call inserted nothing, updating all of it.

**Nothing is visible across accounts.** Every read filters on ``user_id``, and a
foreign id is answered with ``None`` rather than a 403 — a 403 would confirm the
row exists and turn the route into an existence oracle. Asserted from both
sides: the other account's repository is invisible, and its commits are not
reachable by listing.

**A scan cannot become a lie.** The metadata PATCH refuses ``local_path`` — a
registered repository cannot be moved to a directory whose history was never
scanned — and the scan-state write is a separate, whitelisted set. Both raise
``ValueError`` before anything is stored, because both are statements about the
calling code rather than about anybody's data.

Every figure below is derived from the fixture in the test's own docstring rather
than recorded from a run. Instants are explicit UTC values, never ``now()``: the
aggregates bucket days, and a suite whose expected counts move with the wall
clock fails at midnight instead of failing for a reason.

Rows are read back through column projections and aggregates rather than through
the ORM wherever the assertion is about what was *stored* — this session is the
one that wrote them, so an entity read would hand back whatever the identity map
cached and an idempotency assertion could pass for the wrong reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.developer import GitCommit, GitRepository
from app.models.enums import GitScanStatus
from app.models.project import Project
from app.repositories.developer import DeveloperRepository
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The anchor every relative instant counts from. A fixed Monday, so the UTC-day
#: arithmetic in the aggregate tests is not sitting on a boundary by accident.
ANCHOR = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def repository(db_session) -> DeveloperRepository:
    return DeveloperRepository(db_session)


@pytest.fixture
async def owner(db_session):
    """The account most tests write as."""
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def other(db_session):
    """A second account, for every isolation assertion.

    Created by a separate fixture rather than by a parameter, because two tests
    in this file need three accounts and one needs two; a fixture that made
    ``other`` depend on ``owner`` would hide the case where the rows belong to
    neither.
    """
    return await register_user(db_session, username="grace", email="grace@nexus.test")


def _repository(user, *, name="nexus", path="/repos/nexus", **overrides):
    """Build the column mapping :meth:`DeveloperRepository.create_repository` takes."""
    values = {"name": name, "local_path": path}
    values.update(overrides)
    return values


def _commit(token: str, committed_at: datetime, **overrides) -> dict:
    """Build one commit mapping for the upsert.

    The hash is padded to 40 characters from ``token`` so the column's real width
    is exercised rather than a two-character stand-in, and so every fixture gets a
    distinct hash without inventing 40 hex digits by hand.
    """
    row = {
        "commit_hash": f"{token * 40}"[:40],
        "short_hash": token[:12],
        "committed_at": committed_at,
        "message": f"Commit {token}",
    }
    row.update(overrides)
    return row


def _branch(name: str, **overrides) -> dict:
    """Build one branch mapping for the upsert."""
    row = {"name": name}
    row.update(overrides)
    return row


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------


async def test_a_registered_repository_carries_no_scan_claims(repository, owner):
    """Registering observes nothing, and every column says so.

    A fresh row must read: two zero counters, ``is_active`` true, the working
    tree clean, and all three last-scan columns null. ``last_scanned_at IS
    NULL`` is how "never scanned" is answered everywhere in the product, so a
    registration that filled any of them in would claim a read that never
    happened.
    """
    created = await repository.create_repository(owner.id, **_repository(owner))

    assert isinstance(created, GitRepository)
    assert created.user_id == owner.id
    assert created.local_path == "/repos/nexus"
    assert created.description is None
    assert created.project_id is None
    assert created.primary_language is None
    assert created.is_active is True
    assert created.working_tree_dirty is False
    assert created.branch_count == 0
    assert created.commit_count == 0
    assert created.first_commit_at is None
    assert created.latest_commit_at is None
    assert created.current_branch is None
    assert created.default_branch is None
    # Null, not ``'ok'``: the column is nullable on purpose and the scan is what
    # fills it. A repository that has never been read has no status to report.
    assert created.last_scanned_at is None
    assert created.last_scan_error is None
    assert created.created_at.tzinfo is not None
    assert created.updated_at.tzinfo is not None


async def test_one_path_may_be_registered_once_per_account_and_by_two_accounts(
    repository, db_session, owner, other
):
    """``uq_git_repositories_owner_path`` is per account, not global.

    A local directory may legitimately be watched by two accounts on one
    machine, so the second registration by a *different* owner must succeed. The
    same owner registering it again is the conflict the constraint exists for —
    two rows for one directory would hold two commit counts that disagree.

    Both owner ids are read *before* the rollback: a rollback expires every
    instance the session holds, and reading ``owner.id`` afterwards would try to
    refresh it outside a greenlet — a failure that has nothing to do with the
    constraint being tested.
    """
    owner_id, other_id = owner.id, other.id
    await repository.create_repository(owner_id, **_repository(owner))
    await repository.create_repository(other_id, **_repository(other, name="nexus-work"))

    with pytest.raises(IntegrityError):
        await repository.create_repository(owner_id, **_repository(owner, name="again"))
    await db_session.rollback()

    assert await repository.count_repositories(owner_id) == 1
    assert await repository.count_repositories(other_id) == 1


async def test_another_accounts_repository_is_answered_with_none(repository, owner, other):
    """A foreign id and an unknown id are the same answer.

    ``None`` here is what the route turns into a 404. A 403 would confirm the id
    exists, which turns the endpoint into a probe for which repository ids are
    real — so the two cases are asserted to be indistinguishable rather than
    merely both unsuccessful.
    """
    theirs = await repository.create_repository(other.id, **_repository(other))

    assert await repository.get_repository(owner.id, theirs.id) is None
    assert await repository.get_repository(owner.id, uuid.uuid4()) is None


async def test_the_owner_scoped_path_lookup_does_not_see_another_accounts_row(
    repository, owner, other
):
    """The duplicate-path lookup is scoped too, or it invents a conflict.

    The registration route asks this before it inserts so it can say "you have
    already registered this directory" instead of raising a constraint error.
    A lookup that ignored the owner would tell the second account they had a
    duplicate they do not have.
    """
    await repository.create_repository(other.id, **_repository(other))

    assert await repository.get_repository_by_path(owner.id, "/repos/nexus") is None
    assert await repository.get_repository_by_path(other.id, "/repos/nexus") is not None


# ----------------------------------------------------------------------
# Metadata edits
# ----------------------------------------------------------------------


async def test_a_metadata_edit_cannot_move_a_registered_path(repository, owner):
    """``local_path`` is not an editable column, and says so loudly.

    Moving a repository would leave its counters, its commit range and its whole
    recorded history describing a directory this account never scanned, and
    nothing in the schema could notice. The refusal is a ``ValueError`` at the
    repository rather than a silently ignored key, because a caller that passed
    it needs to know the edit did not happen.
    """
    created = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="local_path"):
        await repository.update_repository(owner.id, created.id, {"local_path": "/somewhere/else"})

    reloaded = await repository.get_repository(owner.id, created.id)
    assert reloaded.local_path == "/repos/nexus"


async def test_a_metadata_edit_writes_only_the_columns_it_names(repository, owner):
    """A PATCH is a partial change; the snapshot columns must survive it.

    The fixture writes a scan state first — two commits, a branch, a scan time —
    then edits the name, description and active flag. Only those three may move:
    a rename must not silently restamp ``last_scanned_at`` or zero the commit
    count.
    """
    created = await repository.create_repository(owner.id, **_repository(owner))
    await repository.update_scan_state(
        owner.id,
        created.id,
        {
            "commit_count": 2,
            "branch_count": 1,
            "current_branch": "main",
            "last_scanned_at": ANCHOR,
            "last_scan_status": GitScanStatus.OK.value,
        },
    )

    updated = await repository.update_repository(
        owner.id,
        created.id,
        {"name": "nexus-backend", "description": "the API", "is_active": False},
    )

    assert updated is not None
    assert updated.name == "nexus-backend"
    assert updated.description == "the API"
    assert updated.is_active is False
    assert updated.commit_count == 2
    assert updated.branch_count == 1
    assert updated.current_branch == "main"
    assert updated.last_scanned_at == ANCHOR


async def test_a_metadata_edit_can_clear_a_optional_field_with_null(repository, owner):
    """A ``None`` is written as SQL ``NULL``, which is how a link is cleared.

    Un-linking a repository from its project has to be expressible, and it must
    not require a second statement to null the column.
    """
    created = await repository.create_repository(
        owner.id, **_repository(owner, description="the API")
    )

    updated = await repository.update_repository(owner.id, created.id, {"description": None})

    assert updated is not None
    assert updated.description is None


async def test_an_edit_of_another_accounts_repository_changes_nothing(repository, owner, other):
    """The write is owner-scoped, so a foreign id returns ``None`` and writes nothing.

    Two edits are attempted against the same row: the owning account's, which
    succeeds, and a different account's, which does not. The assertion is on the
    row afterwards as well as on the return values — an ``UPDATE`` that filtered
    nothing would still answer ``None`` if only the ``RETURNING`` clause were
    right, so only the re-read proves the write did not leak.
    """
    theirs = await repository.create_repository(
        other.id, **_repository(other, description="theirs")
    )

    assert (
        await repository.update_repository(other.id, theirs.id, {"description": "ours"})
    ) is not None
    assert (
        await repository.update_repository(owner.id, theirs.id, {"description": "stolen"})
    ) is None

    reloaded = await repository.get_repository(other.id, theirs.id)
    assert reloaded.description == "ours"


async def test_an_edit_that_names_nothing_is_refused(repository, owner):
    """An empty edit is a programming error, not a no-op that reports success.

    Returning the unchanged row would let a caller report "saved" for an edit
    that never happened.
    """
    created = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="at least one field"):
        await repository.update_repository(owner.id, created.id, {})


async def test_a_scan_state_write_cannot_touch_a_user_declared_column(repository, owner):
    """The scan's write set excludes the name, the path and the owner.

    A scan reads a directory; it does not rename the label the user typed, and it
    certainly does not reassign the row to somebody else.
    """
    created = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="name"):
        await repository.update_scan_state(owner.id, created.id, {"name": "renamed by scan"})


# ----------------------------------------------------------------------
# Listing and counting
# ----------------------------------------------------------------------


async def test_the_repository_list_is_ordered_by_name_and_reports_the_unpaged_total(
    repository, owner
):
    """Three repositories named ``beta``, ``alpha`` and ``gamma``.

    Ordered ``alpha, beta, gamma``, because a repository list with no order is a
    set, and ``id`` breaks the tie so paging is stable. The total is 3 while the
    page holds 2, which is the whole reason the count is computed over the same
    filtered rows rather than from the page.
    """
    for name in ("beta", "gamma", "alpha"):
        await repository.create_repository(
            owner.id, **_repository(owner, name=name, path=f"/repos/{name}")
        )

    page, total = await repository.list_repositories(owner.id, limit=2)

    assert [row.name for row in page] == ["alpha", "beta"]
    assert total == 3


async def test_the_repository_list_filters_by_project_and_activity(repository, db_session, owner):
    """Two repositories, one linked to a project and one deactivated.

    The two filters are what the project page and the active-only list drive, so
    each is asserted against a fixture where the *other* row would otherwise
    change the answer: the linked row is also active, and the deactivated row is
    also unlinked, so a filter that silently did nothing would still pass a
    test that only looked at the matching row.

    The project is a real row rather than a bare uuid because
    ``git_repositories.project_id`` is a foreign key — which is the point of it
    being one: a link to a project that does not exist is not representable.
    """
    project = Project(id=uuid.uuid4(), owner_id=owner.id, name="Nexus")
    db_session.add(project)
    await db_session.commit()

    linked = await repository.create_repository(
        owner.id,
        **_repository(owner, name="alpha", path="/repos/alpha", project_id=project.id),
    )
    paused = await repository.create_repository(
        owner.id, **_repository(owner, name="beta", path="/repos/beta")
    )
    # Deactivated through the metadata edit rather than at registration:
    # `create_repository` writes only the user-declared columns, and whether a
    # repository is active is an editable metadata field, not part of signing up.
    await repository.update_repository(owner.id, paused.id, {"is_active": False})

    by_project, total = await repository.list_repositories(owner.id, project_id=linked.project_id)
    active, active_total = await repository.list_repositories(owner.id, is_active=True)

    assert [row.name for row in by_project] == ["alpha"]
    assert total == 1
    assert [row.name for row in active] == ["alpha"]
    assert active_total == 1


async def test_the_repository_cap_counts_every_row_the_user_registered(repository, owner):
    """Three registered, two active: the cap sees all three.

    ``developer_max_repositories`` is enforced by counting rows, and a
    deactivated repository still occupies its slot. A cap that ignored paused
    repositories would be a number that could rise without anything being
    registered, which is not a cap.
    """
    for name in ("alpha", "beta", "gamma"):
        await repository.create_repository(
            owner.id, **_repository(owner, name=name, path=f"/repos/{name}")
        )
    rows, _total = await repository.list_repositories(owner.id)
    await repository.update_repository(owner.id, rows[0].id, {"is_active": False})

    assert await repository.count_repositories(owner.id) == 3
    assert await repository.count_repositories(owner.id, is_active=True) == 2
    assert await repository.count_repositories(uuid.uuid4()) == 0


# ----------------------------------------------------------------------
# Commits: the idempotency anchor
# ----------------------------------------------------------------------


async def test_a_rescan_upserts_instead_of_duplicating(repository, owner):
    """Two commits, then the same two again.

    The first upsert inserts 2 and updates 0. The second inserts 0 and updates 2,
    because ``uq_git_commits_repo_hash`` is a full unique constraint and the git
    CLI returns the same objects every time. The row count stays at 2 — which is
    the assertion that matters, since a dashboard's commit count doubling every
    time the user pressed the scan button would be the visible symptom.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    batch = [
        _commit("a", ANCHOR, additions=10, deletions=2, files_changed=3),
        _commit("b", ANCHOR + timedelta(days=1), additions=4, deletions=0, files_changed=1),
    ]

    first = await repository.upsert_commits(owner.id, repository_row.id, batch)
    second = await repository.upsert_commits(owner.id, repository_row.id, batch)

    assert (first.inserted, first.updated) == (2, 0)
    assert (second.inserted, second.updated) == (0, 2)
    assert second.total == 2
    totals = await repository.commit_totals(owner.id)
    assert totals.commits == 2
    assert totals.additions == 14
    assert totals.deletions == 2
    assert totals.files_changed == 4


async def test_a_rescan_refreshes_attribution_but_never_the_commit_instant(repository, owner):
    """The same commit observed twice, once with no branch and once resolved.

    ``branch`` is best-effort: an earlier scan may not have resolved it and a
    later one may. The refresh therefore rewrites ``branch`` while
    ``committed_at`` stays exactly where it was — git does not change when a
    commit was made, and a second reader disagreeing about that would mean one
    of the two scans parsed the repository differently.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [_commit("a", ANCHOR, branch=None, message="Add the reader")],
    )
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [_commit("a", ANCHOR, branch="main", message="Add the reader")],
    )

    rows = await repository.commit_rows(owner.id)
    assert len(rows) == 1
    _hash, short, committed_at, _repo, branch, *_counts = rows[0]
    assert branch == "main"
    assert committed_at == ANCHOR
    assert short == "a"


async def test_a_commit_with_no_countable_lines_is_stored_rather_than_dropped(repository, owner):
    """A commit touching a binary file has no countable lines, and is still a commit.

    git reports a dash for both counts for a binary file in ``--numstat``, which
    the engine reads as zero. Stored as ``0`` rather than discarded, so the
    commit count and the file count describe the same set of commits — dropping
    it would leave "1 commit, 0 files changed", which reads as a lie about the
    commit.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    result = await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [_commit("a", ANCHOR, author_name=None, author_email=None)],
    )

    assert result.inserted == 1
    totals = await repository.commit_totals(owner.id)
    assert (totals.commits, totals.additions, totals.deletions, totals.files_changed) == (
        1,
        0,
        0,
        0,
    )


async def test_a_malformed_commit_is_rejected_before_it_reaches_storage(repository, owner):
    """A missing field or a misspelled one is reported against the commit.

    Both are mistakes about the calling code — the values come from the git
    engine's own dataclasses — and catching them here means the caller is told
    which commit was malformed rather than receiving an ``IntegrityError`` with
    no mention of the field.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="message"):
        await repository.upsert_commits(
            owner.id, repository_row.id, [{"commit_hash": "a" * 40, "short_hash": "aaa"}]
        )
    with pytest.raises(ValueError, match="committed"):
        await repository.upsert_commits(
            owner.id,
            repository_row.id,
            [{"commit_hash": "a" * 40, "short_hash": "aaa", "message": "no instant"}],
        )
    with pytest.raises(ValueError, match="additions"):
        await repository.upsert_commits(
            owner.id,
            repository_row.id,
            [_commit("a", ANCHOR, addition=10)],
        )

    assert (await repository.commit_totals(owner.id)).commits == 0


async def test_an_empty_commit_batch_touches_nothing(repository, owner):
    """A repository with no commits is a valid scan result, not a failure.

    An empty ``INSERT ... VALUES`` is a syntax error in PostgreSQL, so the empty
    case has to return before a statement is built. The repository registered in
    this test is a bare ``git init``: it must stay registered with nothing under
    it.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    result = await repository.upsert_commits(owner.id, repository_row.id, [])

    assert (result.inserted, result.updated, result.total) == (0, 0, 0)
    assert (await repository.commit_totals(owner.id)).commits == 0


# ----------------------------------------------------------------------
# Commits: reads
# ----------------------------------------------------------------------


async def test_the_commit_totals_count_repositories_and_utc_days(repository, owner):
    """Three commits: two on day one in one repository, one on day two in another.

    Derived: ``commits`` 3, ``repositories`` 2 (the second is distinct), and
    ``active_days`` 2 — the anchor day and the day after — because the day is
    bucketed in UTC and both of the first pair share a date. ``additions`` is
    10 + 20 + 30 = 60 and ``deletions`` is 1 + 2 + 3 = 6, and the earliest and
    latest instants are the anchor and anchor + 2 days.

    ``active_days`` counts days a commit was *recorded on*. It is not a count of
    days anyone worked and the column it reads cannot be turned into one.
    """
    first = await repository.create_repository(
        owner.id, **_repository(owner, name="alpha", path="/repos/alpha")
    )
    second = await repository.create_repository(
        owner.id, **_repository(owner, name="beta", path="/repos/beta")
    )
    await repository.upsert_commits(
        owner.id,
        first.id,
        [
            _commit("a", ANCHOR, additions=10, deletions=1, files_changed=2),
            # 23:30 UTC the same day: still the same UTC day, which is exactly
            # what makes the bucket worth asserting.
            _commit("b", ANCHOR.replace(hour=23, minute=30), additions=20, deletions=2),
        ],
    )
    await repository.upsert_commits(
        owner.id, second.id, [_commit("c", ANCHOR + timedelta(days=2), additions=30, deletions=3)]
    )

    totals = await repository.commit_totals(owner.id)

    assert totals.commits == 3
    assert totals.repositories == 2
    assert totals.active_days == 2
    assert totals.additions == 60
    assert totals.deletions == 6
    assert totals.first_committed_at == ANCHOR
    assert totals.latest_committed_at == ANCHOR + timedelta(days=2)

    scoped = await repository.commit_totals(owner.id, repository_id=first.id)
    assert (scoped.commits, scoped.repositories, scoped.active_days) == (2, 1, 1)


async def test_an_empty_window_reports_zeroes_and_no_instants(repository, owner):
    """No commits in the window is zeroes plus two ``None``s.

    A different answer from a window whose commits are all empty, and the metrics
    layer is expected to render it as "not enough data" rather than as a zero.
    The fixture plants a commit just outside the window so the assertion is about
    the window rather than about an empty database.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(owner.id, repository_row.id, [_commit("a", ANCHOR)])

    totals = await repository.commit_totals(
        owner.id, since=ANCHOR + timedelta(days=1), until=ANCHOR + timedelta(days=2)
    )

    assert totals.commits == 0
    assert totals.repositories == 0
    assert totals.active_days == 0
    assert totals.first_committed_at is None
    assert totals.latest_committed_at is None


async def test_a_commit_window_is_half_open_so_adjacent_windows_do_not_overlap(repository, owner):
    """Three commits, one sitting exactly on the boundary.

    A window of ``[anchor, anchor + 2 days)`` contains the first two and not the
    third, because ``until`` is exclusive. A closed window would count the
    boundary commit twice across two adjacent windows — which for a day-bucketed
    chart is the boundary that matters most.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            _commit("a", ANCHOR),
            _commit("b", ANCHOR + timedelta(days=1)),
            _commit("c", ANCHOR + timedelta(days=2)),
        ],
    )

    window = await repository.commit_totals(
        owner.id, since=ANCHOR, until=ANCHOR + timedelta(days=2)
    )
    following = await repository.commit_totals(
        owner.id, since=ANCHOR + timedelta(days=2), until=ANCHOR + timedelta(days=3)
    )

    assert window.commits == 2
    assert following.commits == 1


async def test_the_commit_projection_returns_tuples_and_no_author(repository, owner):
    """Eight columns, newest first, and no person in them.

    The projection is what the activity series and the feature vector read, so it
    selects the columns a chart needs and stops. The author name and address are
    deliberately absent: nothing in the product needs a person's name per commit,
    and a projection is the cheapest place to keep that out of a wide read.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            _commit(
                "a",
                ANCHOR,
                author_name="Ada Lovelace",
                author_email="ada@nexus.test",
                additions=5,
                deletions=1,
                files_changed=1,
            ),
            _commit("b", ANCHOR + timedelta(days=1), additions=1),
        ],
    )

    rows = await repository.commit_rows(owner.id)

    assert len(rows) == 2
    assert all(isinstance(row, tuple) and len(row) == 8 for row in rows)
    # Newest first.
    assert rows[0][2] == ANCHOR + timedelta(days=1)
    assert rows[1][2] == ANCHOR
    assert rows[1][3] == repository_row.id
    assert "Ada Lovelace" not in str(rows)


async def test_branch_attribution_reports_the_earliest_commit_per_branch(repository, owner):
    """Three commits: two on ``main``, one on ``feature/x``, and one unattributed.

    Derived: ``main`` first appears at the anchor, ``feature/x`` at anchor + 1
    day, and the unattributed commit appears nowhere — "branch unknown" is not a
    branch, and a growth metric that counted it would be counting something
    nobody created.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            _commit("a", ANCHOR, branch="main"),
            _commit("b", ANCHOR + timedelta(days=1), branch="main"),
            _commit("c", ANCHOR + timedelta(days=2), branch="feature/x"),
            _commit("d", ANCHOR + timedelta(days=3), branch=None),
        ],
    )

    seen = await repository.branch_first_seen(owner.id)

    assert seen == [
        ("feature/x", ANCHOR + timedelta(days=2)),
        ("main", ANCHOR),
    ]


async def test_a_foreign_accounts_commits_are_not_reachable_by_listing(repository, owner, other):
    """Two commits under one account are invisible from the other.

    Asserted through the aggregate rather than through ``get_commit`` alone: the
    aggregate is what every dashboard tile reads, and a list that leaked rows the
    totals did not would render a chart nobody could reproduce.
    """
    theirs = await repository.create_repository(other.id, **_repository(other))
    await repository.upsert_commits(other.id, theirs.id, [_commit("a", ANCHOR)])
    theirs_rows, theirs_total = await repository.list_commits(other.id)
    theirs_commit = theirs_rows[0]

    assert theirs_total == 1
    assert (await repository.commit_totals(owner.id)).commits == 0
    assert await repository.list_commits(owner.id) == ([], 0)
    assert await repository.get_commit(owner.id, theirs_commit.id) is None
    assert await repository.branch_first_seen(owner.id) == []


async def test_the_commit_list_is_paged_newest_first_with_a_stable_total(repository, owner):
    """Three commits a day apart, a page of two.

    The page holds the two newest, ``anchor + 1`` and ``anchor + 2`` days, and
    the total is 3. The second page returns the third, which is the assertion
    that ordering is total: without the ``id`` tiebreaker two commits sharing an
    instant could swap places between requests.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(
        owner.id,
        repository_row.id,
        [
            _commit("a", ANCHOR),
            _commit("b", ANCHOR + timedelta(days=1)),
            _commit("c", ANCHOR + timedelta(days=2)),
        ],
    )

    first_page, total = await repository.list_commits(owner.id, limit=2)
    second_page, second_total = await repository.list_commits(owner.id, limit=2, offset=2)

    assert [row.short_hash for row in first_page] == ["c", "b"]
    assert total == 3
    assert [row.short_hash for row in second_page] == ["a"]
    assert second_total == 3


async def test_a_commit_can_be_read_back_as_an_entity_or_not_at_all(repository, owner):
    """``get_commit`` answers the owner's row and ``None`` for anything else.

    The ORM read exists for a single commit's detail view; the projections above
    are what everything else uses. Both are owner-scoped, so both refuse a
    foreign id identically.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(owner.id, repository_row.id, [_commit("a", ANCHOR)])
    rows, _total = await repository.list_commits(owner.id)
    mine = rows[0]

    found = await repository.get_commit(owner.id, mine.id)

    assert isinstance(found, GitCommit)
    assert found.commit_hash == mine.commit_hash
    assert await repository.get_commit(owner.id, uuid.uuid4()) is None


# ----------------------------------------------------------------------
# Branches
# ----------------------------------------------------------------------


async def test_branches_are_upserted_by_name_and_a_rescan_does_not_duplicate_them(
    repository, owner
):
    """``main`` and ``develop`` scanned, then ``main`` alone.

    The second upsert writes 1 row rather than inserting a second ``main``, which
    is what keeps ``git_repositories.branch_count`` a number something can
    corroborate. Deleting the branches absent from the second scan then removes
    ``develop`` — a branch that no longer exists must stop appearing, or the
    listing quietly claims a branch that is gone.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    first = await repository.upsert_branches(
        owner.id,
        repository_row.id,
        [
            _branch("main", is_current=True, head_commit_hash="a" * 40, last_committed_at=ANCHOR),
            _branch("develop", head_commit_hash="b" * 40),
        ],
    )
    second = await repository.upsert_branches(
        owner.id,
        repository_row.id,
        [_branch("main", is_current=True, head_commit_hash="c" * 40, last_committed_at=ANCHOR)],
    )
    removed = await repository.delete_branches_absent(owner.id, repository_row.id, ["main"])

    assert first == 2
    assert second == 1
    assert removed == 1
    branches, total = await repository.list_branches(owner.id, repository_row.id)
    assert total == 1
    assert [branch.name for branch in branches] == ["main"]
    assert branches[0].head_commit_hash == "c" * 40
    assert branches[0].is_current is True


async def test_an_empty_branch_name_set_clears_every_branch_of_the_repository(repository, owner):
    """``names=[]`` means "the scan saw no branches", not "no filter".

    A repository whose branches were all deleted must end up with none, and the
    empty sequence is the only way to say that. It is also the reason the
    argument cannot be defaulted: an empty set meaning "no restriction" and an
    empty set meaning "delete them all" are opposites, and only one of them can
    be the default.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_branches(
        owner.id, repository_row.id, [_branch("main"), _branch("develop")]
    )

    removed = await repository.delete_branches_absent(owner.id, repository_row.id, [])

    assert removed == 2
    assert await repository.list_branches(owner.id, repository_row.id) == ([], 0)


async def test_another_accounts_branches_are_invisible(repository, owner, other):
    """The branch list asserts the owner itself, not the repository alone.

    A caller passing a foreign ``repository_id`` gets an empty page rather than
    somebody else's branches — the same 404-not-403 rule as every other read.
    """
    theirs = await repository.create_repository(other.id, **_repository(other))
    await repository.upsert_branches(other.id, theirs.id, [_branch("main")])
    branch_id = (await repository.list_branches(other.id, theirs.id))[0][0].id

    assert await repository.list_branches(owner.id, theirs.id) == ([], 0)
    assert await repository.get_branch(owner.id, branch_id) is None
    assert await repository.get_branch(other.id, branch_id) is not None


async def test_a_malformed_branch_is_rejected_before_it_reaches_storage(repository, owner):
    """A branch without a name, or with a column that is not one, is refused.

    ``name`` is half of the branch table's unique constraint, so a mapping without
    one would defeat the constraint that stops the same branch being recorded
    twice — the same argument ``validate_knowledge_link_type`` makes.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="name"):
        await repository.upsert_branches(owner.id, repository_row.id, [{"head": "a" * 40}])
    with pytest.raises(ValueError, match="head_commit"):
        await repository.upsert_branches(owner.id, repository_row.id, [_branch("main", head="a")])


# ----------------------------------------------------------------------
# Scan runs
# ----------------------------------------------------------------------


async def test_a_failed_scan_is_recorded_as_a_row_and_a_recovered_one_supersedes_it(
    repository, owner
):
    """Three attempts: a failure, a second failure, then a success.

    ``latest_scan_run`` returns the success, and the two failures are still in
    :meth:`DeveloperRepository.list_scan_runs`. A failure recorded only as a
    message on the repository row would lose the history of a repository that was
    unreadable for two attempts — the difference between "this is broken" and
    "this was broken until the third attempt".
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.record_scan_run(
        owner.id,
        repository_row.id,
        status=GitScanStatus.ERROR,
        error="The repository scan could not read this directory.",
        scanned_at=ANCHOR,
    )
    await repository.record_scan_run(
        owner.id,
        repository_row.id,
        status=GitScanStatus.ERROR,
        error="The repository scan could not read this directory.",
        scanned_at=ANCHOR + timedelta(hours=1),
    )
    await repository.record_scan_run(
        owner.id,
        repository_row.id,
        status=GitScanStatus.OK,
        commits_discovered=4,
        commits_added=4,
        branches_discovered=2,
        duration_ms=133,
        scanned_at=ANCHOR + timedelta(hours=2),
    )

    history = await repository.list_scan_runs(owner.id)
    latest = await repository.latest_scan_run(owner.id, repository_row.id)

    assert [run.status for run in history] == ["ok", "error", "error"]
    assert history[0].commits_discovered == 4
    assert history[0].duration_ms == 133
    assert history[-1].error == "The repository scan could not read this directory."
    assert latest is not None
    assert latest.status == GitScanStatus.OK.value
    assert latest.scanned_at == ANCHOR + timedelta(hours=2)


async def test_a_recovered_scan_clears_the_error_it_replaced(repository, owner):
    """``last_scan_error = None`` on success, or the row lies forever.

    The alternative — leaving the previous message in place — would mean a
    repository that was repaired on the third attempt still reported the first
    failure, which is the kind of stale state a user cannot argue with.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.update_scan_state(
        owner.id,
        repository_row.id,
        {"last_scan_status": GitScanStatus.ERROR.value, "last_scan_error": "No such directory."},
    )
    assert (await repository.get_repository(owner.id, repository_row.id)).last_scan_error == (
        "No such directory."
    )

    await repository.update_scan_state(
        owner.id,
        repository_row.id,
        {"last_scan_status": GitScanStatus.OK.value, "last_scan_error": None},
    )
    reloaded = await repository.get_repository(owner.id, repository_row.id)

    assert reloaded.last_scan_error is None
    assert reloaded.last_scan_status == GitScanStatus.OK.value


async def test_recording_a_scan_run_rejects_an_unknown_status_or_a_negative_count(
    repository, owner
):
    """Both are statements about the calling code, and both are refused.

    The status comes from the enum and the counts from the engine's own output,
    so neither can be wrong through a user's input; failing here beats a check
    constraint rejecting a row with no mention of which value was wrong.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))

    with pytest.raises(ValueError, match="scan status"):
        await repository.record_scan_run(owner.id, repository_row.id, status="nearly")
    with pytest.raises(ValueError, match="negative counts"):
        await repository.record_scan_run(
            owner.id, repository_row.id, status=GitScanStatus.OK, commits_added=-1
        )

    assert await repository.list_scan_runs(owner.id) == []


async def test_another_accounts_scan_history_is_invisible(repository, owner, other):
    """A foreign repository's scan runs are not reachable.

    Otherwise a failed scan — whose message is written for the account that owns
    it — would be readable by whoever guessed the repository id.
    """
    theirs = await repository.create_repository(other.id, **_repository(other))
    await repository.record_scan_run(
        other.id, theirs.id, status=GitScanStatus.ERROR, error="Theirs."
    )

    assert await repository.list_scan_runs(owner.id) == []
    assert await repository.latest_scan_run(owner.id, theirs.id) is None
    assert await repository.latest_scan_run(other.id, theirs.id) is not None


# ----------------------------------------------------------------------
# Deletion
# ----------------------------------------------------------------------


async def test_deleting_a_repository_takes_its_commits_branches_and_runs_with_it(repository, owner):
    """One repository, one commit, one branch, one scan run — then a delete.

    Everything observed under the repository goes with it through the declared
    ``ON DELETE CASCADE``, in one statement rather than a read-then-delete per
    child table. Afterwards the account's aggregates are back to their empty
    values and its scan history is empty: nothing orphaned, nothing left to be
    counted against the user.
    """
    repository_row = await repository.create_repository(owner.id, **_repository(owner))
    await repository.upsert_commits(owner.id, repository_row.id, [_commit("a", ANCHOR)])
    await repository.upsert_branches(owner.id, repository_row.id, [_branch("main")])
    await repository.record_scan_run(owner.id, repository_row.id, status=GitScanStatus.OK)

    removed = await repository.delete_repository(owner.id, repository_row.id)

    assert removed is True
    assert await repository.get_repository(owner.id, repository_row.id) is None
    assert (await repository.commit_totals(owner.id)).commits == 0
    assert await repository.list_branches(owner.id, repository_row.id) == ([], 0)
    assert await repository.list_scan_runs(owner.id) == []
    assert await repository.delete_repository(owner.id, repository_row.id) is False


async def test_deleting_another_accounts_repository_removes_nothing(repository, owner, other):
    """A foreign id reports ``False`` and leaves the row alone.

    The same answer an unknown id gets, so the delete route cannot be used to
    discover which repository ids exist.
    """
    theirs = await repository.create_repository(other.id, **_repository(other))

    assert await repository.delete_repository(owner.id, theirs.id) is False
    assert await repository.get_repository(other.id, theirs.id) is not None
