"""Phase 4 planner tables: calendar events, work sessions and availability.

Three tables, three different kinds of time
-------------------------------------------
``calendar_events`` is time *reserved* — a meeting, a block of study, a deadline.
``work_sessions`` is time *spent* (or planned to be spent) on one task, and is
what the clock actually runs against. ``availability_rules`` is neither: it is
the recurring answer to "when are you generally free". They are separate tables
rather than columns on one because their lifecycles differ — an event is deleted
the day it passes, a session is kept as history, and an availability rule is
edited rarely and applies to every future day at once. Collapsing them would
mean every delete is also a question about what to keep.

The timezone rule for this module
---------------------------------
Every stored *instant* is timezone-aware UTC: ``starts_at``, ``ends_at``,
``scheduled_start``, ``scheduled_end``, ``actual_start``, ``actual_end``,
``completed_at`` and both mixin timestamps are ``DateTime(timezone=True)``.
Callers may send any offset; PostgreSQL normalises to UTC on write and the
application re-attaches the viewer's zone on read. A naive timestamp here would
be a stored wall-clock reading whose instant depends on who is looking, which is
exactly the silent shift the spec forbids.

``availability_rules.starts_at`` and ``.ends_at`` are the deliberate exception,
and they are ``Time`` (naive) rather than ``DateTime`` because "09:00 to 17:00,
Mondays" is *not* an instant — it has no date, so it has no UTC offset. Storing
it as a timestamp would force one onto the row and freeze the rule at whatever
offset happened to be in effect when it was written, which then drifts twice a
year under DST. The wall-clock reading is kept verbatim and combined with a date
*and a zone* at the moment it becomes an instant.

Every "now" comparison runs against the database clock (``func.now()``) rather
than a Python-computed value, so a running session cannot be closed or
rescheduled against a stale application-side clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CalendarEventType, WorkSessionStatus, validate_work_session_status

__all__ = [
    "DEFAULT_CALENDAR_EVENT_TYPE",
    "DEFAULT_WORK_SESSION_STATUS",
    "AvailabilityRule",
    "CalendarEvent",
    "WorkSession",
    # Re-exported from `app.models.enums`, where the member itself is defined,
    # so that callers who reach for the table and callers who reach for the
    # vocabulary get the same object from either import.
    "WorkSessionStatus",
    "validate_work_session_status",
]

#: Sized for a single-line heading — anything longer is a description.
_MAX_TITLE_LENGTH = 200
#: Sized for the longest member of :class:`CalendarEventType` (``personal``) and
#: :class:`WorkSessionStatus` (``cancelled``) with room to spare.
_MAX_ENUM_LENGTH = 16
#: ``09:00`` or ``Deep work block`` — a label, not a title.
_MAX_LABEL_LENGTH = 80


#: What a new event gets when the caller does not choose. ``other`` rather than
#: ``work``: guessing that an unlabelled block is work would inflate the day's
#: scheduled minutes, and "unknown" is the honest default.
DEFAULT_CALENDAR_EVENT_TYPE = CalendarEventType.OTHER.value
DEFAULT_WORK_SESSION_STATUS = WorkSessionStatus.PLANNED.value


class CalendarEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One entry on the calendar: a reserved span of time.

    The span is absolute (:attr:`starts_at` to :attr:`ends_at`) and may optionally
    be linked to the work it is *about* (:attr:`task_id`, :attr:`project_id`)
    without being one — a meeting about a project has no task, and a study block
    may have neither. ``all_day`` is a display hint, not a different shape: the
    span is still two instants, so a range query over a month needs no special
    case.
    """

    __tablename__ = "calendar_events"

    __table_args__ = (
        # A zero-length or inverted span is not a short meeting, it is a row that
        # every consumer misreads: conflict detection computes the overlap as
        # `min(end) - max(start)`, which goes negative; the overload report
        # subtracts it from the day's total; and a month view renders it at a
        # width no renderer can divide by. The Python check that would catch it
        # lives in the request process, while rows also arrive from imports and
        # scripts, so the invariant is made total here and named so a violation
        # names itself.
        #
        # Strict `>` rather than `>=`: a zero-length block reserves nothing, so
        # allowing it would let a caller claim a slot it does not occupy.
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_calendar_events_ends_after_starts",
        ),
        Index(
            "ix_calendar_events_owner_starts_at",
            "owner_id",
            "starts_at",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(_MAX_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_CALENDAR_EVENT_TYPE,
        nullable=False,
    )
    # Both links CASCADE, like every other Phase 3/4 ownership link: an event
    # that outlives the task it was about is a booking for nothing. (Contrast
    # `activity_events`, whose rows are the record and must outlive the thing
    # they describe.)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    #: Timezone-aware UTC. See the module docstring.
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    #: Timezone-aware UTC, strictly after :attr:`starts_at` (CHECK-enforced).
    ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # Display hint only; the span above remains the truth. Defaults to false so
    # an event created through the API without it behaves like a timed event.
    all_day: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    location: Mapped[str | None] = mapped_column(String(_MAX_TITLE_LENGTH), nullable=True)
    #: Timezone-aware UTC. Stamped when the event is ticked off; not derivable
    #: from `updated_at`, which every later edit rewrites.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Index notes:
    #
    # * `ix_calendar_events_owner_starts_at` is the one that earns its keep. The
    #   week and month planner views are `owner_id = ? AND starts_at < ?` with an
    #   overlap term on the end — an equality on the owner and a range on the
    #   start. The single-column owner index serves only the first term, so
    #   PostgreSQL fetches every event the user has ever had (a table that only
    #   grows, since past events are not pruned) and filters in the heap. With
    #   the composite the range resolves inside the index and rows already arrive
    #   in `starts_at` order, which is the order the day view renders in anyway.
    #   `ends_at` is left out on purpose: a btree cannot use a second range term,
    #   so including it would widen the index without narrowing any scan.
    # * `ix_calendar_events_project_id` and `ix_calendar_events_task_id` serve
    #   the two filters the event list exposes (`?project_id=`, `?task_id=`) and
    #   the "what is booked around this task" question the task panel asks. Both
    #   are selective — the id is known before the query — so a single column is
    #   the whole index.
    # * `event_type` deliberately gets none: seven values installation-wide is
    #   far too low a selectivity for PostgreSQL to choose it over a scan, and
    #   the planner never filters on it.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CalendarEvent id={self.id} title={self.title!r} starts_at={self.starts_at}>"

    @property
    def event_type_enum(self) -> CalendarEventType | None:
        """The event's type as a member, or ``None`` if the row drifted.

        ``None`` rather than a fallback, for the reason
        :attr:`app.models.task.Task.status_enum` returns ``None``: a fallback
        would hide a data problem behind a plausible-looking label. Validate on
        write with :func:`app.models.enums.validate_calendar_event_type`.
        """
        try:
            return CalendarEventType(self.event_type)
        except ValueError:
            return None


class WorkSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A block of time earmarked for — and possibly spent on — one task.

    Four timestamps, two states. :attr:`scheduled_start`/:attr:`scheduled_end`
    is what the planner booked; :attr:`actual_start`/:attr:`actual_end` is what
    the clock recorded. They are kept apart rather than overwritten so a session
    that ran late still shows the slot it was *supposed* to occupy — which is
    what the overload report and the scheduler's collision check both need, and
    what a single overwritten pair would lose.

    :attr:`actual_minutes` is therefore written exactly once, at stop, from
    ``actual_start``/``actual_end`` and rounded once — the accumulation error the
    note in :mod:`app.models.task` warns about is avoided by never adding to it
    in Python. The *task's* running total is a different column with a different
    concern: that one is incremented in a single SQL statement so the row lock
    does the serialising.
    """

    __tablename__ = "work_sessions"

    __table_args__ = (
        # Same argument as `ck_calendar_events_ends_after_starts`: an inverted or
        # zero-length session contributes negative minutes to the day's overload
        # figure and makes every interval-overlap test in the scheduler wrong.
        # Strict `>`, because a session that occupies no time is not a session.
        CheckConstraint(
            "scheduled_end > scheduled_start",
            name="ck_work_sessions_scheduled_end_after_start",
        ),
        # A session that started cannot finish before it began, whatever the
        # schedule said. Enforced because `actual_end` is written by a stop
        # request that may be issued seconds or days later, and a negative
        # duration would silently subtract from the task's tracked time.
        CheckConstraint(
            "actual_end IS NULL OR actual_start IS NULL OR actual_end > actual_start",
            name="ck_work_sessions_actual_end_after_start",
        ),
        Index(
            "ix_work_sessions_owner_scheduled_start",
            "owner_id",
            "scheduled_start",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Both nullable: a session can be a deliberate "unassigned focus block", and a
    # project can be worked on directly without picking a task. The scheduler
    # only ever proposes sessions that have a task.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    #: Timezone-aware UTC. See the module docstring.
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    #: Timezone-aware UTC, strictly after :attr:`scheduled_start`.
    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    #: Timezone-aware UTC, set by ``POST /work-sessions/{id}/start``. Compared
    #: against the *database* clock, never a Python-side ``now()``.
    actual_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    #: Timezone-aware UTC, set by ``POST /work-sessions/{id}/stop``.
    actual_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Copied from the task at planning time so the overload ratio and the
    # suggestion explanation can be computed without re-reading the task.
    # Nullable, because "not estimated" is a different answer from "zero".
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NOT NULL with a zero default, so a planned-but-unstarted session has a
    # duration of zero rather than NULL and arithmetic never has to null-check.
    actual_minutes: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_WORK_SESSION_STATUS,
        nullable=False,
    )

    # Index notes:
    #
    # * `ix_work_sessions_owner_scheduled_start` serves the planner's range
    #   queries — the week view, the day view, and the `?from=&to=` filter on
    #   `GET /work-sessions` — all of which are `owner_id = ? AND
    #   scheduled_start < ?` with an overlap term on the end. Same argument, and
    #   the same deliberate omission of the second range column, as
    #   `ix_calendar_events_owner_starts_at`.
    # * `ix_work_sessions_task_id` serves "all the time spent on this task",
    #   asked on every task detail render and by the tracked-time total.
    # * `ix_work_sessions_project_id` serves the project roll-up of effort. Both
    #   are selective and known before the query, so a single column is the whole
    #   index.
    # * `status` gets none: four values installation-wide, and the one query that
    #   filters on it ("what am I running right now") is already answered by
    #   `ix_work_sessions_owner_scheduled_start` plus a client-side check.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WorkSession id={self.id} status={self.status!r} start={self.scheduled_start}>"

    @property
    def status_enum(self) -> WorkSessionStatus | None:
        """The session's status as a member, or ``None`` if the row drifted."""
        try:
            return WorkSessionStatus(self.status)
        except ValueError:
            return None


class AvailabilityRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recurring "I am free between these local times" answer.

    Recurring rather than a set of dated rows because the overwhelmingly common
    answer is "weekdays 09:00 to 17:00", and expressing that as 260 dated rows
    would make the common case a bulk insert and the read a range scan, in order
    to store one fact.

    Why naive ``Time`` and not ``DateTime``
    --------------------------------------
    ``09:00`` is not an instant. Written as a timestamp it would need an offset,
    the offset would be whatever the writer's zone happened to be, and the rule
    would then be pinned to that offset — silently wrong for half the year the
    moment the user crosses a DST boundary, and wrong in a way no reader could
    detect. Keeping the naive wall-clock reading preserves what the user actually
    said; the date and the zone are supplied when the rule is turned into
    concrete instants.

    ``weekday`` is 0=Monday .. 6=Sunday, matching ``date.weekday()``, not
    PostgreSQL's ``DOW`` (0=Sunday). Python is where these rows become instants,
    so Python's convention is the one that must not need translating.
    """

    __tablename__ = "availability_rules"

    __table_args__ = (
        # A user may not keep two windows for Monday that begin at the same
        # minute: "which one wins" has no answer the conflict detector or the
        # scheduler could implement consistently, and `PUT /availability` replaces
        # the whole set, so a duplicate is a bug rather than a mergeable state.
        UniqueConstraint(
            "owner_id",
            "weekday",
            "starts_at",
            name="uq_availability_rules_owner_weekday_start",
        ),
        # 0..6 inclusive. An out-of-range weekday matches no date, so it is a row
        # that silently does nothing. Refused here rather than in the service,
        # because the writer may be an import or a script in another process.
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_availability_rules_weekday_range",
        ),
        # Naive times compare correctly here: both sides carry no zone, so `>`
        # is a plain wall-clock comparison. A window that ends when it starts (or
        # before) covers no time and would make the day's available minutes
        # negative.
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_availability_rules_ends_after_starts",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: 0=Monday .. 6=Sunday. See the class docstring.
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Naive local wall-clock time. See the class docstring.
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    #: Naive local wall-clock time, strictly after :attr:`starts_at`.
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)
    label: Mapped[str | None] = mapped_column(String(_MAX_LABEL_LENGTH), nullable=True)

    # Index notes:
    #
    # * The unique constraint above already builds an index whose leading column
    #   is `owner_id` and would serve "all my rules" on its own. The plain index
    #   is kept because the column contract asks for it, and because it is the
    #   narrower of the two for the query that runs on every planner view —
    #   `WHERE owner_id = ? ORDER BY weekday, starts_at` over a handful of rows.
    # * `weekday` alone gets nothing: seven values across every user on the
    #   installation, and rules are always read per owner.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AvailabilityRule id={self.id} weekday={self.weekday} "
            f"{self.starts_at}-{self.ends_at}>"
        )
