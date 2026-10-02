"""The shared vocabulary of the Phase 3 work-management schema.

Projects, tasks, tags and the activity feed all describe the same handful of
states and grades, and every layer above the ORM — schemas, services, the API and
the frontend — needs to speak about them. They live here, in a module with no
model imports, so that a schema module can import an enum without importing the
tables and a repository can import both without a cycle.

Every one of these is a :class:`~enum.StrEnum`, so a member *is* its persisted
string: ``TaskStatus.TODO == "todo"`` is true, and a value read straight out of a
column can be compared against a member without unwrapping anything.

Why plain ``String`` columns rather than native PostgreSQL enums
--------------------------------------------------------------
The reasoning is :class:`app.models.user.UserRole`'s, and it is worth restating
where the whole Phase 3 vocabulary is defined, because Phase 3 multiplies the
decision from one column to five:

* **Adding a value must be an ordinary transaction.** Growing a native Postgres
  enum needs ``ALTER TYPE ... ADD VALUE``, which historically could not run
  inside a transaction block and so fails halfway through a deploy on some
  deployment paths. Shipping a new task status should be an application
  release, not a migration that can only half-apply. A string with an
  application-side enum adds the value in a plain transaction; a migration is
  only needed for a *column* change.
* **The database cannot be the only place the vocabulary is written.** Rows can
  arrive from a bulk import, a fixture, an admin script or a future service, so
  the value has to be checkable wherever the row is written — which is exactly
  what the ``validate_*`` helpers below are for, and where they can be tested.
* **Rename and delete are free.** Postgres has no ``ALTER TYPE ... RENAME
  VALUE`` before PostgreSQL 10 and no drop at all; the rows are the contract.

The trade-off is real and is not hidden: the database will accept
``status = 'in_progres'``. That is why every write path funnels through
:func:`validate_task_status` rather than trusting the column, and why a stray
value degrades to an unrenderable row rather than a corrupt one — nothing else in
the schema depends on the ordering or completeness of these values.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ActivityEvent",
    "CalendarEventType",
    "KnowledgeEntityType",
    "KnowledgeLinkType",
    "NoteStatus",
    "ProjectPriority",
    "ProjectStatus",
    "ResourceType",
    "TaskPriority",
    "TaskStatus",
    "WorkSessionStatus",
    "validate_activity_event",
    "validate_calendar_event_type",
    "validate_knowledge_entity_type",
    "validate_knowledge_link_type",
    "validate_note_status",
    "validate_project_priority",
    "validate_project_status",
    "validate_resource_type",
    "validate_task_priority",
    "validate_task_status",
    "validate_work_session_status",
]


class ProjectStatus(StrEnum):
    """Where a project sits in its life.

    A project is never deleted in Phase 3; it is archived and then left alone, so
    the last two members — and only the last two — are terminal. A :class:`ProjectStatus`
    is persisted on ``projects.status``.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectPriority(StrEnum):
    """How much a project competes for attention.

    Deliberately the same four grades as :class:`TaskPriority`: a project and
    the work inside it are prioritised on one scale, so the UI can sort a
    project and its tasks with the same control. Persisted on
    ``projects.priority``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(StrEnum):
    """Where a task sits on the board.

    ``COMPLETED`` and ``CANCELLED`` are the two states a task is *not* done in.
    Keeping "dropped" separate from "finished" is what lets the dashboard's
    overdue query exclude the real backlog without also counting work that was
    deliberately abandoned. Persisted on ``tasks.status``.
    """

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """How much a task competes for attention.

    The same four grades as :class:`ProjectPriority`; see that class for why the
    two scales are deliberately identical. Persisted on ``tasks.priority``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CalendarEventType(StrEnum):
    """What a calendar entry *is*, as opposed to where it sits in time.

    The calendar carries three different kinds of row — something to attend, time
    deliberately not spent working, and a date that means nothing until the work
    lands on it — and they behave differently enough to be worth naming. A
    ``meeting`` blocks a slot; a ``break`` releases one; a ``deadline`` does
    neither, it only bounds the scheduler. A calendar that had one generic
    ``event`` member would force every one of those rules to be re-derived from
    the title at read time.

    ``OTHER`` is a genuine member rather than a "no type supplied" hole: a user
    importing a calendar from elsewhere should not have to lie about what an
    appointment is to make it appear. Persisted on ``calendar_events.event_type``.
    """

    WORK = "work"
    STUDY = "study"
    MEETING = "meeting"
    PERSONAL = "personal"
    BREAK = "break"
    DEADLINE = "deadline"
    OTHER = "other"


class WorkSessionStatus(StrEnum):
    """Where a work session sits between planned and finished.

    ``ACTIVE`` is the running timer, and it is the only state with a live
    ``actual_start``. Exactly one session per user is expected to be active at a
    time — which the service layer enforces rather than the database, because
    "the most recent session still running" is a query, not an invariant.

    It lives here rather than beside the table because two layers above the ORM
    need to name it: the request schemas (to validate a status on the way in)
    and the service (to compare against ``.ACTIVE``). A member that only its own
    table used would be a local detail; one that the API surface speaks is
    vocabulary, and vocabulary belongs in this module. Persisted on
    ``work_sessions.status``.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NoteStatus(StrEnum):
    """Where a note sits between "I just typed this" and "this is done".

    Deliberately three members and not a general-purpose workflow: a knowledge
    base that can model fourteen note states has not been given a requirement it
    can satisfy, and every extra state is a value the list filters and the
    counts have to keep exhaustive.

    * ``DRAFT`` is the default and what an autosaving editor produces. It is not
      "unfinished" in any judgemental sense — it is simply not asserted yet.
    * ``PUBLISHED`` is the user's explicit "this is real now" and is the only
      state a link target is *meant* to point at. Nothing in the schema forbids
      linking to a draft, but the UI filters to published by default.
    * ``ARCHIVED`` is set aside, and is reachable only through the archive
      action rather than through a PATCH, so that the archived set cannot be
      left by a generic edit.

    Persisted on ``notes.status``.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class KnowledgeEntityType(StrEnum):
    """Which kind of thing an edge endpoint names.

    ``knowledge_links`` is **polymorphic**: one table holds edges between notes,
    concepts and resources rather than one table per pair (there are six pairs,
    and a seventh entity type would make seven tables). The price is that
    ``source_id``/``target_id`` carry **no foreign key** — a FK can only point at
    one table, and the table it would point at is decided by this column. So the
    database cannot stop a dangling or cross-user edge, and
    :meth:`~app.services.knowledge_service.KnowledgeService.create_link` resolves
    both endpoints through an owner-scoped query before writing one. That comment
    is the security model of this table; do not read it as decoration.

    Persisted on ``knowledge_links.source_type`` and ``.target_type``.
    """

    NOTE = "note"
    CONCEPT = "concept"
    RESOURCE = "resource"


class KnowledgeLinkType(StrEnum):
    """What an edge between two knowledge objects *means*.

    Extensible by design: the spec explicitly forbids hardcoding relationship
    types through the application, so this is one table the UI filters on rather
    than a switch statement per screen. Adding a member here is an ordinary
    transaction and needs no migration.

    The six members come straight from the Phase 5 brief::

        NOTE -> REFERENCES -> NOTE
        NOTE -> EXPLAINS   -> CONCEPT
        CONCEPT -> RELATED_TO -> CONCEPT
        RESOURCE -> SUPPORTS -> CONCEPT
        PROJECT -> USES -> CONCEPT
        TASK -> REQUIRES -> CONCEPT

    The last two name projects and tasks as endpoints, which this phase does not
    yet model: the entity-type vocabulary is deliberately the three Phase 5
    objects, and linking a project to a concept is a later phase's extension to
    both enums rather than a value that exists but can never resolve.

    ``REFERENCES`` is the one the spec cares most about: it is what produces
    backlinks, and a note is far more often the *target* of a reference than the
    source, which is exactly why the link table carries a ``(target_type,
    target_id)`` index.

    Persisted on ``knowledge_links.link_type``.
    """

    REFERENCES = "references"
    EXPLAINS = "explains"
    RELATED_TO = "related_to"
    SUPPORTS = "supports"
    USES = "uses"
    REQUIRES = "requires"


class ResourceType(StrEnum):
    """What kind of external thing a resource points at.

    ``OTHER`` is a real member rather than a nullable column with no default: a
    user saving a link to something that fits none of the eight kinds should not
    have to lie, and "other" is also the value a type filter matches to mean
    "the ones I did not classify". Persisted on ``resources.resource_type``.
    """

    ARTICLE = "article"
    VIDEO = "video"
    COURSE = "course"
    DOCUMENTATION = "documentation"
    REPOSITORY = "repository"
    PAPER = "paper"
    WEBSITE = "website"
    OTHER = "other"


class ActivityEvent(StrEnum):
    """The work-management events worth leaving a trail of.

    Separate from :class:`app.models.audit.AuditEvent`, which records
    *security* events. These record what happened to the work: the same feed
    answers "what did I do to this project last Tuesday" and "who closed this".
    They live in ``activity_events.event_type``, which — like the audit column —
    is filtered on by consumers, so the spelling is part of the contract and is
    never renamed once rows exist.

    The lifecycle members mirror the status transitions: ``TASK_STARTED`` is the
    ``todo -> in_progress`` edge, ``TASK_COMPLETED`` the edge into ``completed``,
    ``TASK_REOPENED`` the edge back out of it, and ``TASK_BLOCKED`` the edge into
    ``blocked``. ``TASK_SCHEDULED`` is the deliberate edge *not* tied to a status
    column — it is the "I put a date on this" moment, which happens far more
    often than the task actually starting.
    """

    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_COMPLETED = "project_completed"
    PROJECT_ARCHIVED = "project_archived"
    PROJECT_RESTORED = "project_restored"
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_REOPENED = "task_reopened"
    TASK_BLOCKED = "task_blocked"
    TASK_PRIORITY_CHANGED = "task_priority_changed"
    TASK_DUE_DATE_CHANGED = "task_due_date_changed"
    TASK_DELETED = "task_deleted"
    TASK_SCHEDULED = "task_scheduled"
    # -- Phase 4 (Planner) --------------------------------------------------
    # The calendar and the clock are separate systems that both move tasks, so
    # both are recorded: the *session* events are about time actually spent,
    # the *calendar* events about time deliberately reserved, and neither
    # implies the other.
    WORK_SESSION_STARTED = "work_session_started"
    WORK_SESSION_COMPLETED = "work_session_completed"
    CALENDAR_EVENT_CREATED = "calendar_event_created"
    CALENDAR_EVENT_UPDATED = "calendar_event_updated"
    CALENDAR_EVENT_DELETED = "calendar_event_deleted"
    TASK_RESCHEDULED = "task_rescheduled"
    PLANNER_SUGGESTION_ACCEPTED = "planner_suggestion_accepted"
    PLANNER_SUGGESTION_REJECTED = "planner_suggestion_rejected"
    # -- Phase 5 (Knowledge) -------------------------------------------------
    # Phase 6 analytics and the Phase 8-9 learning work read this feed to learn
    # *which* knowledge a user actually works with and revisits, so the
    # lifecycle moments are members of their own rather than a generic
    # `NOTE_UPDATED`. `NOTE_REVISION_CREATED` and `NOTE_VIEWED` from the spec are
    # deliberately absent: a revision is recorded by the edit that caused it
    # (already `NOTE_UPDATED`) and a read is not an event on a work feed.
    NOTE_CREATED = "note_created"
    NOTE_UPDATED = "note_updated"
    NOTE_ARCHIVED = "note_archived"
    NOTE_PUBLISHED = "note_published"
    NOTE_RESTORED = "note_restored"
    NOTE_REVISION_RESTORED = "note_revision_restored"
    CONCEPT_CREATED = "concept_created"
    RESOURCE_CREATED = "resource_created"
    BOOKMARK_CREATED = "bookmark_created"
    KNOWLEDGE_LINK_CREATED = "knowledge_link_created"
    KNOWLEDGE_LINK_REMOVED = "knowledge_link_removed"


def validate_project_status(value: ProjectStatus | str) -> ProjectStatus:
    """Coerce a stored or user-supplied value into a :class:`ProjectStatus`.

    Raises:
        ValueError: If the value is not a known status. The column is a string,
            so an unrecognised status is storable and would leave a project
            that no list filter — none of which match an unknown value — can
            ever find again.
    """
    if isinstance(value, ProjectStatus):
        return value
    try:
        return ProjectStatus(value)
    except ValueError:
        raise ValueError(f"Unknown project status: {value!r}") from None


def validate_project_priority(value: ProjectPriority | str) -> ProjectPriority:
    """Coerce a stored or user-supplied value into a :class:`ProjectPriority`.

    Raises:
        ValueError: If the value is not a known priority.
    """
    if isinstance(value, ProjectPriority):
        return value
    try:
        return ProjectPriority(value)
    except ValueError:
        raise ValueError(f"Unknown project priority: {value!r}") from None


def validate_task_status(value: TaskStatus | str) -> TaskStatus:
    """Coerce a stored or user-supplied value into a :class:`TaskStatus`.

    Raises:
        ValueError: If the value is not a known status. This is the boundary
            that keeps the Kanban board's five columns exhaustive: a task
            carrying an unrecognised status belongs to no column and silently
            disappears from the board.
    """
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError:
        raise ValueError(f"Unknown task status: {value!r}") from None


def validate_task_priority(value: TaskPriority | str) -> TaskPriority:
    """Coerce a stored or user-supplied value into a :class:`TaskPriority`.

    Raises:
        ValueError: If the value is not a known priority.
    """
    if isinstance(value, TaskPriority):
        return value
    try:
        return TaskPriority(value)
    except ValueError:
        raise ValueError(f"Unknown task priority: {value!r}") from None


def validate_work_session_status(value: WorkSessionStatus | str) -> WorkSessionStatus:
    """Coerce a stored or user-supplied value into a :class:`WorkSessionStatus`.

    Raises:
        ValueError: If the value is not a known status. An unrecognised status
            belongs to no filter, so the session drops out of the "what am I
            running right now" and the "what did I finish" views alike.
    """
    if isinstance(value, WorkSessionStatus):
        return value
    try:
        return WorkSessionStatus(value)
    except ValueError:
        raise ValueError(f"Unknown work session status: {value!r}") from None


def validate_calendar_event_type(value: CalendarEventType | str) -> CalendarEventType:
    """Coerce a stored or user-supplied value into a :class:`CalendarEventType`.

    Raises:
        ValueError: If the value is not a known type. Same reasoning as the
            other validators here: an unrecognised ``event_type`` belongs to no
            filter, so the event becomes invisible to the "show me my meetings"
            view rather than appearing under an unknown heading.
    """
    if isinstance(value, CalendarEventType):
        return value
    try:
        return CalendarEventType(value)
    except ValueError:
        raise ValueError(f"Unknown calendar event type: {value!r}") from None


def validate_activity_event(value: ActivityEvent | str) -> ActivityEvent:
    """Coerce a stored or user-supplied value into an :class:`ActivityEvent`.

    Raises:
        ValueError: If the value is not a known event. Same reasoning as
            :func:`app.models.audit.validate_audit_event`: a mistyped event name
            is written as a row no filter can ever match, so the history silently
            develops a hole where an event should be.
    """
    if isinstance(value, ActivityEvent):
        return value
    try:
        return ActivityEvent(value)
    except ValueError:
        raise ValueError(f"Unknown activity event: {value!r}") from None


def validate_note_status(value: NoteStatus | str) -> NoteStatus:
    """Coerce a stored or user-supplied value into a :class:`NoteStatus`.

    Raises:
        ValueError: If the value is not a known status. The column is a string,
            so an unrecognised status is storable and would leave a note that no
            list filter — none of which match an unknown value — can ever find
            again.
    """
    if isinstance(value, NoteStatus):
        return value
    try:
        return NoteStatus(value)
    except ValueError:
        raise ValueError(f"Unknown note status: {value!r}") from None


def validate_knowledge_entity_type(value: KnowledgeEntityType | str) -> KnowledgeEntityType:
    """Coerce a value into a :class:`KnowledgeEntityType`.

    Raises:
        ValueError: If the value is not a known entity type. This one is more
            than a data-quality concern: ``knowledge_links`` has no foreign key
            on its endpoints precisely because this column chooses the table, so
            an unrecognised type names a table that does not exist and the edge
            can never be resolved, joined or rendered.
    """
    if isinstance(value, KnowledgeEntityType):
        return value
    try:
        return KnowledgeEntityType(value)
    except ValueError:
        raise ValueError(f"Unknown knowledge entity type: {value!r}") from None


def validate_knowledge_link_type(value: KnowledgeLinkType | str) -> KnowledgeLinkType:
    """Coerce a value into a :class:`KnowledgeLinkType`.

    Raises:
        ValueError: If the value is not a known link type. ``link_type`` is half
            of the link table's unique constraint, so an unrecognised value does
            not merely render badly — it defeats the constraint that stops the
            same edge being recorded twice.
    """
    if isinstance(value, KnowledgeLinkType):
        return value
    try:
        return KnowledgeLinkType(value)
    except ValueError:
        raise ValueError(f"Unknown knowledge link type: {value!r}") from None


def validate_resource_type(value: ResourceType | str) -> ResourceType:
    """Coerce a stored or user-supplied value into a :class:`ResourceType`.

    Raises:
        ValueError: If the value is not a known type, for the same reason as
            :func:`validate_calendar_event_type` — a resource carrying one
            belongs to no type filter and disappears from the filtered view.
    """
    if isinstance(value, ResourceType):
        return value
    try:
        return ResourceType(value)
    except ValueError:
        raise ValueError(f"Unknown resource type: {value!r}") from None
