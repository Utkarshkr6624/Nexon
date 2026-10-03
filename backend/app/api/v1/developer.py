"""Phase 8 endpoints: what the caller's local git repositories recorded.

Where the answers live
----------------------
Every question this router could answer — *how many commits, over which window,
in which repository, which branches did git see* — is answered by
:class:`~app.services.developer.service.DeveloperIntelligenceService`, which owns
the owner-scoped predicates, the idempotent upserts and the assembly of the wire
shapes. This file reads a request, picks a status code, and returns what the
service built. It computes nothing: a figure that appears on one of these routes
appeared in :mod:`app.services.developer.metrics` first, where it can be asserted
without a database and without a ``.git`` directory.

What this layer must not do
----------------------------
**No sentence on this surface may claim working time, productivity, focus or
effort.** That rule is enforced where the sentences are composed — the metrics
module refuses to build an explanation containing one of those words — but it is
restated here because a router is exactly where a convenient summary string gets
invented. A commit timestamp records when a commit object was written. It cannot
record how long anyone worked, so no number this router could send supports the
claim, and a sentence asserting one would be a fabrication with a real number
attached to it.

**No route catches a failing scan.** ``POST
/developer/repositories/{id}/scan`` returns 200 with a ``ScanRunRead`` whose
``status`` is ``'error'`` and whose ``error`` is a human sentence, whatever the
repository on disk turned out to be. A directory deleted since it was registered,
a ``.git`` that will not parse and an unreachable network share are all the same
event here: a row that says the read failed, which is what "a broken repository
must never break NEXUS" looks like from the outside. A ``try`` around the call
that turned any of those into a 500 would be the failure the rule exists to
prevent — the whole difference between a feature that reports a broken repository
and one that loses the page.

Tenancy
-------
**No route here accepts a user id, and none of them accepts a repository id that
it does not first scope.** The caller comes from the bearer token; the service
resolves every repository through an owner-scoped lookup and simply does not load
a row that belongs to somebody else. Another account's repository therefore
answers **404, not 403** — identically to an id nobody ever issued, so these
routes cannot be used to learn which repository ids are real.

Route order is load-bearing here
--------------------------------
**The five literal sub-paths are declared before
``/developer/repositories/{repository_id}``.** Starlette matches routes in the
order they were added and does not prefer a literal segment over a parameter, so
were the parameterised route registered first, ``GET /developer/summary`` would
bind the literal string ``summary`` to the path parameter, fail its uuid
conversion, and answer with a 422 about an id that never existed — while the
dashboard tile quietly lost its data. The same hazard applies to ``metrics``,
``activity``, ``commits`` and ``features``, so all five sit above the
parameterised routes rather than being scattered among them. There is no
path-conversion trick that makes this unnecessary and no ``Path`` annotation that
fixes it; the order is the entire mechanism.

Permission
----------
**Every route here reuses ``Permission.ANALYTICS_READ``**, the Phase 7 precedent,
and none of them adds a member to :class:`~app.core.permissions.Permission`. The
reason is the same one :mod:`app.api.v1.risks` gives and is worth repeating
because this phase *does* write: registering a repository and scanning one are the
caller answering a question about rows derived from their own recorded work, so
the capability that admits the reading already admits the answering. Coining a
``developer.write`` would grant it to exactly the roles ``analytics.read`` already
covers — the role table has no entry for either — while adding a member the
permission test asserts the full set of.

Status codes
------------
``201`` for a registration because a repository row was created and a client may
cache the address; ``200`` for a scan because the contract fixes it and because a
scan that failed is still a completed attempt; ``204`` for a delete, which is why
that one route carries ``response_class=Response`` and no ``response_model`` —
FastAPI refuses a body on a 204, and every delete in this API answers the same
way. Everything else is a ``200`` with a body, including the empty reads: an
account with nothing registered gets zeroes and ``has_data: false``, which is an
absence to explain rather than an error to report.

Pagination
----------
``limit`` is capped at :data:`MAX_PAGE_SIZE`, and the cap is a **rejection rather
than a silent truncation** — ``?limit=500`` is a 422, because a caller that asked
for 500 and received 200 cannot tell a truncated page from a page that was always
200 rows long. The ceiling matches the repository's own clamp rather than
introducing a second, smaller number that would make a legal request look
refused.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, DeveloperIntelligenceServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.developer import (
    BranchListRead,
    CommitListRead,
    DeveloperActivityRead,
    DeveloperFeatureVectorRead,
    DeveloperMetricRead,
    DeveloperSummaryRead,
    ProjectDeveloperRead,
    RepositoryCreate,
    RepositoryListRead,
    RepositoryRead,
    RepositoryUpdate,
    ScanRunRead,
)

__all__ = ["router"]

router = APIRouter(prefix="/developer", tags=["developer"])

#: The page size a caller gets when it names none. The same number
#: :mod:`app.repositories.developer` and
#: :class:`~app.services.developer.service.DeveloperIntelligenceService` default
#: to, so the router's fallback and the storage layer's are one value rather than
#: two that can drift apart.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for, and deliberately the *same* ceiling
#: the repository clamps to. A smaller number here would make a legal request look
#: refused for a reason the client cannot discover; a larger one would only be
#: clipped silently further down, which is the outcome ``docs/api-conventions.md``
#: §Pagination exists to prevent.
MAX_PAGE_SIZE = 200

#: Applied to every route including the three writes. Phase 8 reuses
#: ``analytics.read`` rather than coining a ``developer.write``; see this module's
#: docstring for why, and :mod:`app.api.v1.risks` for the same argument made one
#: phase earlier.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: The window length a window-reading route accepts. It is nullable because an
#: omitted window is **not** the same request as a default one: it asks the server
#: for ``developer_default_window_days`` rather than for a figure this file
#: invented, so a chart cannot be rendered for a range the backend never used.
#:
#: Only the *lower* bound is declared here. The ceiling is
#: ``developer_max_window_days``, a setting the service owns, and a constant in
#: this file would answer 422 against a limit the deployment has raised.
_WINDOW_DAYS = Annotated[int | None, Query(ge=1, description="Length of the window in days.")]

#: One message for a repository that is not the caller's and for one that was
#: never issued. The service raises this; the constant is repeated here so a test
#: can assert both answers are identical and so the two cases are visibly one
#: case rather than two that happened to match.
_REPOSITORY_NOT_FOUND = "That repository was not found."


# ---------------------------------------------------------------------------
# Dashboard reads — the five literal sub-paths, declared first on purpose
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=DeveloperSummaryRead,
    summary="Headline counts over a window, and one factual sentence",
    dependencies=_ANALYTICS_READ,
)
async def developer_summary(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> DeveloperSummaryRead:
    """Return the account-wide counts the dashboard header is built from.

    **Counts only, and the window they cover is carried beside them.** There is
    no score on this surface and no comparison rendered as a verdict:
    ``commits_in_window`` means "how many commits were recorded", and
    ``window_days`` is returned so that any sentence a client writes about it can
    name the range rather than implying a period nobody specified.

    An account with nothing registered gets a 200 carrying zeroes and
    ``has_data: false``. That is a real answer and the good news this route
    exists to deliver — the flag is what tells the client to explain an absence
    instead of rendering a dashboard of zeroes as a finding about the account.

    Errors: 422 when the window exceeds ``developer_max_window_days``.
    """
    return await developer.summary(owner=current_user, window_days=window_days)


@router.get(
    "/metrics",
    response_model=list[DeveloperMetricRead],
    summary="The eight metrics, each with its definition and its figures",
    dependencies=_ANALYTICS_READ,
)
async def developer_metrics(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[DeveloperMetricRead]:
    """Return all eight metrics over one window, fully explained.

    **Always eight elements, never fewer.** A metric the data cannot support comes
    back with ``available: false``, ``value: null`` and a reason, rather than
    being dropped — a client indexing by ``key`` would otherwise meet a hole where
    a card belongs and would have to guess whether the card was absent or merely
    unmeasured. A *measured* zero is a different answer and keeps both its value
    and ``available: true``: "no commits were recorded in this window" is a true
    sentence and survives the trip to the screen.

    Each entry carries ``unit`` as data rather than decoration, so a ratio cannot
    be formatted as a count, and ``explanation`` repeats the definition with the
    figures in it. Nothing on this surface measures time spent: a commit
    timestamp records when a commit was written and cannot support any claim about
    how long anyone worked.

    Errors: 422 when the window exceeds ``developer_max_window_days``; 404 when
    ``repository_id`` is not the caller's.
    """
    return await developer.metrics(
        owner=current_user, window_days=window_days, repository_id=repository_id
    )


@router.get(
    "/activity",
    response_model=DeveloperActivityRead,
    summary="Commits bucketed by day, week or month, gaps included",
    dependencies=_ANALYTICS_READ,
)
async def developer_activity(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
    granularity: Annotated[str | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
) -> DeveloperActivityRead:
    """Return the activity series, one bucket per period across the whole range.

    **The buckets are dense.** A quiet Tuesday arrives carrying ``commits: 0``
    rather than being skipped, because a series that omitted empty buckets
    compresses the timeline and makes a sparse fortnight read as dense as a busy
    one — a misreading of the data rather than a presentational choice, and one a
    reader counting the bars cannot detect.

    ``granularity`` omitted asks the server for
    ``developer_activity_granularity_default`` rather than for a default invented
    here, so the chart's labels and the configuration cannot disagree. An
    unrecognised value is refused rather than guessed at: returning a chart nobody
    asked for is worse than a 422.

    Errors: 422 for an unsupported granularity or an over-long window; 404 when
    ``repository_id`` is not the caller's.
    """
    return await developer.read_activity(
        owner=current_user,
        window_days=window_days,
        granularity=granularity,
        repository_id=repository_id,
    )


@router.get(
    "/commits",
    response_model=CommitListRead,
    summary="The commit timeline across every registered repository",
    dependencies=_ANALYTICS_READ,
)
async def developer_commits(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    branch: Annotated[str | None, Query(max_length=255)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> CommitListRead:
    """Return one page of the account-wide commit timeline, newest first.

    ``branch`` narrows to commits the scan could attribute to one branch.
    Attribution is best-effort by construction, so a commit with no resolvable
    branch is **absent** from a branch filter rather than misfiled into it:
    "branch unknown" is not a branch, and folding it into whichever name was
    asked for would be a claim the scan never made.

    Every filter here narrows ``total`` as well as ``items``, which is why they
    live on the server. A client-side filter can only see the rows the current
    page happens to carry, so it can neither count a band nor offer a pager for
    one.

    Errors: 422 for an out-of-range page size; 404 when ``repository_id`` is not
    the caller's.
    """
    return await developer.commits(
        owner=current_user,
        repository_id=repository_id,
        branch=branch,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/features",
    response_model=DeveloperFeatureVectorRead,
    summary="The ML-ready feature vector, stamped with its schema version",
    dependencies=_ANALYTICS_READ,
)
async def developer_features(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> DeveloperFeatureVectorRead:
    """Return named numbers under ``developer_features.v1``.

    **An extractor, not a model.** Nothing here is trained, loaded, served or
    inferred, and no client may join these features into a prediction: Phase 10
    does that, and the ``schema_version`` is what tells a later trainer what the
    columns meant when this row was produced. ``commits_last_7d`` is a count of
    commit objects git recorded, not a statement about a person.

    ``repository_age_days`` and ``inactivity_days`` are **null** for a repository
    with no commits, and never ``0``: zero would assert it was created today, and
    inside a feature matrix a fabricated zero is indistinguishable from an
    observed one once it reaches a trainer.

    Errors: 422 when the window exceeds ``developer_max_window_days``.
    """
    return await developer.features(owner=current_user, window_days=window_days)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@router.get(
    "/repositories",
    response_model=RepositoryListRead,
    summary="Registered repositories, with the active/inactive split beside them",
    dependencies=_ANALYTICS_READ,
)
async def list_repositories(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    is_active: Annotated[bool | None, Query()] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
) -> RepositoryListRead:
    """Return one page of the caller's registered repositories.

    **The active/inactive split is counted across every matching row, not across
    this page.** A browser-side tally can only count the rows it happens to hold,
    so it would understate a band that continues onto page two and the pager would
    have to be withdrawn for want of a total. The split is omitted only when a
    filter is in play, where one half is necessarily zero and the client already
    knows which filter it sent.

    A repository whose last scan failed is listed like any other, carrying
    ``last_scan_status: 'error'`` and a sentence in ``last_scan_error``. That is
    the mechanism, not an oversight: the row exists so the user can see which of
    their repositories git could not read, instead of one unreadable directory
    quietly removing itself from a list.

    Errors: 422 for an out-of-range page size.
    """
    return await developer.list_repositories(
        owner=current_user,
        project_id=project_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/repositories",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a local repository after validating the path",
    dependencies=_ANALYTICS_READ,
)
async def register_repository(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    payload: RepositoryCreate,
) -> RepositoryRead:
    """Validate a path, prove it is a git work tree, and store it.

    **Validation happens before the write, not after it.** A row pointing at a
    directory that is not a repository would fail on every future scan and would
    already be on the dashboard by the time anybody found out, so the server
    resolves the path, checks for a ``.git`` entry and — when
    ``developer_path_allowlist`` is configured — proves it is under one of the
    roots *before* storing the resolved absolute path. A relative path therefore
    cannot quietly resolve somewhere else once a different working directory runs
    the scan.

    A bare ``git init`` with no commits registers successfully. It is the first
    thing a user does with this feature, and refusing it would tell them their
    new project does not exist.

    One path may be registered **once per account**; two accounts registering the
    same directory is allowed, because it is a local directory and both may
    legitimately watch it. The account is capped at
    ``developer_max_repositories``.

    Errors: 422 for a path that is not a readable work tree, outside the
    allowlist, or naming no project of the caller's; 409 when the path is already
    registered for this account or the cap has been reached.
    """
    return await developer.register_repository(
        owner=current_user,
        local_path=payload.local_path,
        name=payload.name,
        description=payload.description,
        project_id=payload.project_id,
        is_active=payload.is_active,
    )


# ---------------------------------------------------------------------------
# One repository
# ---------------------------------------------------------------------------


@router.get(
    "/repositories/{repository_id}",
    response_model=RepositoryRead,
    summary="One repository, with the outcome of its last scan",
    dependencies=_ANALYTICS_READ,
)
async def get_repository(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
) -> RepositoryRead:
    """Return one registered repository, resolved through an owner-scoped read.

    **404 for another account's repository, never 403.** The row is never loaded
    if it is not the caller's, so this route cannot be used to learn which
    repository ids exist — which is why :data:`_REPOSITORY_NOT_FOUND` is the same
    sentence for a foreign id and for one nobody ever issued.

    ``last_scan_status`` and ``last_scan_error`` are the honest face of "a broken
    repository must never break NEXUS": a repository git could not read is a 200
    carrying a sentence, never an exception.
    """
    return await developer.get_repository(owner=current_user, repository_id=repository_id)


@router.patch(
    "/repositories/{repository_id}",
    response_model=RepositoryRead,
    summary="Edit a repository's metadata — never its path",
    dependencies=_ANALYTICS_READ,
)
async def update_repository(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
    payload: RepositoryUpdate,
) -> RepositoryRead:
    """Change a repository's label, note, project link or active flag.

    **The update mapping is built with ``exclude_unset=True`` on purpose.**
    ``None`` means "write SQL NULL" downstream, so dumping the whole model would
    clear the description and the project link of any caller who only meant to
    rename the repository. A field the client never sent is not a field the user
    asked to clear; an explicit ``null`` is, and reaches the service as one.

    **There is no ``local_path`` in the payload, and the omission is the design.**
    The path is the row's identity and the one field checked against the
    filesystem: letting a PATCH move a repository would leave its counters, its
    commit range and its recorded history describing a directory this account
    never scanned, and nothing in the schema could notice. To repoint a
    repository, register the new one and remove the old.

    ``primary_language`` is absent for the mirror-image reason — it is measured by
    the scan, not typed by a person.

    Errors: 422 when the payload changes no field; 404 when the repository is not
    the caller's or a supplied ``project_id`` belongs to somebody else.
    """
    return await developer.update_repository(
        owner=current_user,
        repository_id=repository_id,
        values=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/repositories/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a repository and everything recorded under it",
    dependencies=_ANALYTICS_READ,
)
async def delete_repository(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
) -> Response:
    """Remove a repository, its commits, its branches and its scan runs.

    **The recorded history goes with it**, through the schema's
    ``ON DELETE CASCADE``, rather than being deactivated. A row that kept its
    history while claiming the repository no longer exists would leave the
    account's metrics reading commits from a work tree the user has explicitly
    removed. The ``REPOSITORY_REMOVED`` activity row survives the cascade, so the
    account's own trail still shows that it happened.

    ``204`` rather than a body, which is why this route alone carries
    ``response_class=Response`` and no ``response_model``: FastAPI forbids a
    response body on a 204, and every delete in this API answers the same way.

    Errors: 404 when the repository is not the caller's or does not exist.
    """
    await developer.delete_repository(owner=current_user, repository_id=repository_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/repositories/{repository_id}/scan",
    response_model=ScanRunRead,
    summary="Read the repository off disk now, and report the attempt",
    dependencies=_ANALYTICS_READ,
)
async def scan_repository(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
    full: Annotated[
        bool,
        Query(
            description=(
                "Read the whole history instead of only what landed since the last "
                "scan. Needed after a rewrite of history, where the stored "
                "high-water mark points at a commit that no longer exists."
            )
        ),
    ] = False,
) -> ScanRunRead:
    """Rescan one repository synchronously and return the record of the attempt.

    **A ``POST`` because it writes** — commits, branches, a scan run and a
    ``REPOSITORY_SCANNED`` event — and **a 200 whether the read worked or not.**
    There is no background scheduler in NEXUS and Phase 8 does not add one, so
    this call *is* the scan, and a repository that cannot be read answers with
    ``status: 'error'`` and a human sentence rather than a rejected request. That
    pairing is the whole mechanism behind the rule that a broken repository must
    never break NEXUS: there is no code path on this router where a bad directory
    produces a 500.

    The write is **incremental and idempotent**: the read passes ``--since`` the
    repository's stored ``latest_commit_at``, and commits are upserted on
    ``(repository_id, commit_hash)``. So a rescan of an unchanged repository
    reports ``commits_discovered`` equal to what git returned and
    ``commits_added`` of 0 — and that gap is the proof the upsert worked, not a
    sign anything went missing.

    The answer is the *attempt*, not the repository: the refreshed row is read
    from ``GET /developer/repositories/{id}`` rather than inferred from this one,
    so a client never has to guess what the counters now say.

    ``?full=true`` re-reads the entire history. The incremental read is the
    default because it is what keeps a rescan cheap, but a rewritten history
    leaves the stored high-water mark pointing at a commit that no longer
    exists, and only a full read can recover from that.

    Errors: 404 when the repository is not the caller's or does not exist.
    """
    return await developer.scan_repository(
        owner=current_user, repository_id=repository_id, full=full
    )


@router.get(
    "/repositories/{repository_id}/commits",
    response_model=CommitListRead,
    summary="One repository's recorded history, newest first",
    dependencies=_ANALYTICS_READ,
)
async def repository_commits(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    branch: Annotated[str | None, Query(max_length=255)] = None,
) -> CommitListRead:
    """Return one page of a single repository's commits.

    **The path already answers which repository**, so ``repository_id`` is not
    repeated as a query parameter: accepting one would give a client two answers
    to one question and let the two disagree. This is the same read as
    ``GET /developer/commits`` scoped by the path, and the scoping happens in the
    owner-scoped lookup before a single commit row is loaded.

    Errors: 422 for an out-of-range page size; 404 when the repository is not the
    caller's.
    """
    return await developer.commits(
        owner=current_user,
        repository_id=repository_id,
        branch=branch,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/repositories/{repository_id}/branches",
    response_model=BranchListRead,
    summary="The branches the last scan observed",
    dependencies=_ANALYTICS_READ,
)
async def repository_branches(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    repository_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BranchListRead:
    """Return the branches git reported under ``refs/heads``, current first.

    ``is_current`` marks the branch HEAD pointed at and ``is_default`` the one git
    resolved as the repository's default; **on a detached HEAD neither is true**,
    which is a normal state rather than a gap in the data. ``last_committed_at`` is
    the head commit's committer date and not a branch creation date, because git
    does not record the latter and deriving one from the tip would let a two-year-
    old branch read as brand new.

    A branch is listed whether or not its head hash could be read, so a scan that
    could name the branch but not its tip still contributes to the picture.

    Errors: 422 for an out-of-range page size; 404 when the repository is not the
    caller's.
    """
    return await developer.branches(
        owner=current_user, repository_id=repository_id, limit=limit, offset=offset
    )


# ---------------------------------------------------------------------------
# Project integration
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}",
    response_model=ProjectDeveloperRead,
    summary="The developer figures for one project, with its repositories",
    dependencies=_ANALYTICS_READ,
)
async def project_developer(
    current_user: AuthenticatedUser,
    developer: DeveloperIntelligenceServiceDep,
    project_id: uuid.UUID,
    window_days: _WINDOW_DAYS = None,
) -> ProjectDeveloperRead:
    """Return one project's developer view, with the repositories behind it.

    **The repositories are carried alongside the counts** so a project page can
    list them without a second request and cannot show a total that disagrees with
    the rows printed beneath it. ``active_days`` is counted across the project
    rather than summed per repository, because one day carrying commits in three
    repositories is one active day.

    The repositories are those linked *while the project existed*: the foreign key
    is ``ON DELETE SET NULL``, so a repository outlives the project it was
    attached to, and this route only ever describes the ones linked now. It is
    reached through the owner-scoped project lookup, so another account's project
    is a 404 rather than an empty view of somebody's work.

    Errors: 422 when the window exceeds ``developer_max_window_days``; 404 when the
    project is not the caller's or does not exist.
    """
    return await developer.project_view(
        owner=current_user, project_id=project_id, window_days=window_days
    )
