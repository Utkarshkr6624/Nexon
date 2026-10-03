"""Phase 9 learning intelligence: goals, skills, and the evidence between them.

Three tables, and the argument for all of them is the same one the phase rests
on: **NEXUS does not know how good anyone is at anything.** Every level in
:mod:`app.models.learning` is either a number the user typed or a number the
engine derived while naming the evidence it derived it from, and the schema
makes the difference a *column* rather than a sentence in a comment.
:attr:`Skill.level_source` is that column: a level is ``user_defined`` because
the person set it, or ``system_estimate`` because NEXUS inferred it — and
:attr:`Skill.confidence` is how much recorded evidence that inference rests on,
which is ``0`` on a fresh skill and never silently a firm number. Nothing in
this module can render "you are not good at Machine Learning", because nothing
here holds a judgement about a person: it holds a claim and its provenance.

**A level is 1-5 and the check constraints say so.** Five is enough to be useful
and few enough that the difference between 3 and 4 means something; a 1-10 scale
would spend half its range on distinctions nobody can act on. Both ranges are
enforced in the database rather than only in the schema layer, because a level
that reached the table from a background job would otherwise be as trustworthy
as one that came through a validated request.

**No stored skill gap.** The obvious Phase 9 table — ``skill_gaps``, one row
per (user, skill) holding ``gap``, ``days_since_last_activity`` and a
``last_calculated_at`` — is deliberately absent, for the reason
:mod:`app.models.risk` gives about ``risk_scores`` and
:mod:`app.models.analytics` gives about weekly and monthly metrics: a gap is a
pure function of ``skills.current_level``, ``skills.target_level`` and a count
of :class:`LearningActivity` rows inside the requested window, so it is
re-derivable on every read. A stored copy would be a *second answer* to "how far
from my target is this skill?" that could disagree with the dashboard the moment
a level was edited, and the disagreement is always resolved by whichever page
the user happened to open first. Because it is computed on read, the gap
service can also answer the question a stored table cannot: a measured zero gap
(``gap=0`` because the target is met) and an unmeasured one (no evidence at all,
so ``available=False`` with a reason) are *different answers* and must not be
collapsed into a single null. That distinction only survives if nothing caches
it.

**``skills`` is unique per (account, name), and the name is the user's.**
``uq_skills_owner_name`` is not a global unique index: two people on two
accounts both being able to name "Python" is the entire point of a per-account
notebook, and a global index would make the second person's skill a conflict
about a vocabulary they do not share. It is scoped to the account rather than
left off because two rows called "Python" for one person would hold two
``current_level`` values that could disagree, and every gap read would have to
pick one.

**``learning_activities`` is append-only and carries no ``updated_at``.** A
recorded activity is a fact about a moment — this study session, these forty
minutes — and an ``onupdate`` stamp on it would assert the moment is still being
revised. The two tables above it *are* revised (a level is re-asserted, a goal
progresses) and therefore carry :class:`~app.db.base.TimestampMixin`. This is the
same split Phase 8 drew between ``git_repositories`` and ``git_commits``.

**``learning_activities`` is SET NULL on both of its references.** ``goal_id``
and ``skill_id`` are treated the same way and for the same reason: an abandoned
goal and a deleted skill are the same event, and neither should erase the record
that the user once worked on it. A cascade on ``skill_id`` would be the one row
in this module whose deletion is invisible — this table has no ``updated_at``, so
nothing would record that the history had been removed, and
``skills.evidence_count`` would go on counting a set that had already been
destroyed. What survives is an append-only fact with an unattributed subject, and
``app/services/learning/metrics.py`` already reads exactly that case as a real
session rather than an orphan. ``user_id`` stays ``CASCADE``, and the asymmetry
is deliberate: a row nobody can reach is an orphan no query can reach, while a
row whose *subject* is gone is still part of the account's own history.

**``duration_minutes`` is null for an event, not zero.** "I opened the article"
and "I spent forty minutes with it" are different facts and the column can tell
them apart; ``0`` would claim a measured zero-length session rather than the
absence of a measurement. The same reasoning is why ``occurred_at`` is *not*
nullable: an activity without an instant is not evidence of anything, so the
default ``now()`` is the only case where NEXUS supplies it, and it is supplied
only when the caller gave none.

**``source_type``/``source_id`` is a polymorphic pair, exactly as
``risks.entity_type``/``entity_id`` already is.** An activity may be typed in by
hand, or it may point at the task, note, project or repository it was derived
from — and rule 3 of the phase ("commits are not task completion") is enforced
by keeping the pointer *labelled*: a ``CODING_ACTIVITY`` whose source is a
repository is stored as a repository observation and nothing reads it back as a
completed task. Nulls do not collide in a btree index, so a manual activity and
a derived one are both representable without a sentinel row.

What is deliberately absent
---------------------------
* **No stored skill gap table**, argued above at length. The gap is a pure
  function and is computed on read by :mod:`app.services.learning.gaps`.
* **No ``updated_at`` on activities**, argued above.
* **No model of learning *outcome*.** There is no ``mastery`` column, no decay
  curve and no half-life: "the user forgets what they learned" is a claim about
  a person that nothing in this system can evidence, and a decaying scoreboard
  for skills is exactly the judgement the brief forbids. Recency is available as
  ``last_activity_at`` and as the window count, which is a fact.
* **No JSONB of any kind.** Unlike a risk evaluation's raw inputs, everything
  here is either a user-supplied column or a row that exists somewhere else;
  a blob would be a second copy of a record that is already in the database.
* **No ``relationship()`` anywhere in this module.** This codebase has zero ORM
  relationships and navigation is an explicit owner-scoped ``select()``, because
  the lazy load a relationship invites is exactly the read that forgets to
  filter on ``user_id`` — and rule 5 of the phase says another account's goal is
  a 404, never a 403.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    LearningGoalStatus,
    ProjectPriority,
    SkillLevelSource,
)

__all__ = [
    "DEFAULT_LEARNING_GOAL_PRIORITY",
    "DEFAULT_LEARNING_GOAL_STATUS",
    "DEFAULT_SKILL_CURRENT_LEVEL",
    "DEFAULT_SKILL_LEVEL_SOURCE",
    "DEFAULT_SKILL_TARGET_LEVEL",
    "MAX_SKILL_LEVEL",
    "MIN_SKILL_LEVEL",
    "LearningActivity",
    "LearningGoal",
    "Skill",
]

#: What a goal carries before the user moves it. The *lowest* member of
#: :class:`~app.models.enums.LearningGoalStatus` rather than ``in_progress``,
#: because a goal somebody created during planning has demonstrably not started —
#: defaulting it forward would claim an activity nobody recorded.
DEFAULT_LEARNING_GOAL_STATUS = LearningGoalStatus.NOT_STARTED.value

#: Goals are prioritised on the same four grades as projects and tasks, so the
#: user can sort a goal next to the work it competes with using one control.
#: Reusing :class:`~app.models.enums.ProjectPriority` rather than defining a
#: near-identical vocabulary: a second scale would give the two tables a way to
#: disagree about what "high" means.
DEFAULT_LEARNING_GOAL_PRIORITY = ProjectPriority.MEDIUM.value

#: A new skill is a name the user typed and nothing else, so its level is
#: theirs. The ``user_defined`` default is the honest starting state; a skill
#: created as ``system_estimate`` would be claiming an inference that has not
#: happened yet.
DEFAULT_SKILL_LEVEL_SOURCE = SkillLevelSource.USER_DEFINED.value

#: A freshly named skill starts at the floor with the aim of reaching three.
#: Both are *the user's* starting position, and both are defaults rather than
#: constraints — the check constraints below own the 1-5 range.
DEFAULT_SKILL_CURRENT_LEVEL = 1
DEFAULT_SKILL_TARGET_LEVEL = 3

#: The ends of the level scale, exported because the gap service and the skill
#: schema layer both have to agree with the check constraints on
#: :class:`Skill` that enforce them. Kept here rather than in the enum module
#: because there is no enum — a level is a number the user typed, and inventing
#: a ``SkillLevel`` enum would make the stored value look more authoritative
#: than it is.
MIN_SKILL_LEVEL = 1
MAX_SKILL_LEVEL = 5

#: Sized for a :class:`~app.models.enums.LearningGoalStatus` member. The longest
#: is ``not_started`` (11), so 16 leaves room for the next member without a
#: migration.
_MAX_GOAL_STATUS_LENGTH = 16
#: Sized for a :class:`~app.models.enums.ProjectPriority` member. The longest is
#: ``critical`` (8); 16 is the width the risk and task tables already use for
#: the very same vocabulary.
_MAX_GOAL_PRIORITY_LENGTH = 16
#: Sized for a :class:`~app.models.enums.SkillLevelSource` member. The longest
#: is ``system_estimate`` (15) — by far the longest word in the module, and the
#: reason this column is 24 and not 16.
_MAX_LEVEL_SOURCE_LENGTH = 24
#: Sized for a :class:`~app.models.enums.LearningActivityType` member. The
#: longest is ``project_completed`` (17), so 32 has room for a longer event
#: without a migration.
_MAX_ACTIVITY_TYPE_LENGTH = 32
#: A subsystem name: ``manual``, ``task``, ``note``, ``project``, ``repository``.
#: The longest is ``repository`` (10), and the column is deliberately twice that
#: plus margin because this is the one column whose vocabulary is *open* — a new
#: Phase 10 subsystem deriving activities must not need a migration to say so,
#: and an unknown value here is inert (it simply names no known subsystem)
#: rather than dangerous.
_MAX_ACTIVITY_SOURCE_TYPE_LENGTH = 32
#: A one-line goal title or activity title. A name for a piece of learning, not
#: a sentence about it; anything longer belongs in ``description``.
_MAX_GOAL_TITLE_LENGTH = 200
_MAX_ACTIVITY_TITLE_LENGTH = 200
#: A skill name is a single technology or discipline the user typed. Wider than
#: a tag and narrower than a title: "Natural Language Processing" fits, a
#: paragraph about it does not.
_MAX_SKILL_NAME_LENGTH = 120
#: A skill's category. ``language``, ``framework``, ``domain`` and ``practice``
#: are suggestions the UI offers and deliberately *not* a closed set — a user
#: who calls something "embedded" is not wrong — so this holds a free word with
#: room to spare rather than an enum.
_MAX_SKILL_CATEGORY_LENGTH = 64
#: A topic the goal is about, in the user's own words, for the goal whose topic
#: has no :class:`Skill` row yet.
_MAX_TARGET_TOPIC_LENGTH = 200


class LearningGoal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One thing the user said they meant to learn, and how it is going.

    ``progress`` is the user's own percentage, or the one they were shown and
    accepted — it is never computed by summing activities, because a study
    session and a goal are different units and pretending otherwise is the first
    step towards NEXUS claiming to know whether somebody learned something.
    """

    __tablename__ = "learning_goals"

    __table_args__ = (
        Index("ix_learning_goals_user_id", "user_id"),
        # "My goals, by state" — the dashboard's active-goals panel, and the
        # filter behind the completion rate.
        Index("ix_learning_goals_owner_status", "user_id", "status"),
        # "What is due soon" and the deadline-distance feature. The composite
        # carries the nulls the plain `(user_id)` index cannot order by date.
        Index("ix_learning_goals_owner_target_date", "user_id", "target_date"),
        CheckConstraint(
            "progress >= 0 AND progress <= 100", name="ck_learning_goals_progress_range"
        ),
        CheckConstraint(
            "completed_at IS NULL OR status = 'completed'",
            name="ck_learning_goals_completed_has_terminal_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: What the user called it. Never rewritten by NEXUS.
    title: Mapped[str] = mapped_column(String(_MAX_GOAL_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The skill this goal is about, when one already exists. ``SET NULL``
    #: because a goal can name a topic before the skill does — that is what
    #: :attr:`target_topic` is for, and forcing a skill row to exist first would
    #: put a barrier in front of "I want to learn Rust".
    target_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The same idea in the user's own words, for a goal whose topic is not (yet)
    #: a tracked skill. Both may be set; neither has to be.
    target_topic: Mapped[str | None] = mapped_column(
        String(_MAX_TARGET_TOPIC_LENGTH), nullable=True
    )
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: A :class:`~app.models.enums.ProjectPriority` value, stored as a plain
    #: string against the very same four grades projects use.
    priority: Mapped[str] = mapped_column(
        String(_MAX_GOAL_PRIORITY_LENGTH),
        server_default=DEFAULT_LEARNING_GOAL_PRIORITY,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(_MAX_GOAL_STATUS_LENGTH),
        server_default=DEFAULT_LEARNING_GOAL_STATUS,
        nullable=False,
    )
    #: 0-100, enforced in the database. Enforced rather than merely bounded in
    #: Python because a background job writing 150 would be a claim about
    #: progress that no page could render honestly.
    progress: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: The user's own estimate of the work involved. Explicitly *not* NEXUS's:
    #: deriving it from activity durations would be the first of the several
    #: places this phase could have implied that NEXUS knows what a task takes,
    #: and it does not. Null means "not estimated", which is not the same as zero.
    estimated_effort_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The project this goal is working towards, and the note it belongs to.
    #: Both ``SET NULL``: a goal the user wrote outlives the project or the note
    #: it referenced, and deleting a project must not delete the intention.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    note_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: When the goal reached ``completed``. Nullable, and the check constraint
    #: below holds the two together: a completion stamp without a terminal status
    #: is a contradiction, and a terminal status without the stamp loses the one
    #: date the user's history actually cares about.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One thing the user is learning, and where they say they are with it.

    The row carries a claim, its source and the evidence behind it — never a
    judgement. :attr:`current_level` is 1-5 because five is enough to be useful
    and few enough that 3 and 4 differ; :attr:`level_source` says whether the
    number came from the user or from an inference that must show its working;
    :attr:`confidence` is how much recorded evidence that inference rests on,
    with ``0`` meaning none rather than "low but present".
    """

    __tablename__ = "skills"

    __table_args__ = (
        # One row per (account, name). Not global: two accounts may each track
        # "Python", and a global index would make the second one a conflict
        # about a vocabulary they do not share. See the module docstring.
        UniqueConstraint("user_id", "name", name="uq_skills_owner_name"),
        Index("ix_skills_user_id", "user_id"),
        CheckConstraint(
            "current_level >= 1 AND current_level <= 5", name="ck_skills_current_level_range"
        ),
        CheckConstraint(
            "target_level >= 1 AND target_level <= 5", name="ck_skills_target_level_range"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_skills_confidence_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The user's own name for it, and the identity of the row within the
    #: account — see ``uq_skills_owner_name``.
    name: Mapped[str] = mapped_column(String(_MAX_SKILL_NAME_LENGTH), nullable=False)
    #: ``language`` / ``framework`` / ``domain`` / ``practice`` are suggestions,
    #: not a closed set, so this is a free word rather than an enum value.
    category: Mapped[str | None] = mapped_column(String(_MAX_SKILL_CATEGORY_LENGTH), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: THE honesty column's other half. 1-5, and the range is enforced in the
    #: database so a level written by anything other than the validated schema
    #: layer still lands inside the scale the UI knows how to render.
    current_level: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_SKILL_CURRENT_LEVEL), nullable=False
    )
    target_level: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_SKILL_TARGET_LEVEL), nullable=False
    )
    #: ``user_defined`` or ``system_estimate``, as a
    #: :class:`~app.models.enums.SkillLevelSource` value stored as a string. The
    #: single most important column in this module: it is what lets the UI say
    #: "your self-assessed level" and "our estimate from 6 recorded activities"
    #: with the same sentence shape and no dishonesty in either.
    level_source: Mapped[str] = mapped_column(
        String(_MAX_LEVEL_SOURCE_LENGTH),
        server_default=DEFAULT_SKILL_LEVEL_SOURCE,
        nullable=False,
    )
    #: 0-100: how much evidence backs an estimate. ``0`` on a user-defined
    #: level is not "untrustworthy", it is "there was nothing to estimate from",
    #: which is why it is the default rather than a mid value that would imply
    #: a thin sample exists.
    confidence: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: How many :class:`LearningActivity` rows point here. A cached counter
    #: rather than a stored gap: the count is a plain integer that changes only
    #: when a row is inserted, and the gap that depends on it is computed on read
    #: precisely so that no second answer can go stale. ``0`` is a real count.
    evidence_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    #: Null when nothing has been recorded against this skill, which is a
    #: different fact from "recorded, long ago" — and the staleness rule the
    #: recommendation engine reads treats the two differently.
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LearningActivity(UUIDPrimaryKeyMixin, Base):
    """One recorded thing the user did that counts as evidence of learning.

    Append-only, and therefore the one table here without an ``updated_at``: an
    activity is a fact about a moment, and stamping a revision on it would
    assert the moment is still being rewritten. ``RESOURCE_VIEWED`` is the
    weakest member of the vocabulary and is weighted as such by the consumer —
    opening a page is evidence that a page was opened, and nothing stronger.
    """

    __tablename__ = "learning_activities"

    __table_args__ = (
        Index("ix_learning_activities_user_id", "user_id"),
        # "My learning history, newest first" and every metric window. Separate
        # from the `(user_id)` probe above because the ordering column differs.
        Index("ix_learning_activities_user_occurred", "user_id", "occurred_at"),
        # "Everything recorded against this skill" — the per-skill evidence count
        # and the last-30-days figure the gap service reports.
        Index("ix_learning_activities_skill_id", "skill_id"),
        # "Everything recorded towards this goal", which must survive the goal.
        Index("ix_learning_activities_goal_id", "goal_id"),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes >= 0",
            name="ck_learning_activities_duration_non_negative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The skill this is evidence for, when the user named one. ``SET NULL``:
    #: an activity that outlived the skill it was recorded against still says
    #: *this user did this thing on this date*, and deleting it would make the
    #: recorded trail the one thing in this schema that a single row can erase.
    #: Nullable on the same account — a study session legitimately precedes
    #: having a skill row — so "no skill was named" and "the skill was deleted"
    #: are the same ``NULL`` and both are ordinary rather than loss.
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The goal this was recorded towards. ``SET NULL``, the same rule as
    #: :attr:`skill_id` and for the same reason: an abandoned goal and a deleted
    #: skill are the same event, and neither should erase the record that the
    #: user once worked on it — which is exactly the history the skill's
    #: evidence count summarises.
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_goals.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: A :class:`~app.models.enums.LearningActivityType` member. The longest is
    #: ``project_completed`` (17), and this is 32 so the next event type does not
    #: need a migration.
    activity_type: Mapped[str] = mapped_column(String(_MAX_ACTIVITY_TYPE_LENGTH), nullable=False)
    title: Mapped[str] = mapped_column(String(_MAX_ACTIVITY_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When it happened. Not nullable: an activity with no instant is not
    #: evidence of anything, and every read here is windowed.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    #: Null for an *event* ("I finished the chapter") as opposed to a *span*
    #: ("I spent forty minutes on it"). Zero would claim a measured
    #: zero-length session, which is a different and false fact.
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The record this was derived from, labelled — the same
    #: ``entity_type``/``entity_id`` shape ``risks`` already uses. Both null for
    #: a hand-entered activity, and nulls do not collide in an index, so a
    #: manual activity and a repository-derived one coexist without a sentinel
    #: row. Keeping the label is what stops "6 commits touched Python files"
    #: from being read back as "6 Python tasks completed".
    source_type: Mapped[str | None] = mapped_column(
        String(_MAX_ACTIVITY_SOURCE_TYPE_LENGTH), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    #: Deliberately not :class:`~app.db.base.TimestampMixin`: a recorded
    #: activity is immutable, and an ``updated_at`` here would assert otherwise.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
