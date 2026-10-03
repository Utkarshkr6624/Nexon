"""The skill gap, computed on read and never stored.

A gap is the distance between a skill's current level and the level its owner
wants to reach. It is the single most tempting number in this phase to put in a
table — ``skill_gaps(skill_id, gap, days_since_last_activity, last_calculated_at)``
— and it is deliberately absent, for the reason
:mod:`app.models.learning` gives at length: a gap is a pure function of
``skills.current_level``, ``skills.target_level`` and a count of
:class:`~app.models.learning.LearningActivity` rows inside the requested window.
A stored copy would be a *second answer* to "how far from my target is this
skill?" that could disagree with the dashboard the moment a level was edited, and
the disagreement is always resolved by whichever page the user opened first.

Being computed on read is not merely cheaper. It is what makes the distinction
this module is built around expressible at all: **a measured zero and an absent
measurement are two different answers, and something that caches them has already
thrown one away.** A skill whose target equals its current level has a gap of
zero — a real measurement against a stated target. A skill with nothing recorded
at all has no reading, and the answer to that is ``available=False`` with a
reason, never ``0``. No table with a nullable gap column and a null-means-zero
flag can hold both without eventually being read the wrong way; a function that
computes the shape from the facts can.

Two levels, and the level_source decides how the sentence is allowed to read
---------------------------------------------------------------------------
The brief's first rule is that NEXUS never claims to know how good anyone is at
anything, so a level is either a number the person typed or a number the engine
derived while naming the evidence it came from. :class:`SkillGap` carries both the
number and its
:class:`~app.models.enums.SkillLevelSource`, and
:data:`LEVEL_SOURCE_PHRASES` decides which words the explanation may use:

* ``user_defined`` → *"current self-assessed 2/5"*. The claim is the user's and
  NEXUS is quoting it back.
* ``system_estimate`` → *"current NEXUS system estimate of 2/5"*. The claim is
  NEXUS's and it has to sign it.

That mapping is enforced, not merely intended.
:meth:`SkillGap.__post_init__` rejects an explanation that omits the phrase its
level source requires, so "current 2/5" — the bare number the rule forbids — cannot
be constructed at all. The digit check lives beside it for the same reason
:class:`~app.services.risk.recommendation.RecommendationDraft` enforces one: "you
are working towards your target" is an adjective, and an explanation with no figure
in it is not explaining anything.

When NEXUS declines rather than estimating
------------------------------------------
``learning_min_evidence_for_estimate`` exists for a specific failure: an account
with one recorded activity would get a level derived from it, and a number derived
from a single row reads with exactly the same confidence as one derived from
forty. So the threshold is a **refusal**, not a low-confidence badge — a thin
sample shown with a "low confidence" label is still a claim, whereas a stated
refusal is not. When the level on the skill is a ``system_estimate`` and fewer
than the threshold's worth of activity was recorded in the window, the response
says so in the reason field and refuses to present the gap as a reading.

The asymmetry is deliberate and is worth stating plainly: **a user-defined level
is never second-guessed here.** If the person said they are at 2 and want 4, the
gap is 2, and NEXUS's job is to report that arithmetic and then say, honestly,
that 6 related activities were recorded in the last 30 days. It is not the
engine's place to decide the self-assessment was wrong.

Purity
------
Nothing in this module touches a database, an ORM model, a clock or a request. It
imports exactly one thing from outside: the read-only
:class:`~app.models.enums.SkillLevelSource` vocabulary. "Now" is a parameter
rather than a call to the clock, so the same tuple of inputs returns the same gap
forever and can be asserted to the exact value in a test with no PostgreSQL in it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models.enums import SkillLevelSource
from app.services.learning.metrics import ActivitySample

__all__ = [
    "EVIDENCE_WINDOW_DAYS",
    "LEVEL_SOURCE_PHRASES",
    "MIN_EVIDENCE_FOR_ESTIMATE",
    "NOTHING_RECORDED",
    "TOO_LITTLE_EVIDENCE_TO_ESTIMATE",
    "SkillGap",
    "SkillSample",
    "skill_gap",
    "skill_gaps",
]

#: The window :attr:`SkillGap.evidence_last_30d` counts inside. Fixed at thirty
#: rather than a parameter because the field is *named* for it: a field called
#: ``evidence_last_30d`` holding a seven-day count would be a lie in the one place
#: a reader would look for the size of the sample behind an estimate. The window's
#: boundaries are still supplied by the caller — ``now`` minus thirty days —
#: because this module reads no clock.
EVIDENCE_WINDOW_DAYS = 30

#: Below this many activities in the window, NEXUS will not present a level it
#: inferred itself. Mirrors ``learning_min_evidence_for_estimate`` in
#: :mod:`app.core.config`, which is the value a deployment tunes; this is the
#: pure module's default so a test needs no settings object. A caller that reads
#: the setting passes it in explicitly.
MIN_EVIDENCE_FOR_ESTIMATE = 3

#: What the explanation may call each kind of level. The single most important
#: pair of strings in the phase: the left one is the user's claim and NEXUS is
#: quoting it; the right one is NEXUS's own inference and it has to sign it. A
#: gap explanation that omits its required phrase cannot be constructed, so the
#: pair is enforced by :meth:`SkillGap.__post_init__` rather than by whoever
#: writes the next sentence.
LEVEL_SOURCE_PHRASES: dict[SkillLevelSource, str] = {
    SkillLevelSource.USER_DEFINED: "self-assessed",
    SkillLevelSource.SYSTEM_ESTIMATE: "system estimate",
}

#: Why a skill with nothing recorded against it gets no reading. ``0`` appears in
#: the copy on purpose: the sentence a user reads has to say that nothing was
#: recorded rather than leaving a bare "no data" beside an empty gap.
NOTHING_RECORDED = (
    "NEXUS recorded 0 related learning activities in the last {window} days, so it "
    "offers no reading of this gap."
)

#: Why a level NEXUS inferred itself is not presented when the sample behind it is
#: too thin. A template rather than a bare phrase because the refusal is only
#: credible with its numbers attached: "we need more data" is a shrug, "2 recorded
#: activities against the 3 needed" is a reason. The ``(y/ies)`` spelling is the
#: house idiom used elsewhere in the codebase's copy.
TOO_LITTLE_EVIDENCE_TO_ESTIMATE = (
    "NEXUS recorded {count} related learning activit(y/ies) in the last {window} days, "
    "below the {minimum} it needs before it will present an estimate of a level."
)


@dataclass(frozen=True, slots=True)
class SkillSample:
    """One tracked skill, reduced to the fields a gap reads.

    Deliberately not the ORM model: the gap must be computable from a hand-built
    instance in a test with no database in it. A sample is what the service layer
    builds from a ``skills`` row.

    ``evidence_count`` and ``last_activity_at`` are the row's own cached
    counters — ``evidence_count`` is a lifetime count of
    :class:`~app.models.learning.LearningActivity` rows pointing at the skill, and
    ``last_activity_at`` is null when nothing has ever been recorded, which is a
    different fact from "recorded, long ago". Both are read rather than recomputed
    here because only the service knows the lifetime figures; the *window* count
    in :attr:`SkillGap.evidence_last_30d` is computed from the activities the
    caller passes.

    Attributes:
        name: The user's own name for the skill.
        current_level: The level on the record, 1-5.
        target_level: The level being aimed at, 1-5.
        level_source: Whether the current level is the user's or NEXUS's estimate.
        skill_id: The row's identity, or ``None`` for an unsaved skill.
        evidence_count: Lifetime count of activities recorded against it.
        last_activity_at: When the last one happened, or ``None``.
    """

    name: str
    current_level: int
    target_level: int
    level_source: SkillLevelSource = SkillLevelSource.USER_DEFINED
    skill_id: uuid.UUID | None = None
    evidence_count: int = 0
    last_activity_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SkillGap:
    """One skill's distance from its target, and everything behind the number.

    Frozen with ``slots=True`` because a gap passes from this module to a service
    to a schema without anything in between having a reason to edit it.

    ``available`` and ``reason_if_unavailable`` are the pair the whole phase turns
    on, and the two readings they distinguish are genuinely different answers:

    * ``gap=0, available=True`` — the target is met. A real measurement, and a
      useful one to show.
    * ``gap=0, available=False`` — NEXUS has nothing recorded against this skill,
      so it has no business saying anything about how close anyone is. The ``gap``
      field is still an ``int`` rather than ``int | None`` because the contract
      freezes its type; the ``available`` flag is what every reader must honour,
      and :meth:`__post_init__` guarantees it is ``False`` exactly when there is a
      reason attached, so the zero can never be read as a measurement by accident.

    The field order is the contract's, verbatim, including ``skill_id`` first and
    ``explanation`` last. It is stated here because a dataclass cannot give a
    trailing field a default once an earlier one has one, and moving ``skill_id``
    to the end would have been a silent deviation from a frozen document for no
    gain: ``skill_id`` carries no default, so every caller supplies it, and
    ``None`` is the honest value for a skill that has not been saved yet.

    Attributes:
        skill_id: The row's identity, or ``None`` for an unsaved skill.
        skill_name: The user's own name for the skill.
        target_level: The level being aimed at, 1-5.
        current_level: The level on the record, 1-5.
        level_source: Whose claim the current level is.
        gap: ``max(0, target - current)``. Zero is a measurement, not an absence.
        evidence_count: Lifetime count of activities recorded against the skill.
        evidence_last_30d: How many fell inside the evidence window.
        days_since_last_activity: Days since the last recorded activity, or
            ``None`` when there has never been one. Never ``0`` for "unknown":
            ``0`` would claim something happened today.
        available: Whether there is a reading at all.
        reason_if_unavailable: Why not, when there is not.
        explanation: The sentence shown to the user, carrying its own figures.
    """

    skill_id: uuid.UUID | None
    skill_name: str
    target_level: int
    current_level: int
    level_source: SkillLevelSource
    gap: int
    evidence_count: int
    evidence_last_30d: int
    days_since_last_activity: int | None
    available: bool
    reason_if_unavailable: str | None
    explanation: str

    def __post_init__(self) -> None:
        """Reject a gap that could not explain itself honestly.

        Raises:
            ValueError: If the explanation carries no digit, does not describe the
                level with the phrase its source requires, or the gap is
                unavailable without saying why. The first is the brief's stated
                failure — an explanation with no figure in it is an adjective — and
                is the rule :class:`~app.services.risk.recommendation.RecommendationDraft`
                enforces on a recommendation's reason. The second is Phase 9's
                first rule: a bare "current 2/5" reads identically whether the
                number came from the user or from NEXUS, which is precisely the
                claim this design exists to forbid. The third is what stops
                "unavailable" rendering as an unexplained blank.
        """
        _check_gap_explanation(self.skill_name, self.level_source, self.explanation)
        if not self.available and not self.reason_if_unavailable:
            raise ValueError(
                f"Skill gap for {self.skill_name!r} is unavailable and must say why. An "
                "unavailable gap with no reason renders as an unexplained blank, and a "
                "gap of 0 with no reason is read as a measurement."
            )


def _as_utc(value: datetime) -> datetime:
    """Read an instant as UTC, normalising an aware one to that zone.

    The same rule :mod:`app.services.learning.metrics` applies before bucketing: an
    instant with no offset would otherwise be counted against the machine's local
    midnight, so the same session could fall inside the evidence window on one
    server and outside it on another.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _check_gap_explanation(
    skill_name: str, level_source: SkillLevelSource, explanation: str
) -> None:
    """Enforce the two rules every gap explanation must satisfy.

    Split out of :meth:`SkillGap.__post_init__` so the sentence can be checked
    before it is attached to a gap, and so the rule has one implementation rather
    than two that could drift.

    Args:
        skill_name: The skill's name, named in the error so the failure points at
            the row a reviewer will be looking at.
        level_source: The kind of claim the current level is.
        explanation: The sentence to check.

    Raises:
        ValueError: If it carries no digit, or omits the phrase its level source
            requires.
    """
    if not any(character.isdigit() for character in explanation):
        raise ValueError(
            f"Skill gap for {skill_name!r} has an explanation with no figure in it: "
            f"{explanation!r}. A gap explanation must state the levels and the evidence "
            "count it was built from, or it is an adjective rather than a reason."
        )
    required = LEVEL_SOURCE_PHRASES[level_source]
    if required not in explanation.lower():
        raise ValueError(
            f"Skill gap for {skill_name!r} describes a {level_source.value!r} level "
            f"without calling it {required!r}: {explanation!r}. A bare number reads "
            "the same whether the user set it or NEXUS inferred it, and Phase 9 "
            "forbids exactly that ambiguity."
        )


def _level_phrase(level_source: SkillLevelSource, current_level: int) -> str:
    """The *"current ..."* fragment of a gap explanation.

    Args:
        level_source: Whose claim the level is.
        current_level: The level on the record.

    Returns:
        ``"current self-assessed 2/5"`` for a user's own claim, or
        ``"current NEXUS system estimate of 2/5"`` for NEXUS's inference.
    """
    if level_source is SkillLevelSource.SYSTEM_ESTIMATE:
        return f"current NEXUS system estimate of {current_level}/5"
    return f"current self-assessed {current_level}/5"


def skill_gap(
    skill: SkillSample,
    *,
    now: datetime,
    activities: Sequence[ActivitySample] = (),
    min_evidence_for_estimate: int = MIN_EVIDENCE_FOR_ESTIMATE,
) -> SkillGap:
    """How far one skill sits from its target, and what is recorded behind that.

    The whole computation in one place, and it is short enough to read in one
    sitting: the gap is ``max(0, target - current)``, the evidence count is the
    number of the caller's activities for this skill inside the window, and the
    availability rule below decides whether either is something NEXUS is entitled
    to present.

    **The availability rule, stated plainly.** A reading is offered when NEXUS has
    something recorded for the skill *or* when the current level is the user's own.
    It is refused in exactly two cases:

    1. **Nothing is recorded at all** — no lifetime evidence, no last activity, no
       activity in the window. There is nothing to read, and a gap of ``0``
       computed against a target would be reassuring invented from the absence of
       data. This holds *even when the target is already met*: "you have reached
       the level you set for a skill you have never recorded learning for" is not
       a finding NEXUS is entitled to offer.
    2. **The level is NEXUS's own estimate and the sample behind it is too thin**
       — fewer than ``min_evidence_for_estimate`` activities in the window. A
       number inferred from one row reads with exactly the same weight as one
       inferred from forty, so the engine refuses rather than showing a
       low-confidence badge on a claim it invented.

    A user-defined level is never second-guessed. The person said they are at 2
    and want 4; the gap is 2, and NEXUS's job is to report that arithmetic and then
    say honestly how much was recorded against it.

    ``days_since_last_activity`` is ``None`` — never ``0`` — when nothing has ever
    been recorded, because ``0`` would claim something happened today.

    Args:
        skill: The skill to read.
        now: The instant treated as now. Supplied rather than read, because this
            module reads no clock and a gap that depended on the machine's wall
            time could not be asserted to an exact value.
        activities: Every recorded activity for this skill. Activities naming a
            different skill, or a different account's, are ignored here — the
            service has already filtered on ``user_id``, and this function
            filters on ``skill_id`` as a second, independent guard.
        min_evidence_for_estimate: How many activities in the window NEXUS needs
            before it will present an estimate it derived itself.

    Returns:
        The gap, available or not.
    """
    instant = _as_utc(now)
    window_start = instant - timedelta(days=EVIDENCE_WINDOW_DAYS)

    # A skill with no identity matches nothing. Comparing `activity.skill_id`
    # against a `None` skill id would otherwise match every activity that names no
    # skill — handing a brand-new, unsaved skill somebody else's untracked
    # sessions as its evidence.
    in_window = (
        []
        if skill.skill_id is None
        else [
            activity
            for activity in activities
            if activity.skill_id == skill.skill_id
            and window_start <= _as_utc(activity.occurred_at) <= instant
        ]
    )
    evidence_last_30d = len(in_window)

    days_since = None
    if skill.last_activity_at is not None:
        days_since = max(0, (instant.date() - _as_utc(skill.last_activity_at).date()).days)

    gap = max(0, skill.target_level - skill.current_level)
    header = (
        f"Target {skill.target_level}/5, {_level_phrase(skill.level_source, skill.current_level)}."
    )

    if skill.level_source is SkillLevelSource.SYSTEM_ESTIMATE and (
        evidence_last_30d < min_evidence_for_estimate
    ):
        reason = TOO_LITTLE_EVIDENCE_TO_ESTIMATE.format(
            count=evidence_last_30d,
            window=EVIDENCE_WINDOW_DAYS,
            minimum=min_evidence_for_estimate,
        )
        return SkillGap(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            target_level=skill.target_level,
            current_level=skill.current_level,
            level_source=skill.level_source,
            gap=gap,
            evidence_count=skill.evidence_count,
            evidence_last_30d=evidence_last_30d,
            days_since_last_activity=days_since,
            available=False,
            reason_if_unavailable=reason,
            explanation=(
                f"{header} {reason} The level on record is left where it is rather than "
                "restated as an estimate."
            ),
        )

    has_record = (
        skill.evidence_count > 0 or skill.last_activity_at is not None or evidence_last_30d > 0
    )
    if not has_record:
        reason = NOTHING_RECORDED.format(window=EVIDENCE_WINDOW_DAYS)
        return SkillGap(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            target_level=skill.target_level,
            current_level=skill.current_level,
            level_source=skill.level_source,
            gap=gap,
            evidence_count=skill.evidence_count,
            evidence_last_30d=0,
            days_since_last_activity=None,
            available=False,
            reason_if_unavailable=reason,
            explanation=f"{header} {reason}",
        )

    plural = "activity" if evidence_last_30d == 1 else "activities"
    return SkillGap(
        skill_id=skill.skill_id,
        skill_name=skill.name,
        target_level=skill.target_level,
        current_level=skill.current_level,
        level_source=skill.level_source,
        gap=gap,
        evidence_count=skill.evidence_count,
        evidence_last_30d=evidence_last_30d,
        days_since_last_activity=days_since,
        available=True,
        reason_if_unavailable=None,
        explanation=(
            f"{header} NEXUS recorded {evidence_last_30d} related learning {plural} in "
            f"the last {EVIDENCE_WINDOW_DAYS} days."
        ),
    )


def skill_gaps(
    skills: Sequence[SkillSample],
    *,
    now: datetime,
    activities: Sequence[ActivitySample] = (),
    min_evidence_for_estimate: int = MIN_EVIDENCE_FOR_ESTIMATE,
) -> tuple[SkillGap, ...]:
    """Compute a gap for every skill, in the order the skills were given.

    Ordering is deliberately left to the caller. Sorting here would bake one
    priority into a module that has no business holding an opinion about which of
    someone's skills matters most — and a panel that wants the widest gap first,
    or the most recently touched first, can say so in the one line that sorts.

    The full ``activities`` sequence is passed to each :func:`skill_gap`, which
    filters on ``skill_id`` itself. That is one linear pass per skill rather than
    an index, which is the right trade at the scale ``learning_max_skills`` allows
    (a hundred rows against a hundred activities) and keeps this module free of
    any assumption about how the service fetched them.

    Args:
        skills: Every skill the caller has, already filtered on ``user_id``.
        now: The instant treated as now, shared by every gap so they cannot
            disagree about which day a session fell on.
        activities: Every recorded activity the caller has.
        min_evidence_for_estimate: How many activities in the window NEXUS needs
            before it will present an estimate it derived itself.

    Returns:
        One gap per skill, in the order given, including the unavailable ones. A
        skill with no reading is still returned — the point of the design is that
        the client can say *why* rather than omit the row and let the user wonder.
    """
    return tuple(
        skill_gap(
            skill,
            now=now,
            activities=activities,
            min_evidence_for_estimate=min_evidence_for_estimate,
        )
        for skill in skills
    )
