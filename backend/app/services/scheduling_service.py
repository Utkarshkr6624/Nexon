"""The deterministic scheduling engine, and pure conflict detection.

**No LLM, and none is wanted here.** Every suggestion this module emits carries a
sentence a user can argue with and an ``evidence`` dict holding the inputs the
decision was made from. A suggestion the user cannot check is indistinguishable
from a guess, and a planner that quietly guesses where somebody's afternoon went
is worse than one that proposes nothing — so the engine returns an **empty list
with a stated reason** whenever it has nothing to build on, and never invents a
slot to fill the shape of a response.

Determinism
-----------
The same inputs produce the same plan, in the same order, every time:

* **"Now" is an input, not an ambient read.** It comes from the database clock
  (``func.now()``) and is threaded in as a parameter, so two calls with the same
  arguments — including the same ``now`` — produce byte-identical output. That is
  what makes the engine testable and what stops a plan from reshuffling between
  a preview and the accept click a second later.
* **Ties are broken by id.** Tasks sort by ``(due_date ASC NULLS LAST,
  priority DESC, id)``; the id tiebreak is what stops two tasks due the same day
  at the same priority from swapping places between calls.
* **Slots step on a fixed grid.** Candidate starts advance by
  ``planner_min_session_minutes`` from the start of each availability window, so
  there is no float arithmetic that could land a second's rounding apart.

Why the ordering is due date first, priority second
---------------------------------------------------
By **due date ascending** because a deadline is the only hard constraint the
engine honours: a suggestion that lands after the due date is worthless no matter
how important the work is. Sorting by priority first would fill the nearest free
slot with a ``critical`` task due in six weeks and push a ``low`` task due
tomorrow past its own deadline — optimising a soft preference at the cost of a
hard promise.

Then by **priority descending**, because among tasks competing for the *same*
slot, which one gets it is a real choice, and the answer the user would give
themselves is the more urgent one.

An earlier cap is the other half of the answer: ``planner_max_suggestions_per_task``
stops one large task from filling the horizon ahead of everything else, so a
six-hour task due next month cannot take every slot before a one-hour task due
tomorrow is even considered.

Timezones
---------
Day boundaries are built with :func:`app.services.planner_service.day_bounds` in
the requested IANA zone and converted to UTC before anything is compared, so the
engine reasons about the days the user is looking at. An unknown zone name is a
422 — never a silent fall back to UTC, which would shift every boundary by the
zone's offset and quietly answer a different question.

Repository contract relied on by this module is
:class:`app.services.planner_service.PlannerService`'s, plus
``TaskRepository.list_for_user``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import ActivityEvent, TaskPriority, TaskStatus
from app.models.planner import WorkSession, WorkSessionStatus
from app.models.task import Task
from app.models.user import User
from app.repositories.task import TaskRepository
from app.schemas.planner import (
    Conflict,
    PlannerSuggestion,
    PlannerWindow,
    SuggestionResponse,
)
from app.services.planner_service import (
    PlannerService,
    _aware,
    _overlaps,
    availability_windows_for,
    day_bounds,
    local_day,
    resolve_timezone,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.services.activity_service import ActivityService
    from app.services.audit_service import AuditService

__all__ = ["MAX_SUGGESTIONS", "SchedulingService", "detect_conflicts"]

#: Ceiling on one response, across all tasks. ``planner_max_suggestions_per_task``
#: bounds one task's appetite; this bounds the page. A user with a 400-task
#: backlog would otherwise get several thousand rows of proposals, which is not a
#: plan, it is a dump — and the truncated answer is still ordered by urgency, so
#: the first rows are the ones that matter.
MAX_SUGGESTIONS = 50

#: Rows pulled per status when collecting candidates. The scheduler is bounded by
#: ``planner_lookahead_days`` and by the deadline filter, and anything past this
#: is past the horizon or has no estimate; the walk is bounded so a huge backlog
#: costs a bounded number of queries rather than a full scan.
MAX_CANDIDATES = 1000

#: How many skipped intervals one suggestion's ``evidence`` names. The count is
#: reported in full; the detail is truncated, because "here are the 400 meetings
#: I walked past" is not evidence a user reads.
MAX_EVIDENCE_SKIPS = 5

#: How much of a skipped interval's detail travels in the evidence.
_PRIORITY_RANK: Mapping[str, int] = {
    TaskPriority.LOW.value: 0,
    TaskPriority.MEDIUM.value: 1,
    TaskPriority.HIGH.value: 2,
    TaskPriority.CRITICAL.value: 3,
}

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

_TASK_NOT_FOUND = "Task not found."
_INVALID_TASK_ID = "One of those task ids is not a task of yours."


def _priority_rank(value: str) -> int:
    """Unknown priorities sort last, not first — a drifted row must not win."""
    return _PRIORITY_RANK.get(value, -1)


def _task_order_key(task: Task) -> tuple[date, int, str]:
    """``(due_date, -priority, id)``.

    ``due_date`` is non-null for every candidate — the engine refuses to plan
    work with no deadline — so there is no ``NULLS LAST`` to express here; the
    filter has already removed the nulls. The id tiebreak is the determinism
    guarantee.
    """
    # ``due_date`` is non-null for every candidate: ``_is_candidate`` has already
    # rejected the rest, so this key never has to order against a NULL.
    due = task.due_date or date.max
    return (due, -_priority_rank(task.priority), str(task.id))


def _is_candidate(task: Task) -> bool:
    """Whether a task has enough information to be scheduled.

    An **estimate** and a **due date** are both required, and the reason is the
    same in each case: without one there is no answer. Without an estimate there
    is no block to reserve; without a due date there is no boundary to stop at,
    and a task with neither is better described by asking the user for the two
    numbers than by receiving an invented slot.
    """
    return (
        task.due_date is not None
        and task.estimated_minutes is not None
        and task.estimated_minutes > 0
    )


def _window_label(start: time, end: time) -> str:
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def _describe(value: datetime) -> str:
    return _aware(value).isoformat()


def _busy_intervals(
    events: Sequence[Any], sessions: Sequence[WorkSession]
) -> list[tuple[datetime, datetime, dict[str, Any]]]:
    """Collect the intervals a new session must not touch.

    Cancelled sessions are excluded: they hold no time, and honouring one would
    refuse work against a booking the user has already called off.
    """
    intervals: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for event in events:
        intervals.append(
            (
                _aware(event.starts_at),
                _aware(event.ends_at),
                {"kind": "event", "title": event.title, "id": str(event.id)},
            )
        )
    for row in sessions:
        if str(row.status) == WorkSessionStatus.CANCELLED.value:
            continue
        intervals.append(
            (
                _aware(row.scheduled_start),
                _aware(row.scheduled_end),
                {"kind": "session", "title": "Work session", "id": str(row.id)},
            )
        )
    intervals.sort(key=lambda item: (item[0], item[1]))
    return intervals


def _collides(
    start: datetime, end: datetime, intervals: Sequence[tuple[datetime, datetime, Any]]
) -> list[tuple[datetime, datetime, Any]]:
    """The intervals a half-open ``[start, end)`` would overlap.

    Touching is not colliding: an event ending at 10:00 and a session starting at
    10:00 are back-to-back, which is the normal shape of a calendar.
    """
    return [
        (busy_start, busy_end, detail)
        for busy_start, busy_end, detail in intervals
        if _overlaps(start, end, busy_start, busy_end)
    ]


def detect_conflicts(
    *,
    events: Sequence[Any],
    sessions: Sequence[WorkSession],
    availability: Sequence[Any],
    tasks_by_id: Mapping[uuid.UUID, Task] | None,
    owner_tz: str = "UTC",
) -> list[Conflict]:
    """Find everything wrong with a span of schedule.

    **Pure: no database, no clock, no settings.** Every input — including the
    zone — is passed in, so this is unit-testable against hand-built rows and
    cannot drift between two calls that describe the same schedule.

    Four kinds, matching the four ways a planner actually goes wrong:

    ``overlapping_events``
        Two calendar events occupying the same instant.
    ``overlapping_sessions``
        Two work sessions reserved over each other.
    ``outside_availability``
        A session booked where the user said they are not working. **Only
        sessions.** A meeting outside the working pattern is how meetings happen;
        work outside the pattern is how a plan gets broken. And a user with *no*
        availability rules at all produces no conflict here — nothing declared is
        unknown, not violated.
    ``after_deadline``
        A session ending after its task's due date. A task whose id is not in
        ``tasks_by_id`` is skipped rather than guessed at: the deadline is a fact
        about a row this call was not given.

    Args:
        events: Calendar events in the span.
        sessions: Work sessions in the span.
        availability: The owner's weekly availability rules.
        tasks_by_id: The tasks the sessions point at, for the deadline check.
        owner_tz: IANA zone the days are cut on.

    Returns:
        The conflicts, in detection order: event overlaps, session overlaps,
        out-of-availability sessions, then past-deadline sessions.
    """
    zone = _zone(owner_tz)
    conflicts: list[Conflict] = []

    for earlier, later, left, right in _pairwise_overlaps(events, "starts_at", "ends_at"):
        conflicts.append(
            Conflict(
                kind="overlapping_events",
                severity="error",
                message=(f"{later.title!r} overlaps {earlier.title!r}."),
                entity_type="calendar_event",
                entity_id=getattr(later, "id", None),
                evidence={
                    "events": [left, right],
                    "overlap_starts_at": max(left["starts_at"], right["starts_at"]),
                    "overlap_ends_at": min(left["ends_at"], right["ends_at"]),
                },
            )
        )

    for _earlier, later, left, right in _pairwise_overlaps(
        sessions, "scheduled_start", "scheduled_end", skip_cancelled=True
    ):
        conflicts.append(
            Conflict(
                kind="overlapping_sessions",
                severity="error",
                message="Two work sessions are booked over each other.",
                entity_type="work_session",
                entity_id=getattr(later, "id", None),
                evidence={"sessions": [left, right]},
            )
        )

    for row in sessions:
        if str(row.status) == WorkSessionStatus.CANCELLED.value:
            continue
        window = availability_windows_for(
            availability, local_day(_aware(row.scheduled_start), zone)
        )
        if not window:
            # Nothing declared for this weekday: unknown, not violated.
            continue
        if not _fully_covered(_aware(row.scheduled_start), _aware(row.scheduled_end), window, zone):
            conflicts.append(
                Conflict(
                    kind="outside_availability",
                    severity="warning",
                    message="A work session falls outside the declared availability.",
                    entity_type="work_session",
                    entity_id=row.id,
                    evidence={
                        "scheduled_start": _describe(row.scheduled_start),
                        "scheduled_end": _describe(row.scheduled_end),
                        "declared": [
                            {
                                "weekday": local_day(_aware(row.scheduled_start), zone).weekday(),
                                "starts_at": _window_label(start, end),
                            }
                            for start, end in window
                        ],
                        "local_date": local_day(_aware(row.scheduled_start), zone).isoformat(),
                    },
                )
            )

    for row in sessions:
        if str(row.status) == WorkSessionStatus.CANCELLED.value:
            continue
        task = (tasks_by_id or {}).get(row.task_id) if row.task_id is not None else None
        if task is None or task.due_date is None:
            continue
        end_local = _aware(row.scheduled_end).astimezone(zone).date()
        if end_local > task.due_date:
            conflicts.append(
                Conflict(
                    kind="after_deadline",
                    severity="error",
                    message=(
                        f"A session for {task.title!r} ends on {end_local.isoformat()}, "
                        f"after its due date {task.due_date.isoformat()}."
                    ),
                    entity_type="work_session",
                    entity_id=row.id,
                    evidence={
                        "task_id": str(task.id),
                        "task_title": task.title,
                        "due_date": task.due_date.isoformat(),
                        "scheduled_end": _describe(row.scheduled_end),
                    },
                )
            )
    return conflicts


def _zone(owner_tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(owner_tz)
    except Exception:
        raise ValidationError(f"Unknown IANA time zone: {owner_tz!r}.") from None


def _pairwise_overlaps(
    rows: Sequence[Any], start_attr: str, end_attr: str, *, skip_cancelled: bool = False
) -> list[tuple[Any, Any, dict[str, Any], dict[str, Any]]]:
    """Yield ``(earlier, later, left_evidence, right_evidence)`` for every overlap.

    O(n²) over an already-bounded span, and a sweep line would be premature: a
    day view holds tens of rows, and the pairwise form reports *which two* rows
    collided, which is what the message names. Sorting by start lets the inner
    loop stop as soon as a row begins after the comparison window closes.
    """
    ordered = sorted(
        (
            row
            for row in rows
            if not (skip_cancelled and str(row.status) == WorkSessionStatus.CANCELLED.value)
        ),
        key=lambda row: _aware(getattr(row, start_attr)),
    )
    found: list[tuple[Any, Any, dict[str, Any], dict[str, Any]]] = []
    for index, earlier in enumerate(ordered):
        earlier_end = _aware(getattr(earlier, end_attr))
        for later in ordered[index + 1 :]:
            later_start = _aware(getattr(later, start_attr))
            if later_start >= earlier_end:
                break
            if not _overlaps(
                _aware(getattr(earlier, start_attr)),
                earlier_end,
                later_start,
                _aware(getattr(later, end_attr)),
            ):
                continue
            found.append(
                (
                    earlier,
                    later,
                    {
                        "id": str(getattr(earlier, "id", "")),
                        "title": getattr(earlier, "title", None) or "Work session",
                        "starts_at": _describe(getattr(earlier, start_attr)),
                        "ends_at": _describe(getattr(earlier, end_attr)),
                    },
                    {
                        "id": str(getattr(later, "id", "")),
                        "title": getattr(later, "title", None) or "Work session",
                        "starts_at": _describe(getattr(later, start_attr)),
                        "ends_at": _describe(getattr(later, end_attr)),
                    },
                )
            )
    return found


def _fully_covered(
    start: datetime, end: datetime, windows: list[tuple[time, time]], zone: ZoneInfo
) -> bool:
    """Whether ``[start, end)`` lies entirely inside the declared windows.

    The window is cut at local midnight and each day checked against its *own*
    rules, so a session crossing midnight is measured against both days rather
    than against whichever day's rules happen to be listed first. Within a day a
    cursor walks the windows in order, so two abutting rules read as one
    continuous stretch — which is what somebody who wrote "09:00-12:00" and
    "12:00-15:00" meant.
    """
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    first = local_day(start, zone)
    last = local_day(end - timedelta(microseconds=1), zone)
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        day_start = datetime.combine(day, time.min, tzinfo=zone)
        segment_start = max(local_start, day_start)
        segment_end = min(local_end, day_start + timedelta(days=1))
        if segment_start >= segment_end:
            continue
        cursor = segment_start
        for span_start, span_end in sorted(
            (
                datetime.combine(day, w_start, tzinfo=zone),
                datetime.combine(day, w_end, tzinfo=zone),
            )
            for w_start, w_end in windows
        ):
            if span_start <= cursor < span_end:
                cursor = span_end
        if cursor < segment_end:
            return False
    return True


class SchedulingService:
    """The deterministic planner: propose slots, accept or reject them."""

    def __init__(
        self,
        planner: PlannerService,
        tasks: TaskRepository,
        activity: ActivityService | None = None,
        audit: AuditService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            planner: Owns the calendar, the sessions, the availability and the
                clock. Taking the whole service rather than the four repositories
                is what keeps one set of validation rules for "a session that
                ends before it starts" shared by a hand-created session and one
                accepted from a suggestion.
            tasks: The candidate backlog.
            activity: Where accept/reject are recorded. Optional.
            audit: Accepted for symmetry and deliberately unused.
            settings: Application settings.
        """
        self.planner = planner
        self.tasks = tasks
        self.activity = activity
        self.audit = audit
        self.settings = settings or get_settings()

    async def suggest(
        self,
        *,
        owner: User,
        task_ids: Sequence[uuid.UUID] | None = None,
        tz: str | None = None,
        now: datetime | None = None,
    ) -> SuggestionResponse:
        """Propose slots for the caller's schedulable tasks.

        Args:
            owner: The authenticated caller. The scope is the caller's backlog
                and the caller's own calendar.
            task_ids: Restrict to these tasks. Every id is resolved through the
                scoped task lookup first, so naming somebody else's is a
                ``NotFoundError`` and not a suggestion for their work.
            tz: IANA zone the days are cut on; ``settings.planner_default_timezone``
                when absent.
            now: The instant to plan forward from. ``None`` reads the database
                clock. Passing it explicitly is what makes two runs comparable —
                and is how the engine's determinism is verified.

        Returns:
            The suggestions in the order they were placed, plus the span and zone
            they were computed for. ``reason_if_empty`` is set only when the list
            is empty and says which of the preconditions was missing.
        """
        zone = resolve_timezone(tz, self.settings)
        reference = _aware(now) if now is not None else await self._db_now()
        settings = self.settings
        today = reference.astimezone(zone).date()
        last_day = today + timedelta(days=max(0, settings.planner_lookahead_days - 1))
        window_start, window_end = day_bounds(today, zone)
        window_end = max(window_end, day_bounds(last_day, zone)[1])

        rules = await self.planner.availability.list_for_user(owner.id)
        events, _ = await self.planner.events.list_for_user(
            owner.id, start=window_start, end=window_end, limit=MAX_CANDIDATES
        )
        sessions, _ = await self.planner.sessions.list_for_user(
            owner.id, start=window_start, end=window_end, limit=MAX_CANDIDATES
        )
        candidates = await self._candidates(
            owner, today=today, last_day=last_day, task_ids=task_ids
        )

        generated_for = PlannerWindow(start_date=today, end_date=last_day, timezone=str(zone))

        if not rules:
            return SuggestionResponse(
                suggestions=[],
                generated_for=generated_for,
                reason_if_empty=(
                    "You have no availability rules, so there is nowhere to place work. "
                    "Set them with PUT /api/v1/availability first."
                ),
            )
        if not candidates:
            return SuggestionResponse(
                suggestions=[],
                generated_for=generated_for,
                reason_if_empty=(
                    f"No open task has both an estimate and a due date inside the next "
                    f"{settings.planner_lookahead_days} days."
                ),
            )

        intervals = _busy_intervals(events, sessions)
        suggestions: list[PlannerSuggestion] = []
        deadline_hit = False
        for task in candidates:
            if len(suggestions) >= MAX_SUGGESTIONS:
                break
            placed = 0
            for offset in range((task.due_date - today).days + 1):
                if placed >= task.estimated_minutes or len(suggestions) >= MAX_SUGGESTIONS:
                    break
                if (
                    len([s for s in suggestions if s.task_id == task.id])
                    >= settings.planner_max_suggestions_per_task
                ):
                    break
                day = today + timedelta(days=offset)
                emitted = self._place_for_day(
                    task=task,
                    day=day,
                    zone=zone,
                    reference=reference,
                    rules=rules,
                    intervals=intervals,
                    placed=placed,
                    suggestions=suggestions,
                )
                placed = emitted
                if emitted == 0 and offset > 0:
                    # A day that offered nothing will not offer anything later
                    # either unless a nearer deadline intervenes, so the walk
                    # continues but the day is not revisited.
                    continue
            if placed == 0 and task.due_date <= today:
                deadline_hit = True

        if not suggestions and deadline_hit:
            reason = (
                "Every task in range is already past its due date, so there is no deadline "
                "left to plan towards. Move a due date or clear the backlog first."
            )
        elif not suggestions:
            reason = (
                "No free slot before the earliest deadline: every availability window in "
                "range is already covered by an existing event or work session."
            )
        else:
            reason = None
        return SuggestionResponse(
            suggestions=suggestions, generated_for=generated_for, reason_if_empty=reason
        )

    async def accept(self, *, owner: User, suggestion: PlannerSuggestion) -> WorkSession:
        """Turn an accepted suggestion into a real work session.

        Written through :meth:`PlannerService.create_session` so an accepted
        proposal passes exactly the validation a hand-created one does — an
        accepted suggestion is not a privileged path into the table.
        """
        from app.schemas.planner import WorkSessionCreate  # local: avoids a cycle at import time

        row = await self.planner.create_session(
            owner=owner,
            data=WorkSessionCreate(
                scheduled_start=suggestion.suggested_start,
                scheduled_end=suggestion.suggested_end,
                task_id=suggestion.task_id,
            ),
        )
        await self._record(
            ActivityEvent.PLANNER_SUGGESTION_ACCEPTED,
            owner=owner,
            task_id=row.task_id,
            project_id=row.project_id,
            metadata={
                "session_id": str(row.id),
                "suggested_start": _describe(suggestion.suggested_start),
                "suggested_end": _describe(suggestion.suggested_end),
                "reason": suggestion.reason,
            },
        )
        return row

    async def reject(self, *, owner: User, suggestion: PlannerSuggestion) -> None:
        """Record that a suggestion was turned down.

        Recorded, not persisted as a preference: the engine has no learning and
        must not pretend to. The event exists so "why is this still being
        suggested?" has an answer the user can see.
        """
        await self._record(
            ActivityEvent.PLANNER_SUGGESTION_REJECTED,
            owner=owner,
            task_id=suggestion.task_id,
            metadata={
                "suggested_start": _describe(suggestion.suggested_start),
                "suggested_end": _describe(suggestion.suggested_end),
                "reason": suggestion.reason,
            },
        )

    async def conflicts(
        self,
        *,
        owner: User,
        start: date,
        end: date,
        tz: str | None = None,
    ) -> list[Conflict]:
        """Read a span and run :func:`detect_conflicts` over it.

        The read is here; the judgement is in the pure function, so the same
        rules can be exercised without a database.
        """
        zone = resolve_timezone(tz, self.settings)
        window_start, window_end = day_bounds(start, zone)
        window_end = max(window_end, day_bounds(end, zone)[1])
        events, _ = await self.planner.events.list_for_user(
            owner.id, start=window_start, end=window_end, limit=MAX_CANDIDATES
        )
        sessions, _ = await self.planner.sessions.list_for_user(
            owner.id, start=window_start, end=window_end, limit=MAX_CANDIDATES
        )
        rules = await self.planner.availability.list_for_user(owner.id)
        task_ids = {row.task_id for row in sessions if row.task_id is not None}
        tasks_by_id: dict[uuid.UUID, Task] = {}
        for task_id in sorted(task_ids, key=str):
            task = await self.tasks.get_by_id_for_user(task_id, owner.id)
            if task is not None:
                tasks_by_id[task_id] = task
        return detect_conflicts(
            events=events,
            sessions=sessions,
            availability=rules,
            tasks_by_id=tasks_by_id,
            owner_tz=str(zone),
        )

    # -- Internals -----------------------------------------------------------

    def _place_for_day(
        self,
        *,
        task: Task,
        day: date,
        zone: ZoneInfo,
        reference: datetime,
        rules: Sequence[Any],
        intervals: list[tuple[datetime, datetime, Any]],
        placed: int,
        suggestions: list[PlannerSuggestion],
    ) -> int:
        """Place as many sessions for ``task`` as this one day allows.

        Returns the updated total minutes placed for the task.
        """
        settings = self.settings
        duration = timedelta(
            minutes=min(task.estimated_minutes, settings.planner_max_session_minutes)
        )
        if duration <= timedelta(0):
            return placed
        step = timedelta(minutes=max(1, settings.planner_min_session_minutes))
        # The task's deadline is the exclusive upper bound: a session must *end*
        # by the end of the due day, so the last minute of the deadline day is
        # usable and nothing beyond it is.
        deadline = datetime.combine(
            task.due_date + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        floor_hour = time(hour=settings.planner_day_start_hour)
        ceil_hour = time(hour=settings.planner_day_end_hour)

        for window_start, window_end in availability_windows_for(rules, day):
            usable_start = max(window_start, floor_hour)
            usable_end = min(window_end, ceil_hour)
            if usable_end <= usable_start:
                continue
            cursor = datetime.combine(day, usable_start, tzinfo=zone).astimezone(UTC)
            cursor = _ceil(cursor, reference, step)
            window_close = datetime.combine(day, usable_end, tzinfo=zone).astimezone(UTC)
            avoided: list[dict[str, Any]] = []
            skipped_count = 0
            while cursor + duration <= window_close:
                end = cursor + duration
                if end > deadline:
                    # Past the deadline: this day and every later one are out.
                    return placed
                skipped = _collides(cursor, end, intervals)
                if skipped:
                    skipped_count += 1
                    if len(avoided) < MAX_EVIDENCE_SKIPS:
                        avoided.extend(
                            {
                                "kind": detail["kind"],
                                "title": detail["title"],
                                "id": detail["id"],
                                "starts_at": _describe(busy_start),
                                "ends_at": _describe(busy_end),
                            }
                            for busy_start, busy_end, detail in skipped
                            if len(avoided) < MAX_EVIDENCE_SKIPS
                        )
                else:
                    placed += int(duration.total_seconds() // 60)
                    suggestions.append(
                        _build_suggestion(
                            task=task,
                            start=cursor,
                            end=end,
                            day=day,
                            zone=zone,
                            window=(usable_start, usable_end),
                            placed=placed,
                            avoided=avoided,
                            skipped=skipped_count,
                        )
                    )
                    intervals.append(
                        (
                            cursor,
                            end,
                            {"kind": "suggestion", "title": task.title, "id": str(task.id)},
                        )
                    )
                    avoided, skipped_count = [], 0
                cursor += step
                if placed >= task.estimated_minutes:
                    # The estimate is covered; further blocks would be padding,
                    # and padding ahead of another task is how a planner buries
                    # the urgent work under the merely large.
                    return placed
        return placed

    async def _candidates(
        self,
        owner: User,
        *,
        today: date,
        last_day: date,
        task_ids: Sequence[uuid.UUID] | None,
    ) -> list[Task]:
        """The caller's schedulable tasks, in the engine's order.

        Only the three **open** statuses are read. ``completed`` and ``cancelled``
        are excluded by construction rather than filtered afterwards, so a
        finished task cannot consume a slot on the day its estimate would have
        needed it.

        ``task_ids`` is resolved through the scoped lookup for every id before
        anything is planned, so a payload naming another account's task changes
        nothing at all rather than half-applying.
        """
        wanted: set[uuid.UUID] | None = None
        if task_ids is not None:
            wanted = set()
            for task_id in task_ids:
                task = await self.tasks.get_by_id_for_user(task_id, owner.id)
                if task is None:
                    raise NotFoundError(_INVALID_TASK_ID)
                wanted.add(task_id)

        found: list[Task] = []
        for status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
            rows, _ = await self.tasks.list_for_user(
                owner.id,
                limit=MAX_CANDIDATES,
                offset=0,
                status=status.value,
                due_after=today,
                due_before=last_day,
                sort="due_date",
                order="asc",
            )
            found.extend(row for row in rows if _is_candidate(row))
        if wanted is not None:
            found = [row for row in found if row.id in wanted]
        found.sort(key=_task_order_key)
        return found

    async def _db_now(self) -> datetime:
        """The database clock, read once. See ``PlannerService._db_now``."""
        value = await self.tasks.session.scalar(select(func.now()))
        return _aware(value)

    async def _record(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self.activity is None:
            return
        await self.activity.record(
            event.value,
            user_id=owner.id,
            project_id=project_id,
            task_id=task_id,
            metadata=metadata,
        )


def _ceil(instant: datetime, reference: datetime, step: timedelta) -> datetime:
    """Move ``instant`` forward to the next slot start that is not in the past.

    Today starts part-way through, so the first candidate has to be the next grid
    point at or after *now* — never a slot that began five minutes ago. Rounding
    up rather than down is deliberate: a session cannot start in the past.
    """
    if instant >= reference:
        return instant
    gap = reference - instant
    steps = -(-gap // step)  # ceiling division on a timedelta
    return instant + steps * step


def _build_suggestion(
    *,
    task: Task,
    start: datetime,
    end: datetime,
    day: date,
    zone: ZoneInfo,
    window: tuple[time, time],
    placed: int,
    avoided: list[dict[str, Any]],
    skipped: int,
) -> PlannerSuggestion:
    """Assemble one proposal, with the sentence and the evidence behind it."""
    remaining = max(0, task.estimated_minutes - placed)
    duration_minutes = int((end - start).total_seconds() // 60)
    reason = (
        f"{duration_minutes} min on {_WEEKDAY_NAMES[day.weekday()]} {day.isoformat()} "
        f"inside your {_window_label(*window)} availability, finishing before the "
        f"{task.due_date.isoformat()} deadline; "
        f"{placed} of {task.estimated_minutes} estimated minutes placed."
    )
    if skipped:
        reason += f" Skipped {skipped} busy slot(s) to reach this one."
    evidence: dict[str, Any] = {
        "due_date": task.due_date.isoformat(),
        "estimated_minutes": task.estimated_minutes,
        "minutes_placed_so_far": placed,
        "minutes_remaining": remaining,
        "session_minutes": duration_minutes,
        "weekday": day.weekday(),
        "availability_window": {
            "starts_at": window[0].isoformat(),
            "ends_at": window[1].isoformat(),
        },
        "slot": {"starts_at": _describe(start), "ends_at": _describe(end)},
        "local_day_bounds": {
            "starts_at": _describe(day_bounds(day, zone)[0]),
            "ends_at": _describe(day_bounds(day, zone)[1]),
        },
        "avoided": avoided,
        "busy_slots_skipped": skipped,
    }
    return PlannerSuggestion(
        task_id=task.id,
        task_title=task.title,
        suggested_start=start,
        suggested_end=end,
        reason=reason,
        evidence=evidence,
    )
