"""The skill-gap formulas, exercised as pure arithmetic.

No database, no HTTP, no ``integration`` marker: everything in
:mod:`app.services.learning.gaps` takes a :class:`~app.services.learning.gaps.SkillSample`,
a list of :class:`~app.services.learning.metrics.ActivitySample` and an instant,
and returns a :class:`~app.services.learning.gaps.SkillGap`. A gap can therefore
be checked against a hand-derived value without provisioning PostgreSQL, which
matters because the gap is the one figure in Phase 9 that is *about a person*: a
formula that quietly drifts does not raise, it renders.

Every expected figure below is derived by hand from the documented rule and
written into the test's own docstring, never copied from a run. The instants come
from a fixed UTC base rather than from ``datetime.now()``, so a gap computed today
returns the same gap next year and the suite cannot start failing because
somebody's timezone moved.

Five things are asserted rather than merely exercised:

* **The arithmetic.** ``max(0, target - current)``, always clamped — a target
  below the current level yields ``0``, not a negative distance backwards.
* **The two shapes of "no measurement".** An absence of measurement is
  ``available=False`` with a reason; a real measurement of zero is ``gap=0,
  available=True``. Both are asserted, and so is the case where they collide: a
  skill whose target equals its current level *and* which has nothing recorded
  against it, which is the single case where the frozen contract's two sentences
  overlap and where this module had to choose.
* **Who is allowed to say the level.** ``user_defined`` must be described as
  *self-assessed* and ``system_estimate`` as a *system estimate*, and the check is
  asserted as a construction-time failure because that is the only way it cannot
  be forgotten by the next person to write a sentence.
* **The refusal to estimate.** Below ``learning_min_evidence_for_estimate`` the
  engine declines rather than inventing a level — and declines *only* for a level
  it inferred itself, never for one the user set.
* **Purity and isolation.** Activities outside the window, activities naming a
  different skill, and activities with no skill at all are all excluded; each is
  asserted separately because each is a way a count could quietly overstate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.enums import SkillLevelSource
from app.services.learning.gaps import (
    EVIDENCE_WINDOW_DAYS,
    LEVEL_SOURCE_PHRASES,
    MIN_EVIDENCE_FOR_ESTIMATE,
    SkillGap,
    SkillSample,
    skill_gap,
    skill_gaps,
)
from app.services.learning.metrics import ActivitySample

#: The instant every test treats as "now". Fixed rather than "now" so a gap
#: derived from it is derived, not recorded.
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

#: The three skill identities used below. Real UUIDs rather than integers so a
#: mistake that compares them as opaque values fails loudly.
SKILL_ML = uuid.UUID("11111111-1111-4111-8111-111111111111")
SKILL_RUST = uuid.UUID("22222222-2222-4222-8222-222222222222")
SKILL_META = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _activity(
    *,
    days_ago: float = 1.0,
    skill_id: uuid.UUID | None = SKILL_ML,
    minutes: int | None = None,
) -> ActivitySample:
    """One recorded activity, ``days_ago`` days before :data:`NOW`.

    Half days are allowed so a test can place an activity outside a window without
    depending on the window's exact boundary.
    """
    return ActivitySample(
        occurred_at=NOW - timedelta(days=days_ago),
        skill_id=skill_id,
        duration_minutes=minutes,
    )


def _activities(count: int, *, skill_id: uuid.UUID | None = SKILL_ML) -> list[ActivitySample]:
    """``count`` activities spread one per day over the days before :data:`NOW`.

    One per day, days 1..``count``, so the count is unambiguous even under the
    half-open window: none of them sits on the boundary.
    """
    return [_activity(days_ago=day, skill_id=skill_id) for day in range(1, count + 1)]


def _skill(
    *,
    name: str = "Machine Learning",
    current_level: int = 2,
    target_level: int = 4,
    level_source: SkillLevelSource = SkillLevelSource.USER_DEFINED,
    skill_id: uuid.UUID | None = SKILL_ML,
    evidence_count: int = 0,
    last_activity_at: datetime | None = None,
) -> SkillSample:
    """One tracked skill with only the fields a gap reads."""
    return SkillSample(
        name=name,
        current_level=current_level,
        target_level=target_level,
        level_source=level_source,
        skill_id=skill_id,
        evidence_count=evidence_count,
        last_activity_at=last_activity_at,
    )


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_gap_is_the_difference_between_the_target_and_the_current_level():
    """Target 4, current 2 → a gap of 2, and the explanation names both.

    The sentence is asserted as an exact string because it is the contract's own
    worked example: *"Target 4/5, current self-assessed 2/5. NEXUS recorded 6
    related learning activities in the last 30 days."* Six activities are supplied
    — one per day on days 1 through 6 — so the window count is 6 and the evidence
    count on the row is the lifetime 9, which is a different figure and must not be
    confused with it.
    """
    gap = skill_gap(
        _skill(evidence_count=9, last_activity_at=NOW - timedelta(days=3)),
        now=NOW,
        activities=_activities(6),
    )

    assert gap.gap == 2
    assert gap.target_level == 4
    assert gap.current_level == 2
    assert gap.evidence_last_30d == 6
    assert gap.evidence_count == 9
    assert gap.available is True
    assert gap.reason_if_unavailable is None
    assert gap.explanation == (
        "Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning "
        "activities in the last 30 days."
    )


def test_a_gap_is_never_negative_when_the_target_is_below_the_current_level():
    """Target 2, current 4 → ``gap=0``, not ``-2``.

    A negative gap would be arithmetically correct and product nonsense: nobody
    is two levels *behind* a target they have already passed. The clamp is in
    ``max(0, target - current)`` and it is asserted here because it is the one
    place in the phase where a raw subtraction would have produced a number that
    reads as an accusation.
    """
    gap = skill_gap(
        _skill(current_level=4, target_level=2, evidence_count=3, last_activity_at=NOW),
        now=NOW,
        activities=_activities(3),
    )

    assert gap.gap == 0
    assert gap.available is True
    assert gap.explanation.startswith("Target 2/5, current self-assessed 4/5.")


def test_days_since_last_activity_counts_whole_utc_days():
    """Last activity 3 days ago → ``3``, and the count is clamped at zero.

    Derived from dates rather than from a raw duration so a session at 23:00
    yesterday reads as *yesterday*, which is the granularity a reader of this
    figure is thinking in. A ``last_activity_at`` in the future — clock skew
    between two servers, or a caller that passed the wrong instant — clamps to 0
    rather than reporting a negative number of days.
    """
    recent = skill_gap(
        _skill(evidence_count=3, last_activity_at=NOW - timedelta(days=3)),
        now=NOW,
        activities=_activities(3),
    )
    future = skill_gap(
        _skill(evidence_count=3, last_activity_at=NOW + timedelta(days=2)),
        now=NOW,
        activities=_activities(3),
    )

    assert recent.days_since_last_activity == 3
    assert future.days_since_last_activity == 0


# ---------------------------------------------------------------------------
# The two shapes of "no measurement"
# ---------------------------------------------------------------------------


def test_a_skill_with_nothing_recorded_against_it_is_unavailable_and_says_why():
    """Target 3, current 1, zero evidence → ``available=False`` with a reason.

    The gap arithmetic still runs and still yields 2, because the arithmetic does
    not depend on the evidence; what is withheld is the *reading*. The distinction
    matters because ``gap=2, available=False`` says "here are two numbers and
    NEXUS declines to draw a conclusion from them", which is a different and more
    honest card than a confident 2 with no caveat.

    ``days_since_last_activity`` is ``None``, not ``0``: ``0`` would claim
    something happened today.
    """
    gap = skill_gap(
        _skill(name="Rust", current_level=1, target_level=3, skill_id=SKILL_RUST), now=NOW
    )

    assert gap.available is False
    assert gap.gap == 2
    assert gap.evidence_count == 0
    assert gap.evidence_last_30d == 0
    assert gap.days_since_last_activity is None
    assert gap.reason_if_unavailable == (
        "NEXUS recorded 0 related learning activities in the last 30 days, so it "
        "offers no reading of this gap."
    )
    assert gap.explanation == (
        "Target 3/5, current self-assessed 1/5. NEXUS recorded 0 related learning "
        "activities in the last 30 days, so it offers no reading of this gap."
    )


def test_a_target_equals_its_current_level_with_evidence_is_a_measured_zero():
    """Target 4, current 4, five recorded activities → ``gap=0, available=True``.

    This is the *measured* zero: the user has said where they are, that is where
    they meant to be, and five activities are on the record behind it. It is a
    real answer and the panel should render it as one.
    """
    gap = skill_gap(
        _skill(
            current_level=4,
            target_level=4,
            evidence_count=5,
            last_activity_at=NOW - timedelta(days=1),
        ),
        now=NOW,
        activities=_activities(5),
    )

    assert gap.gap == 0
    assert gap.available is True
    assert gap.reason_if_unavailable is None
    assert gap.explanation == (
        "Target 4/5, current self-assessed 4/5. NEXUS recorded 5 related learning "
        "activities in the last 30 days."
    )


def test_a_measured_zero_and_an_unmeasured_zero_are_different_answers():
    """The same ``gap=0``, from two skills, in two different shapes.

    This is the case the whole design exists for, and it is the one place the
    frozen contract's two sentences overlap: §4 says a target met by the current
    level reports ``gap=0, available=True``, and it says a skill with nothing
    recorded reports ``available=False``. A brand-new skill sits in both
    descriptions at once.

    This module resolves the collision in favour of the second, and the argument
    is that ``available`` answers *"is there anything here to read?"* rather than
    *"is the arithmetic non-negative?"*. "You have reached the level you set for a
    skill you have never recorded learning for" is not a finding NEXUS is entitled
    to offer — it is reassurance manufactured out of the absence of data, which is
    the precise failure the phase's rules exist to prevent. So the measured zero
    above is available *because the evidence is there and the target is met*, and
    this one is not available at all.

    Both carry ``gap=0``; only one carries ``reason_if_unavailable``.
    """
    measured = skill_gap(
        _skill(
            name="Measured", current_level=4, target_level=4, evidence_count=5, last_activity_at=NOW
        ),
        now=NOW,
        activities=_activities(5),
    )
    unmeasured = skill_gap(
        _skill(name="Unmeasured", current_level=4, target_level=4, skill_id=SKILL_META),
        now=NOW,
    )

    assert (measured.gap, measured.available) == (0, True)
    assert (unmeasured.gap, unmeasured.available) == (0, False)
    assert measured.reason_if_unavailable is None
    assert unmeasured.reason_if_unavailable is not None
    assert "0 related learning activities" in unmeasured.explanation


def test_a_skill_with_evidence_outside_the_window_only_is_unavailable():
    """Two activities, but 45 and 60 days old → still nothing to read.

    Derived rather than guessed: the evidence window is
    ``now - 30 days``, so 45 and 60 days ago are both outside it, and the gap
    falls back to the *lifetime* ``evidence_count`` on the row for the "has
    anything ever been recorded" question. This skill has a lifetime count of 0,
    so nothing has ever been recorded and the reading is refused.

    The companion case — a lifetime count above zero but nothing inside the
    window — is available, because a reading that rests on three-month-old
    evidence is still a reading; the sentence then says the window found nothing,
    which is the honest way to say it.
    """
    stale = skill_gap(
        _skill(evidence_count=0, last_activity_at=None),
        now=NOW,
        activities=[_activity(days_ago=45), _activity(days_ago=60)],
    )
    old_but_recorded = skill_gap(
        _skill(evidence_count=2, last_activity_at=NOW - timedelta(days=45)),
        now=NOW,
        activities=[_activity(days_ago=45), _activity(days_ago=60)],
    )

    assert stale.available is False
    assert stale.evidence_last_30d == 0
    assert old_but_recorded.available is True
    assert old_but_recorded.evidence_last_30d == 0
    assert old_but_recorded.days_since_last_activity == 45
    assert "NEXUS recorded 0 related learning activities" in old_but_recorded.explanation


# ---------------------------------------------------------------------------
# Who is allowed to say the level
# ---------------------------------------------------------------------------


def test_a_user_defined_level_is_described_as_self_assessed():
    """A level the person set is quoted back, not endorsed.

    The contract's exact wording: *"current self-assessed 2/5"*. The claim is the
    user's and NEXUS is reporting it; two words carry that entire distinction and
    the whole product depends on them being there every time.
    """
    gap = skill_gap(
        _skill(evidence_count=3, last_activity_at=NOW),
        now=NOW,
        activities=_activities(3),
    )

    assert gap.level_source is SkillLevelSource.USER_DEFINED
    assert "current self-assessed 2/5" in gap.explanation
    assert "estimate" not in gap.explanation


def test_a_system_estimate_is_described_as_a_system_estimate():
    """A level NEXUS inferred is signed by NEXUS.

    Four activities — above the default threshold of 3 — so the estimate is
    presented rather than declined, and the sentence reads *"current NEXUS system
    estimate of 3/5"*. The word *estimate* is what makes this honest: without it
    the sentence would be byte-identical to the self-assessed one and the user
    would have no way to tell whose claim they were reading.
    """
    gap = skill_gap(
        _skill(
            current_level=3,
            target_level=5,
            level_source=SkillLevelSource.SYSTEM_ESTIMATE,
            evidence_count=4,
            last_activity_at=NOW,
        ),
        now=NOW,
        activities=_activities(4),
    )

    assert gap.level_source is SkillLevelSource.SYSTEM_ESTIMATE
    assert gap.available is True
    assert "current NEXUS system estimate of 3/5" in gap.explanation
    assert "self-assessed" not in gap.explanation


def test_both_level_sources_have_a_distinct_required_phrase():
    """The phrase table is the rule, stated as data.

    Asserted rather than left implicit because this mapping is the single most
    important pair of strings in the phase. The two phrases must be different
    strings, and neither may be a substring of the other, or one could be rendered
    in place of the other by accident.
    """
    user_phrase = LEVEL_SOURCE_PHRASES[SkillLevelSource.USER_DEFINED]
    estimate_phrase = LEVEL_SOURCE_PHRASES[SkillLevelSource.SYSTEM_ESTIMATE]

    assert user_phrase == "self-assessed"
    assert estimate_phrase == "system estimate"
    assert user_phrase not in estimate_phrase
    assert estimate_phrase not in user_phrase


# ---------------------------------------------------------------------------
# The refusal to estimate
# ---------------------------------------------------------------------------


def test_a_system_estimate_below_the_threshold_is_declined_rather_than_presented():
    """One activity behind a system estimate → NEXUS refuses to present it.

    The reason is asserted as an exact string because the refusal is only
    credible with its numbers attached: 1 recorded activity against the 3 needed.
    The gap arithmetic still runs and is still reported — withholding the *level*,
    not the arithmetic — and the sentence adds that the level is left where it is
    rather than restated, because a reader who sees a gap and no level needs to
    know which of the two was withheld.
    """
    gap = skill_gap(
        _skill(
            current_level=3,
            target_level=5,
            level_source=SkillLevelSource.SYSTEM_ESTIMATE,
            evidence_count=1,
            last_activity_at=NOW - timedelta(days=1),
        ),
        now=NOW,
        activities=_activities(1),
    )

    assert gap.available is False
    assert gap.gap == 2
    assert gap.evidence_last_30d == 1
    assert gap.reason_if_unavailable == (
        "NEXUS recorded 1 related learning activit(y/ies) in the last 30 days, below "
        "the 3 it needs before it will present an estimate of a level."
    )
    assert gap.explanation == (
        "Target 5/5, current NEXUS system estimate of 3/5. NEXUS recorded 1 related "
        "learning activit(y/ies) in the last 30 days, below the 3 it needs before it "
        "will present an estimate of a level. The level on record is left where it is "
        "rather than restated as an estimate."
    )


def test_a_user_defined_level_is_never_second_guessed_by_the_threshold():
    """One activity behind a *self-assessed* level → still available.

    The threshold guards the engine's own inferences and nothing else. One
    recorded activity is thin evidence for anything NEXUS might infer, and it is
    ample evidence that the user *did something*: the level is theirs, the gap is
    the difference between two of their own numbers, and the sentence honestly
    says the window found 1 activity. Declining here would be NEXUS overruling a
    self-assessment, which is the one thing rule 1 forbids.
    """
    gap = skill_gap(
        _skill(evidence_count=1, last_activity_at=NOW - timedelta(days=1)),
        now=NOW,
        activities=_activities(1),
    )

    assert gap.available is True
    assert gap.gap == 2
    assert gap.reason_if_unavailable is None
    assert gap.explanation == (
        "Target 4/5, current self-assessed 2/5. NEXUS recorded 1 related learning "
        "activity in the last 30 days."
    )


def test_the_threshold_is_a_parameter_the_service_may_raise():
    """A caller-supplied minimum is honoured rather than assumed.

    Six activities sit behind a system estimate here, and the default threshold of
    3 would present it. Raising the minimum to 8 declines it instead, which is what
    a deployment that reads ``learning_min_evidence_for_estimate`` from its own
    settings needs to happen.
    """
    skill = _skill(
        current_level=2,
        target_level=4,
        level_source=SkillLevelSource.SYSTEM_ESTIMATE,
        evidence_count=6,
        last_activity_at=NOW,
    )
    activities = _activities(6)

    assert skill_gap(skill, now=NOW, activities=activities).available is True

    strict = skill_gap(skill, now=NOW, activities=activities, min_evidence_for_estimate=8)
    assert strict.available is False
    assert "the 8 it needs" in strict.reason_if_unavailable


# ---------------------------------------------------------------------------
# What the constructor refuses
# ---------------------------------------------------------------------------


def _gap_kwargs(**overrides: object) -> dict[str, object]:
    """A complete, valid set of ``SkillGap`` fields, with overrides applied."""
    fields: dict[str, object] = {
        "skill_id": SKILL_ML,
        "skill_name": "Machine Learning",
        "target_level": 4,
        "current_level": 2,
        "level_source": SkillLevelSource.USER_DEFINED,
        "gap": 2,
        "evidence_count": 9,
        "evidence_last_30d": 6,
        "days_since_last_activity": 3,
        "available": True,
        "reason_if_unavailable": None,
        "explanation": (
            "Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related learning "
            "activities in the last 30 days."
        ),
    }
    fields.update(overrides)
    return fields


def test_an_explanation_with_no_figure_in_it_is_rejected():
    """A gap whose sentence states no number cannot be constructed.

    "You are working towards your target level" is an adjective. The digit check
    is the same rule :class:`~app.services.risk.recommendation.RecommendationDraft`
    enforces on a recommendation's reason, and it lives at construction so the
    failure is loud in a test rather than silent on a screen.
    """
    with pytest.raises(ValueError, match="no figure in it"):
        SkillGap(**_gap_kwargs(explanation="You are working towards your target level."))


def test_a_bare_level_number_is_rejected_for_both_sources():
    """*"Target 4/5, current 2/5"* fails for either level source.

    A bare number reads identically whether the user typed it or NEXUS inferred
    it, and that ambiguity is precisely what Phase 9 exists to remove. Both cases
    are asserted because the failure is symmetric and either one reintroduced
    would be a regression.
    """
    bare = "Target 4/5, current 2/5. NEXUS recorded 6 related learning activities."

    with pytest.raises(ValueError, match="self-assessed"):
        SkillGap(**_gap_kwargs(explanation=bare))

    with pytest.raises(ValueError, match="system estimate"):
        SkillGap(**_gap_kwargs(level_source=SkillLevelSource.SYSTEM_ESTIMATE, explanation=bare))


def test_an_unavailable_gap_without_a_reason_is_rejected():
    """``available=False`` and ``reason_if_unavailable=None`` cannot be built.

    This is the failure that would render as a confident zero: a panel reading
    ``gap=0`` with no reason beside it would report that the user has met a target
    on a skill NEXUS knows nothing about. The constructor refuses the combination
    rather than trusting the caller to keep them consistent.
    """
    with pytest.raises(ValueError, match="must say why"):
        SkillGap(**_gap_kwargs(gap=0, available=False, reason_if_unavailable=None))


# ---------------------------------------------------------------------------
# Isolation between skills, and the assembly helper
# ---------------------------------------------------------------------------


def test_only_this_skill_s_activities_are_counted():
    """Six for Machine Learning and two for Rust → 6 and 2, never 8.

    Both gaps are computed from **one** list containing both skills' activities,
    which is how the service will call it: one query for the account's activities,
    filtered per skill here. Counting every activity against every skill would
    report a Rust gap backed by eight sessions of machine learning.
    """
    activities = _activities(6, skill_id=SKILL_ML) + _activities(2, skill_id=SKILL_RUST)

    ml = skill_gap(
        _skill(evidence_count=6, last_activity_at=NOW),
        now=NOW,
        activities=activities,
    )
    rust = skill_gap(
        _skill(
            name="Rust",
            current_level=1,
            target_level=3,
            skill_id=SKILL_RUST,
            evidence_count=2,
            last_activity_at=NOW,
        ),
        now=NOW,
        activities=activities,
    )

    assert ml.evidence_last_30d == 6
    assert rust.evidence_last_30d == 2


def test_an_unsaved_skill_matches_no_activity_at_all():
    """A skill with no ``skill_id`` borrows nobody's evidence.

    Without the guard, comparing an activity's ``skill_id`` against a ``None``
    skill id would match every activity that names no skill — handing a brand-new
    skill somebody else's untracked sessions as if they were its evidence.
    """
    gap = skill_gap(
        _skill(name="Unsaved", current_level=1, target_level=3, skill_id=None),
        now=NOW,
        activities=[ActivitySample(occurred_at=NOW - timedelta(days=1), skill_id=None)],
    )

    assert gap.evidence_last_30d == 0
    assert gap.available is False


def test_an_activity_at_the_exact_window_boundary_is_inside_it():
    """Exactly ``30`` days ago still counts; one second earlier does not.

    The window runs ``now - 30 days`` up to and including ``now``, so an activity
    sitting exactly on the lower edge is inside and the one a second before it is
    out. This is asserted because the boundary is the single place where a
    different choice would move a number without anybody noticing.
    """
    skill = _skill(evidence_count=1, last_activity_at=NOW - timedelta(days=30))
    boundary = NOW - timedelta(days=30)

    on_boundary = skill_gap(
        skill,
        now=NOW,
        activities=[ActivitySample(occurred_at=boundary, skill_id=SKILL_ML)],
    )
    just_past = skill_gap(
        skill,
        now=NOW,
        activities=[ActivitySample(occurred_at=boundary - timedelta(seconds=1), skill_id=SKILL_ML)],
    )

    assert on_boundary.evidence_last_30d == 1
    assert just_past.evidence_last_30d == 0


def test_an_activity_recorded_after_now_does_not_count():
    """A future-dated activity is excluded.

    A caller who passes the window end as something other than "now" must not get
    rows beyond it counted. ``activities`` is bounded above by ``now`` for the same
    reason the metrics' windows are half-open: a row on the boundary would
    otherwise belong to two readings at once.
    """
    gap = skill_gap(
        _skill(evidence_count=1, last_activity_at=NOW),
        now=NOW,
        activities=[
            _activity(days_ago=1),
            ActivitySample(occurred_at=NOW + timedelta(minutes=5)),
        ],
    )

    assert gap.evidence_last_30d == 1


def test_skill_gaps_returns_one_gap_per_skill_in_the_order_given():
    """Three skills in, three gaps out, in the same order, unmeasured included.

    The unavailable one is **kept** rather than filtered out: the whole point of
    the design is that the client can say *why* a row has no reading, and a panel
    that silently drops the row leaves the user wondering whether NEXUS lost it.
    """
    skills = [
        _skill(name="Machine Learning", skill_id=SKILL_ML, evidence_count=6, last_activity_at=NOW),
        _skill(name="Rust", current_level=1, target_level=3, skill_id=SKILL_RUST),
        _skill(
            name="Postgres",
            current_level=3,
            target_level=5,
            skill_id=SKILL_META,
            evidence_count=4,
            last_activity_at=NOW,
        ),
    ]

    gaps = skill_gaps(skills, now=NOW, activities=_activities(6))

    assert [gap.skill_name for gap in gaps] == ["Machine Learning", "Rust", "Postgres"]
    assert [gap.available for gap in gaps] == [True, False, True]
    assert [gap.gap for gap in gaps] == [2, 2, 2]


def test_skill_gaps_of_nothing_is_an_empty_tuple():
    """No skills, no gaps — and not a crash.

    A new account has no skills, and ``GET /learning/gaps`` has to answer with an
    empty list rather than an error.
    """
    assert skill_gaps([], now=NOW) == ()


# ---------------------------------------------------------------------------
# Agreement with the configuration the service will read
# ---------------------------------------------------------------------------


def test_the_pure_module_default_matches_the_configured_threshold():
    """``MIN_EVIDENCE_FOR_ESTIMATE`` is the value ``get_settings()`` returns.

    The pure module cannot import settings without making a pure test depend on
    the environment, so it carries the same default as a constant and the service
    passes the configured value in explicitly. That only works while the two agree,
    and this test is what keeps them from drifting apart silently.
    """
    assert get_settings().learning_min_evidence_for_estimate == MIN_EVIDENCE_FOR_ESTIMATE


def test_the_evidence_window_is_the_thirty_days_the_field_is_named_for():
    """``EVIDENCE_WINDOW_DAYS`` is 30, matching the name of the field it fills.

    ``evidence_last_30d`` is a fixed 30-day window rather than a parameter: a field
    by that name holding a seven-day count would be a lie in the one place a
    reader looks for the size of the sample behind an estimate.
    """
    assert EVIDENCE_WINDOW_DAYS == 30
