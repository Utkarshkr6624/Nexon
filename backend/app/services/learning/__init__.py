"""Phase 9 — learning intelligence, split into arithmetic and orchestration.

Two modules here, one direction of flow:

* :mod:`app.services.learning.metrics` — pure functions from recorded activities
  and goals to the eight explained metrics and the activity series. No database,
  no ORM, no clock, no request.
* :mod:`app.services.learning.gaps` — pure functions from a skill and its
  activities to a :class:`~app.services.learning.gaps.SkillGap`. No database, no
  clock, no request.
* :mod:`app.services.learning.service` — *to be written by the next wave*. Reads
  and writes Phase 9 storage, calls the two pure modules, assembles the wire
  shapes, and emits the history events.

The split is the same one :mod:`app.services.risk` makes, and for the same
reason. A gap computed from ``(target=4, current=2, evidence in window=6)``
returns the same thing on any machine and can be asserted to the exact value in a
test that provisions no database. The orchestration above it is asserted
separately against a seeded fixture. Neither test needs the other, and a rule that
cannot be tested without a live PostgreSQL instance is a rule that stops being
checked the moment the environment is awkward — which is the moment it matters.

Why the boundary is drawn *there*
---------------------------------
The tempting alternative is a single ``LearningService`` that also computes the
gaps, so there is one file and one import. It fails in a specific way: the moment
anything about the arithmetic changes, the change has to be re-asserted through a
database, so it is not, so the arithmetic drifts. It fails in a second way, which
is worse — a service that can compute a gap can also *invent* one. Here
:mod:`gaps` is the only thing that constructs a
:class:`~app.services.learning.gaps.SkillGap`, and that class refuses to be built
from an explanation carrying no figure or one that renders a bare "current 2/5"
without saying whether the user set it or NEXUS inferred it. The rule that a
level is the user's *or visibly derived* is therefore a property of the code that
would have to be deliberately edited to break, not an intention in a docstring
somewhere above it.

Two things this package refuses to do, stated so the next wave inherits them
-----------------------------------------------------------------------

**It never stores a gap.** A ``skill_gaps`` table would be a second answer to
"how far from my target is this skill?" that could disagree with the dashboard the
moment a level was edited. Being computed on read is also what makes the phase's
sharpest distinction expressible at all: a *measured* zero gap (``gap=0`` against
a stated target) and an *unmeasured* one (``available=False`` with a reason) are
different answers, and a cache has already thrown one of them away by the time it
is written.

**It never moves a level.** Nothing in either pure module reads, infers, adjusts
or scores a ``current_level``. The levels live in :mod:`gaps` as a claim plus its
:class:`~app.models.enums.SkillLevelSource`, and the metrics appear beside them
only as counts of what was recorded. A level that moved because a metric changed
would be NEXUS editing a claim the user made, which is the one thing rule 1 of the
phase forbids.
"""

from app.services.learning.gaps import (
    EVIDENCE_WINDOW_DAYS,
    LEVEL_SOURCE_PHRASES,
    MIN_EVIDENCE_FOR_ESTIMATE,
    NOTHING_RECORDED,
    TOO_LITTLE_EVIDENCE_TO_ESTIMATE,
    SkillGap,
    SkillSample,
    skill_gap,
    skill_gaps,
)
from app.services.learning.metrics import (
    ACTIVITY_GRANULARITIES,
    FORBIDDEN_CLAIM_WORDS,
    LEARNING_METRICS,
    METRIC_UNITS,
    NOT_ENOUGH_DATA,
    OPEN_GOAL_STATUSES,
    RECENT_MONTH_DAYS,
    RECENT_WEEK_DAYS,
    ActivityGranularity,
    ActivitySample,
    GoalSample,
    LearningActivitySeries,
    LearningBucket,
    LearningMetric,
    MetricKey,
    activity_series,
    bucket_for,
    build_metrics,
    completion_rate,
    goal_deadline_distance_days,
    goal_progress,
    learning_consistency,
    learning_minutes,
    sessions_last_7d,
    sessions_last_30d,
    skill_activity_frequency,
)

__all__ = [
    "ACTIVITY_GRANULARITIES",
    "EVIDENCE_WINDOW_DAYS",
    "FORBIDDEN_CLAIM_WORDS",
    "LEARNING_METRICS",
    "LEVEL_SOURCE_PHRASES",
    "METRIC_UNITS",
    "MIN_EVIDENCE_FOR_ESTIMATE",
    "NOTHING_RECORDED",
    "NOT_ENOUGH_DATA",
    "OPEN_GOAL_STATUSES",
    "RECENT_MONTH_DAYS",
    "RECENT_WEEK_DAYS",
    "TOO_LITTLE_EVIDENCE_TO_ESTIMATE",
    "ActivityGranularity",
    "ActivitySample",
    "GoalSample",
    "LearningActivitySeries",
    "LearningBucket",
    "LearningMetric",
    "MetricKey",
    "SkillGap",
    "SkillSample",
    "activity_series",
    "bucket_for",
    "build_metrics",
    "completion_rate",
    "goal_deadline_distance_days",
    "goal_progress",
    "learning_consistency",
    "learning_minutes",
    "sessions_last_7d",
    "sessions_last_30d",
    "skill_activity_frequency",
    "skill_gap",
    "skill_gaps",
]

# The orchestration layer imports the schemas and repositories this package's pure
# half deliberately does not, so importing ``app.services.learning`` eagerly would
# pull the ORM into every consumer of a pure function. ``app.services.developer``
# takes the opposite trade because its pure half is much smaller; either way the
# rule is the same — nothing that only needs the arithmetic should be made to
# import the database to get it.
from app.services.learning.service import LearningIntelligenceService

__all__ += ["LearningIntelligenceService"]
