"""Phase 7 recommendations: turning a stored risk into a proposed, justified action.

A detection pass produces *conditions*; this module produces *suggestions*. The
distinction is the whole point of the two tables, and it is why every rule here
reads a :class:`~app.models.risk.Risk` rather than the analytics behind it: the
numbers a suggestion quotes must be the numbers that were persisted with the
risk, or the Risk Center and the recommendation list would be able to disagree
about the same fact.

A recommendation is four things at once
--------------------------------------
The brief asks for **WHAT**, **WHY**, **RELATED DATA** and a **SUGGESTED ACTION**
on every suggestion, and it is a failure of a recommendation to carry all four —
not of the UI to render them. So the split is structural rather than editorial:

* ``title`` is WHAT, a short noun phrase naming the thing to do.
* ``description`` is the SUGGESTED ACTION, in the imperative and specific enough
  to act on without reading anything else.
* ``reason`` is WHY, **with the numbers**, and :class:`RecommendationDraft`
  refuses to be constructed without them. The check is deliberately mechanical —
  the reason must be non-blank and must contain at least one digit — because the
  failure the brief rules out is a bare imperative that renders perfectly well on
  a card, so nobody notices the sentence justifying it is gone. A rule cannot be
  reviewed by reading it; it has to be *constructed*, and construction is where
  the guarantee goes.
* ``entity_type``/``entity_id`` are the RELATED DATA, and ``risk_id`` — filled in
  by :meth:`RecommendationService.generate` rather than by the rule — is the link
  back to the condition the suggestion came from.

**Priority is derived, never chosen.** A rule cannot set one: :meth:`RecommendationService._draft`
reads it off the raising risk's severity through a single table, so ``priority``
and ``severity`` are two renderings of one number and cannot drift apart. A rule
that wanted "this one is urgent" has to raise a risk that says so, which is the
only place urgency is a fact about anything.

The engine proposes; it never performs
--------------------------------------
Every member of :class:`~app.models.enums.RecommendationType` names something a
person does, and the brief is explicit that nothing is rescheduled without
confirmation. That is a property of the type list rather than of this module, but
it is repeated in the copy: the descriptions below are instructions to a reader
("Add a 2h session…", "Decide the next step…"), never announcements ("Your task
has been rescheduled"). Neutral, factual, no characterisation of the person
either: a rule that has counted three reschedules says so, and does not say the
task is impossible or the user is disorganised.

Deduplication is the repository's, not this module's
----------------------------------------------------
:meth:`RecommendationService.generate` asks
:meth:`~app.repositories.risk.RiskRepository.find_open_recommendation` before it
writes, and skips the write only when the open suggestion is **already saying
exactly this** — same type, same title, same reason, same priority. That is a
narrower test than "is one open", and deliberately so: the reason is the numbers,
the numbers move every run, and
:meth:`~app.repositories.risk.RiskRepository.upsert_recommendation` documents
that a refresh replaces the wording rather than merging it. A suggestion whose
reason still says "3h unscheduled" when the data says "5h" would be arguing for
something the data no longer says. When the wording is genuinely unchanged the
write is skipped, so ``updated_at`` on a stable suggestion means "the numbers
moved" rather than "a scheduled job ran again".

``generate`` returns only rows the call **created**, never ones it refreshed. The
caller is
:meth:`~app.services.risk.detection.RiskDetectionService.evaluate`, which writes
that length into ``risk_evaluations.recommendations_created``; counting a
refreshed suggestion as a creation would make the run summary claim suggestions
the user has already seen.

One gap, named rather than papered over
---------------------------------------
:data:`recommendation_rules` maps :class:`~app.models.enums.RiskType` to the rules
that fire on it, and :data:`~app.models.enums.RiskType.SCHEDULING` maps to no
rules at all. The contracts freeze eight rules, none of them a scheduling one, so
a scheduling risk is raised and stored without a suggestion attached. That is a
deliberate reading of a frozen contract rather than an oversight: inventing a
ninth rule here would put a column other agents are already coding against behind
a name nobody agreed on. The empty tuple is spelled in the table so the gap is
visible as data — adding the rule later is one entry in one dict.

What the rules need, and why the constructor looks like it does
--------------------------------------------------------------
Four collaborators, matching the contracts: the risk repository (every write and
the dedup lookup), the task and project repositories (a suggestion that names a
task or a project has to name the *right* one, and the metadata captured at
detection time is a snapshot rather than a lookup), and the activity sink.

The activity sink is also a **reader** here, not only a writer.
:meth:`RecommendationService._rule_break_down_task` answers "how many times has
this task been rescheduled" through
:meth:`~app.services.activity_service.ActivityService.feed`, which is already
owner-scoped. The dedicated count query lives on the analytics repository, which
this service is not given, and taking it would mean adding a fifth collaborator
the contract does not name. With no sink wired the rule declines rather than
guessing a count: a suggestion built on an invented number is exactly the
fabricated-zero failure the detection module is written to avoid.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn

from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import (
    ActivityEvent,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
    RiskSeverity,
    RiskType,
    TaskStatus,
    validate_recommendation_type,
    validate_risk_type,
)
from app.models.risk import LIVE_RISK_STATUSES, Recommendation, Risk
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.risk import RiskRepository
from app.repositories.task import TaskRepository
from app.services.activity_service import ActivityService
from app.services.risk.scoring import DEFAULT_SEVERITY_THRESHOLDS

__all__ = [
    "ENTITY_ACCOUNT",
    "ENTITY_PROJECT",
    "ENTITY_TASK",
    "MIN_RESCHEDULES",
    "Recommendation",
    "RecommendationDraft",
    "RecommendationService",
    "recommendation_rules",
]

#: The three values ``risks.entity_type`` and ``recommendations.entity_type``
#: can hold. Spelled here rather than imported from
#: :mod:`app.services.risk.detection` for the reason
#: :mod:`app.repositories.risk` duplicates its index predicates: a rule module
#: that imports the orchestrator to read three strings cannot be read without
#: dragging the analytics layer in behind it, and the coupling would be harder to
#: review than the duplication. They must stay identical to detection's — a typo
#: here is a deduplication failure, not a display bug, because these strings are
#: a third of the partial unique index on both tables.
ENTITY_TASK = "task"
ENTITY_PROJECT = "project"
ENTITY_ACCOUNT = "account"

#: Recorded ``TASK_RESCHEDULED`` events on one task before the break-it-down rule
#: speaks. Three is the point at which a task has been moved three times rather
#: than adjusted once or twice, and it is the threshold the contracts name.
MIN_RESCHEDULES = 3

#: A recommendation's priority is the raising risk's severity, read through this
#: table. Keyed by member rather than by position so that re-ordering either enum
#: cannot silently repoint the mapping, and complete so that adding a band
#: without adding a row here is a lookup miss that
#: :meth:`RecommendationService._draft` handles as a defect rather than as a
#: decision.
_SEVERITY_TO_PRIORITY: dict[RiskSeverity, RecommendationPriority] = {
    RiskSeverity.CRITICAL: RecommendationPriority.CRITICAL,
    RiskSeverity.HIGH: RecommendationPriority.HIGH,
    RiskSeverity.MEDIUM: RecommendationPriority.MEDIUM,
    RiskSeverity.LOW: RecommendationPriority.LOW,
}

#: The lowest score the ``medium`` band accepts, read from the scoring module's
#: own ladder rather than restated as 25. Two rules use it — the deadline rule
#: that only speaks when the deadline is genuinely pressing, and nothing else —
#: and a deployment that retunes the ladder gets the new floor for free. A copy
#: of the number here would drift the moment the thresholds were configured.
_MEDIUM_FLOOR: int = next(
    floor
    for floor, severity in DEFAULT_SEVERITY_THRESHOLDS
    if severity == RiskSeverity.MEDIUM.value
)

#: What a rule looks like. Every rule is an async method taking keyword-only
#: ``owner`` and ``risk`` and returning a draft or ``None``; the type alias says
#: so once instead of in eight signatures.
_Rule = Callable[..., Awaitable["RecommendationDraft | None"]]

#: Shared with ``app.api.v1.recommendations`` on purpose. The two surfaces
#: answer the same condition — an id that is not the caller's, and an id
#: that was never issued — so they must not answer it in two different
#: sentences. Neither wording leaks which case it was; both simply read
#: better than a generic 404 body.
_RECOMMENDATION_NOT_FOUND = "That recommendation does not exist."


@dataclass(frozen=True, slots=True)
class RecommendationDraft:
    """One proposed action, before it has an id, a status, or a place in the feed.

    Frozen because a draft is passed from a rule to :meth:`RecommendationService.generate`
    and read by neither on the way; nothing has an opportunity to edit it, and a
    mutable carrier would suggest one.

    ``__post_init__`` enforces the three invariants the brief makes non-optional:
    a non-blank title, a non-blank description, and a ``reason`` that is both
    non-blank *and* carries a digit. The digit test is the blunt part and it is
    the useful part. "Related data" in the brief means the numbers, and a rule
    that builds a reason out of adjectives passes every other check while
    producing exactly the unexplained suggestion the brief rules out. Requiring a
    figure makes the failure loud at construction, where a reviewer will see it,
    rather than silent in a card, where nobody will.
    """

    recommendation_type: RecommendationType
    #: Derived from the raising risk's severity by
    #: :meth:`RecommendationService._draft`. Present on the carrier because
    #: :meth:`~app.repositories.risk.RiskRepository.upsert_recommendation` writes
    #: it, and rules do not set it.
    priority: RecommendationPriority
    title: str
    description: str
    reason: str
    entity_type: str | None
    entity_id: uuid.UUID | None

    def __post_init__(self) -> None:
        """Reject a draft that would render as a bare imperative.

        Raises:
            ValueError: If the title or the description is blank, or if the
                reason is blank or carries no figure. The first two render as an
                empty card; the third is the brief's stated failure — a
                suggestion with no stated reason — and is the one this check
                exists for.
        """
        for field_name, value in (("title", self.title), ("description", self.description)):
            if not value.strip():
                raise ValueError(
                    f"A recommendation must carry a {field_name}; a suggestion cannot be "
                    f"rendered from an empty one."
                )
        stripped = self.reason.strip()
        if not stripped or not any(character.isdigit() for character in stripped):
            raise ValueError(
                "A recommendation's reason must state the figures behind it. The brief "
                "requires what, why, related data and a suggested action, and a reason "
                "carrying no number is a restatement of the title rather than a why."
            )


#: Which rules fire on which kind of risk.
#:
#: The mapping is a name of a method on :class:`RecommendationService` rather
#: than a callable, so the table is readable in one screen and a rule cannot be
#: registered for a risk type it does not handle. A risk type with several rules
#: — ``deadline`` splits on whether any work is unbooked, ``project`` can raise
#: both the blocked-work and the review-the-signals suggestion — lists them in the
#: order the stronger one should be read first.
#:
#: The empty tuple for :data:`~app.models.enums.RiskType.SCHEDULING` is the
#: documented gap this module's docstring describes: the contracts freeze eight
#: rules and none of them proposes an action for a fault in the plan.
recommendation_rules: Mapping[RiskType, tuple[str, ...]] = {
    RiskType.DEADLINE: ("_rule_block_time", "_rule_review_deadline"),
    RiskType.WORKLOAD: ("_rule_reduce_workload",),
    RiskType.TASK: ("_rule_complete_blocked_task", "_rule_break_down_task"),
    RiskType.PROJECT: ("_rule_complete_blocked_task", "_rule_review_project"),
    RiskType.ESTIMATION: ("_rule_update_estimate",),
    RiskType.CONSISTENCY: ("_rule_review_consistency",),
    RiskType.SCHEDULING: (),
}


class RecommendationService:
    """Raise suggestions from risks, and record what the user did about them.

    One instance per request. It holds no state between calls, so two concurrent
    evaluations of the same account are both correct: they collide through the
    partial unique index and the repository's advisory lock rather than through
    anything this class remembers.
    """

    def __init__(
        self,
        risks: RiskRepository,
        tasks: TaskRepository,
        projects: ProjectRepository,
        activity: ActivityService | None = None,
    ) -> None:
        """Wire the service.

        Args:
            risks: Recommendation persistence, including the deduplicating upsert
                and the open-suggestion lookup.
            tasks: Task persistence, for a rule that has to read the live status
                of the task a risk was raised about. A risk's metadata is a
                snapshot taken at detection time; a suggestion that says "it is
                blocked" has to mean blocked *now*.
            projects: Project persistence, so a suggestion can name the project a
                task belongs to rather than carrying an id a reader cannot use.
            activity: The history sink, and — for the reschedule rule — a reader
                of it. ``None`` is a real mode for exercising the rules in
                isolation, and the one thing it costs is that the reschedule rule
                declines rather than guessing a count. The API layer is not that
                caller; see :func:`app.api.deps.get_recommendation_service`.
        """
        self.risks = risks
        self.tasks = tasks
        self.projects = projects
        self.activity = activity

    # -- Generation ----------------------------------------------------------

    async def generate(self, *, owner: User, risks: Sequence[Risk]) -> list[Recommendation]:
        """Turn one pass's risks into suggestions, deduplicated.

        Walks the risks in the order given, applies every rule registered for the
        risk's type, and writes what they produce. The per-identity ``seen`` set
        is the *in-pass* dedup: two rules on one risk can agree on an identity —
        a project that is both blocked and under pressure raises two suggestions,
        but a project that is blocked twice does not — and the set is what stops
        the second one asking the repository a question whose answer is already
        known. The cross-run dedup is the repository's partial index, reached
        through :meth:`~RiskRepository.find_open_recommendation` and
        :meth:`~RiskRepository.upsert_recommendation`.

        Only *live* risks raise suggestions. A risk the user has resolved or
        dismissed is a condition they have answered, and re-proposing an action
        for it would be the engine talking over them.

        Args:
            owner: The account the pass ran for.
            risks: The risks the pass wrote, capped by the caller. Order is
                preserved so the returned list follows the Risk Center's own
                worst-first ordering.

        Returns:
            The rows this call **created**, in the order they were written.
            Refreshed suggestions are not included, because the caller's
            ``recommendations_created`` counter is about suggestions the user has
            not seen.

        Raises:
            ValueError: If a risk carries a ``risk_type`` outside
                :class:`~app.models.enums.RiskType`. The value is validated on
                the way into storage, so this means the data and the vocabulary
                disagree, and skipping the row quietly would hide exactly the
                drift that makes every later filter miss it.
        """
        created: list[Recommendation] = []
        seen: set[tuple[str, str | None, uuid.UUID | None]] = set()

        for risk in risks:
            if not _is_live(risk):
                continue
            for rule_name in recommendation_rules[validate_risk_type(risk.risk_type)]:
                draft = await self._rule(rule_name)(owner=owner, risk=risk)
                if draft is None:
                    continue
                identity = (
                    draft.recommendation_type.value,
                    draft.entity_type,
                    draft.entity_id,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                row = await self._persist(owner=owner, risk=risk, draft=draft)
                if row is not None:
                    created.append(row)
        return created

    def _rule(self, name: str) -> _Rule:
        """Resolve a rule name from :data:`recommendation_rules` to its method.

        A name is resolved on every call rather than bound once in a dict of
        callables, because a dict of bound methods would have to be built inside
        ``__init__`` and would capture the instance before it was wired. A name
        that is not a rule method raises ``AttributeError`` at the point of use,
        which is the same behaviour as a missing method and the right place for
        it to be noticed.
        """
        return getattr(self, name)

    async def _persist(
        self, *, owner: User, risk: Risk, draft: RecommendationDraft
    ) -> Recommendation | None:
        """Write one draft, or skip it when an open suggestion already says this.

        Returns the row when it was created, and ``None`` when the write was
        skipped or refreshed. The two are reported separately on purpose: the
        caller counts creations, and a refreshed suggestion that was counted as
        new would inflate the run summary by the number of suggestions a user has
        chosen to leave open.
        """
        existing = await self.risks.find_open_recommendation(
            owner.id,
            recommendation_type=draft.recommendation_type,
            entity_type=draft.entity_type,
            entity_id=draft.entity_id,
        )
        if existing is not None and _says_the_same(existing, draft):
            return None

        row, was_created = await self.risks.upsert_recommendation(
            owner.id,
            recommendation_type=draft.recommendation_type,
            priority=draft.priority,
            title=draft.title,
            description=draft.description,
            reason=draft.reason,
            risk_id=risk.id,
            entity_type=draft.entity_type,
            entity_id=draft.entity_id,
            metadata={
                "rule": draft.recommendation_type.value,
                "risk_type": risk.risk_type,
                "risk_score": int(risk.score),
                "risk_severity": risk.severity,
                "evidence_strength": risk.evidence_strength,
            },
        )
        if was_created:
            await self._record_event(
                ActivityEvent.RECOMMENDATION_CREATED, owner=owner, row=row, risk=risk
            )
        return row if was_created else None

    def _draft(
        self,
        *,
        risk: Risk,
        recommendation_type: RecommendationType,
        title: str,
        description: str,
        reason: str,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> RecommendationDraft:
        """Build a draft with its priority derived from the raising risk.

        Every rule goes through here, which is what makes "priority comes from
        severity" a property of the class rather than a convention in eight
        methods: no rule has a ``priority`` parameter to pass, so none of them
        can choose one.

        Args:
            risk: The risk raising the suggestion; the sole source of priority.
            recommendation_type: What action is proposed.
            title: WHAT, in a few words.
            description: The SUGGESTED ACTION, in the imperative.
            reason: WHY, with the numbers.
            entity_type: ``task`` / ``project`` / ``account``, or ``None`` for a
                suggestion that points at no row.
            entity_id: The row the action is about.

        Returns:
            The draft. Constructing it validates that the three texts are present
            and that the reason carries a figure, so a rule that has nothing to
            say fails here rather than at render time.

        Raises:
            ValueError: Propagated from :class:`RecommendationDraft`, or if the
                risk carries a severity outside :class:`~app.models.enums.RiskSeverity`
                — in which case the fallback is the *lowest* band, so a defect can
                never invent urgency.
        """
        severity = RiskSeverity(risk.severity)
        return RecommendationDraft(
            recommendation_type=validate_recommendation_type(recommendation_type),
            priority=_SEVERITY_TO_PRIORITY.get(severity, RecommendationPriority.LOW),
            title=title,
            description=description,
            reason=reason,
            entity_type=entity_type,
            entity_id=entity_id,
        )

    # -- Rule 1: deadline gap ------------------------------------------------

    async def _rule_block_time(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """Deadline risk with unbooked work left: schedule the remainder.

        The first of the two deadline rules, and the one that has an action
        behind it — there is a specific number of minutes to find. The title
        shape is the contracts': "Schedule another N before <due date>".

        Declines for a deadline that has already passed. "Schedule another 3h
        before 20 Aug" is not an instruction anyone can carry out, and the second
        deadline rule owns that case; proposing both would put two suggestions on
        one task saying contradictory things about a date that is gone.
        """
        meta = risk.metadata_
        remaining = _int(meta, "remaining_minutes")
        available = _int(meta, "available_minutes")
        hours = _float_or_none(meta, "deadline_in_hours")
        gap = remaining - available
        if gap <= 0 or (hours is not None and hours <= 0):
            return None

        name = _clip(str(meta.get("title") or "this task"), 120)
        due = _due_phrase(hours, risk.detected_at)
        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.BLOCK_TIME,
            title=f"Schedule another {_duration(gap)} for {name} before {due}",
            description=(
                f"Add {_duration(gap)} of unscheduled work to {name} on or before {due}, "
                f"so the time exists before the date it is needed."
            ),
            reason=(
                f"{_duration(remaining)} of estimated work remains on {name} and "
                f"{_duration(available)} is booked before {due}, leaving {_duration(gap)} "
                f"with no time scheduled. {_score_clause(risk)}"
            ),
            entity_type=risk.entity_type,
            entity_id=risk.entity_id,
        )

    # -- Rule 2: deadline pressure -------------------------------------------

    async def _rule_review_deadline(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """Deadline risk with nothing left to schedule: reconsider the date.

        The complementary half of rule 1. Once every remaining minute is booked,
        "book more time" is no longer available and the only lever left is the
        date itself or the scope, both of which are the user's to decide. The
        suggestion is therefore to *look at it*, which is honest about the fact
        that the engine has no further move.

        Gated on the score rather than on the severity word, through
        :data:`_MEDIUM_FLOOR`: a task due in three weeks with all of its work
        booked is a healthy plan, and a suggestion about it would be noise. A
        passed deadline is exempt from the gate, because it scores 100 by
        construction and gating it would be a formality.
        """
        meta = risk.metadata_
        remaining = _int(meta, "remaining_minutes")
        available = _int(meta, "available_minutes")
        hours = _float_or_none(meta, "deadline_in_hours")
        past = hours is not None and hours <= 0
        if not past and remaining - available > 0:
            return None
        if not past and int(risk.score) < _MEDIUM_FLOOR:
            return None

        name = _clip(str(meta.get("title") or "this task"), 120)
        if past:
            reason = (
                f"The due date for {name} passed {_ago_phrase(abs(hours or 0.0))} with "
                f"{_duration(remaining)} of estimated work outstanding, of which "
                f"{_duration(available)} was booked. {_score_clause(risk)}"
            )
            description = (
                f"Decide the next step for {name}: move its due date, reduce its scope, "
                f"or record the work as done."
            )
        else:
            due = _due_phrase(hours, risk.detected_at)
            reason = (
                f"All {_duration(remaining)} of the work remaining on {name} is already "
                f"booked before {due}, which is {_hours_phrase(hours)} away, so any slip "
                f"has nowhere to go. {_score_clause(risk)}"
            )
            description = (
                f"Review whether {name} is still achievable against {due}, and move the "
                f"date or the scope if it is not."
            )
        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.REVIEW_DEADLINE,
            title=f"Check whether {name} is still achievable",
            description=description,
            reason=reason,
            entity_type=risk.entity_type,
            entity_id=risk.entity_id,
        )

    # -- Rule 3: workload ----------------------------------------------------

    async def _rule_reduce_workload(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """Workload risk: move the part of the plan that does not fit.

        Account-level, so the suggestion is about the *plan* and never about a
        particular task: the engine can measure that the fortnight holds more
        than the fortnight can, and it cannot say which of twenty items should
        move. Naming one would be an opinion the data does not support, so the
        suggestion names the excess and leaves the choice where it belongs.
        """
        meta = risk.metadata_
        scheduled = _int(meta, "scheduled_minutes")
        available = _int(meta, "available_minutes")
        window = str(meta.get("window_label") or "this window")
        excess = scheduled - available
        if excess <= 0:
            return None
        ratio = round(scheduled / available * 100) if available else 0

        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.REDUCE_WORKLOAD,
            title=f"Move about {_duration(excess)} of planned work to later dates",
            description=(
                f"Reschedule roughly {_duration(excess)} of the work planned across "
                f"{window} to a later date, or take it off the plan."
            ),
            reason=(
                f"{_duration(scheduled)} of work is scheduled across {window} against "
                f"{_duration(available)} of declared availability, which is {ratio}% of "
                f"capacity and about {_duration(excess)} beyond it. {_score_clause(risk)}"
            ),
            entity_type=risk.entity_type or ENTITY_ACCOUNT,
            entity_id=risk.entity_id,
        )

    # -- Rule 4: blocked work ------------------------------------------------

    async def _rule_complete_blocked_task(
        self, *, owner: User, risk: Risk
    ) -> RecommendationDraft | None:
        """Blocked work on a task, or blocked work inside a project.

        One rule covering both because the action is the same one and the words
        are the same sentence; the two inputs differ only in where the suggestion
        is filed. A task risk is checked against the task's *current* status
        rather than against the status captured at detection time — a task
        unblocked since the pass should not be told it is blocked — and a project
        risk is read from the counts the detector already measured.

        The task branch additionally counts what is waiting on the task, because
        "unblock this" is a different amount of work depending on whether two
        other cards are parked behind it, and that is a related data point the
        risk itself does not carry.
        """
        if risk.entity_type == ENTITY_TASK and risk.entity_id is not None:
            return await self._blocked_task_draft(owner=owner, risk=risk)
        if risk.entity_type == ENTITY_PROJECT:
            return self._blocked_project_draft(risk=risk)
        return None

    async def _blocked_task_draft(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """The task branch of :meth:`_rule_complete_blocked_task`."""
        task = await self.tasks.get_by_id_for_user(risk.entity_id, owner.id)
        if task is None or task.status != TaskStatus.BLOCKED.value:
            return None
        project = (
            await self.projects.get_by_id_for_user(task.project_id, owner.id)
            if task.project_id
            else None
        )
        dependents = await self.tasks.list_dependents(task.id)
        name = _clip(task.title, 120)
        where = project.name if project is not None else "this project"
        estimate = (
            f" against an estimate of {_duration(task.estimated_minutes)}"
            if task.estimated_minutes
            else ""
        )
        waiting = (
            f" {len(dependents)} task(s) are waiting on it and cannot move while it is blocked."
            if dependents
            else " Nothing else is recorded as waiting on it."
        )
        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.COMPLETE_BLOCKED_TASK,
            title=f"Resolve the blocked work in {where}",
            description=(
                f"Unblock or re-scope {name}, which is recorded in the blocked status."
                + (f" {len(dependents)} task(s) are waiting on it." if dependents else "")
            ),
            reason=(
                f"{name} is recorded as blocked{estimate}, last updated on "
                f"{_day_phrase(task.updated_at)}.{waiting} {_score_clause(risk)}"
            ),
            entity_type=ENTITY_TASK,
            entity_id=task.id,
        )

    def _blocked_project_draft(self, *, risk: Risk) -> RecommendationDraft | None:
        """The project branch of :meth:`_rule_complete_blocked_task`."""
        meta = risk.metadata_
        blocked = _int(meta, "blocked_tasks")
        if blocked <= 0:
            return None
        name = str(meta.get("project_name") or "this project")
        remaining = _int(meta, "remaining_tasks")
        overdue = _int(meta, "overdue_tasks")
        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.COMPLETE_BLOCKED_TASK,
            title=f"Resolve the blocked work in {name}",
            description=(
                f"Review the {blocked} blocked task(s) in {name} and either clear the block "
                f"or re-scope the work behind it."
            ),
            reason=(
                f"{name} has {blocked} task(s) recorded as blocked, of {remaining} still "
                f"unfinished, and {overdue} of them past their due date. {_score_clause(risk)}"
            ),
            entity_type=ENTITY_PROJECT,
            entity_id=risk.entity_id,
        )

    # -- Rule 5: repeatedly rescheduled --------------------------------------

    async def _rule_break_down_task(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """A task moved three or more times: it may be too large to place.

        The count comes from the activity feed rather than from the risk, because
        reschedules are the one signal with no row of their own — they are events
        — and no detector measures them today. The threshold is
        :data:`MIN_RESCHEDULES`, the number the contracts name.

        Declines without an activity sink rather than falling back to a guess. A
        break-it-down suggestion justified by a count this service never read
        would be the fabricated figure the whole engine is built to avoid, and
        there is a second-hand alternative available: the user who is being told
        to split a task can see their own history.
        """
        if risk.entity_type != ENTITY_TASK or risk.entity_id is None:
            return None
        if self.activity is None:
            return None
        task = await self.tasks.get_by_id_for_user(risk.entity_id, owner.id)
        if task is None:
            return None

        feed = await self.activity.feed(
            owner=owner,
            limit=1,
            offset=0,
            task_id=task.id,
            event_type=ActivityEvent.TASK_RESCHEDULED.value,
        )
        count = feed.meta.total
        if count < MIN_RESCHEDULES:
            return None

        name = _clip(task.title, 120)
        estimate = (
            f", against an estimate of {_duration(task.estimated_minutes)}"
            if task.estimated_minutes
            else ""
        )
        due = f", with a due date of {_day_phrase(task.due_date)}" if task.due_date else ""
        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.BREAK_DOWN_TASK,
            title=f"Break {name} into smaller pieces",
            description=(
                f"Split {name} into tasks small enough to finish in one sitting, then "
                f"schedule the first piece."
            ),
            reason=(
                f"{count} reschedules of {name} are recorded in its history{estimate}{due}. "
                f"{_score_clause(risk)}"
            ),
            entity_type=ENTITY_TASK,
            entity_id=task.id,
        )

    # -- Rule 6: estimation overrun ------------------------------------------

    async def _rule_update_estimate(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """Systematic overrun: correct the estimate rather than the work.

        The action is deliberately about the *estimate*. Telling a user whose
        completed tasks ran 67% over to work faster is not an option the engine
        has; telling them to add the measured margin to the next estimate is one,
        and it is the one the data actually supports. The second half of the
        description names the other honest option — reduce the scope accepted at
        the current estimate — so the suggestion does not read as advice to
        inflate every number.

        Declines for a mean overrun of zero or less, which cannot happen for a
        stored estimation risk (the detector only scores over-runs) but is the
        guard that keeps the wording honest if a threshold is ever retuned.
        """
        meta = risk.metadata_
        mean_overrun = _float_or_none(meta, "mean_overrun")
        if mean_overrun is None or mean_overrun <= 0:
            return None
        samples = _int(meta, "sample_count")
        under = _int(meta, "under_estimation_count")
        over = _int(meta, "over_estimation_count")
        percent = round(mean_overrun * 100)

        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.UPDATE_ESTIMATE,
            title=f"Recent tasks ran about {percent}% over estimate",
            description=(
                f"Add about {percent}% to the estimate on new work, or reduce the scope "
                f"you are accepting at the current estimate."
            ),
            reason=(
                f"Across {samples} completed task(s) in this window, the recorded duration "
                f"ran about {percent}% over the estimate it was given, with {under} running "
                f"long and {over} running short. {_score_clause(risk)}"
            ),
            entity_type=risk.entity_type or ENTITY_ACCOUNT,
            entity_id=risk.entity_id,
        )

    # -- Rule 7: consistency drop --------------------------------------------

    async def _rule_review_consistency(
        self, *, owner: User, risk: Risk
    ) -> RecommendationDraft | None:
        """Fewer days recorded active than the equally long period before.

        The wording is the brief's and the scoring module's: this is a count of
        *rows recorded*, and the suggestion is to review what changed, never to
        suggest that anything about the person changed. A decline to record
        activity is a legitimate answer to a question this engine has no standing
        to ask, and a sentence that implied otherwise would be the exact failure
        the Phase 7 language rules name.

        Filed as :data:`RecommendationType.REVIEW_PROJECT` against the account,
        per the contracts' rule table. The type is a label the UI groups by, and
        "review what is going on" is the closest thing the closed vocabulary has
        to "look at this yourself"; the entity columns are what keep it
        unambiguous.
        """
        meta = risk.metadata_
        active = _int(meta, "active_days")
        window = _int(meta, "window_days")
        previous = _int(meta, "previous_active_days")
        previous_window = _int(meta, "previous_window_days")
        current_rate = _float_or_none(meta, "current_rate")
        previous_rate = _float_or_none(meta, "previous_rate")
        if previous_window <= 0 or window <= 0:
            return None

        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.REVIEW_PROJECT,
            title="Recorded activity is lower than the previous period",
            description=(
                "Look at which tasks carried the previous period's recorded activity and "
                "decide what is worth carrying into this one."
            ),
            reason=(
                f"Recorded activity covers {active} of {window} day(s) in this window "
                f"against {previous} of {previous_window} before it"
                + (
                    f", which is {_percent(current_rate)} of days against {_percent(previous_rate)}"
                    if current_rate is not None and previous_rate is not None
                    else ""
                )
                + f". {_score_clause(risk)}"
            ),
            entity_type=risk.entity_type or ENTITY_ACCOUNT,
            entity_id=risk.entity_id,
        )

    # -- Rule 8: project signals ---------------------------------------------

    async def _rule_review_project(self, *, owner: User, risk: Risk) -> RecommendationDraft | None:
        """Combined pressure on one project: read the signals, choose one to act on.

        The last rule, and the one that exists because the project detector
        returns a single weighted number built from five sub-signals. A score of
        64 does not say *what* to do, and inventing a "do this" from a weighted
        sum would be an interpretation the arithmetic does not license. So the
        reason names each contributing signal separately — the same clause
        breakdown the risk's own description carries — and the action is to
        choose. Every contributing count is already a digit, so the reason never
        depends on the score clause to satisfy the digit rule.
        """
        if risk.entity_type != ENTITY_PROJECT or risk.entity_id is None:
            return None
        meta = risk.metadata_
        project = await self.projects.get_by_id_for_user(risk.entity_id, owner.id)
        name = str(meta.get("project_name") or (project.name if project else "this project"))
        parts = _project_signal_clauses(meta)
        if not parts:
            return None

        return self._draft(
            risk=risk,
            recommendation_type=RecommendationType.REVIEW_PROJECT,
            title=f"Review the open signals on {name}",
            description=(
                f"Go through the open signals on {name} — the overdue, blocked and remaining "
                f"work listed against it — and decide which one to act on first."
            ),
            reason=f"Signals recorded for {name}: {_clause(parts)} {_score_clause(risk)}",
            entity_type=ENTITY_PROJECT,
            entity_id=risk.entity_id,
        )

    # -- Lifecycle -----------------------------------------------------------

    async def accept(self, *, owner: User, recommendation_id: uuid.UUID) -> Recommendation:
        """Record that the user intends to act on this suggestion.

        The engine performs nothing here. Accepting is a *statement of intent*
        held against a later :meth:`complete`, and the pair is the training
        signal Phase 10 wants: "I will do this" and "I did this" are two
        different facts about two different people at two different moments, and
        collapsing them loses the one that predicts behaviour.

        ``responded_at`` is stamped by the repository, which owns the rule for
        which statuses count as an answer.

        Args:
            owner: The caller. Ownership is a predicate in the write.
            recommendation_id: The suggestion being accepted.

        Returns:
            The stored row in its new state.

        Raises:
            NotFoundError: If no such suggestion exists, or it is not the
                caller's — identically, so the endpoint cannot be used to probe
                which ids are real.
            ConflictError: If the suggestion is in a state that cannot be
                accepted, naming the state it is in.
        """
        return await self._respond(
            owner=owner,
            recommendation_id=recommendation_id,
            status=RecommendationStatus.ACCEPTED,
            event=ActivityEvent.RECOMMENDATION_ACCEPTED,
        )

    async def reject(self, *, owner: User, recommendation_id: uuid.UUID) -> Recommendation:
        """Record that the user declined this suggestion.

        The most valuable label in the table, and the one the dedup design is
        built around: a rejection is terminal, so the identical suggestion is not
        re-raised while the condition stands, and a future model can learn that
        this kind of suggestion, offered to this kind of account, does not work.
        Nothing else in the product produces a clean, attributable "no".

        Args:
            owner: The caller. Ownership is a predicate in the write.
            recommendation_id: The suggestion being declined.

        Returns:
            The stored row in its new state.

        Raises:
            NotFoundError: If no such suggestion exists, or it is not the
                caller's.
            ConflictError: If the suggestion is already terminal.
        """
        return await self._respond(
            owner=owner,
            recommendation_id=recommendation_id,
            status=RecommendationStatus.REJECTED,
            event=ActivityEvent.RECOMMENDATION_REJECTED,
        )

    async def complete(self, *, owner: User, recommendation_id: uuid.UUID) -> Recommendation:
        """Record that the suggested action has been carried out.

        Reachable from both ``viewed`` and ``accepted``, which is the whole point
        of accepting first: the state a suggestion was in when the work actually
        happened is part of the label. Completing a suggestion the user never
        accepted is legitimate — they may simply have done the thing — and the
        two are distinguishable afterwards.

        Args:
            owner: The caller. Ownership is a predicate in the write.
            recommendation_id: The suggestion being completed.

        Returns:
            The stored row in its new state.

        Raises:
            NotFoundError: If no such suggestion exists, or it is not the
                caller's.
            ConflictError: If the suggestion has already been answered, or expired.
        """
        return await self._respond(
            owner=owner,
            recommendation_id=recommendation_id,
            status=RecommendationStatus.COMPLETED,
            event=ActivityEvent.RECOMMENDATION_COMPLETED,
        )

    async def view(self, *, owner: User, recommendation_id: uuid.UUID) -> Recommendation:
        """Record that the suggestion was opened, and nothing more.

        **Opening a suggestion is not answering it.** The repository keeps
        ``viewed`` inside the open set and outside the answered set, and this
        method is where that distinction is enforced: no ``responded_at`` is
        stamped, and the event is the ``viewed`` one. A system that counted a
        read as a response would train itself on a signal that says nothing.

        Idempotent in the sense that matters: viewing an already-viewed
        suggestion writes nothing and reports success, because a second read of
        the same card is a normal thing for a user to do.

        Args:
            owner: The caller. Ownership is a predicate in the write.
            recommendation_id: The suggestion being opened.

        Returns:
            The stored row in its new state.

        Raises:
            NotFoundError: If no such suggestion exists, or it is not the
                caller's.
            ConflictError: If the suggestion has already been answered or
                expired — there is no answer to read any more.
        """
        return await self._respond(
            owner=owner,
            recommendation_id=recommendation_id,
            status=RecommendationStatus.VIEWED,
            event=ActivityEvent.RECOMMENDATION_VIEWED,
        )

    async def _respond(
        self,
        *,
        owner: User,
        recommendation_id: uuid.UUID,
        status: RecommendationStatus,
        event: ActivityEvent,
    ) -> Recommendation:
        """Move one suggestion to ``status`` and write the matching event.

        The current status is read first so the event can carry it. That is an
        extra round trip on a user-initiated action, and it buys the transition
        rather than just the destination: "accepted from viewed" and "accepted
        without being read" are different rows in the training data, and a status
        column that has already been overwritten cannot say which happened.

        Args:
            owner: The caller.
            recommendation_id: The suggestion to move.
            status: Where to move it.
            event: The :class:`~app.models.enums.ActivityEvent` for that move.

        Returns:
            The stored row in its new state.

        Raises:
            NotFoundError: If the suggestion does not exist or is not the
                caller's.
            ConflictError: If the row exists and is the caller's, but its current
                status cannot reach ``status``.
        """
        before = await self.risks.get_recommendation(owner.id, recommendation_id)
        if before is None:
            raise NotFoundError(_RECOMMENDATION_NOT_FOUND)

        # Captured as a string, deliberately, before the transition below.
        #
        # `before` is the same identity-mapped instance the repository's
        # `UPDATE ... RETURNING` will refresh (`populate_existing=True`), so
        # reading `before.status` *after* the transition returns the **target**
        # status — every lifecycle event then recorded `from_status` equal to
        # the status it was moving to. That silently flattened the exact
        # distinction this event exists to preserve: "accepted from viewed" and
        # "accepted without being read" are different rows in a future training
        # set, and a `from_status` that always equals the target cannot tell
        # them apart.
        from_status = before.status

        row = await self.risks.transition_recommendation(
            owner.id, recommendation_id, status=status.value
        )
        if row is None:
            await self._raise_unreachable(
                owner=owner, recommendation_id=recommendation_id, target=status
            )
        await self._record_event(event, owner=owner, row=row, from_status=from_status)
        return row

    async def _raise_unreachable(
        self, *, owner: User, recommendation_id: uuid.UUID, target: RecommendationStatus
    ) -> NoReturn:
        """Distinguish "not yours" from "cannot go there", then raise.

        The repository answers ``None`` for both an unmatched row and a row whose
        current status cannot reach the target, deliberately, because the
        repository's job is a write and not an explanation. The API layer needs
        the difference: the first is a 404, the second is a 409 that tells the
        user the suggestion has already been answered. Re-reading the row costs
        one lookup on a path that is already a failure.
        """
        current = await self.risks.get_recommendation(owner.id, recommendation_id)
        if current is None:
            raise NotFoundError(_RECOMMENDATION_NOT_FOUND)
        raise ConflictError(
            f"This recommendation is {current.status} and cannot be moved to {target.value}."
        )

    async def expire_for_resolved(self, *, owner: User, risk_ids: Sequence[uuid.UUID]) -> int:
        """Make moot every suggestion whose risk is no longer live.

        Called when a risk is closed on purpose rather than by a detection sweep:
        a user resolving or dismissing one from the Risk Center. The repository
        owns the rule — an open suggestion whose ``risk_id`` has no live risk is
        expired — and this method exists so the reason a suggestion went moot
        travels through one service rather than two.

        "Moot" is recorded rather than the row deleted, because a suggestion the
        user never saw because its problem went away is a different observation
        from a suggestion they declined. Collapsing them would teach a future
        model that silence meant "no".

        No event is written: :class:`~app.models.enums.ActivityEvent` has no
        member for expiry, because the engine expiring its own suggestion is not
        something a user did.

        Args:
            owner: The account whose suggestions are being closed.
            risk_ids: The risks that were just closed. An empty list is a no-op
                and costs nothing.

        Returns:
            How many open suggestions were made moot.
        """
        if not risk_ids:
            return 0
        return await self.risks.expire_recommendations_for_risks(owner.id, list(risk_ids))

    # -- Events --------------------------------------------------------------

    async def _record_event(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        row: Recommendation,
        risk: Risk | None = None,
        from_status: str | None = None,
    ) -> None:
        """Write one recommendation lifecycle event, when a sink is wired.

        Metadata is ids, words and numbers only. A task or project title is the
        user's own words and already lives on the row the event points at;
        duplicating it into a feed is how a summary ends up quoting text the
        source has since changed.
        """
        if self.activity is None:
            return
        metadata: dict[str, Any] = {
            "recommendation_id": str(row.id),
            "recommendation_type": row.recommendation_type,
            "priority": row.priority,
            "status": row.status,
        }
        if from_status is not None:
            metadata["from_status"] = from_status
        if risk is not None:
            metadata["risk_id"] = str(risk.id)
            metadata["risk_type"] = risk.risk_type
            metadata["risk_score"] = int(risk.score)
        await self.activity.record(
            event,
            user_id=owner.id,
            project_id=row.entity_id if row.entity_type == ENTITY_PROJECT else None,
            task_id=row.entity_id if row.entity_type == ENTITY_TASK else None,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Small pure helpers over a risk's stored metadata
# ---------------------------------------------------------------------------


def _int(meta: Mapping[str, Any], key: str) -> int:
    """Read an integer out of a risk's metadata, defaulting to zero.

    Zero rather than an error because every caller of this helper is a sentence
    about a *count*, and "this count is missing" and "this count is none" render
    as the same clause. A risk row written before a detector started recording a
    field must still produce a readable suggestion.
    """
    value = meta.get(key)
    return int(value) if isinstance(value, int | float) else 0


def _float_or_none(meta: Mapping[str, Any], key: str) -> float | None:
    """Read a float out of a risk's metadata, or ``None`` when it is not there.

    ``None`` is preserved rather than defaulted to zero for the two fields where
    the difference is the whole sentence: "no previous period to compare against"
    and "no due date on this task" are both facts, and rendering either as ``0``
    would put a figure in a sentence that does not have one.
    """
    value = meta.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _is_live(risk: Risk) -> bool:
    """Whether this risk is still one a suggestion may be raised from.

    The status set is imported rather than restated, for the reason the
    repository restates the same set: the index, this predicate and the
    repository's own filters all key on ``active``/``acknowledged``, and a second
    copy is a second opinion.
    """
    return risk.status in LIVE_RISK_STATUSES


def _says_the_same(existing: Recommendation, draft: RecommendationDraft) -> bool:
    """Whether the open suggestion is already saying exactly what the rule says.

    Compared on the three fields the upsert would replace — title, reason and
    priority — and on nothing else. Comparing the description too would suppress
    a refresh for a copy edit, which is the one case where a refresh is not
    wanted; comparing the entity would be redundant, since the lookup was keyed
    on it. What this guards is narrower than "is one open": it is the write that
    would change nothing.
    """
    return (
        existing.title == draft.title
        and existing.reason == draft.reason
        and existing.priority == draft.priority.value
    )


def _score_clause(risk: Risk) -> str:
    """The closing clause every reason carries: the score, the band, the evidence.

    Uniform across all eight rules, and appended to reasons that already state
    their own figures. The score is the risk's, the band is derived from it by
    the scoring module, and the evidence strength is how much data the score came
    from — which is deliberately not called confidence, because it is a sample
    count and the brief rules out presenting it as anything else.

    It also guarantees a digit in every reason, which is what lets
    :class:`RecommendationDraft` insist on one.
    """
    return (
        f"Recorded risk score {int(risk.score)} of 100 "
        f"({risk.severity} severity, {risk.evidence_strength} evidence strength)."
    )


def _project_signal_clauses(meta: Mapping[str, Any]) -> list[str]:
    """The project's contributing signals as separate factual clauses.

    Each signal is named on its own rather than blended into a verdict, so a
    reader can see which of the five is carrying the score and act on that one.
    Mirrors the breakdown the detector puts in the risk's own description, and
    built from the same metadata keys, so the two cannot describe different sets
    of signals.

    Returns:
        Non-empty strings, in descending weight. Empty when the project has no
        contributing signal, which is a zero score and never a stored risk.
    """
    parts: list[str] = []
    overdue = _int(meta, "overdue_tasks")
    blocked = _int(meta, "blocked_tasks")
    remaining = _int(meta, "remaining_tasks")
    days = meta.get("days_to_deadline")
    required, recent = meta.get("required_velocity"), meta.get("recent_velocity")
    if overdue:
        parts.append(f"{overdue} task(s) are past their due date")
    if blocked:
        parts.append(f"{blocked} task(s) are blocked")
    if isinstance(days, int | float) and remaining > 0:
        parts.append(f"the target date is {int(days)} day(s) away")
    if required and recent is not None and float(required) > float(recent):
        parts.append(
            f"{float(recent):.1f} task(s) a week were completed against "
            f"{float(required):.1f} needed"
        )
    if remaining:
        parts.append(f"{remaining} task(s) are unfinished")
    return parts


def _clause(parts: list[str]) -> str:
    """Join factual fragments into one sentence: ``"a, b and c."``.

    Every caller guarantees a non-empty list, so there is no empty case to
    render; the type says so and the callers in this module are the only ones.
    """
    if len(parts) == 1:
        return f"{parts[0]}."
    return f"{', '.join(parts[:-1])} and {parts[-1]}."


def _duration(minutes: float) -> str:
    """``240`` -> ``"4h"``, ``90`` -> ``"1h 30m"``, ``45`` -> ``"45m"``.

    Human units because a title is read by a person deciding whether to act; the
    arithmetic behind it stays in minutes. The same shapes as
    :mod:`app.services.risk.detection`'s private formatter, repeated rather than
    imported for the reason that module gives: a suggestion's wording should not
    move when an evidence line's format is retuned.
    """
    total = round(minutes)
    if total <= 0:
        return "0m"
    hours, remainder = divmod(total, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _hours_phrase(hours: float) -> str:
    """``18`` -> ``"18 hours"``, ``72`` -> ``"3 days"``. A distance still ahead."""
    if hours >= 48:
        return f"{round(hours / 24)} days"
    if hours < 1:
        return "less than an hour"
    return f"{round(hours)} hours"


def _ago_phrase(hours: float) -> str:
    """The same distances, for one that has already passed."""
    return f"{_hours_phrase(abs(hours))} ago"


def _percent(fraction: float) -> str:
    """``0.5714`` -> ``"57%"``. A rate the reader can compare without arithmetic."""
    return f"{round(fraction * 100)}%"


def _due_phrase(hours: float | None, detected_at: datetime | None) -> str:
    """Name the due date, derived from the hours the detector recorded.

    The detector measures ``deadline_in_hours`` from the instant of the pass to
    the end of the due day, and ``detected_at`` is that instant, so adding one
    back lands on the date itself. The alternative — storing the due date in the
    risk's metadata — would mean changing the frozen scoring module and the
    migration for one word in a sentence, which is not a trade worth making.

    Falls back to a pronoun rather than a date whenever either input is missing:
    "before its due date" is true in every case, where a date computed from an
    absent number would not be.
    """
    if hours is None or detected_at is None:
        return "its due date"
    moment = (detected_at if detected_at.tzinfo else detected_at.replace(tzinfo=UTC)) + timedelta(
        hours=hours
    )
    return f"{moment:%d %b %Y}"


def _day_phrase(value: datetime | date) -> str:
    """``date(2026, 8, 20)`` -> ``"20 Aug 2026"``, for a task's own columns."""
    return f"{value:%d %b %Y}"


def _clip(text: str, limit: int) -> str:
    """Shorten a user-written title to fit a sentence, on a character boundary.

    The stored title is already clipped to the column width by the detection
    module; this is the same bound applied again on the way into copy, because a
    suggestion's title has to leave room for the numbers around it.
    """
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"
