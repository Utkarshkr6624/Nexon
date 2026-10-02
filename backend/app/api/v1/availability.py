"""Availability: the weekly pattern of hours the user says they are free.

Why this is its own router rather than ``/planner/availability``
---------------------------------------------------------------
**It is not a planner view, it is a setting the planner reads.** ``/planner``
answers "what is happening on Tuesday"; this answers "when are you free on
Tuesdays, generally". Three reasons that makes a separate router the honest
placement rather than a cosmetic one:

* **It is not windowed.** Every planner route is bounded — a day, a week, a
  month, or an explicit ``from``/``to``. Availability has no window at all:
  ``(owner_id, weekday, starts_at)`` is unique, so the entire answer is at most
  24 x 7 = 168 rows and is never paginated in the sense the other listings are.
  Putting a paginated-looking endpoint under a prefix where every sibling is
  windowed invites a client to pass a range that does nothing.
* **Its permissions and its editors differ.** It is written by the user editing
  their own defaults — the one place in the planner where the answer is a
  preference rather than a fact — and it is read by the scheduler on every
  suggestion run. That is a different lifecycle from "the calendar for a week".
* **The specified path is top-level.** ``PUT /api/v1/availability`` and
  ``GET /api/v1/availability``. A prefix is a URL contract, not an internal
  detail, and matching the contract the rest of the codebase is coding against
  is worth more than the tidiness of nesting it.

Timezones
---------
**The rules carry no zone.** ``starts_at``/``ends_at`` are naive ``TIME``
columns because "09:00 to 17:00" is a wall-clock statement with no instant in
it; the zone it is read in is the ``tz`` of the request that reads it, and it is
never persisted — a user who moves has one set of working hours, not one per
city. ``?tz`` on ``GET`` therefore decides how the returned window reads, and
changing it does not change a single stored row.

Write semantics
---------------
``PUT`` **replaces the whole week** rather than patching it. "I no longer work
Tuesdays" and "I did not send Tuesday" have to be the same request, and they are
only the same request if the endpoint replaces instead of merging; a diff-based
endpoint has to guess between them. Sending ``{"rules": []}`` clears the week,
which is a real answer rather than a no-op.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, PlannerServiceDep, SettingsDep
from app.core.config import Settings
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.schemas.planner import AvailabilityReplacement, AvailabilityRuleRead, AvailabilityWeek
from app.services.planner_service import resolve_timezone

router = APIRouter(prefix="/availability", tags=["availability"])

#: At most 24 windows per weekday, and the schema enforces ``ends_at >
#: starts_at``, so a day cannot be split into more pieces than a clock has
#: minutes. The bound is a rejection rather than a clamp for the same reason
#: ``limit`` is.
MAX_RULES_PER_WEEKDAY = 24


@router.get(
    "",
    response_model=AvailabilityWeek,
    summary="Read the caller's weekly availability",
    dependencies=[Depends(require_permission(Permission.CALENDAR_READ))],
)
async def get_availability(
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    settings: SettingsDep,
    tz: Annotated[
        str | None,
        Query(description="IANA zone the wall-clock times are reported in."),
    ] = None,
) -> AvailabilityWeek:
    """Return the stored weekly pattern and the zone it was read in.

    **Every rule is returned; there is no page.** The unique constraint makes
    the answer at most 168 rows, so a ``limit`` here would be a limit nobody
    could reach.

    ``?tz`` changes only how the answer is *described*: the stored values are
    naive wall-clock times and are returned as such, because they are what the
    user typed. The zone travels alongside so a client knows how to render them
    without re-deriving the default.

    An unknown zone is a **422, never a silent fallback to UTC**. A silent
    fallback would shift every boundary by that zone's offset and answer a
    well-formed question with another question's data — and the user would not
    see the shift, only a calendar in the wrong hours.

    Errors: 422 for an unknown IANA zone name.
    """
    rules = await planner.get_availability(owner=current_user)
    zone = _resolve_zone(tz, settings)
    return AvailabilityWeek(
        rules=[AvailabilityRuleRead.model_validate(rule) for rule in rules],
        timezone=zone,
    )


@router.put(
    "",
    response_model=AvailabilityWeek,
    summary="Replace the caller's weekly availability",
    dependencies=[Depends(require_permission(Permission.CALENDAR_WRITE))],
)
async def replace_availability(
    payload: AvailabilityReplacement,
    current_user: AuthenticatedUser,
    planner: PlannerServiceDep,
    settings: SettingsDep,
    tz: Annotated[
        str | None,
        Query(description="IANA zone the response is described in."),
    ] = None,
) -> AvailabilityWeek:
    """Replace the whole week with the rules in the body.

    **The body is the complete pattern, not a list of changes.** See this
    module's docstring for why that distinction is the whole design of the
    verb. An empty ``rules`` list clears the week, which is a real answer: a user
    who has not told us their hours has not told us nothing, they have told us
    they have not said.

    Each rule is validated before anything is written: ``weekday`` in 0-6,
    ``ends_at`` after ``starts_at``. **The order is not normalised** — a day with
    two overlapping windows is stored as sent and reported as overlapping,
    because silently reordering or clipping the user's stated hours would make
    the response disagree with the request they can still see.

    Two rules sharing a ``(weekday, starts_at)`` collide on a unique constraint
    and are a **409 naming the limit**, not a 500 and not a silent last-writer
    win: the payload genuinely contains two rows the schema says cannot both
    exist, and the caller has to choose which one they meant.

    Errors: 409 for a duplicate ``(weekday, starts_at)``; 422 for a malformed
    rule or an unknown ``tz``.
    """
    rules = await planner.replace_availability(owner=current_user, rules=payload.rules)
    zone = _resolve_zone(tz, settings)
    return AvailabilityWeek(
        rules=[AvailabilityRuleRead.model_validate(rule) for rule in rules],
        timezone=zone,
    )


def _resolve_zone(tz: str | None, settings: Settings) -> str:
    """Return the zone key, raising the service's ``ValidationError`` on a bad name.

    Delegated to :func:`app.services.planner_service.resolve_timezone` rather
    than reimplemented: the fallback to ``settings.planner_default_timezone`` and
    the refusal to silently degrade to UTC are one decision, and a second copy
    here would be a second place to keep them in step. It returns a ``ZoneInfo``;
    this returns its key because that is what the response model carries.
    """
    return resolve_timezone(tz, settings).key


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
