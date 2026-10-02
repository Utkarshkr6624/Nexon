"""Project request/response models.

A project is the container work is organised into: a named effort with an
optional window and a lifecycle of its own.

Two decisions shape this module and are worth stating once here rather than in
each class.

**``status`` is not settable through :class:`ProjectUpdate`.** A project's status
is a lifecycle, not a field: completing one stamps ``completed_at`` and writing
an event, archiving one stamps ``archived_at``, restoring one clears it. A
blanket PATCH carrying ``status`` would let a client set ``completed_at``'s
premise without either of those side effects, leaving a project that claims to
be finished and carries no evidence of finishing. Those transitions get their
own endpoints so the rules cannot be bypassed. ``ProjectUpdate`` answers 422 for
the field instead of dropping it, for the reason spelled out on
:class:`~app.schemas.user.UserUpdate`.

**Percentages are computed here, not stored.** :class:`ProjectSummary` derives
``progress_percent`` from the two counts it is given, so the number a client
renders cannot drift away from the counts beside it. The counts themselves come
from the service's aggregate query — deriving them per row here would mean a
query per project in a list response.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ProjectPriority

__all__ = [
    "MAX_PROJECT_DESCRIPTION_LENGTH",
    "MAX_PROJECT_NAME_LENGTH",
    "ProjectCreate",
    "ProjectRead",
    "ProjectStats",
    "ProjectSummary",
    "ProjectUpdate",
]

#: Must match ``projects.name`` in ``app.models.project``.
MAX_PROJECT_NAME_LENGTH = 200
#: Bounds free text on the way in so an unbounded ``description`` cannot be used
#: to store something that is not prose.
MAX_PROJECT_DESCRIPTION_LENGTH = 2000

#: Both are dates, not datetimes: "the project targets the 3rd" is the whole
#: requirement, and a time component would invite a comparison that silently
#: depends on the caller's timezone.


class _ProjectTextNormaliser:
    """Shared before-validator trimming the project's free-text fields.

    Leading and trailing whitespace is stripped rather than rejected because it
    is invisible in the UI that produced it. A blank ``description`` becomes
    ``None`` so a client that clears the field can send ``""`` and mean "unset"
    rather than storing a column of whitespace. ``name`` is stripped but *not*
    mapped to ``None``: an empty name is a mistake, and leaving it as ``""``
    lets ``min_length`` reject it as one instead of silently clearing the name.
    """

    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before", check_fields=False)
    @classmethod
    def _blank_description_is_unset(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class ProjectCreate(_ProjectTextNormaliser, BaseModel):
    """Creation payload for a project.

    ``status`` is absent: a project is always created ``planned`` and reaches
    the other states through its transition endpoints. As in
    :class:`~app.schemas.user.UserCreate`, unexpected fields are ignored rather
    than rejected — the strictness here is about the *update* path, where a
    client has a reason to believe a field it sent was applied.
    """

    name: str = Field(
        min_length=1,
        max_length=MAX_PROJECT_NAME_LENGTH,
        description="Display name, unique only within the owner's projects.",
        examples=["Q4 Platform Migration"],
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_PROJECT_DESCRIPTION_LENGTH,
        examples=["Move the billing stack off the legacy host."],
    )
    priority: ProjectPriority = Field(
        default=ProjectPriority.MEDIUM,
        description="Relative urgency; independent of the project's lifecycle status.",
    )
    start_date: date | None = Field(default=None, description="First day of the planned window.")
    target_date: date | None = Field(
        default=None, description="Date the project aims to be finished by."
    )

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        """Reject a window that ends before it starts.

        A target date earlier than the start date is never a state any client
        meant to express — there is no "working backwards" project — so it is
        caught here and answered as a 422 rather than being persisted and
        rendered as an impossible schedule.
        """
        if (
            self.start_date is not None
            and self.target_date is not None
            and self.target_date < self.start_date
        ):
            raise ValueError("target_date must not be earlier than start_date.")
        return self


class ProjectUpdate(_ProjectTextNormaliser, BaseModel):
    """Partial update of a project's details.

    ``status`` is deliberately absent — see this module's docstring. Everything
    here is a plain attribute of a project; the transitions are not.

    ``extra="forbid"`` is what turns "that field is not updatable here" into an
    answer the client can act on. Pydantic's default is ``"ignore"``, under
    which a client sending ``{"status": "completed"}`` gets a cheerful 200 with
    the field dropped, and a caller who reads that as "the project is finished"
    has believed something false about their own project. Rejecting names the
    offending field in a 422 so the client is told the route does not own it.

    **The date window is only checked when both dates are in the payload.** A
    PATCH that moves ``start_date`` past a stored ``target_date`` carries no
    ``target_date`` for the schema to compare against, so the service applies
    the same rule against the persisted row before writing. Enforcing it here
    for the both-present case keeps the common request self-contained and keeps
    the invariant a 422 rather than a 500.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_PROJECT_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_PROJECT_DESCRIPTION_LENGTH)
    priority: ProjectPriority | None = None
    start_date: date | None = None
    target_date: date | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        """Reject a window that ends before it starts, when both dates are sent."""
        if (
            "target_date" in self.model_fields_set
            and "start_date" in self.model_fields_set
            and self.start_date is not None
            and self.target_date is not None
            and self.target_date < self.start_date
        ):
            raise ValueError("target_date must not be earlier than start_date.")
        return self


class ProjectRead(BaseModel):
    """A project as returned by the project endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str | None
    status: str = Field(description="One of the ``ProjectStatus`` values.")
    priority: str = Field(description="One of the ``ProjectPriority`` values.")
    start_date: date | None
    target_date: date | None
    completed_at: datetime | None = Field(
        default=None,
        description="Stamped by the complete transition, never by a field update.",
    )
    archived_at: datetime | None = Field(
        default=None,
        description="Stamped by the archive transition, cleared by restore.",
    )
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    """The lean shape a project list or board renders.

    Counts come from the service's aggregate query rather than from the row, and
    ``progress_percent`` is derived from them here so the percentage on screen is
    always the percentage of the number beside it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    priority: str
    target_date: date | None
    task_count: int = Field(default=0, ge=0, description="Tasks in the project.")
    completed_task_count: int = Field(
        default=0,
        ge=0,
        description="Of those, the ones whose status is ``completed``.",
    )
    progress_percent: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="completed_task_count / task_count, as a percentage.",
    )

    @model_validator(mode="after")
    def _derive_progress(self) -> Self:
        # A project with no tasks has no progress to report. Zero rather than
        # None: an empty board is not "unknown", it is not started, and a null
        # here would push that decision onto every client.
        self.progress_percent = (
            round(self.completed_task_count / self.task_count * 100, 1) if self.task_count else 0.0
        )
        return self

    @classmethod
    def build(
        cls, row: ProjectRead | Any, *, task_count: int, completed_task_count: int
    ) -> ProjectSummary:
        """Build a summary from a project row plus its aggregate counts.

        **The counts come from the caller's grouped query, not from the row.**
        Counting tasks per project inside this model would mean one query per
        row of a paginated list; the list endpoints run a single ``GROUP BY``
        and hand the results in here.

        Args:
            row: The persisted project, read through ``from_attributes``.
            task_count: Tasks belonging to the project.
            completed_task_count: Of those, the ones with status ``completed``.

        Returns:
            The summary, with ``progress_percent`` already derived.
        """
        # Round-tripped through the dict rather than overlaid with
        # ``model_copy(update=...)``: an overlay skips validation, and the
        # percentage is derived by the validator — so an overlay would publish
        # the 0.0 computed from the row's absent counts and the counts beside it
        # would disagree with it.
        data = cls.model_validate(row).model_dump()
        data["task_count"] = task_count
        data["completed_task_count"] = completed_task_count
        return cls.model_validate(data)


class ProjectStats(BaseModel):
    """Project counts for the owner's dashboard.

    The five status buckets partition the owner's projects, so on a complete
    partition they sum to ``total``. They are not asserted to do so here: the
    status vocabulary can grow, and a new state would make a strict check
    reject correct counts rather than catch a wrong one.
    """

    total: int = Field(ge=0)
    active: int = Field(ge=0)
    completed: int = Field(ge=0)
    planned: int = Field(ge=0)
    on_hold: int = Field(ge=0)
    archived: int = Field(ge=0)
