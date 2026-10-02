"""Phase 6 analytics: the data engine behind every number NEXUS shows.

Two layers, and the split is the design
---------------------------------------
* :mod:`app.services.analytics.scoring` is **pure**. Plain numbers in, a score
  and its breakdown out. No database, no clock, no I/O — so every formula in
  this phase is a function someone can call twice with the same arguments and
  get the same answer, and can be argued about without a fixture.
* :class:`~app.services.analytics.service.AnalyticsService` is where the numbers
  come from: it groups the underlying rows into ``daily_metrics``, reads the
  previous equal-length window for comparison, and assembles the response
  carrying the score, its components and a formula string the UI renders
  verbatim.

What every layer below here guarantees
--------------------------------------
**A metric that cannot be computed from real rows is reported as unavailable,
never as zero.** Not "close enough to zero" — ``available=False`` with a stated
reason, which the API surfaces and the UI shows instead of a percentage. A user
who has completed two tasks is not told their deadline adherence is 0%; they are
told NEXUS has not observed enough to say.

That single rule is why ``daily_metrics`` carries ``UNIQUE (user_id,
metric_date)`` and why :func:`scoring.percent_change` returns ``None`` instead
of ``float('inf')``: a number that cannot be justified is worse than no number,
because nothing downstream can tell it apart from one that can.
"""

from app.services.analytics.scoring import (
    DEFAULT_WEIGHTS,
    NOT_ENOUGH_ACTIVITY,
    EstimationResult,
    ScoreComponent,
    ScoreResult,
    absolute_change,
    clamp_percentage,
    consistency_score,
    deadline_adherence,
    estimation_accuracy,
    focus_score,
    median,
    percent_change,
    productivity_score,
    rate,
)
from app.services.analytics.service import AnalyticsService

__all__ = [
    "DEFAULT_WEIGHTS",
    "NOT_ENOUGH_ACTIVITY",
    "AnalyticsService",
    "EstimationResult",
    "ScoreComponent",
    "ScoreResult",
    "absolute_change",
    "clamp_percentage",
    "consistency_score",
    "deadline_adherence",
    "estimation_accuracy",
    "focus_score",
    "median",
    "percent_change",
    "productivity_score",
    "rate",
]
