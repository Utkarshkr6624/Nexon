"""Phase 9 endpoints: the career page the user wrote, and what was recorded.

Where the answers live
----------------------
Every question this router could answer — *what is on my profile, how many
records do I have, how much of this evidence did I enter myself* — is answered by
:class:`~app.services.career.service.CareerIntelligenceService`, which owns the
owner-scoped lookups, the upsert, the vocabulary checks and the assembly of the
wire shapes. This file reads a request, picks a status code, and returns what
the service built.

What this layer must not do
----------------------------
**Nothing here may write a qualification.** Not a certification, not an
employer, not a date, not an achievement, not a sentence about the person reading
the page. Every ``title``, ``organisation``, ``summary`` and ``occurred_on`` on
this surface reaches the database exactly as the request body spelled it, and the
whole of this router's design pressure is spent keeping it that way. The write
models carry no ``source`` for the same reason: ``source`` is a column of
``uq_career_evidence_source_identity``, and a client that could write
``source='project'`` on a hand-typed achievement could both impersonate a
subsystem and make two rows collide.

**No route here invents a profile.** ``GET /career/profile`` answers ``200`` with
a ``null`` body for an account that has never written one. That is a cold start,
not a 404: a career record, a repository or a risk are all addressed by an id the
caller supplied, so a miss is a 404, while the profile is not addressed by
anything at all and "you have not written one yet" is a state the UI has to
render. Filling that gap server-side would be the first career row NEXUS wrote.

**No route accepts a user id, and none trusts one in a body.** The caller comes
from the bearer token. Every read and every write resolves its row through an
owner-scoped lookup, so another account's record or piece of evidence answers
**404, not 403** — identically to an id nobody ever issued, which is what keeps
these endpoints from being an existence oracle.

Route order is load-bearing here
--------------------------------
**The literal sub-paths — ``/summary``, ``/profile``, ``/experience``,
``/evidence``, ``/features`` — are declared before
``/career/experience/{experience_id}`` and ``/career/evidence/{evidence_id}``.**
Starlette matches routes in the order they were added and does not prefer a
literal segment over a parameter, so a parameterised route registered first would
bind the literal word ``profile`` to a path parameter, fail its uuid conversion,
and answer with a 422 about an id that never existed — while the page quietly
lost its header. There is no path-conversion trick that makes this unnecessary;
the order is the entire mechanism.

PATCH is a partial edit, not a full replacement
------------------------------------------------
**Every PATCH here applies its body with ``exclude_unset=True``.** ``None``
means "write SQL NULL" downstream, so dumping the whole model would clear the
organisation and the dates of any caller who only meant to correct a typo. A
field the client never sent is not a field the user asked to clear; an explicit
``null`` is, and reaches the service as one. On ``PUT /career/profile`` the same
rule is what stops a caller editing a headline from silently wiping the summary
beneath it.

Permission
----------
**Every route here reuses ``Permission.ANALYTICS_READ``,** the Phase 7, 8 and 9
precedent, and none of them adds a member to
:class:`~app.core.permissions.Permission` — the permission test asserts the full
member set. Entering a certification is the caller answering a question about
rows derived from their own record, so the capability that admits the reading
already admits the answering; a ``career.write`` would be granted to exactly the
roles ``analytics.read`` already covers.

Status codes
------------
``201`` for each of the two creations, because a row was written and a client may
cache its address. ``204`` for each of the two deletes, which is why those routes
carry ``response_class=Response`` and no ``response_model`` — FastAPI forbids a
body on a 204. Everything else is a ``200`` with a body, including the empty
reads and the missing profile: an account with nothing written gets zeroes and
``has_data: false``, which is an absence to explain rather than an error to
report.

Pagination
----------
``limit`` is capped at :data:`MAX_PAGE_SIZE`, and the cap is a **rejection rather
than a silent truncation** — ``?limit=500`` is a 422, because a caller that asked
for 500 and received 200 cannot tell a truncated page from a page that was always
200 rows long. Every list here filters on the server, so ``total`` and the tallies
beside it describe the whole matching set rather than the page the caller happens
to be holding.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, CareerIntelligenceServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.career import (
    CareerEvidenceListRead,
    CareerEvidenceRead,
    CareerEvidenceUpdate,
    CareerEvidenceWrite,
    CareerExperienceListRead,
    CareerExperienceRead,
    CareerExperienceUpdate,
    CareerExperienceWrite,
    CareerFeatureVectorRead,
    CareerProfileRead,
    CareerProfileWrite,
    CareerSummaryRead,
)

__all__ = ["router"]

router = APIRouter(prefix="/career", tags=["career"])

#: The page size a caller gets when it names none. The same number
#: :class:`~app.services.career.service.CareerIntelligenceService` defaults to, so
#: the router's fallback and the storage layer's are one value rather than two
#: that can drift apart.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for, and deliberately the same ceiling the
#: service clamps to. A smaller number here would make a legal request look
#: refused for a reason the client cannot discover; a larger one would only be
#: clipped silently further down.
MAX_PAGE_SIZE = 200

#: Applied to every route including the two writes. Phase 9 reuses
#: ``analytics.read`` rather than coining a ``career.write``; see this module's
#: docstring for why, and :mod:`app.api.v1.developer` for the same argument made
#: one phase earlier.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: The window length a window-reading route accepts. It is nullable because an
#: omitted window is **not** the same request as a default one: it asks the server
#: for ``learning_default_window_days`` rather than for a figure this file
#: invented, so a card cannot be rendered for a range the backend never used.
#:
#: The bounds are the *learning* ones on purpose. The frozen configuration defines
#: no career-specific window, so the two pages share one window vocabulary rather
#: than declaring two ceilings for the same question. Only the lower bound is
#: declared here; the ceiling is ``learning_max_window_days``, a setting the
#: service owns.
_WINDOW_DAYS = Annotated[int | None, Query(ge=1, description="Length of the window in days.")]

#: One message for a row that is not the caller's and for one that was never
#: issued. The service raises these; the constants are repeated here so a test can
#: assert both answers are identical and so the two cases are visibly one case
#: rather than two that happened to match.
_EXPERIENCE_NOT_FOUND = "That career record was not found."
_EVIDENCE_NOT_FOUND = "That career evidence was not found."


# ---------------------------------------------------------------------------
# Dashboard reads — the literal sub-paths, declared first on purpose
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=CareerSummaryRead,
    summary="Headline counts over a window, and one factual sentence",
    dependencies=_ANALYTICS_READ,
)
async def career_summary(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> CareerSummaryRead:
    """Return the account-wide counts the career header is built from.

    **Counts only.** There is no score, no rank and no "profile strength" here,
    and no ordering of evidence by importance: anything that ordered a person's
    evidence by weight would be a judgement about them that no column in this
    schema could justify.

    ``manual_evidence_count`` is the one provenance figure the summary states,
    because it separates what the person wrote from what the system observed — and
    it is the honest answer to "how much of this page is mine".

    ``has_data`` is the cold-start flag. False on an account with no profile, no
    records and no evidence, so a page of zeroes reads as an absence rather than
    as a finding about the person who owns the account.

    Errors: 422 when the window exceeds ``learning_max_window_days``.
    """
    return await career.summary(owner=current_user, window_days=window_days)


@router.get(
    "/profile",
    response_model=CareerProfileRead | None,
    summary="The one profile this account has, or null before it has one",
    dependencies=_ANALYTICS_READ,
)
async def get_career_profile(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
) -> CareerProfileRead | None:
    """Return the account's profile, or ``null`` when it has never been written.

    **A cold start is a 200, not a 404.** Every other row in this API is
    addressed by an id the caller supplied, so a miss is a not-found; the profile
    is addressed by nothing, and an account that has never written one is in a
    state the UI has to render rather than an absence it has to explain.

    **The service is never asked to fill the gap.** There is no generated
    profile, no stub headline and no placeholder summary: an empty profile invented
    here would be the first career row NEXUS wrote, and rule 2 of this phase is
    that nothing on this surface originates with the system.
    """
    return await career.get_profile(owner=current_user)


@router.put(
    "/profile",
    response_model=CareerProfileRead,
    summary="Write the one profile this account has, creating it if absent",
    dependencies=_ANALYTICS_READ,
)
async def upsert_career_profile(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    payload: CareerProfileWrite,
) -> CareerProfileRead:
    """Upsert the profile on the unique ``user_id``.

    **A ``PUT`` because ``career_profiles.user_id`` is unique**, so this can only
    ever be a revision. There is no create-and-keep-the-old path, because a second
    profile would be a second *answer* to every question about this person's
    career and the read would then have to decide which one wins.

    **The mapping is built with ``exclude_unset=True``**, so an omitted field
    leaves the column alone and an explicit ``null`` clears it. Editing a headline
    must not silently blank the summary written underneath it. ``links`` is
    replaced wholesale rather than merged — an empty list is the user having
    supplied no URLs, which is an answer rather than a mistake.

    Every writable field is something the user typed. There is no field NEXUS may
    fill in on this route, and no ``user_id`` in the body: ownership comes from the
    session and the upsert key is the caller's own id.

    Errors: 422 when the body names no field at all.
    """
    return await career.upsert_profile(
        owner=current_user,
        values=payload.model_dump(exclude_unset=True),
    )


# ---------------------------------------------------------------------------
# The dated records
# ---------------------------------------------------------------------------


@router.get(
    "/experience",
    response_model=CareerExperienceListRead,
    summary="Education, experience and certifications, with the shape tally beside them",
    dependencies=_ANALYTICS_READ,
)
async def list_career_experience(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[str | None, Query()] = None,
) -> CareerExperienceListRead:
    """Return one page of the caller's dated career records.

    **The three shapes share one table and are separated by ``kind`` alone** —
    education, work experience and certifications. ``by_kind`` carries all three
    keys zeroed where nothing was found, so a client reading
    ``by_kind.certification`` never meets a missing key and quietly reports the
    same number as an empty section.

    ``current_count`` is the other half of the date story: records with no end
    date, which is how the profile says "this is ongoing". Every count here
    describes **every matching record**, not this page.

    Errors: 422 for an out-of-range page size or a ``kind`` outside the closed
    vocabulary.
    """
    return await career.list_experience(owner=current_user, kind=kind, limit=limit, offset=offset)


@router.post(
    "/experience",
    response_model=CareerExperienceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record one dated line the user supplied",
    dependencies=_ANALYTICS_READ,
)
async def create_career_experience(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    payload: CareerExperienceWrite,
) -> CareerExperienceRead:
    """Store one dated record, verbatim.

    **Every value is a transcription.** The ``title`` the user wrote, the
    ``organisation`` they named or left null, the dates they gave. There is no
    lookup behind any of them and none may be added, so ``organisation`` being
    null is the normal case for a self-directed project rather than a gap to be
    filled in later.

    ``kind`` and ``title`` are the honest minimum for a real record, and both are
    required. Requiring the dates would exclude the open-ended degree and the
    self-directed course, which are both common and both true; ``ended_on`` being
    absent is how an ongoing record says so.

    Errors: 422 for a ``kind`` outside the vocabulary, an empty ``title``, or an
    ``ended_on`` before ``started_on``.
    """
    return await career.create_experience(
        owner=current_user,
        kind=payload.kind,
        title=payload.title,
        organisation=payload.organisation,
        started_on=payload.started_on,
        ended_on=payload.ended_on,
        description=payload.description,
        url=payload.url,
    )


@router.patch(
    "/experience/{experience_id}",
    response_model=CareerExperienceRead,
    summary="Correct a dated record — a correction, not a re-filing",
    dependencies=_ANALYTICS_READ,
)
async def update_career_experience(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    experience_id: uuid.UUID,
    payload: CareerExperienceUpdate,
) -> CareerExperienceRead:
    """Change only what the caller actually set.

    **The mapping is built with ``exclude_unset=True`` on purpose**, so an omitted
    key leaves the column alone and an explicit null clears it — which is how a
    self-directed entry stays honest instead of being padded with an organisation
    somebody did not attend.

    **The date check runs against the merged record**, not against this payload: a
    patch supplying only ``ended_on`` would otherwise sail past a check on its own
    values and land a range that runs backwards against the stored start date.

    ``created_at`` is not present at all, and unknown fields are refused rather
    than dropped, so "that field is not editable here" is a 422 naming the field
    rather than a cheerful 200 that discarded it.

    Errors: 422 when the payload changes no field or would leave ``ended_on``
    before ``started_on``; 404 when the record is not the caller's.
    """
    return await career.update_experience(
        owner=current_user,
        experience_id=experience_id,
        values=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/experience/{experience_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove one dated record, keeping the claims that named it",
    dependencies=_ANALYTICS_READ,
)
async def delete_career_experience(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    experience_id: uuid.UUID,
) -> Response:
    """Remove one dated record.

    **The evidence rows that may name this record are not swept.** Those foreign
    keys are ``SET NULL``, so a claim the user made survives as their own claim
    with the pointer dropped — a delete that swept the children would be a delete
    that believed the evidence was a view rather than a trail.

    Errors: 404 when the record is not the caller's or does not exist.
    """
    await career.delete_experience(owner=current_user, experience_id=experience_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# The evidence
# ---------------------------------------------------------------------------


@router.get(
    "/evidence",
    response_model=CareerEvidenceListRead,
    summary="Evidence of work, with the type and provenance tallies beside it",
    dependencies=_ANALYTICS_READ,
)
async def list_career_evidence(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    evidence_type: Annotated[str | None, Query()] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    skill_id: Annotated[uuid.UUID | None, Query()] = None,
    repository_id: Annotated[uuid.UUID | None, Query()] = None,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> CareerEvidenceListRead:
    """Return one page of career evidence, newest first.

    **Every filter narrows ``total`` as well as ``items``,** which is why they
    live on the server: a client-side filter can only see the rows the current
    page carries, so it can neither count a band nor offer a pager for one. The
    ``by_type`` tally carries all seven types zeroed where nothing was found;
    ``by_source`` is deliberately **not** completed, because that vocabulary is
    open and a subsystem that has never run has no band to zero-fill.

    ``manual_count`` describes every matching row and is the figure worth stating
    next to a career profile: it is what separates what the person wrote from what
    the system observed.

    The date bounds are half-open — ``since`` inclusive, ``until`` exclusive — so
    evidence dated on the boundary belongs to one window rather than two.

    Errors: 422 for an out-of-range page size or an ``evidence_type`` outside the
    closed vocabulary.
    """
    return await career.list_evidence(
        owner=current_user,
        evidence_type=evidence_type,
        project_id=project_id,
        skill_id=skill_id,
        repository_id=repository_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/evidence",
    response_model=CareerEvidenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enter one thing the user wants to be able to point at",
    dependencies=_ANALYTICS_READ,
)
async def create_career_evidence(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    payload: CareerEvidenceWrite,
) -> CareerEvidenceRead:
    """Store one piece of evidence, in the user's words and dated by the user.

    **Every word of this payload is the caller's.** NEXUS supplies no title, no
    date, no employer and no credential, and the payload carries no ``source``:
    that column is part of the row's uniqueness key, and a client that could write
    it could both impersonate a subsystem and defeat the deduplication constraint
    that keeps derived evidence from being inserted twice.

    Every ``*_id`` names a record in this account that already exists — NEXUS
    creates none of them — and all three may be omitted, which is exactly what a
    hand-written achievement looks like. **A supplied id belonging to another
    account is a 404, never a 403**, because a 403 would confirm it exists.

    ``occurred_on`` is required by the service: undated evidence cannot be placed
    in a timeline, and the placeholder that would make it renderable would be a
    date nobody gave.

    Errors: 422 for an ``evidence_type`` outside the vocabulary, an empty title, or
    a missing date; 404 when a supplied project, skill or repository is not the
    caller's; 409 at ``career_max_evidence`` or when a row with the same identity
    already exists.
    """
    return await career.create_evidence(
        owner=current_user,
        evidence_type=payload.evidence_type,
        title=payload.title,
        occurred_on=payload.occurred_on,
        description=payload.description,
        project_id=payload.project_id,
        skill_id=payload.skill_id,
        repository_id=payload.repository_id,
    )


@router.patch(
    "/evidence/{evidence_id}",
    response_model=CareerEvidenceRead,
    summary="Correct the words and the date — never the provenance",
    dependencies=_ANALYTICS_READ,
)
async def update_career_evidence(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    evidence_id: uuid.UUID,
    payload: CareerEvidenceUpdate,
) -> CareerEvidenceRead:
    """Correct one evidence row, and only what the user may correct.

    **The mapping is built with ``exclude_unset=True``**, so an omitted key leaves
    the column alone and an explicit null clears it.

    **``source`` and the three ``*_id`` pointers are absent from the payload**, and
    each absence is a rule: provenance is part of the row's identity, and
    re-pointing it at a different project would let a rename become a second
    record — the exact failure ``uq_career_evidence_source_identity`` exists to
    prevent. The person is allowed to be wrong about what they wrote, and not
    about where it came from.

    An explicit ``occurred_on: null`` is refused rather than stored: undated
    evidence cannot be placed in a timeline, and the placeholder that would make it
    renderable would be a date the user never gave.

    Errors: 422 when the payload changes no field or clears the date; 404 when the
    row is not the caller's; 409 when the edit would collide with another row's
    identity.
    """
    return await career.update_evidence(
        owner=current_user,
        evidence_id=evidence_id,
        values=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/evidence/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove one piece of evidence, or nothing at all",
    dependencies=_ANALYTICS_READ,
)
async def delete_career_evidence(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    evidence_id: uuid.UUID,
) -> Response:
    """Remove one evidence row.

    Another account's id deletes nothing and reports the same not-found an
    unknown id does.

    Errors: 404 when the row is not the caller's or does not exist.
    """
    await career.delete_evidence(owner=current_user, evidence_id=evidence_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Features — extracted, never modelled
# ---------------------------------------------------------------------------


@router.get(
    "/features",
    response_model=CareerFeatureVectorRead,
    summary="The ML-ready feature vector, stamped with its schema version",
    dependencies=_ANALYTICS_READ,
)
async def career_features(
    current_user: AuthenticatedUser,
    career: CareerIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> CareerFeatureVectorRead:
    """Return named numbers under ``career_features.v1``.

    **An extractor, not a model.** Nothing here is trained, loaded, served or
    inferred, and no client may join these features into a forecast about
    somebody's employability: Phase 10 does that, and ``schema_version`` is the
    contract with whatever consumes it later.

    ``project_activity`` is the contract's own worked example of the null-not-zero
    rule: it is **null** when no repository has ever been scanned, because ``0``
    would assert that a repository exists and carries no commits when the truth is
    that nobody has looked. The five figures beside it are counts of the caller's
    own rows, so a genuine zero survives as a measurement.

    Errors: 422 when the window exceeds ``learning_max_window_days``.
    """
    return await career.features(owner=current_user, window_days=window_days)
