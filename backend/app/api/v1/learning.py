"""Phase 9 endpoints: what the caller meant to learn, and what they recorded.

Where the answers live
----------------------
Every question this router could answer — *how many goals are open, how far is
this skill from the level the caller set, what has actually been recorded in the
last thirty days* — is answered by
:class:`~app.services.learning.service.LearningIntelligenceService`, which owns
the owner-scoped predicates, the caps, the vocabulary checks and the assembly of
the wire shapes. This file reads a request, picks a status code, and returns what
the service built. It computes no figure and composes no sentence: a gap
explanation or a metric sentence that originated here would be a sentence no
assertion anywhere could check.

What this layer must not do
----------------------------
**No route here invents a level, and none of them may gain a field that would
let one.** A ``current_level`` is either the caller's claim, written with
``level_source='user_defined'``, or an estimate the read path derived and
labelled. ``POST /learning/skills`` therefore records a supplied level as the
user's and nothing else — a client cannot file its own inference as a
self-assessment — and ``PATCH /learning/skills/{skill_id}`` cannot move
``evidence_count`` or ``last_activity_at``, because those are NEXUS's own
observations and a skill that could claim six recorded sessions that do not exist
would then have that claim quoted back as the evidence behind its level.

**No route takes a user id.** Not as a path segment, not as a query parameter,
not in a body. The caller comes from the bearer token; the service resolves every
goal, skill and activity through an owner-scoped lookup and simply never loads a
row that belongs to somebody else. Another account's row therefore answers
**404, not 403** — identically to an id nobody ever issued, so these routes
cannot be used to learn which ids are real.

Route order is load-bearing here
--------------------------------
**The five literal sub-paths are declared before every parameterised route.**
Starlette matches routes in the order they were added and does not prefer a
literal segment over a parameter, so a ``/learning/goals/{goal_id}`` registered
above them would bind the literal string ``summary`` to the path parameter, fail
its uuid conversion, and answer with a 422 about an id that never existed — while
the dashboard tile quietly lost its data. There is no path-conversion trick that
makes this unnecessary and no ``Path`` annotation that fixes it; the order is the
entire mechanism.

The reads are named for what they are
-------------------------------------
``GET /learning/activity`` calls ``LearningIntelligenceService.read_activity``,
**not** ``activity``. The service's ``self.activity`` is its optional history
sink, so a method of that name would be shadowed by the attribute and the read
would silently return ``None`` behind a route that still returned 200.

Permission
----------
**Every route here reuses ``Permission.ANALYTICS_READ``**, the Phase 7 and
Phase 8 precedent, and none of them adds a member to
:class:`~app.core.permissions.Permission` — the permission test asserts the full
member set. The argument is the one :mod:`app.api.v1.developer` makes: writing a
goal here is the caller answering a question about rows derived from their own
record, so the capability that admits the reading already admits the answering. A
``learning.write`` would be granted to exactly the roles ``analytics.read``
already covers — the role table has no entry for either — while adding a member
the permission test asserts the complete set of.

PATCH is a partial edit, not a full replacement
------------------------------------------------
**Every PATCH here applies its body with ``exclude_unset=True``.** ``None``
means "write SQL NULL" downstream, so dumping the whole model would clear the
description, the project link and the target skill of any caller who only meant
to rename something. A field the client never sent is not a field the user asked
to clear; an explicit ``null`` is, and reaches the service as one.

Status codes
------------
``201`` for each of the three creations, because a row was written and a client
may cache its address. ``204`` for each of the three deletes, which is why those
routes carry ``response_class=Response`` and no ``response_model`` — FastAPI
refuses a body on a 204. Everything else is a ``200`` with a body, including the
empty reads: an account with nothing recorded gets zeroes and ``has_data:
false``, which is an absence to explain rather than an error to report.

Pagination
----------
``limit`` is capped at :data:`MAX_PAGE_SIZE`, and the cap is a **rejection rather
than a silent truncation** — ``?limit=500`` is a 422, because a caller that asked
for 500 and received 200 cannot tell a truncated page from a page that was always
200 rows long. The ceiling matches the service's own clamp rather than
introducing a second, smaller number that would make a legal request look
refused.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    AuthenticatedUser,
    LearningIntelligenceServiceDep,
    RecommendationServiceDep,
)
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.learning import (
    LearningActivityListRead,
    LearningActivityRead,
    LearningActivitySeriesRead,
    LearningActivityWrite,
    LearningFeatureVectorRead,
    LearningGoalListRead,
    LearningGoalRead,
    LearningGoalUpdate,
    LearningGoalWrite,
    LearningMetricRead,
    LearningSummaryRead,
    SkillGapListRead,
    SkillListRead,
    SkillRead,
    SkillUpdate,
    SkillWrite,
)
from app.schemas.recommendation import RecommendationRead

__all__ = ["router"]

router = APIRouter(prefix="/learning", tags=["learning"])

#: The page size a caller gets when it names none. The same number
#: :class:`~app.services.learning.service.LearningIntelligenceService` defaults
#: to, so the router's fallback and the storage layer's are one value rather than
#: two that can drift apart.
DEFAULT_PAGE_SIZE = 50

#: The largest page any caller may ask for, and deliberately the same ceiling the
#: service clamps to. A smaller number here would make a legal request look
#: refused for a reason the client cannot discover; a larger one would only be
#: clipped silently further down.
MAX_PAGE_SIZE = 200

#: Applied to every route including the six writes. Phase 9 reuses
#: ``analytics.read`` rather than coining a ``learning.write``; see this module's
#: docstring for why, and :mod:`app.api.v1.developer` for the same argument made
#: one phase earlier.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: The window length a window-reading route accepts. It is nullable because an
#: omitted window is **not** the same request as a default one: it asks the
#: server for ``learning_default_window_days`` rather than for a figure this file
#: invented, so a chart cannot be rendered for a range the backend never used.
#:
#: Only the *lower* bound is declared here. The ceiling is
#: ``learning_max_window_days``, a setting the service owns, and a constant in
#: this file would answer 422 against a limit the deployment has raised.
_WINDOW_DAYS = Annotated[int | None, Query(ge=1, description="Length of the window in days.")]

#: A goal arrives at the floor of its own progress scale unless the caller
#: asserted something else. The write model makes ``progress`` optional so a goal
#: can be recorded as a bare intention, and "did not state a progress" has to
#: arrive at the database as ``0`` rather than as SQL ``NULL`` against a
#: ``NOT NULL`` column: the column's own default is the honest answer for a goal
#: that demonstrably has not started.
_DEFAULT_GOAL_PROGRESS = 0

#: One message for a row that is not the caller's and for one that was never
#: issued. The service raises these; the constants are repeated here so a test can
#: assert both answers are identical and so the two cases are visibly one case
#: rather than two that happened to match.
_GOAL_NOT_FOUND = "That learning goal was not found."
_SKILL_NOT_FOUND = "That skill was not found."


# ---------------------------------------------------------------------------
# Dashboard reads — the five literal sub-paths, declared first on purpose
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=LearningSummaryRead,
    summary="Headline counts over a window, and one factual sentence",
    dependencies=_ANALYTICS_READ,
)
async def learning_summary(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> LearningSummaryRead:
    """Return the account-wide counts the learning header is built from.

    **Counts only, and the window they cover is carried beside them.** There is
    no score on this surface and no comparison rendered as a verdict:
    ``activities_in_window`` means "how many activities were recorded", and
    ``window_days`` is returned so that any sentence a client writes about it can
    name the range rather than implying a period nobody specified.

    ``minutes_in_window`` is null when nothing in the window carried a duration,
    because ``0`` would claim time was measured and found to be nothing.
    ``has_data`` is the cold-start flag: false when nothing has ever been
    recorded, so a dashboard of zeroes reads as an absence rather than as a
    finding about the account.

    Errors: 422 when the window exceeds ``learning_max_window_days``.
    """
    return await learning.summary(owner=current_user, window_days=window_days)


@router.get(
    "/metrics",
    response_model=list[LearningMetricRead],
    summary="The eight metrics, each with its definition and its figures",
    dependencies=_ANALYTICS_READ,
)
async def learning_metrics(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> list[LearningMetricRead]:
    """Return all eight metrics over one window, fully explained.

    **Always eight elements, never fewer.** A metric the data cannot support comes
    back with ``available: false``, ``value: null`` and a reason rather than being
    dropped — a client indexing by ``key`` would otherwise meet a hole where a card
    belongs and would have to guess whether the card was absent or merely
    unmeasured. A *measured* zero is a different answer and keeps both its value
    and ``available: true``: "nothing was recorded in these thirty days" is a true
    sentence and survives the trip to the screen.

    No metric here measures ability. Each carries ``unit`` as data rather than
    decoration, so a duration is not formatted as a count, and each
    ``explanation`` repeats its definition with the figures in it.

    Errors: 422 when the window exceeds ``learning_max_window_days``.
    """
    return list(await learning.metrics(owner=current_user, window_days=window_days))


@router.get(
    "/gaps",
    response_model=SkillGapListRead,
    summary="Every skill's distance from its target, computed on read",
    dependencies=_ANALYTICS_READ,
)
async def learning_gaps(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SkillGapListRead:
    """Return one page of skill gaps, widest first.

    **A gap is computed on read and never stored**, so this route cannot disagree
    with a skill card built from the same rows: there is no second answer to drift.
    The arithmetic lives in :mod:`app.services.learning.gaps`, where a gap cannot
    be constructed from an explanation that omits the levels and the evidence
    count.

    **A measured zero and an unmeasured gap are different answers.** A skill whose
    target is already met reports ``gap=0`` with ``available: true``, and that is
    the most reassuring thing this page can say. A skill with nothing recorded
    reports ``available: false`` with a reason, because "no evidence" is not "no
    gap". Both counts come back beside the rows so a client can say "5 gaps, 1 not
    measured yet" rather than silently dropping the row it could not measure.

    Errors: 422 for an out-of-range page size.
    """
    return await learning.gaps(owner=current_user, limit=limit, offset=offset)


@router.get(
    "/activity",
    response_model=LearningActivitySeriesRead,
    summary="Recorded activities bucketed by day, week or month, gaps included",
    dependencies=_ANALYTICS_READ,
)
async def learning_activity(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
    granularity: Annotated[str | None, Query()] = None,
    skill_id: Annotated[uuid.UUID | None, Query()] = None,
    goal_id: Annotated[uuid.UUID | None, Query()] = None,
) -> LearningActivitySeriesRead:
    """Return the activity series, one bucket per period across the whole range.

    The buckets are **dense**. A quiet Tuesday arrives carrying ``activities: 0``
    rather than being skipped, because a series that omitted empty buckets
    compresses the timeline and makes a sparse fortnight read as dense as a busy
    one — a misreading of the data rather than a presentational choice.

    ``sessions`` counts the ``study_session`` activities separately from the raw
    total, because "6 sessions" and "6 activities" answer different questions and
    a chart showing only the second would overstate a fortnight of page views.

    This calls ``read_activity`` rather than ``activity``: the service's
    ``self.activity`` is its event sink, and a method of that name would be
    shadowed by the attribute.

    Errors: 422 for an unsupported granularity or an over-long window; 404 when
    ``skill_id`` or ``goal_id`` is not the caller's.
    """
    return await learning.read_activity(
        owner=current_user,
        window_days=window_days,
        granularity=granularity,
        skill_id=skill_id,
        goal_id=goal_id,
    )


@router.get(
    "/features",
    response_model=LearningFeatureVectorRead,
    summary="The ML-ready feature vector, stamped with its schema version",
    dependencies=_ANALYTICS_READ,
)
async def learning_features(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    window_days: _WINDOW_DAYS = None,
) -> LearningFeatureVectorRead:
    """Return named numbers under ``learning_features.v1``.

    **An extractor, not a model.** Nothing here is trained, loaded, served or
    inferred, and no client may join these features into a prediction: Phase 10
    does that, and ``schema_version`` is what tells a later trainer what the
    columns meant when this row was produced.

    **A figure that could not be computed is ``null``, never ``0``.** An account
    with no goals does not have goals that are zero percent complete; an empty
    completion-rate denominator is not a completion rate of 0.0; and nothing
    recorded in the window is not a measured consistency of zero. Inside a
    training matrix a fabricated zero is indistinguishable from an observed one.

    Errors: 422 when the window exceeds ``learning_max_window_days``.
    """
    return await learning.features(owner=current_user, window_days=window_days)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.get(
    "/goals",
    response_model=LearningGoalListRead,
    summary="Recorded goals, with the state tally beside the rows",
    dependencies=_ANALYTICS_READ,
)
async def list_learning_goals(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query()] = None,
    target_skill_id: Annotated[uuid.UUID | None, Query()] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    target_after: Annotated[date | None, Query()] = None,
    target_before: Annotated[date | None, Query()] = None,
) -> LearningGoalListRead:
    """Return one page of the caller's goals.

    **Every filter narrows ``total`` as well as ``items``,** which is why they
    live on the server: a client-side filter can only see the rows the current
    page happens to carry, so it can neither count a band nor offer a pager for
    one. The ``by_status`` tally describes every matching goal rather than this
    page, so a header cannot report fewer completed goals on page two than on
    page one.

    A goal with no ``target_date`` is **excluded** by either date bound rather
    than being treated as due on the epoch, and a goal naming a topic before the
    account tracks a skill carries no ``target_skill_id`` and is excluded by that
    filter rather than folded into it.

    Errors: 422 for an out-of-range page size or a vocabulary outside
    ``LearningGoalStatus``.
    """
    return await learning.list_goals(
        owner=current_user,
        status=status,
        target_skill_id=target_skill_id,
        project_id=project_id,
        target_after=target_after,
        target_before=target_before,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/goals",
    response_model=LearningGoalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record one thing the user said they meant to learn",
    dependencies=_ANALYTICS_READ,
)
async def create_learning_goal(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    payload: LearningGoalWrite,
) -> LearningGoalRead:
    """Store one intention, with every pointer proved first.

    **Every ``target_skill_id``, ``project_id`` and ``note_id`` is proved to
    belong to the caller before the write.** One naming another account's row is a
    not-found, identically to an id nobody has issued — a 403 would confirm the id
    exists, and the learning page is not a directory of other people's notes.

    ``title`` is the only required field. A goal with nothing else on it — "I
    want to learn Rust" — is a legitimate first record, and forcing a deadline or
    an estimate up front would put a form in front of an intention.

    ``progress`` and ``estimated_effort_minutes`` are **the user's** figures and
    are stored exactly as given, or at the floor when they gave none. Nothing here
    sums activities into a progress bar: a study session and a goal are different
    units, and deriving one from the other is the first step towards NEXUS
    claiming to know whether somebody learned something.

    Errors: 409 when the account already holds ``learning_max_goals`` goals; 404
    when a supplied skill, project or note is not this account's; 422 for a
    vocabulary or bounded value outside its range.
    """
    return await learning.create_goal(
        owner=current_user,
        title=payload.title,
        description=payload.description,
        target_skill_id=payload.target_skill_id,
        target_topic=payload.target_topic,
        target_date=payload.target_date,
        priority=payload.priority,
        status=payload.status,
        progress=(payload.progress if payload.progress is not None else _DEFAULT_GOAL_PROGRESS),
        estimated_effort_minutes=payload.estimated_effort_minutes,
        project_id=payload.project_id,
        note_id=payload.note_id,
    )


@router.get(
    "/goals/{goal_id}",
    response_model=LearningGoalRead,
    summary="One goal, resolved through an owner-scoped read",
    dependencies=_ANALYTICS_READ,
)
async def get_learning_goal(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    goal_id: uuid.UUID,
) -> LearningGoalRead:
    """Return one goal.

    **404 for another account's goal, never 403.** The row is never loaded if it
    is not the caller's, so this route cannot be used to learn which goal ids
    exist — which is why :data:`_GOAL_NOT_FOUND` is the same sentence for a
    foreign id and for one nobody ever issued.
    """
    return await learning.get_goal(owner=current_user, goal_id=goal_id)


@router.patch(
    "/goals/{goal_id}",
    response_model=LearningGoalRead,
    summary="Edit a goal — never its completion stamp",
    dependencies=_ANALYTICS_READ,
)
async def update_learning_goal(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    goal_id: uuid.UUID,
    payload: LearningGoalUpdate,
) -> LearningGoalRead:
    """Change only the fields the caller actually set.

    **The mapping is built with ``exclude_unset=True`` on purpose.** ``None``
    means "write SQL NULL" downstream, so dumping the whole model would clear the
    description and the project link of any caller who only meant to rename a
    goal. A field the client never sent is not a field the user asked to clear; an
    explicit ``null`` is, and reaches the service as one.

    **``completed_at`` is absent from the payload, and that is the design.** It is
    a completion stamp with exactly one producer —
    ``POST /learning/goals/{goal_id}/complete`` — which is what keeps "when did
    they finish this" a fact with one writer rather than two that can disagree.

    Errors: 422 when the payload changes no field or names one the goal may not
    write; 404 when the goal is not the caller's or a supplied skill, project or
    note belongs to somebody else.
    """
    return await learning.update_goal(
        owner=current_user,
        goal_id=goal_id,
        values=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/goals/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a goal, and keep the record that it was worked on",
    dependencies=_ANALYTICS_READ,
)
async def delete_learning_goal(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    goal_id: uuid.UUID,
) -> Response:
    """Remove one goal.

    **The recorded activities survive it**, through the schema's
    ``ON DELETE SET NULL``. Deleting the intention must not delete the evidence: a
    skill's evidence count is a history, and a user who abandons a goal has not
    unlearned anything.

    ``204`` rather than a body, which is why this route alone carries
    ``response_class=Response`` and no ``response_model``: FastAPI forbids a
    response body on a 204, and every delete in this API answers the same way.

    Errors: 404 when the goal is not the caller's or does not exist.
    """
    await learning.delete_goal(owner=current_user, goal_id=goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/goals/{goal_id}/complete",
    response_model=LearningGoalRead,
    summary="Mark one goal finished — stamped from the database clock",
    dependencies=_ANALYTICS_READ,
)
async def complete_learning_goal(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    goal_id: uuid.UUID,
) -> LearningGoalRead:
    """Set the status, stamp ``completed_at`` and raise progress to 100, together.

    Three things happen in one call because they are one fact. The database's
    check constraint holds the first two together, and a caller doing them in
    separate requests would leave a window in which the row claimed neither or
    both.

    **The instant comes from the database clock**, not from the request's: the
    server's clock is not evidence of when the user finished, and a body
    carrying a ``completed_at`` could carry any instant at all. Completing an
    already-completed goal is not an error; it re-stamps the row the caller named.

    Errors: 404 when the goal is not the caller's or does not exist.
    """
    return await learning.complete_goal(owner=current_user, goal_id=goal_id)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get(
    "/skills",
    response_model=SkillListRead,
    summary="Tracked skills, with the claimed-versus-estimated tally beside them",
    dependencies=_ANALYTICS_READ,
)
async def list_skills(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    category: Annotated[str | None, Query(max_length=64)] = None,
) -> SkillListRead:
    """Return one page of the caller's skills.

    ``by_level_source`` is the tally worth carrying beside the rows: it answers
    "how much of this page is NEXUS's opinion rather than the user's", which is a
    question the design is obliged to make answerable rather than to make go away.
    ``by_category`` is **not** completed, because the category vocabulary is
    deliberately open and zero-filling it would invent groupings nobody used.

    ``skills_with_evidence`` and ``skills_without_evidence`` describe every
    matching skill, so a header can say how much of the page is a name rather
    than a history without counting a page slice.

    Errors: 422 for an out-of-range page size.
    """
    return await learning.list_skills(
        owner=current_user, category=category, limit=limit, offset=offset
    )


@router.post(
    "/skills",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
    summary="Track one skill, at the level the caller says they are at",
    dependencies=_ANALYTICS_READ,
)
async def create_skill(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    payload: SkillWrite,
) -> SkillRead:
    """Store one tracked skill, and record who claimed its level.

    **A level sent by a client is the client's claim**, so the row is always
    written with ``level_source='user_defined'``. A caller cannot file its own
    inference as a self-assessment, and it cannot create a skill already carrying
    a ``system_estimate`` whose evidence has not been recorded yet — the one level
    this schema lets NEXUS derive, and the read path decides when it has earned
    one.

    ``current_level`` and ``target_level`` are optional and default to the schema's
    floor and three. A caller who wants a level NEXUS derived does not set one
    here, and NEXUS never raises a target: raising it would be an opinion about
    what the user should want.

    Errors: 409 when this account already tracks that name, or has reached
    ``learning_max_skills``; 422 for a level outside 1-5.
    """
    return await learning.create_skill(
        owner=current_user,
        name=payload.name,
        category=payload.category,
        description=payload.description,
        current_level=payload.current_level,
        target_level=payload.target_level,
    )


@router.get(
    "/skills/{skill_id}",
    response_model=SkillRead,
    summary="One skill, with the provenance of its level",
    dependencies=_ANALYTICS_READ,
)
async def get_skill(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    skill_id: uuid.UUID,
) -> SkillRead:
    """Return one skill.

    **``level_source`` travels with ``current_level``**, and a client that renders
    the number without it would be rendering "you are not good at X" rather than
    "your self-assessed level is 2/5". 404 for another account's skill, never 403.
    """
    return await learning.get_skill(owner=current_user, skill_id=skill_id)


@router.patch(
    "/skills/{skill_id}",
    response_model=SkillRead,
    summary="Edit a skill's claim about itself, and nothing about its evidence",
    dependencies=_ANALYTICS_READ,
)
async def update_skill(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    skill_id: uuid.UUID,
    payload: SkillUpdate,
) -> SkillRead:
    """Change a skill's label, note, category or levels.

    **The mapping is built with ``exclude_unset=True`` on purpose**: an omitted key
    leaves the column alone and an explicit ``null`` clears it, which is the only
    reading under which a PATCH can both leave a description alone and blank it.

    **``evidence_count``, ``last_activity_at``, ``confidence`` and
    ``level_source`` are absent from the payload, and each absence is a rule.**
    The first two are NEXUS's own observations — a PATCH that could move them
    would let a skill claim six recorded study sessions that do not exist, which
    the gap read would then quote as the evidence behind a level. The last two
    would let a client file its own inference as a self-assessment.

    Sending ``current_level`` re-records the source as ``user_defined``, because
    the person is the one making the claim now. A level that stayed
    ``system_estimate`` after the user re-asserted it would keep crediting NEXUS
    for a number the user just typed.

    Errors: 422 when the payload changes no field or names one the skill may not
    write; 404 when the skill is not the caller's.
    """
    return await learning.update_skill(
        owner=current_user,
        skill_id=skill_id,
        values=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a skill and everything recorded against it",
    dependencies=_ANALYTICS_READ,
)
async def delete_skill(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    skill_id: uuid.UUID,
) -> Response:
    """Remove one skill.

    **The activities cascade.** One left pointing at a skill that no longer exists
    would sit in no skill's evidence count and no page would ever show it. The
    user's levels go with it, and any career evidence that named the skill
    survives as the user's own claim with the pointer dropped — every foreign key
    onto ``skills`` is ``SET NULL`` precisely so that a claim outlives the thing
    it claims.

    Errors: 404 when the skill is not the caller's or does not exist.
    """
    await learning.delete_skill(owner=current_user, skill_id=skill_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Activities — the evidence, append-only
# ---------------------------------------------------------------------------


@router.get(
    "/activities",
    response_model=LearningActivityListRead,
    summary="The recorded evidence, newest first, with the type tally beside it",
    dependencies=_ANALYTICS_READ,
)
async def list_learning_activities(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    skill_id: Annotated[uuid.UUID | None, Query()] = None,
    goal_id: Annotated[uuid.UUID | None, Query()] = None,
    activity_type: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> LearningActivityListRead:
    """Return one page of recorded learning activities.

    The window is **half-open** — ``since`` inclusive, ``until`` exclusive —
    matching the activity window everywhere else. A closed window would count an
    activity landing on the boundary twice.

    ``by_type`` carries all seven types in weighting order and describes every
    matching row, so a header can say "6 activities: 4 study sessions, 2 page
    views" without re-tallying a page slice and quoting it as the whole history.

    This is the paged read; ``GET /learning/activity`` is the charted series over
    the same rows.

    Errors: 422 for an out-of-range page size or an ``activity_type`` outside the
    vocabulary.
    """
    return await learning.list_activities(
        owner=current_user,
        skill_id=skill_id,
        goal_id=goal_id,
        activity_type=activity_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/activities",
    response_model=LearningActivityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record one thing that happened, and count it against the skill",
    dependencies=_ANALYTICS_READ,
)
async def record_learning_activity(
    current_user: AuthenticatedUser,
    learning: LearningIntelligenceServiceDep,
    payload: LearningActivityWrite,
) -> LearningActivityRead:
    """Append one row to the evidence, and bump the skill's counters.

    **Nothing here moves a level.** Recording an activity bumps a counter and a
    clock; the level is asserted separately and labelled, which is the whole of
    what this phase is allowed to claim about anybody.

    ``title`` is required because NEXUS will not invent a description of learning
    nobody described. ``duration_minutes`` is the difference between an *event*
    ("I finished the chapter") and a *span* ("I spent forty minutes on it"), so it
    is omitted for the former rather than sent as ``0``.

    ``source_type`` is the label that stops "6 commits touched Python files" being
    read back as "6 Python tasks completed", so it is stored exactly as sent and is
    never reconstructed by a client.

    Errors: 404 when a supplied skill or goal is not the caller's; 422 for an
    activity type outside the vocabulary or a negative duration.
    """
    return await learning.record_activity(
        owner=current_user,
        title=payload.title,
        activity_type=payload.activity_type,
        skill_id=payload.skill_id,
        goal_id=payload.goal_id,
        description=payload.description,
        occurred_at=payload.occurred_at,
        duration_minutes=payload.duration_minutes,
        source_type=payload.source_type,
        source_id=payload.source_id,
    )


@router.post(
    "/recommendations",
    response_model=list[RecommendationRead],
    summary="Run the deterministic learning rules and return what they newly raised",
    dependencies=_ANALYTICS_READ,
)
async def evaluate_learning_recommendations(
    current_user: AuthenticatedUser,
    recommendations: RecommendationServiceDep,
) -> list[RecommendationRead]:
    """Evaluate the two learning rules and return the suggestions this call raised.

    **A ``POST`` because it writes**, and **synchronous for the same reason
    ``POST /intelligence/evaluate`` is**: the rules are threshold comparisons over
    rows already in the database, so there is nothing to schedule and no reason to
    tell the caller to come back later for an answer that is available now.

    The learning rules are registered under the ``None`` key of
    :data:`~app.services.risk.recommendation.recommendation_rules` — the key that
    means *not caused by a risk* — because a goal approaching its deadline with
    35% recorded progress is a real signal that no risk row describes. Their
    suggestions therefore carry ``risk_id = NULL``.

    **Only newly raised rows come back.** A rule that fires again on the second
    run refreshes its existing suggestion in place and returns nothing, which is
    the same contract :meth:`RecommendationService.generate` offers and the same
    reason: a caller polling this endpoint must be able to tell a new suggestion
    from one it has already been shown, and returning the whole table every time
    would make that impossible.

    The rules are deterministic — thresholds compared against the caller's own
    recorded figures — and every one of them states those figures in its reason.
    There is no model here, and none is planned for this endpoint.

    Errors: 401 without a session, 403 without ``analytics.read``.
    """
    return [
        RecommendationRead.model_validate(row)
        for row in await recommendations.generate_learning(owner=current_user)
    ]
