"""Planner request/response models: calendar, work sessions, availability, engine.

Two decisions shape this module.

**Every instant must be timezone-aware, and a naive one is rejected outright.**
A naive datetime does not fail loudly on the way in — it is read as local time
by whichever process happens to handle it, so the same request written from two
browsers with different clocks lands hours apart and nothing anywhere reports an
error. Every field that names a moment on the timeline is typed
:class:`Instant`, which raises with a message naming the fix. The one deliberate
exception is :class:`AvailabilityRuleCreate`, whose ``starts_at``/``ends_at``
are :class:`datetime.time` values: "09:00 to 17:00" has no instant until a
date is attached, and storing a fake one would put the ambiguity somewhere less
visible than the schema.

**Nothing here shifts an event.** An event is stored as the instant the caller
sent and is returned as that instant, with its offset intact. Which *days* those
instants fall on is a question about a timezone, and it is answered by the
``tz`` parameter on the planner routes rather than by quietly normalising the
stored value — normalising would move a 23:00 event to the previous UTC day and
the user would never be told.

Invalids are rejected here, as 422s, rather than left to the service or the
database: a window that ends before it starts, a weekday outside 0-6, a rule
whose end is not after its start, and a negative estimate are all things a
client can be told what to do about.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import CalendarEventType
from app.models.planner import WorkSessionStatus
from app.schemas.common import PageMeta

__all__ = [
    "MAX_PAGE_LIMIT",
    "AvailabilityReplacement",
    "AvailabilityRuleCreate",
    "AvailabilityRuleRead",
    "AvailabilityWeek",
    "CalendarEventCreate",
    "CalendarEventRead",
    "CalendarEventType",
    "CalendarEventUpdate",
    "Conflict",
    "ConflictKind",
    "ConflictList",
    "ConflictSeverity",
    "DayLoad",
    "Instant",
    "PlannerDay",
    "PlannerSlot",
    "PlannerSuggestion",
    "PlannerWeek",
    "PlannerWindow",
    "SuggestionResponse",
    "WeekTotals",
    "WorkSessionCreate",
    "WorkSessionRead",
    "WorkSessionStatus",
    "WorkSessionUpdate",
]

#: Matches the cap every other list in the API applies. Exceeding it is a 422,
#: not a silent clamp: a caller that asked for 500 rows and got 100 has been
#: handed a page that looks complete and is not.
MAX_PAGE_LIMIT = 100

#: Must match ``calendar_events.title``.
MAX_EVENT_TITLE_LENGTH = 200
#: Must match ``calendar_events.location``.
MAX_EVENT_LOCATION_LENGTH = 200
#: Must match ``availability_rules.label``.
MAX_AVAILABILITY_LABEL_LENGTH = 80
MAX_EVENT_DESCRIPTION_LENGTH = 8000
#: Reuses ``app.schemas.task``'s bound, so a task estimate and a session
#: estimate are refused on the same grounds.
MAX_ESTIMATED_MINUTES = 100_000


ConflictKind = Literal[
    "overlapping_events",
    "overlapping_sessions",
    "outside_availability",
    "after_deadline",
]
ConflictSeverity = Literal["info", "warning", "error"]


def _require_aware(value: datetime) -> datetime:
    """Reject a naive datetime.

    The message names the cause and the fix, because "must be timezone-aware"
    on its own leaves a caller guessing which of their inputs was wrong.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "Datetimes must be timezone-aware and include a UTC offset "
            "(e.g. 2026-03-04T09:00:00+01:00). A naive value would be read as "
            "local time and land hours from where it was intended."
        )
    return value


#: A moment on the timeline. Every field that stores or reports one uses this.
Instant = Annotated[datetime, AfterValidator(_require_aware)]
#: Bounds shared by the calendar and session estimators, reused so the two
#: surfaces refuse the same numbers for the same reason.
NonNegativeMinutes = Annotated[int, Field(ge=0, le=MAX_ESTIMATED_MINUTES)]


class _TitleStripper:
    """Strip the leading and trailing whitespace of a title.

    Stripped rather than rejected because the space is invisible in the UI that
    produced it, and not mapped to ``None`` because an empty title is a mistake
    that ``min_length`` should report as one.
    """

    @field_validator("title", mode="before", check_fields=False)
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class _WindowChecker:
    """Shared ``end > start`` comparison.

    A plain mixin rather than a model: a second ``BaseModel`` in the bases would
    make pydantic try to build two models into one, and the rule is three lines.
    """

    def _check_window(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at.")
        return self


class CalendarEventCreate(_TitleStripper, _WindowChecker, BaseModel):
    """Creation payload for a calendar event."""

    title: str = Field(min_length=1, max_length=MAX_EVENT_TITLE_LENGTH, examples=["Design review"])
    starts_at: Instant = Field(examples=["2026-03-04T09:00:00+01:00"])
    ends_at: Instant = Field(examples=["2026-03-04T10:00:00+01:00"])
    event_type: CalendarEventType = Field(default=CalendarEventType.OTHER)
    description: str | None = Field(default=None, max_length=MAX_EVENT_DESCRIPTION_LENGTH)
    project_id: UUID | None = Field(
        default=None, description="Project this event belongs to; must be owned by the caller."
    )
    task_id: UUID | None = Field(default=None, description="Task this event is time reserved for.")
    all_day: bool = Field(
        default=False, description="Marks the event as spanning a whole day rather than a window."
    )
    location: str | None = Field(default=None, max_length=MAX_EVENT_LOCATION_LENGTH)

    @model_validator(mode="after")
    def _validate_window(self) -> Self:
        return self._check_window()


class CalendarEventUpdate(_TitleStripper, BaseModel):
    """Partial update of a calendar event.

    ``extra="forbid"`` for the reason given on
    :class:`~app.schemas.task.TaskUpdate`: a silently dropped field reads as a
    successful write.

    A PATCH that moves only one end of the window is checked against the
    persisted row by the service; here only the case where both arrive is
    decidable, and checking less than that would accept an impossible pair.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_EVENT_TITLE_LENGTH)
    starts_at: Instant | None = None
    ends_at: Instant | None = None
    event_type: CalendarEventType | None = None
    description: str | None = Field(default=None, max_length=MAX_EVENT_DESCRIPTION_LENGTH)
    project_id: UUID | None = None
    task_id: UUID | None = None
    all_day: bool | None = None
    location: str | None = Field(default=None, max_length=MAX_EVENT_LOCATION_LENGTH)
    completed_at: Instant | None = None

    @model_validator(mode="after")
    def _validate_window(self) -> Self:
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at.")
        return self


class CalendarEventRead(BaseModel):
    """A calendar event as returned by the calendar endpoints.

    ``starts_at`` and ``ends_at`` come back as the instants that were stored,
    offset intact. No field here is recomputed in the caller's timezone — a
    client that wants the local time converts the instant itself, which is the
    only way to keep the offset it was given.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    title: str
    description: str | None
    event_type: str = Field(description="One of the ``CalendarEventType`` values.")
    project_id: UUID | None
    task_id: UUID | None
    starts_at: datetime
    ends_at: datetime
    all_day: bool
    location: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkSessionCreate(_WindowChecker, BaseModel):
    """Creation payload for a work session."""

    scheduled_start: Instant = Field(examples=["2026-03-04T09:00:00+01:00"])
    scheduled_end: Instant = Field(examples=["2026-03-04T10:30:00+01:00"])
    task_id: UUID | None = None
    project_id: UUID | None = None
    estimated_minutes: NonNegativeMinutes | None = Field(
        default=None, description="Planned effort; null when not estimated."
    )
    status: WorkSessionStatus = Field(default=WorkSessionStatus.PLANNED)

    @model_validator(mode="after")
    def _validate_window(self) -> Self:
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start.")
        return self


class WorkSessionUpdate(BaseModel):
    """Partial update of a work session.

    ``actual_start``, ``actual_end`` and ``actual_minutes`` are **not fields**,
    and that is the fix rather than an omission. They used to be declared here
    and dropped by the service, so ``PATCH {"actual_minutes": 600}`` answered
    200 and changed nothing — a write the caller was told had happened, which
    is the worst of the three options available (persist it, remove it, or
    discard it silently).

    They are **removed**, not persisted, because they are the product of the
    routed ``POST /work-sessions/{id}/start`` and ``/stop`` pair and of nothing
    else. Those two read the **database** clock, so the figure they write is a
    measurement; a PATCH-supplied one would be an assertion, and every surface
    that reports time to a user — the session card, the day totals, and the
    analytics Phase 10 will train on — reads these same three columns. The
    contract is now enforced at the edge: ``extra="forbid"`` turns the old
    request into a **422 naming the field**, which is a loud, actionable answer
    rather than a lie.

    ``status`` is writable here, unlike a task's: a session has no lifecycle
    rules that a blanket write could bypass — starting and stopping are routed
    because the clock is involved, but cancelling a slot is an ordinary edit.
    """

    model_config = ConfigDict(extra="forbid")

    scheduled_start: Instant | None = None
    scheduled_end: Instant | None = None
    estimated_minutes: NonNegativeMinutes | None = None
    status: WorkSessionStatus | None = None
    task_id: UUID | None = None
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_windows(self) -> Self:
        if (
            self.scheduled_start is not None
            and self.scheduled_end is not None
            and self.scheduled_end <= self.scheduled_start
        ):
            raise ValueError("scheduled_end must be after scheduled_start.")
        return self


class WorkSessionRead(BaseModel):
    """A work session as returned by the work-session endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    task_id: UUID | None
    project_id: UUID | None
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    estimated_minutes: int | None
    actual_minutes: int
    status: str = Field(description="One of the ``WorkSessionStatus`` values.")
    created_at: datetime
    updated_at: datetime


class AvailabilityRuleCreate(BaseModel):
    """One recurring "I am working then" window, in the user's own wall clock.

    ``weekday`` is 0 for Monday through 6 for Sunday, and ``starts_at``/
    ``ends_at`` are times rather than datetimes because a recurring weekly
    window has no instant of its own. The zone they are interpreted in is the
    ``tz`` the planner is asked for, and is never persisted on the rule — a
    user who moves has one set of working hours, not one per city.
    """

    weekday: int = Field(ge=0, le=6, description="0 = Monday … 6 = Sunday.")
    starts_at: time
    ends_at: time
    label: str | None = Field(default=None, max_length=MAX_AVAILABILITY_LABEL_LENGTH)

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at.")
        return self


class AvailabilityRuleRead(BaseModel):
    """A persisted availability rule."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    weekday: int
    starts_at: time
    ends_at: time
    label: str | None
    created_at: datetime
    updated_at: datetime


class AvailabilityReplacement(BaseModel):
    """The whole week, for ``PUT /availability``.

    The body is the complete pattern rather than a list of changes: "I no longer
    work Tuesdays" and "I did not send Tuesday" have to be the same request, and
    they are only the same request if the endpoint replaces rather than patches.
    """

    rules: list[AvailabilityRuleCreate] = Field(
        default_factory=list, description="The user's complete weekly pattern; empty clears it."
    )


class AvailabilityWeek(BaseModel):
    """The stored pattern, plus the zone it was read against."""

    rules: list[AvailabilityRuleRead] = Field(default_factory=list)
    timezone: str = Field(description="IANA zone the rules' wall-clock times are read in.")
    meta: PageMeta = Field(
        default_factory=lambda: PageMeta(total=0, limit=MAX_PAGE_LIMIT, offset=0),
        description="Counters for the returned slice.",
    )


class PlannerSlot(BaseModel):
    """One item on a planner day: an event, a session, or an availability window.

    ``kind`` is a discriminator rather than a subclass so a day renders as one
    ordered list; the caller switches on it rather than on the shape.
    """

    kind: Literal["event", "session", "availability"]
    starts_at: datetime
    ends_at: datetime
    id: UUID | None = Field(default=None, description="Set for events and sessions.")


class DayLoad(BaseModel):
    """One day's capacity against its commitments.

    ``ratio`` is scheduled ÷ available, and is ``None`` — never ``NaN`` or
    infinity — when ``available`` is 0, which is what a day with no availability
    rule looks like. A number that cannot be expressed is a ``None`` here so the
    field never has to be read defensively by every client.
    """

    date: date
    available: int = Field(ge=0, description="Minutes the user says they are free.")
    scheduled: int = Field(ge=0, description="Minutes already committed.")
    overload_minutes: int = Field(
        ge=0, description="``scheduled - available``; 0 when not overloaded."
    )
    ratio: float | None = Field(
        default=None, description="scheduled ÷ available, or None when available is 0."
    )


class PlannerDay(BaseModel):
    """One local day of the planner.

    ``available_minutes`` is ``None`` rather than 0 when the user has defined no
    availability for that weekday, because "no hours declared" and "declared
    zero hours" are different answers and only one of them means the day is
    overloaded. With ``None``, ``overload_minutes`` is ``None`` and
    ``overloaded`` is ``False``.
    """

    date: date
    events: list[CalendarEventRead] = Field(default_factory=list)
    sessions: list[WorkSessionRead] = Field(default_factory=list)
    available_minutes: int | None = None
    scheduled_minutes: int = 0
    overload_minutes: int | None = None
    overloaded: bool = False
    ratio: float | None = Field(
        default=None, description="scheduled ÷ available, or None when availability is unknown."
    )


class WeekTotals(BaseModel):
    """Capacity and commitments summed across a week."""

    available_minutes: int | None = None
    scheduled_minutes: int = 0
    overload_minutes: int | None = None
    overloaded_days: int = Field(ge=0, default=0)
    event_count: int = Field(ge=0, default=0)
    session_count: int = Field(ge=0, default=0)


class PlannerWindow(BaseModel):
    """The span and the zone a planner response was computed for.

    Returned alongside the payload so a client never has to guess which days
    were considered, or in whose time they were counted.
    """

    start_date: date
    end_date: date
    timezone: str


class PlannerWeek(BaseModel):
    """A week of planner days, with its own totals and the window it covers."""

    week_start: date
    week_end: date
    days: list[PlannerDay] = Field(default_factory=list)
    totals: WeekTotals = Field(default_factory=WeekTotals)
    window: PlannerWindow | None = Field(
        default=None, description="The range and zone the days were computed for."
    )


class PlannerSuggestion(BaseModel):
    """One proposed slot, with the reasoning that produced it.

    ``reason`` and ``evidence`` are not decoration. A schedule the user did not
    write needs to be arguable, and an engine that cannot say why it put work
    at 14:00 on Tuesday is indistinguishable from one that guessed. ``evidence``
    carries the inputs the decision was made from — the due date, the estimate,
    the collisions that were skipped — so the claim can be checked rather than
    believed.
    """

    task_id: UUID
    task_title: str
    suggested_start: datetime
    suggested_end: datetime
    reason: str = Field(description="One sentence a user can read and agree or dispute.")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="The inputs behind the decision; never a fabricated slot."
    )


class SuggestionResponse(BaseModel):
    """The engine's answer, including why it is empty when it is.

    An empty ``suggestions`` list with a populated ``reason_if_empty`` is the
    honest response when there was nothing to schedule. Returning a plausible
    slot instead would be worse than returning nothing: the user would book time
    on the strength of it.
    """

    suggestions: list[PlannerSuggestion] = Field(default_factory=list)
    generated_for: PlannerWindow = Field(description="The span and zone the engine walked.")
    reason_if_empty: str | None = Field(
        default=None, description="Why there are no suggestions; null when there are some."
    )


class Conflict(BaseModel):
    """One problem found in a schedule, with the evidence behind it."""

    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    entity_type: Literal["calendar_event", "work_session"]
    entity_id: UUID | None = Field(
        default=None, description="Null for a conflict between two things that were both found."
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="The two intervals, or the window the work fell outside."
    )


class ConflictList(BaseModel):
    """Every conflict found over a span, in the order they were detected.

    ``truncated`` and ``truncated_reasons`` exist because the scan is bounded:
    overlap detection is pairwise, so the conflicts a span can produce grow with
    the square of the rows in it, and a cap that was not reported would be a
    response indistinguishable from a complete one. ``meta.total`` is the number
    of conflicts **returned**, and with ``truncated`` set it is a floor on the
    number that exist rather than a count of them.
    """

    window: PlannerWindow
    conflicts: list[Conflict] = Field(default_factory=list)
    truncated: bool = Field(
        default=False, description="True when the scan stopped before covering the whole span."
    )
    truncated_reasons: list[str] = Field(
        default_factory=list, description="Why the scan is incomplete; empty when it is not."
    )
    meta: PageMeta = Field(
        default_factory=lambda: PageMeta(total=0, limit=MAX_PAGE_LIMIT, offset=0)
    )
