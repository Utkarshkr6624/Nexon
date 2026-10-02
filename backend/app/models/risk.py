"""Phase 7 derived intelligence: risks, recommendations, and evaluation runs.

Three tables, and the shape of each is argued rather than assumed.

**``risks`` holds one row per live condition, not one row per evaluation.**
The brief's hardest requirement is "the same underlying risk should not generate
hundreds of identical records", and the naive design — insert on every run —
fails it in a way that is only visible in production. So the deduplication is a
*partial unique index* over ``(user_id, risk_type, entity_type, entity_id)``
restricted to the two live statuses::

    CREATE UNIQUE INDEX uq_risks_live_identity
        ON risks (user_id, risk_type, entity_type, entity_id)
        WHERE status IN ('active', 'acknowledged');

A partial index rather than a full-table unique constraint because a resolved
and then re-detected risk is a genuinely new observation and must be
recordable: the user cleared their overdue backlog, the risk went away, and then
the backlog came back. A full unique constraint would make that second episode
either impossible or an error. The partial index says exactly what it means —
*at most one live risk per condition* — and lets history accumulate without it.

The index is on ``entity_type``/``entity_id`` rather than on ``entity_id`` alone
because a task id and a project id are both uuids drawn from the same space. An
index keyed on the bare id would treat "task 7 is at risk" and "project 7 is at
risk" as the same risk, and the second one would silently swallow the first.
``entity_id`` is nullable for a risk that is about the account rather than a
row — the workload and consistency detectors both produce those — and a null
does not collide in a btree unique index, which is what lets a user hold several
distinct account-level risks of the same type at once. That is correct rather
than lucky: "workload risk" and "workload risk for next week" are different
conditions that happen to share a type.

**``recommendations`` deduplicates the same way**, over
``(user_id, recommendation_type, entity_type, entity_id)`` restricted to the
open statuses. The open set is ``new`` and ``viewed`` rather than "not
terminal", because a rejected recommendation should be re-raisable — the user
declined a suggestion once and the underlying condition did not change, so
re-opening the identical suggestion minutes later would be nagging.

**``risk_evaluations`` is one summary row per run, not one per risk.**
The brief asks for risk snapshots "where appropriate" and warns against
excessive duplication. A per-evaluation row is bounded by (runs x 1) rather than
(runs x risks), which is the difference between a table that grows when you open
the page and one that grows when you have work to do. It carries the counts by
severity and by type, plus when the run happened and how long it took — enough
to answer "is my risk trending up", "how long does resolution take" and "what
does a typical evaluation cost", which are the three questions Phase 10 will
ask. Per-risk resolution timing is already recoverable from ``risks.resolved_at``
and ``detected_at``; storing it twice would create a second answer that could
disagree with the first.

Why no ``risk_scores`` history table
------------------------------------
The obvious Phase 10 input is a time series of "what score did this task carry
each week". It is deliberately absent: a score is a pure function of the
analytics it reads, so it is re-derivable by re-running detection over a past
window, and a stored copy would be a second answer that could disagree with the
one the dashboard is showing. ``feature_snapshot`` from Phase 6 already extracts
the training features on demand, and the detection service here reads the same
inputs -- so the dataset is buildable without a table nobody can invalidate.

``score`` is NOT NULL with a zero default
-----------------------------------------
A risk always carries a number, because the brief forbids unexplained scores and
a severity band is derived from it. The explanation lives beside it in
``evidence`` (see :attr:`Risk.evidence`) rather than in a second table: the
evidence belongs to the detection that produced the risk and is replaced with it.
``metadata`` is the other half of the pair — the raw inputs the score was
computed from — and is what makes a stored risk auditable months later without
re-running detection over data that has since been rebuilt.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RecommendationPriority, RiskSeverity, RiskStatus

__all__ = [
    "DEFAULT_RECOMMENDATION_PRIORITY",
    "DEFAULT_RECOMMENDATION_STATUS",
    "DEFAULT_RISK_SEVERITY",
    "DEFAULT_RISK_STATUS",
    "Recommendation",
    "Risk",
    "RiskEvaluation",
]

#: What a new task gets when the caller does not choose. Same pattern as
#: :data:`app.models.task.DEFAULT_TASK_STATUS`, and here it is the *lowest*
#: member of each scale rather than the most common one, because a risk written
#: without a computed score is a bug and should look like one rather than
#: defaulting to a band nobody chose.
DEFAULT_RISK_SEVERITY = RiskSeverity.LOW.value
DEFAULT_RISK_STATUS = RiskStatus.ACTIVE.value
DEFAULT_RECOMMENDATION_PRIORITY = RecommendationPriority.MEDIUM.value
DEFAULT_RECOMMENDATION_STATUS = "new"

#: Sized for a one-line risk title. A risk title names a condition in a few
#: words; anything longer belongs in ``description``.
_MAX_RISK_TITLE_LENGTH = 200
_MAX_RECOMMENDATION_TITLE_LENGTH = 200
#: Sized for a short vocabulary word. The longest member any column here stores
#: is ``acknowledged`` (12).
_MAX_RISK_ENUM_LENGTH = 16
#: Sized for an entity type like ``project`` or ``task``.
_MAX_ENTITY_TYPE_LENGTH = 32

#: The statuses a risk can be re-detected into. Named here because the partial
#: unique index below, the repository's dedup query and the service's
#: resolution sweep all key on this same set: a change to one is a change to the
#: semantics of all three.
LIVE_RISK_STATUSES = ("active", "acknowledged")

#: The statuses a recommendation can be re-raised into.
OPEN_RECOMMENDATION_STATUSES = ("new", "viewed")

#: Spelled as a literal rather than interpolated. These two predicates are part
#: of the schema — the index definitions are generated from them by Alembic —
#: so building them by string formatting would mean a typo in a tuple became a
#: silently different index instead of a syntax error.
_RISKS_LIVE_PREDICATE = "status IN ('active', 'acknowledged')"
_RECOMMENDATIONS_OPEN_PREDICATE = "status IN ('new', 'viewed')"


class Risk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One live condition the detection engine found, and why it thinks so.

    ``score`` is 0-100 and ``severity`` is *derived* from it — never set
    independently. Keeping both would allow a row that says 91 and reads
    ``medium``, and the brief's "can I explain why every risk exists" is exactly
    the question such a row makes unanswerable.
    """

    __tablename__ = "risks"

    __table_args__ = (
        # The deduplication anchor. See the module docstring for why it is
        # partial: a resolved-and-returned risk is a new observation, and a full
        # unique constraint would make that episode unrepresentable.
        Index(
            "uq_risks_live_identity",
            "user_id",
            "risk_type",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=_RISKS_LIVE_PREDICATE,
        ),
        # Serves the Risk Center's default query, which is "my live risks,
        # worst first". The partial index above cannot serve it because it is
        # keyed for the equality probe of the dedup insert, not for the ordering
        # of a list.
        Index("ix_risks_owner_status_severity", "user_id", "status", "severity"),
        # "When did this get found?" for the dashboard's newest-first list.
        Index("ix_risks_detected_at", "detected_at"),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_risks_score_range",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR status IN ('resolved', 'dismissed')",
            name="ck_risks_terminal_has_timestamp",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    risk_type: Mapped[str] = mapped_column(String(_MAX_RISK_ENUM_LENGTH), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(_MAX_RISK_ENUM_LENGTH),
        server_default=DEFAULT_RISK_SEVERITY,
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    title: Mapped[str] = mapped_column(String(_MAX_RISK_TITLE_LENGTH), nullable=False)
    #: The plain-language statement of the condition. Neutral and factual by
    #: brief: it says what is true about the data, never what it implies about
    #: the person.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Ordered evidence lines — one per input that moved the score. This is what
    #: the UI renders under "Why", and it is why a risk with a score and no
    #: evidence cannot be constructed through the service.
    evidence: Mapped[list[str]] = mapped_column(
        JSONB,
        server_default="[]",
        default=list,
        nullable=False,
    )
    #: How much data the score was computed from. ``LOW`` is the cold-start
    #: signal: a thin sample is still reported, but never as though it were firm.
    evidence_strength: Mapped[str] = mapped_column(
        String(_MAX_RISK_ENUM_LENGTH), server_default="low", nullable=False
    )

    #: ``task`` / ``project`` / ``account``. Named rather than implied so an
    #: account-level risk (workload, consistency) can exist alongside a
    #: row-level one of the same type.
    entity_type: Mapped[str | None] = mapped_column(String(_MAX_ENTITY_TYPE_LENGTH), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(
        String(_MAX_RISK_ENUM_LENGTH),
        server_default=DEFAULT_RISK_STATUS,
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: The instant the status last changed to a terminal one. Null for a live
    #: risk, which is what makes "how long was this open" answerable without
    #: inspecting the event log.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The raw inputs the score was computed from. Kept beside ``evidence`` so a
    #: stored risk stays auditable after the underlying aggregates are rebuilt.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default="{}",
        default=dict,
        nullable=False,
    )


class Recommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A proposed action, its reason, and what the user did about it.

    Every member of :class:`~app.models.enums.RecommendationType` names something
    a *person* does. The engine proposes; it never performs — which the brief
    requires and which this table makes structural, because there is no type
    that means "the system rescheduled your task for you" to attach one to.
    """

    __tablename__ = "recommendations"

    __table_args__ = (
        # Same partial-unique shape as ``risks``, over the open statuses. A
        # rejected recommendation must be re-raisable: the user declined a
        # suggestion and the condition did not change, so re-raising the
        # identical suggestion would be nagging rather than noticing.
        Index(
            "uq_recommendations_open_identity",
            "user_id",
            "recommendation_type",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=_RECOMMENDATIONS_OPEN_PREDICATE,
        ),
        Index(
            "ix_recommendations_owner_status_priority",
            "user_id",
            "status",
            "priority",
        ),
        CheckConstraint(
            "priority IN ('critical', 'high', 'medium', 'low')",
            name="ck_recommendations_priority_known",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    recommendation_type: Mapped[str] = mapped_column(String(_MAX_RISK_ENUM_LENGTH), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(_MAX_RISK_ENUM_LENGTH),
        server_default=DEFAULT_RECOMMENDATION_PRIORITY,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(_MAX_RECOMMENDATION_TITLE_LENGTH), nullable=False)
    #: WHAT the user is being asked to do, in the imperative.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: WHY — the numbers, in words. Paired with :attr:`Risk.evidence` so a
    #: recommendation is never a bare imperative with nothing behind it.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    entity_type: Mapped[str | None] = mapped_column(String(_MAX_ENTITY_TYPE_LENGTH), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: The risk that raised this, when there was one. Nullable because a
    #: recommendation may come from a rule that fires without a persisted risk
    #: (cold start raises suggestions before anything is severe enough to be
    #: worth storing). ``ON DELETE SET NULL`` rather than CASCADE: deleting a
    #: risk must not delete the record of having acted on it, which is the
    #: training label.
    risk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risks.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(_MAX_RISK_ENUM_LENGTH),
        server_default=DEFAULT_RECOMMENDATION_STATUS,
        nullable=False,
    )
    #: When the user acted. Null until they do, which is how "how many
    #: recommendations were never answered" is answered without a second query.
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the risk behind this was resolved — the suggestion is *moot*,
    #: which is a more useful thing to record than "old".
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default="{}",
        default=dict,
        nullable=False,
    )


class RiskEvaluation(UUIDPrimaryKeyMixin, Base):
    """One summary row per detection run.

    Deliberately *not* one row per risk per run — see the module docstring. The
    counts are a snapshot of a moment; the risks themselves are the durable
    record, and a per-risk history would multiply this table by exactly the
    factor the brief warns against.
    """

    __tablename__ = "risk_evaluations"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "evaluated_at",
            "run_token",
            name="uq_risk_evaluations_run",
        ),
        # "My risk history, newest first" — the one read this table has.
        Index("ix_risk_evaluations_owner_evaluated", "user_id", "evaluated_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Distinguishes two evaluations that land in the same microsecond. The
    #: ``(user_id, evaluated_at, run_token)`` unique constraint exists purely so
    #: a retried insert cannot become a second summary of the same run; it is
    #: not a business key.
    run_token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )

    risks_found: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    risks_created: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    risks_updated: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    risks_resolved: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: ``{severity: count}`` for this run — the Risk Center's header tally.
    by_severity: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", default=dict, nullable=False
    )
    #: ``{risk_type: count}`` for this run — the "what kind of trouble am I in"
    #: breakdown.
    by_type: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", default=dict, nullable=False
    )
    recommendations_created: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    #: Milliseconds the detection pass took. Cheap to record and the only way to
    #: notice that a change made evaluation expensive, which is the performance
    #: failure the brief asks about and the one that never shows up in a
    #: functional test.
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: The window the evaluation reasoned over, so a snapshot is interpretable
    #: without also having to reconstruct which range produced it.
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
