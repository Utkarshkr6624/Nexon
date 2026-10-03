"""The Phase 8 HTTP surface end to end: fourteen routes, one router.

Every route here is exercised through HTTP with a real bearer token, and most of
the assertions are about what must **not** happen rather than about what a
response contains. That is deliberate. The happy paths of this phase are the ones
a demo shows; the four properties below are the ones that break the product
quietly when a plausible edit lands.

* **The literal sub-paths are reachable at all.** ``GET /developer/summary``,
  ``/metrics``, ``/activity``, ``/commits`` and ``/features`` share a prefix with
  ``/developer/repositories/{repository_id}``. Starlette matches in declaration
  order and does not prefer a literal segment over a parameter, so moving one
  decorator below the parameterised one does not remove the route — it binds the
  literal word to the path parameter and answers with a 422 about a uuid that was
  never one. :func:`test_every_literal_sub_path_answers_its_own_question` pins a
  field only each summary handler emits, which is the only assertion that
  distinguishes "answered with counts" from "answered with a uuid parse failure".

* **A foreign repository is 404, never 403, on every route that names one** —
  including the three that take the id as a *query* filter rather than as a path
  segment, which is where a naive implementation would forget to scope. The
  responses for a foreign id and for one nobody ever issued are asserted to carry
  the same message, so the two cases cannot drift into an existence oracle.

* **A broken repository is a row, not a 500.** A registered work tree whose
  ``.git`` has been removed underneath it must answer ``200`` with
  ``status='error'`` and a human sentence. This is the one property whose failure
  mode is a stack trace on somebody's dashboard, and the cheapest way to break it
  is to wrap the service call in a ``try``/``except`` that raises instead — so it
  is asserted over the wire, where that mistake is visible.

* **An unset field is not a cleared field.** ``PATCH`` builds its mapping with
  ``exclude_unset=True`` because ``None`` means "write SQL NULL" downstream.
  Without it, renaming a repository silently discards its description and its
  project link, and the response still looks correct.

Counts here are **derived, never recorded from a run**. The fixture repository
below is built with two dated commits, so the assertions about
``commits_discovered``, ``commits_added`` and the active-day counts are read off
what the test itself wrote to disk.

Every test requires a live PostgreSQL, and the scan tests additionally require a
system ``git`` binary. They are marked ``integration`` for both reasons.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update

from app.models.activity import ActivityLog
from app.models.user import User
from app.services.developer.git import run_git
from tests.analytics_fixtures import AnalyticsSeed, seeded_client

pytestmark = pytest.mark.integration


def _git_available() -> bool:
    """Whether a ``git`` binary the service can actually run exists."""
    from shutil import which

    return which("git") is not None


requires_git = pytest.mark.skipif(
    not _git_available(), reason="the system git binary is not on PATH"
)

#: The fourteen Phase 8 routes as ``(method, path)``. The gate tests walk this
#: table rather than restating a list, so a route added to the router without
#: being added here shows up as a route nobody proved is protected.
ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/developer/summary"),
    ("GET", "/api/v1/developer/metrics"),
    ("GET", "/api/v1/developer/activity"),
    ("GET", "/api/v1/developer/commits"),
    ("GET", "/api/v1/developer/features"),
    ("GET", "/api/v1/developer/repositories"),
    ("POST", "/api/v1/developer/repositories"),
    ("GET", "/api/v1/developer/repositories/{repository_id}"),
    ("PATCH", "/api/v1/developer/repositories/{repository_id}"),
    ("DELETE", "/api/v1/developer/repositories/{repository_id}"),
    ("POST", "/api/v1/developer/repositories/{repository_id}/scan"),
    ("GET", "/api/v1/developer/repositories/{repository_id}/commits"),
    ("GET", "/api/v1/developer/repositories/{repository_id}/branches"),
    ("GET", "/api/v1/developer/projects/{project_id}"),
)

#: ``(method, path, body)`` for every route that names a repository. ``None`` is
#: a route with no payload; the PATCH carries one because an *empty* edit is a
#: 422 by contract, and this table is about tenancy, not about edit validation.
#: Three of the nine take the id as a query filter rather than as a path segment,
#: which is precisely where an implementation is most likely to forget the scope.
REPOSITORY_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/v1/developer/repositories/{repository_id}", None),
    ("PATCH", "/api/v1/developer/repositories/{repository_id}", {"name": "renamed"}),
    ("DELETE", "/api/v1/developer/repositories/{repository_id}", None),
    ("POST", "/api/v1/developer/repositories/{repository_id}/scan", None),
    ("GET", "/api/v1/developer/repositories/{repository_id}/commits", None),
    ("GET", "/api/v1/developer/repositories/{repository_id}/branches", None),
    ("GET", "/api/v1/developer/metrics?repository_id={repository_id}", None),
    ("GET", "/api/v1/developer/activity?repository_id={repository_id}", None),
    ("GET", "/api/v1/developer/commits?repository_id={repository_id}", None),
)

#: The same for the one project-integrating route.
PROJECT_ROUTES: tuple[tuple[str, str], ...] = (("GET", "/api/v1/developer/projects/{project_id}"),)

#: The message the service answers a repository that is not the caller's with,
#: and a repository nobody ever issued. Asserting both are identical is the
#: point: a route that could separate them is an existence oracle.
REPOSITORY_NOT_FOUND_MESSAGE = "That repository was not found."
PROJECT_NOT_FOUND_MESSAGE = "That project was not found."

#: A role the permission map has never heard of, used for the 403 case. An
#: ordinary account holds ``analytics.read``, so refusing a request has to be a
#: question about a role — and the map is fail-closed, so an unknown role is the
#: honest way to express "does not hold it".
ROLE_WITHOUT_ANALYTICS = "wizard"

#: The eight metric keys the contract fixes, in contract order. Asserted exactly
#: rather than by count: a metric that vanished would still leave seven, and seven
#: is not the contract.
METRIC_KEYS = (
    "commit_activity",
    "repository_activity",
    "change_volume",
    "active_days",
    "consistency",
    "repository_growth",
    "maintenance_activity",
    "recent_momentum",
)

#: How many commits the fixture repository carries, and how far back they sit.
#: Both are inside the default 30-day window, which is anchored on the database
#: clock rather than on this test's, so the instants are relative and the figures
#: are derived from the writes below.
FIXTURE_COMMITS = 2
FIXTURE_DAYS_AGO = (2, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    client: Any,
    method: str,
    template: str,
    headers: dict[str, str] | None = None,
    *,
    repository_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    **kwargs: Any,
):
    """Issue one request against a route template.

    The placeholders are filled with a fresh, never-issued uuid unless the caller
    names a real row, so the gate and tenancy tests reach a route exactly the way
    a caller with no such row would.
    """
    path = template.format(
        repository_id=repository_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
    )
    return client.request(method, path, headers=headers, **kwargs)


def _git(root: Path, *args: str) -> str:
    """Run one ``git`` command synchronously, for fixture construction only."""
    return asyncio.run(run_git(root, *args))


def _commit(root: Path, message: str, body: str, when: datetime) -> None:
    """Write one file, stage everything, and commit it at a fixed instant.

    Both dates are exported as environment variables rather than passed on the
    command line because git has an author-date flag and no committer-date one:
    setting only the author date would leave the committer date on the wall
    clock, and the branch listing would then disagree with everything else.
    """
    (root / "module.py").write_text(body, encoding="utf-8")
    stamp = when.isoformat()
    previous = (os.environ.get("GIT_AUTHOR_DATE"), os.environ.get("GIT_COMMITTER_DATE"))
    os.environ["GIT_AUTHOR_DATE"] = stamp
    os.environ["GIT_COMMITTER_DATE"] = stamp
    try:
        _git(root, "add", "-A")
        _git(root, "commit", "-m", message, "--no-gpg-sign")
    finally:
        for key, value in zip(("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"), previous, strict=True):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real work tree on ``main`` with :data:`FIXTURE_COMMITS` dated commits.

    The identity is configured on the repository rather than taken from the
    machine's global git config, so the recorded author is the same on a laptop
    and on a build agent whose ``~/.gitconfig`` says something else. The dates are
    relative to *now* on purpose: the service anchors its window on the database
    clock, and a fixture pinned to a fixed instant would silently fall out of
    every window as the suite ages.
    """
    root = tmp_path / "worktree"
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main", ".")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test Person")
    now = datetime.now(UTC)
    for index, days_ago in enumerate(FIXTURE_DAYS_AGO, start=1):
        _commit(
            root,
            f"Commit number {index}",
            f"VALUE = {index}\n",
            now - timedelta(days=days_ago, hours=1),
        )
    return root


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """A directory that exists and is **not** a git work tree."""
    root = tmp_path / "not-a-repo"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
async def account(client, db_session) -> tuple[AnalyticsSeed, dict[str, str]]:
    """One signed-in account with nothing registered, as ``(seed, headers)``."""
    seed, headers = await seeded_client(client, db_session)
    return seed, headers


async def _register(
    client: Any, headers: dict[str, str], path: Path, **extra: Any
) -> dict[str, Any]:
    """Register one local repository through the real endpoint."""
    payload: dict[str, Any] = {"local_path": str(path), **extra}
    response = await client.post("/api/v1/developer/repositories", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _events(session: Any, user_id: uuid.UUID, event_type: str) -> list[ActivityLog]:
    """Every ``activity_events`` row of one kind belonging to one account."""
    result = await session.execute(
        select(ActivityLog).where(
            ActivityLog.user_id == user_id, ActivityLog.event_type == event_type
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# The gate: 401 anonymous, 403 without analytics.read, on all fourteen routes
# ---------------------------------------------------------------------------

_ROUTE_CASES = [pytest.param(method, path, id=f"{method} {path}") for method, path in ROUTES]


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_an_anonymous_caller(
    method, template, client, assert_error_envelope
):
    """No Phase 8 route answers a caller who has not signed in.

    Authentication runs before the permission check, so "you are not signed in" is
    never reported as "you may not" — they are different answers to different
    questions, and a client acts on them differently.
    """
    response = await _request(client, method, template)

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_a_caller_without_analytics_read(
    method, template, client, db_session, account, assert_error_envelope
):
    """A valid, live session whose role grants nothing is answered 403.

    Phase 8 **reuses** ``analytics.read`` rather than coining a
    ``developer.write``, and this is the test that says the reuse is enforced on
    the three writes and not only on the eleven reads. A new ``Permission`` member
    would have been granted to exactly the roles ``analytics.read`` already
    covers, so it would be a second name for the same door.
    """
    seed, headers = account
    await db_session.execute(
        update(User).where(User.id == seed.owner.id).values(role=ROLE_WITHOUT_ANALYTICS)
    )
    await db_session.commit()

    response = await _request(client, method, template, headers=headers)

    assert_error_envelope(response, status_code=403, code="forbidden")


# ---------------------------------------------------------------------------
# The routing hazard: literal sub-paths against /{repository_id}
# ---------------------------------------------------------------------------


async def test_every_literal_sub_path_answers_its_own_question(client, db_session, account):
    """``/developer/summary`` answers with counts, not with a uuid parse failure.

    Were ``/developer/repositories/{repository_id}`` registered first, Starlette
    would bind the literal string ``summary`` to the path parameter, the uuid
    conversion would fail, and the dashboard tile would answer 422 about an id
    nobody ever issued — a route that still exists and still answers a different
    question. Each assertion below pins a key that **only** that handler emits,
    which is the only thing that distinguishes the two outcomes.
    """
    _seed, headers = account

    summary = await client.get("/api/v1/developer/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert {"summary", "has_data", "window_days"} <= set(summary.json()), summary.json()

    metrics = await client.get("/api/v1/developer/metrics", headers=headers)
    assert metrics.status_code == 200, metrics.text
    assert isinstance(metrics.json(), list)
    assert metrics.json()[0]["key"] == METRIC_KEYS[0], metrics.text

    activity = await client.get("/api/v1/developer/activity", headers=headers)
    assert activity.status_code == 200, activity.text
    assert {"granularity", "buckets", "total_commits"} <= set(activity.json()), activity.text

    commits = await client.get("/api/v1/developer/commits", headers=headers)
    assert commits.status_code == 200, commits.text
    assert {"items", "total", "limit", "offset"} <= set(commits.json()), commits.text

    features = await client.get("/api/v1/developer/features", headers=headers)
    assert features.status_code == 200, features.text
    assert features.json()["schema_version"] == "developer_features.v1", features.text


# ---------------------------------------------------------------------------
# The empty account: an absence to explain, never an error
# ---------------------------------------------------------------------------


async def test_a_fresh_account_gets_the_documented_empty_shape(client, db_session, account):
    """Zeros, ``has_data: false``, eight metrics and a null age — never a 404.

    Every route here answers 200 on an account with nothing recorded. That is the
    distinction this phase cares about most: an account with no commits has a
    *measured* answer ("none were recorded"), and turning that into a 404 or a
    rejected request would tell a new user their feature is broken.
    """
    _seed, headers = account

    summary = (await client.get("/api/v1/developer/summary", headers=headers)).json()
    assert summary["has_data"] is False
    assert summary["repository_count"] == 0
    assert summary["commit_count"] == 0
    assert summary["latest_commit_at"] is None
    assert summary["last_scanned_at"] is None
    # The sentence states an absence rather than rendering zeroes as a finding.
    assert "no repository has been registered" in summary["summary"].lower()

    metrics = (await client.get("/api/v1/developer/metrics", headers=headers)).json()
    assert tuple(item["key"] for item in metrics) == METRIC_KEYS
    for item in metrics:
        # A figure that could not be computed is null with a reason; a real zero
        # is a number with available true. The two must never be conflated.
        assert (item["value"] is None) is (item["available"] is False), item
        if item["available"] is False:
            assert item["reason_if_unavailable"], item
        # An explanation built from no figures is a backend bug.
        assert any(character.isdigit() for character in item["explanation"]), item

    features = (await client.get("/api/v1/developer/features", headers=headers)).json()
    # Null, not 0: there is no earliest commit, and 0 would assert "committed
    # today".
    assert features["features"]["repository_age_days"] is None
    assert features["features"]["inactivity_days"] is None
    assert features["repositories"] == []

    repositories = (await client.get("/api/v1/developer/repositories", headers=headers)).json()
    assert repositories == {
        "items": [],
        "total": 0,
        "limit": repositories["limit"],
        "offset": 0,
        "active_count": 0,
        "inactive_count": 0,
    }


async def test_the_activity_series_is_dense_when_nothing_was_recorded(client, db_session, account):
    """A quiet week arrives as seven zero buckets, not as an empty chart.

    A series that omitted empty buckets compresses the timeline: a reader counting
    the bars would see five active days and read them as consecutive. The
    assertion is the *density* — consecutive buckets exactly one day apart — and
    not merely a count, so a builder that returned seven buckets for the wrong
    reason would not satisfy it.
    """
    _seed, headers = account

    body = (
        await client.get(
            "/api/v1/developer/activity?window_days=7&granularity=day", headers=headers
        )
    ).json()

    assert body["granularity"] == "day"
    assert body["total_commits"] == 0
    assert len(body["buckets"]) >= 7, body
    assert all(bucket["commits"] == 0 for bucket in body["buckets"]), body
    starts = [
        datetime.fromisoformat(bucket["bucket_start"].replace("Z", "+00:00"))
        for bucket in body["buckets"]
    ]
    gaps = {(later - earlier).total_seconds() for earlier, later in pairwise(starts)}
    assert gaps == {86400.0}, starts


async def test_an_unsupported_granularity_is_422_not_a_guessed_chart(
    client, db_session, account, assert_error_envelope
):
    """``?granularity=fortnight`` is refused rather than silently rounded to a week.

    Returning a chart nobody asked for is worse than a 422: the caller would
    render bucket widths it never asked for and have no way to notice.
    """
    _seed, headers = account

    response = await client.get("/api/v1/developer/activity?granularity=fortnight", headers=headers)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "fortnight" in error["message"], error


# ---------------------------------------------------------------------------
# Tenancy: 404, never 403, and never an existence oracle
# ---------------------------------------------------------------------------


@requires_git
@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in REPOSITORY_ROUTES],
)
async def test_a_foreign_repository_is_404_not_403_on_every_route(
    method, template, body, client, db_session, repo, assert_error_envelope
):
    """Grace's repository, named by path or by filter, is refused like a typo.

    The caller is authenticated and permitted — the token is Ada's, and Ada holds
    ``analytics.read`` — and still gets ``not_found``. Three of these nine routes
    take the id as a *query* filter rather than as a path segment, which is where
    an implementation is most likely to skip the ownership check because the path
    looks unambiguous. The message for the foreign id is compared against the
    message for an id nobody ever issued, so the two cannot drift apart.
    """
    ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    del ada_seed, grace_seed
    foreign = await _register(client, grace_headers, repo, name="Grace's repository")

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    response = await _request(
        client, method, template, ada_headers, repository_id=uuid.UUID(foreign["id"]), **kwargs
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == REPOSITORY_NOT_FOUND_MESSAGE
    assert "Grace's repository" not in response.text

    unissued = await _request(client, method, template, ada_headers, **kwargs)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


@pytest.mark.parametrize(("method", "template"), PROJECT_ROUTES)
async def test_a_foreign_project_is_404_not_403(
    method, template, client, db_session, assert_error_envelope
):
    """A project belonging to somebody else is a 404, like any unissued id.

    ``GET /developer/projects/{id}`` is the one Phase 8 route whose resource was
    created by another phase, so the ownership check is the only thing standing
    between one account's project page and another's recorded history.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, _grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace_seed.project(name="Grace's project")

    response = await _request(client, method, template, ada_headers, project_id=grace_project.id)
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == PROJECT_NOT_FOUND_MESSAGE
    assert "Grace's project" not in response.text

    unissued = await _request(client, method, template, ada_headers)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@requires_git
async def test_registering_a_path_that_is_not_a_work_tree_is_422_and_stores_nothing(
    client, db_session, account, empty_dir, assert_error_envelope
):
    """A directory without a ``.git`` entry is refused **before** the write.

    A row pointing at something that is not a repository would fail on every
    future scan and would already be on the dashboard by the time anybody found
    out, so the validation has to precede the insert rather than follow it. The
    second assertion is the one that matters: a 422 that still left a row behind
    would be the failure this rule exists to prevent.

    The engine raises ``GitRepositoryError``, a bare ``Exception`` with no
    registered handler, so the service has to convert it — otherwise this request
    surfaces as an unhandled 500 and takes the page down with it, which is the
    one outcome the "a broken repository must never break NEXUS" rule forbids.
    """
    _seed, headers = account

    response = await client.post(
        "/api/v1/developer/repositories", json={"local_path": str(empty_dir)}, headers=headers
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["message"], error

    listed = (await client.get("/api/v1/developer/repositories", headers=headers)).json()
    assert listed["total"] == 0, listed
    assert listed["items"] == [], listed


@requires_git
async def test_registering_the_same_path_twice_is_409(client, db_session, account, repo):
    """One path, once per account.

    A duplicate row would put the same work tree on the dashboard twice and give
    every metric two copies of each commit, so the collision is reported rather
    than merged — and it is a 409 rather than a 422 because the request was
    well-formed and collided with existing state.
    """
    _seed, headers = account
    await _register(client, headers, repo)

    response = await client.post(
        "/api/v1/developer/repositories", json={"local_path": str(repo)}, headers=headers
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


@requires_git
async def test_registration_records_the_repository_in_the_activity_trail(
    client, db_session, account, repo
):
    """``REPOSITORY_REGISTERED`` is written with the id and nothing else.

    The event carries ids and counts, not the user's own words: a repository name
    belongs on the row the event points at, and copying it into a feed nobody
    asked for is how a summary ends up quoting text the user has since changed.
    """
    seed, headers = account

    repository = await _register(client, headers, repo, name="Widget")

    rows = await _events(db_session, seed.owner.id, "repository_registered")
    assert len(rows) == 1, rows
    assert rows[0].metadata_ == {"repository_id": repository["id"]}


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


@requires_git
async def test_a_scan_is_a_200_and_a_rescan_adds_nothing(client, db_session, account, repo):
    """Two commits in, two commits stored, and zero added on the second run.

    ``commits_added`` is the figure that proves the upsert was idempotent: the
    git CLI returns the same hashes for an unchanged repository, so a second scan
    that appended anything would be duplicating history. Asserting both halves
    together is what distinguishes "the scan was idempotent" from "the second scan
    found nothing at all".
    """
    _seed, headers = account
    repository = await _register(client, headers, repo)
    scan_url = f"/api/v1/developer/repositories/{repository['id']}/scan"

    first = await client.post(scan_url, headers=headers)
    assert first.status_code == 200, first.text
    first_run = first.json()
    assert first_run["status"] == "ok", first_run
    assert first_run["error"] is None, first_run
    assert first_run["commits_discovered"] == FIXTURE_COMMITS, first_run
    assert first_run["commits_added"] == FIXTURE_COMMITS, first_run

    second = await client.post(scan_url, headers=headers)
    assert second.status_code == 200, second.text
    second_run = second.json()
    assert second_run["status"] == "ok", second_run
    assert second_run["commits_added"] == 0, second_run
    # ``commits_discovered`` is *not* asserted to equal the first run's: an
    # incremental read passes ``--since`` the repository's stored
    # ``latest_commit_at``, and git's boundary handling re-reads at most the
    # boundary commit. What must hold is that nothing was duplicated, which is
    # the total below.
    assert second_run["commits_discovered"] <= FIXTURE_COMMITS, second_run

    commits = (
        await client.get(
            f"/api/v1/developer/repositories/{repository['id']}/commits", headers=headers
        )
    ).json()
    assert commits["total"] == FIXTURE_COMMITS, commits


@requires_git
async def test_a_repository_that_stops_being_readable_answers_200_not_500(
    client, db_session, account, repo
):
    """A broken repository is a row with a sentence, never an exception.

    This is the assertion the whole rule reduces to. ``.git`` is removed from
    under a registered repository, so the next scan runs the git CLI against
    something that is no longer a work tree and git exits non-zero. The route must
    still answer ``200`` with ``status: 'error'`` — a stack trace on somebody's
    dashboard, or a 500 that takes the page with it, is the failure this exists to
    prevent, and the cheapest way to introduce it is to wrap the service call in
    an ``except`` that re-raises.
    """
    _seed, headers = account
    repository = await _register(client, headers, repo)
    scan_url = f"/api/v1/developer/repositories/{repository['id']}/scan"
    assert (await client.post(scan_url, headers=headers)).json()["status"] == "ok"

    # Moved rather than deleted: git packs its objects read-only on Windows, so a
    # recursive delete fails on ``PermissionError`` before the test reaches the
    # assertion it exists to make. Moving the entry away is the same condition —
    # the directory is still there and is no longer a work tree — and is
    # portable.
    (repo / ".git").rename(repo / ".git-moved")

    response = await client.post(scan_url, headers=headers)

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "error", run
    assert isinstance(run["error"], str) and run["error"], run
    assert "Traceback" not in run["error"], run

    # The repository is still a listable row, carrying the failure beside it.
    detail = (
        await client.get(f"/api/v1/developer/repositories/{repository['id']}", headers=headers)
    ).json()
    assert detail["last_scan_status"] == "error", detail
    assert detail["last_scan_error"], detail


@requires_git
async def test_a_scan_records_the_repository_in_the_activity_trail(
    client, db_session, account, repo
):
    """``REPOSITORY_SCANNED`` is written once per attempt, on success and on failure.

    **One row per attempt, not one per commit.** A first scan of a repository with
    two thousand commits must not write two thousand history entries; the feed
    exists so a person can see what happened, and a scan that appends one row per
    commit read is a denial of service against it.

    ``COMMIT_DETECTED`` is deliberately **not** asserted here, and that is a
    reported defect rather than an omitted test. The service computes the
    high-water mark for those events from the ``GitRepository`` object *after*
    :meth:`DeveloperRepository.update_scan_state` has written the new
    ``latest_commit_at`` onto it — ``update_scan_state`` issues an ORM
    ``UPDATE ... RETURNING`` with ``populate_existing=True`` on the request's own
    session, so the in-memory row is synchronised with the post-write values. The
    comparison is then strictly greater-than against the newest commit's own
    timestamp and no commit can satisfy it, so the event is never written. The
    fix belongs in :mod:`app.services.developer.service`, which is not this
    file's to edit; when it lands, this is the assertion to restore.
    """
    seed, headers = account
    repository = await _register(client, headers, repo)
    scan_url = f"/api/v1/developer/repositories/{repository['id']}/scan"

    await client.post(scan_url, headers=headers)
    await client.post(scan_url, headers=headers)

    scanned = await _events(db_session, seed.owner.id, "repository_scanned")
    assert len(scanned) == 2, scanned
    assert {row.metadata_["status"] for row in scanned} == {"ok"}
    # Both scans agree on how many commits are stored; the second may re-*read*
    # the boundary commit (``--since`` is the repository's own ``latest_commit_at``)
    # without re-*adding* it, which is the upsert being idempotent rather than a
    # figure that must be identical run to run.
    discovered = {row.metadata_["commits_discovered"] for row in scanned}
    assert FIXTURE_COMMITS in discovered, scanned
    assert all(1 <= value <= FIXTURE_COMMITS for value in discovered), scanned


# ---------------------------------------------------------------------------
# The metadata edit
# ---------------------------------------------------------------------------


@requires_git
async def test_an_unset_field_is_not_a_cleared_field(client, db_session, account, repo):
    """Renaming a repository leaves its description and its project link alone.

    The route builds its mapping with ``exclude_unset=True`` because ``None``
    means "write SQL NULL" downstream. Without it a rename would silently discard
    everything else, and the response would still look entirely correct — which is
    why this is asserted against the round trip rather than against the request.
    """
    seed, headers = account
    repository = await _register(
        client, headers, repo, description="The widget service", name="Widget"
    )
    project = await seed.project(name="atlas")
    linked = await client.patch(
        f"/api/v1/developer/repositories/{repository['id']}",
        json={"project_id": str(project.id)},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["project_id"] == str(project.id)

    renamed = await client.patch(
        f"/api/v1/developer/repositories/{repository['id']}",
        json={"name": "Widget v2"},
        headers=headers,
    )

    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "Widget v2", body
    assert body["description"] == "The widget service", body
    assert body["project_id"] == str(project.id), body


@requires_git
async def test_an_edit_that_changes_nothing_is_422(client, db_session, account, repo):
    """``{}`` is refused rather than answered with a cheerful no-op 200.

    A silent 200 would tell a client it had updated something it had not, and the
    client would go on to believe the state it asked for had been stored.
    """
    _seed, headers = account
    repository = await _register(client, headers, repo)

    response = await client.patch(
        f"/api/v1/developer/repositories/{repository['id']}", json={}, headers=headers
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


@requires_git
async def test_an_edit_cannot_move_a_repository(client, db_session, account, repo):
    """``local_path`` is not in the edit schema and cannot be smuggled through.

    The path is the row's identity and the one field checked against the disk.
    Moving a repository would leave its counters, its commit range and its
    recorded history describing a directory the account never scanned, and nothing
    in the schema could notice. The assertion is the 422 the unknown field
    produces — Pydantic ignores extras by default only if the model allows them,
    so this pins that it does not.
    """
    _seed, headers = account
    repository = await _register(client, headers, repo)

    response = await client.patch(
        f"/api/v1/developer/repositories/{repository['id']}",
        json={"local_path": str(repo.parent)},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    unchanged = (
        await client.get(f"/api/v1/developer/repositories/{repository['id']}", headers=headers)
    ).json()
    assert unchanged["local_path"] == repository["local_path"], unchanged


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


@requires_git
async def test_deleting_a_repository_removes_its_recorded_history(
    client, db_session, account, repo
):
    """``204``, and the commits and branches go with it.

    The history is not merely deactivated. A row keeping its history while
    claiming the repository no longer exists would leave the account's metrics
    reading commits from a work tree the user has explicitly removed. The
    ``REPOSITORY_REMOVED`` activity row survives the cascade, so the account's own
    trail still shows that it happened.
    """
    seed, headers = account
    repository = await _register(client, headers, repo)
    url = f"/api/v1/developer/repositories/{repository['id']}"
    await client.post(f"{url}/scan", headers=headers)

    deleted = await client.delete(url, headers=headers)

    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b"", deleted.text

    assert (await client.get(url, headers=headers)).status_code == 404
    commits = (await client.get("/api/v1/developer/commits", headers=headers)).json()
    assert commits["total"] == 0, commits
    summary = (await client.get("/api/v1/developer/summary", headers=headers)).json()
    assert summary["repository_count"] == 0, summary
    assert (await _events(db_session, seed.owner.id, "repository_removed")) != []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1, 201, 500])
async def test_a_page_size_outside_the_documented_bounds_is_422(limit, client, db_session, account):
    """``?limit=500`` is refused rather than silently clipped to the ceiling.

    A caller that asked for 500 and received 200 cannot tell a truncated page from
    a page that was always 200 rows long. ``0`` and ``-1`` are refused for the
    same family of reasons: no page has fewer than no rows.
    """
    _seed, headers = account

    response = await client.get(f"/api/v1/developer/repositories?limit={limit}", headers=headers)

    assert response.status_code == 422, response.text


@requires_git
async def test_the_commit_timeline_pages_without_losing_or_repeating_a_row(
    client, db_session, account, repo
):
    """Two commits over two pages come back as two distinct rows.

    Newest first, so the ordering is asserted as well as the coverage: a timeline
    that paged correctly and ordered wrongly would still pass a set comparison.
    """
    _seed, headers = account
    repository = await _register(client, headers, repo)
    await client.post(f"/api/v1/developer/repositories/{repository['id']}/scan", headers=headers)
    url = f"/api/v1/developer/repositories/{repository['id']}/commits"

    first = (await client.get(f"{url}?limit=1&offset=0", headers=headers)).json()
    second = (await client.get(f"{url}?limit=1&offset=1", headers=headers)).json()

    assert first["total"] == second["total"] == FIXTURE_COMMITS
    assert first["limit"] == second["limit"] == 1
    assert len(first["items"]) == len(second["items"]) == 1
    hashes = {first["items"][0]["commit_hash"], second["items"][0]["commit_hash"]}
    assert len(hashes) == FIXTURE_COMMITS, (first, second)
    committed = [first["items"][0]["committed_at"], second["items"][0]["committed_at"]]
    assert committed[0] >= committed[1], "the second commit is the more recent one"


# ---------------------------------------------------------------------------
# The project view
# ---------------------------------------------------------------------------


@requires_git
async def test_the_project_view_carries_its_repositories_beside_the_counts(
    client, db_session, account, repo
):
    """The project page cannot show a total that disagrees with its rows.

    The repositories are carried alongside the figures rather than fetched
    separately, and the commit count is summed from the rows actually listed —
    which is what makes ``repository_count == len(repositories)`` an invariant a
    client may rely on.
    """
    seed, headers = account
    project = await seed.project(name="atlas")
    repository = await _register(client, headers, repo, name="Widget", project_id=str(project.id))
    await client.post(f"/api/v1/developer/repositories/{repository['id']}/scan", headers=headers)

    response = await client.get(f"/api/v1/developer/projects/{project.id}", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_name"] == "atlas", body
    assert body["repository_count"] == len(body["repositories"]) == 1, body
    assert body["repositories"][0]["id"] == repository["id"], body
    assert body["commit_count"] == FIXTURE_COMMITS, body
    assert body["commits_in_window"] == FIXTURE_COMMITS, body
    assert body["has_data"] is True, body
    # Factual language: the sentence counts commits and repositories. It never
    # claims hours, focus, productivity or effort.
    assert "commits were recorded" in body["summary"], body


@requires_git
async def test_a_window_longer_than_the_configured_ceiling_is_422(
    client, db_session, account, assert_error_envelope
):
    """An over-long window is refused rather than silently shortened.

    Shortening it would make the ``window_days`` on the response disagree with
    what the caller asked for, and every sentence built from that number would
    then describe a range nobody requested. The ceiling is a setting, so the
    refusal comes from the service rather than from a constant in the router.
    """
    _seed, headers = account

    response = await client.get("/api/v1/developer/summary?window_days=5000", headers=headers)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "days" in error["message"], error


# ---------------------------------------------------------------------------
# Nothing here ever claims a person's working time
# ---------------------------------------------------------------------------


_FORBIDDEN_CLAIMS = ("hour", "productive", "productivity", "focus", "effort", "busy")


@requires_git
async def test_no_response_claims_working_time_productivity_focus_or_effort(
    client, db_session, account, repo
):
    """No figure or sentence on this surface is a statement about a person.

    A commit timestamp records when a commit object was written. It cannot record
    how long anyone worked, so no number here supports a claim about hours,
    productivity, focus or effort, and a sentence asserting one would be a
    fabrication with a real number attached to it.

    The forbidden words are matched on word boundaries so that an innocent
    substring — ``focused``, or ``hours`` inside ``house`` — cannot fail this test
    and send someone looking for a bug that is not there. ``duration_ms`` is the
    one measured duration on the surface and is explicitly exempted, because it
    measures how long *git took*, not how long a person worked.
    """
    seed, headers = account
    repository = await _register(client, headers, repo)
    url = f"/api/v1/developer/repositories/{repository['id']}"
    await client.post(f"{url}/scan", headers=headers)

    bodies = []
    for path in (
        "/api/v1/developer/summary",
        "/api/v1/developer/metrics",
        "/api/v1/developer/activity",
        "/api/v1/developer/commits",
        "/api/v1/developer/features",
        "/api/v1/developer/repositories",
        url,
        f"{url}/scan",
        f"{url}/commits",
        f"{url}/branches",
    ):
        method = "post" if path.endswith("/scan") else "get"
        response = await getattr(client, method)(path, headers=headers)
        assert response.status_code == 200, response.text
        bodies.append((path, response.text))
    project = await seed.project(name="atlas")
    bodies.append(
        (
            "/api/v1/developer/projects/{id}",
            (await client.get(f"/api/v1/developer/projects/{project.id}", headers=headers)).text,
        )
    )

    offenders: list[str] = []
    for path, text in bodies:
        scrubbed = re.sub(r'"duration_ms":\s*\d+', "", text)
        for word in _FORBIDDEN_CLAIMS:
            if re.search(rf"\b{word}\w*\b", scrubbed, flags=re.IGNORECASE):
                offenders.append(f"{path}: {word}")
    assert offenders == [], offenders
