"""Intelligence endpoints: run the detectors now, and read what past runs did.

Two routes, because there are two questions
-------------------------------------------
``POST /intelligence/evaluate`` asks *what does the engine see right now*, and
``GET /intelligence/evaluations`` asks *what has it seen before*. They are
separate because they have opposite costs: the first is a full pass over the
analytics tables and it writes rows, and the second is one bounded read of a table
that holds one row per run.

Why evaluation is a POST on its own route
-----------------------------------------
An evaluation is a **write over every risk the caller owns** — upserts, the
resolution sweep, the recommendations and one ``risk_evaluations`` row — so it is
never a GET, and a client must not cache it as one. It is idempotent rather than
merely repeatable: the same condition found twice updates one row through the
partial unique index rather than writing a second, so a user pressing the button
twice gets the same Risk Center and two run summaries rather than two Risk
Centers.

It is synchronous and returns **200**, not 202. Unlike the analytics rebuild — a
maintenance pass over a date range that does not need to finish before the user
looks at it — the detection pass is bounded by the size of the caller's own
recorded data and its result *is* the response body. A client that got an empty
202 would have nothing to show until it polled for a summary that the next
``GET /intelligence/evaluations`` would have told it to expect anyway.

The window, and who owns it
---------------------------
``window_days`` defaults to :data:`_DEFAULT_WINDOW_DAYS` and is validated by the
detection service against ``ANALYTICS_MAX_RANGE_DAYS``, which raises the same
``ValidationError`` every other over-wide window raises and so lands in the shared
error envelope as a 422. The ceiling is deliberately *not* duplicated as a
``Query(le=...)`` here: one place owns the limit, and a second copy would be a
second number to keep in step the day the setting changes.

The anchor date is the **database's** ``now()``, read through the same dependency
:mod:`app.api.v1.analytics` uses, for the reason that module gives: a host whose
clock has drifted from the server's would otherwise bucket today's activity into
yesterday and judge every deadline against the wrong day.

An evaluation that assessed nothing is a successful answer
-----------------------------------------------------------
A pass in which every detector declined returns **200** with ``evaluated: false``
and the reasons in ``reason_if_not_evaluated`` — "not enough recorded activity to
compare against a previous period" is the difference between a user in their
first fortnight and a user with nothing wrong, and only the first can be told from
the second if the reasons travel with the response. A 204, a 404 or a silent
empty body would make them identical.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, RiskRepositoryDep, RiskServiceDep
from app.api.v1.analytics import get_today
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.models.risk import RiskEvaluation
from app.schemas.risk import EvaluationRead

__all__ = ["router"]

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

#: Applied to every route on the reason :mod:`app.api.v1.risks` gives: Phase 7
#: reuses ``analytics.read`` rather than coining a second capability, because the
#: rows this surface exposes are derived from the caller's own recorded work and
#: the roles that may see them are already exactly the roles that hold it.
_ANALYTICS_READ = [Depends(require_permission(Permission.ANALYTICS_READ))]

#: The evaluation window a caller gets when they do not ask for one. Fourteen days
#: is the length the detection service itself defaults to, restated here as the
#: value the route advertises in its generated documentation. It is a trailing
#: window long enough for the consistency detector to have a period to compare
#: against, and short enough that re-running it is not a scan of all history.
_DEFAULT_WINDOW_DAYS = 14

#: The most recent runs a caller may ask for. A ceiling rather than a default,
#: and a rejection rather than a silent truncation for the reason
#: ``docs/api-conventions.md`` §Pagination gives.
_MAX_EVALUATION_ROWS = 100

#: The default number of recent runs returned.
_DEFAULT_EVALUATION_ROWS = 20

#: The database's current date, as a dependency. Imported from
#: :mod:`app.api.v1.analytics` rather than re-read here: it is the same question
#: with the same answer, and two ``SELECT now()`` implementations would be two
#: chances to disagree about which day NEXUS thinks it is.
_Today = Annotated[date, Depends(get_today)]


@router.post(
    "/evaluate",
    response_model=EvaluationRead,
    summary="Run the detectors now",
    dependencies=_ANALYTICS_READ,
)
async def evaluate(
    current_user: AuthenticatedUser,
    risks: RiskServiceDep,
    today: _Today,
    window_days: Annotated[int, Query(ge=1)] = _DEFAULT_WINDOW_DAYS,
) -> EvaluationRead:
    """Run one detection pass over the caller's recorded data and report it.

    One read, six detectors, one reconciliation, one summary row — the shape is
    :meth:`RiskDetectionService.evaluate`'s and this route adds nothing to it but
    a window and a status code.

    **The summary is the point, not the risks.** It separates what was *created*
    from what was *refreshed*, which is how "the same underlying risk should not
    generate hundreds of identical records" is demonstrated rather than asserted,
    and it separates both from what was *resolved* by a condition disappearing.

    A detector that could not judge contributes no row and no event; its reason
    travels on ``reason_if_not_evaluated``. A detector that measured zero
    contributes no row either — a "nothing is wrong" row in a list whose job is
    to show what needs attention is noise the user has to dismiss by hand, every
    run, forever — and is reported as a measurement instead.

    Errors: 422 for ``window_days`` below 1 or wider than
    ``ANALYTICS_MAX_RANGE_DAYS``.
    """
    return await risks.evaluate(owner=current_user, today=today, window_days=window_days)


@router.get(
    "/evaluations",
    response_model=list[EvaluationRead],
    summary="Recent detection runs, newest first",
    dependencies=_ANALYTICS_READ,
)
async def list_evaluations(
    current_user: AuthenticatedUser,
    risks: RiskRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_EVALUATION_ROWS)] = _DEFAULT_EVALUATION_ROWS,
) -> list[EvaluationRead]:
    """Return the caller's recent detection runs, newest first.

    **One row per run, never one per risk per run.** The risks themselves are the
    durable record; a table that multiplied by the number of live risks on every
    evaluation would be the growth the brief rules out, and per-risk timing is
    already recoverable from ``detected_at`` and ``resolved_at`` without a second
    copy that could disagree with the first.

    An empty list is a real answer: it means detection has not been run, which a
    brand-new account should be told plainly rather than by a list of nothing.
    """
    rows = await risks.list_evaluations(current_user.id, limit=limit)
    return [_evaluation_read(row) for row in rows]


def _evaluation_read(row: RiskEvaluation) -> EvaluationRead:
    """Build the wire model for one stored run.

    Unlike the risk and recommendation read models this one validates straight
    off the ORM instance: :class:`~app.schemas.risk.EvaluationRead` has no
    ``metadata`` field, so nothing here collides with an attribute SQLAlchemy
    reserves on a declarative class. ``evaluated`` keeps its ``True`` default
    because a row exists only for a pass that ran — the ``False`` case is a live
    answer from ``POST /evaluate``, never a persisted one.
    """
    return EvaluationRead.model_validate(row)
