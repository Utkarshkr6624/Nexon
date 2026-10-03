"""The eight developer metrics, as pure functions from recorded facts to numbers.

Nothing in this module touches a database, an ORM, a clock or a subprocess. That
is not restraint for its own sake — it is the property that makes these numbers
auditable. A metric computed from ``(commits=18, active days=5, window=30)``
returns the same thing forever, on any machine, and can be asserted to the exact
value in a test that provisions no database and reads no git repository. It is
the same split :mod:`app.services.risk.scoring` and
:mod:`app.services.analytics.scoring` already make, and for the same reason: the
service layer gathers rows, this module does the arithmetic.

Git data is evidence, not a verdict
-----------------------------------
The governing rule of this phase is a rule about **language**, not about
politeness. A commit timestamp records when a commit object was written; it
cannot record how long anyone worked, how focused they were, or how hard they
tried. So this module emits counts, day totals, line totals and ratios — *"18
commits were recorded this week"* — and there is no function here whose output
could be read as *"you were productive for 6 hours"*.

The vocabulary is enforced rather than merely intended:
:data:`FORBIDDEN_CLAIM_WORDS` is checked by :func:`_explain`, so a metric whose
sentence reaches for "productivity", "focus", "effort" or "hours" fails at the
call site a reviewer will read, not silently on a dashboard. That is the
difference between a rule and a hope.

Absence of measurement is not zero
----------------------------------
This is the module's other load-bearing rule, and it is stated in
:class:`DeveloperMetric`'s own field list. **A real measurement of zero is
``value=0, available=True``; an absence of measurement is ``available=False``
with a reason.** They are different answers, and conflating them is how a
dashboard ends up telling someone with no history that they have no activity — a
confident zero that reads as reassurance about a question nothing was measured to
answer.

Which metrics can decline
-------------------------
Most cannot. ``commit_activity``, ``repository_activity``, ``change_volume``,
``active_days``, ``consistency``, ``repository_growth`` and
``maintenance_activity`` are all counts over rows the caller passed in, and a
count of zero rows is a *measurement*: "no commits were recorded in this window"
is a true and useful sentence, reported as one.

``recent_momentum`` is the exception, and it is a real exception. It is a ratio
with a denominator — commits in the seven days before the window — and dividing by
a denominator that is zero has no answer that is not invented. ``18 / 0`` yields
infinity, which a chart would render as infinite growth; ``0 / 0`` yields a
number, which would be a claim about a comparison that cannot be made. Both are
declined with :data:`NOT_ENOUGH_DATA` instead.

Every explanation carries its figures
------------------------------------
:class:`DeveloperMetric.__post_init__` rejects an explanation with no digit in
it, the same rule :class:`~app.services.risk.recommendation.RecommendationDraft`
enforces on a recommendation's reason. "Your activity has been consistent" is an
adjective; "5 of the last 30 days carried a commit" is a finding. A metric whose
sentence has no number in it is not explaining anything, and requiring the figure
at construction makes that failure loud in a test rather than silent on a screen.

Why this module does not read the file history itself
----------------------------------------------------
Three of the eight metrics need to know something the window's rows cannot say on
their own: which branches existed *before* the window opened
(:func:`repository_growth`), when each file was last touched *before* the window
(:func:`maintenance_activity`), and what instant "now" is
(:func:`recent_momentum`). Each of those is passed in by the caller rather than
derived here, because only the service layer knows what the record held before
the window started. Deriving them inside this module would mean assuming the
caller passed the repository's entire history — and a caller who passed a single
window's worth would get every commit classified as new, every file classified as
stale, and a momentum figure invented from a window that does not exist.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

__all__ = [
    "ACTIVITY_GRANULARITIES",
    "DEFAULT_MAINTENANCE_LOOKBACK_DAYS",
    "DEVELOPER_METRICS",
    "FORBIDDEN_CLAIM_WORDS",
    "METRIC_UNITS",
    "MOMENTUM_COMPARISON_DAYS",
    "NOT_ENOUGH_DATA",
    "ActivityBucket",
    "ActivityGranularity",
    "ActivitySeries",
    "DeveloperMetric",
    "MetricKey",
    "MetricSample",
    "active_days",
    "activity_series",
    "bucket_for",
    "build_metrics",
    "change_volume",
    "commit_activity",
    "consistency",
    "maintenance_activity",
    "recent_momentum",
    "repository_activity",
    "repository_growth",
]

#: What every metric says when it cannot judge. One string, not one per metric:
#: this is product copy, and product copy belongs in one place. The value and the
#: spelling match :data:`app.services.risk.scoring.NOT_ENOUGH_DATA` deliberately —
#: a user who reads "Not enough data" on two screens is reading one phrase, and two
#: phrasings would read as two different answers to the same question.
NOT_ENOUGH_DATA = "Not enough data to assess this yet"


class MetricKey(StrEnum):
    """The eight metric identities Phase 8 promises to report.

    A :class:`~enum.StrEnum` rather than eight bare strings because the key is a
    wire value: a URL filter, a dictionary key in the response, and the identity
    of a test case. One vocabulary object means a typo is an import error rather
    than a metric that silently never appears on the dashboard.
    """

    COMMIT_ACTIVITY = "commit_activity"
    REPOSITORY_ACTIVITY = "repository_activity"
    CHANGE_VOLUME = "change_volume"
    ACTIVE_DAYS = "active_days"
    CONSISTENCY = "consistency"
    REPOSITORY_GROWTH = "repository_growth"
    MAINTENANCE_ACTIVITY = "maintenance_activity"
    RECENT_MOMENTUM = "recent_momentum"


#: The eight metric keys in the order ``/developer/metrics`` returns them. A tuple
#: and not a set: the order is a reading order a human follows, and a set would
#: hand the same eight metrics back in whatever order the interpreter happened to
#: hash them, which changes the card order between runs.
DEVELOPER_METRICS: tuple[MetricKey, ...] = tuple(MetricKey)

#: The unit each metric's value is expressed in. Named rather than inferred from
#: the magnitude: ``0.5`` is a ratio or a score depending on which metric it came
#: from, and a client that guesses picks the wrong formatter and renders "0.5
#: commits".
METRIC_UNITS: dict[MetricKey, str] = {
    MetricKey.COMMIT_ACTIVITY: "count",
    MetricKey.REPOSITORY_ACTIVITY: "count",
    MetricKey.CHANGE_VOLUME: "lines",
    MetricKey.ACTIVE_DAYS: "days",
    MetricKey.CONSISTENCY: "ratio",
    MetricKey.REPOSITORY_GROWTH: "count",
    MetricKey.MAINTENANCE_ACTIVITY: "count",
    MetricKey.RECENT_MOMENTUM: "ratio",
}

#: How far back :func:`maintenance_activity` looks when deciding whether a file is
#: stale. Ninety days because that is the quarter a "has this been touched in a
#: while" question conventionally means, and because it is long enough that a file
#: inside an actively-developed area does not read as abandoned and short enough
#: that a file left behind two years ago still does.
DEFAULT_MAINTENANCE_LOOKBACK_DAYS = 90

#: The length of both halves of the :func:`recent_momentum` comparison. Equal
#: because a ratio of unequal windows measures the difference in *window length*
#: as much as the difference in activity — seven days against thirty would always
#: look like a collapse.
MOMENTUM_COMPARISON_DAYS = 7

#: Words no explanation here may contain. Not a style preference: the Phase 8
#: brief forbids claiming working time, focus, productivity or effort from git
#: data, and a metric whose sentence says "productive" is making a claim about a
#: person that no commit timestamp can support. Checking it in :func:`_explain`
#: makes the rule a property of the code rather than of whoever writes the next
#: metric's copy.
FORBIDDEN_CLAIM_WORDS: frozenset[str] = frozenset(
    {
        "busy",
        "burnout",
        "effort",
        "focus",
        "focused",
        "hard-working",
        "hardworking",
        "hours",
        "hours'",
        "productivity",
        "productive",
        "time spent",
        "work-life",
    }
)


class ActivityGranularity(StrEnum):
    """How wide one point on the activity chart is."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


#: The accepted ``granularity`` values, for an error message that lists them.
ACTIVITY_GRANULARITIES: tuple[str, ...] = tuple(member.value for member in ActivityGranularity)


@dataclass(frozen=True, slots=True)
class DeveloperMetric:
    """One metric: its number, and everything needed to argue with the number.

    A metric is never returned as a bare float. The bare float is the part that
    goes stale — a card showing ``0.4`` with no definition, no window and no
    source is a number the user has to simply trust, and "can I explain why every
    number here exists" is the question this phase is required to answer.
    ``definition``, ``window_days``, ``source`` and ``explanation`` are what make
    it answerable, and they are required fields rather than optional context for
    exactly that reason.

    Frozen with ``slots=True`` because a metric passes from a metric function to
    a service to a schema without anything in between having a reason to edit it;
    a mutable carrier would imply there is one.

    Attributes:
        key: The metric's stable identity, a :class:`MetricKey` value.
        label: Short human name for the card.
        value: The measured figure.
        unit: One of ``count``, ``lines``, ``days``, ``ratio``, ``score``.
        definition: One sentence saying how ``value`` is computed.
        window_days: The window the figure covers, or ``None`` for whole history.
        source: Which recorded facts the computation read, named so a reader can
            find the rows behind the number.
        explanation: The sentence shown to the user, carrying its own figures.
        available: Whether there is a measurement at all.
        reason_if_unavailable: Why not, when there is not. ``None`` when there is.
    """

    key: str
    label: str
    value: float
    unit: str
    definition: str
    window_days: int | None
    source: str
    explanation: str
    available: bool = True
    reason_if_unavailable: str | None = None

    def __post_init__(self) -> None:
        """Reject a metric that could not explain itself.

        Raises:
            ValueError: If the explanation carries no digit, names a claim this
                phase is forbidden to make, or the metric is unavailable without
                saying why. The first is the brief's "a metric with no figure in
                it is a bug" — an adjective is not an explanation. The second is
                the brief's refusal to infer working time, productivity, focus or
                effort from git data, checked here so that writing such a sentence
                fails at the call site rather than on a dashboard nobody will
                review. The third is what stops "unavailable" rendering as an
                unexplained blank.
        """
        _check_explanation(self.key, self.explanation)
        if not self.available and not self.reason_if_unavailable:
            raise ValueError(
                f"Metric {self.key!r} is unavailable and must say why. An unavailable "
                "metric with no reason renders as an unexplained blank."
            )


@dataclass(frozen=True, slots=True)
class MetricSample:
    """One recorded commit, reduced to the fields the metrics actually read.

    Deliberately not an ORM model: the metrics must be computable from a
    hand-built list in a test with no database in it. A sample is what the
    service layer builds from ``git_commits`` rows — the repository it belongs
    to, its branch, when it was committed, and how many lines it changed.

    Two fields exist because the window's own rows cannot supply the history:

    Attributes:
        committed_at: When the commit was authored, as the repository recorded it.
        repository_id: The repository the commit belongs to.
        branch: The branch the commit was attributed to, or ``None``.
        additions: Lines added.
        deletions: Lines deleted.
        files_changed: How many files the commit touched.
        first_seen_branches: Branch names first recorded inside the window. Empty
            for every branch that already existed when the window opened. Drives
            :func:`repository_growth`.
        file_paths: The paths the commit touched. Drives
            :func:`maintenance_activity`, which compares them against the
            repository's earlier history.
    """

    committed_at: datetime
    repository_id: str
    branch: str | None = None
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0
    first_seen_branches: frozenset[str] = frozenset()
    file_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityBucket:
    """One point on the activity chart: a period and the commits inside it.

    ``start`` is the bucket's first instant, floored to UTC midnight (or to the
    Monday that starts its week, or to the first of its month) so consecutive
    buckets are exactly adjacent. A chart that drew Tuesday 06:00-to-Wednesday
    06:00 would put a commit at 02:00 on the wrong bar.

    ``commit_count`` and ``line_change_count`` are kept separately rather than
    only summed, because "42 commits" and "12 changed files" answer different
    questions and a client forced to re-derive one from the other has no way to
    know which the tooltip meant.
    """

    start: datetime
    label: str
    commit_count: int
    line_change_count: int
    repository_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivitySeries:
    """A complete, gap-free run of buckets across a range.

    ``buckets`` is dense by construction — one entry per day, week or month from
    the first to the last, quiet periods included as zeros. That is the property
    the series builder exists for: a chart emitting only the days with commits
    would silently drop the quiet Tuesday, and a reader counting the bars would
    see five active days and read them as consecutive.
    """

    granularity: str
    start: datetime
    end: datetime
    buckets: tuple[ActivityBucket, ...]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    """Read an instant as UTC, normalising an aware one to that zone.

    The same rule :mod:`app.repositories.risk` applies before writing a
    ``timestamptz``: an instant with no offset would otherwise be bucketed
    against the machine's local midnight, so the same commit could land on two
    different days depending on which server ran the query. Bucketing is where
    that ambiguity becomes visible, which is why the conversion happens here
    rather than at the storage boundary.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _check_explanation(key: str, explanation: str) -> None:
    """Enforce the two rules every explanation sentence must satisfy.

    Split out of :meth:`DeveloperMetric.__post_init__` so the *building* helpers
    below can check a sentence before it is attached to a metric, and so the rule
    has one implementation rather than two that could drift.

    Args:
        key: The metric key, named in the error so the failure points at a caller.
        explanation: The sentence to check.

    Raises:
        ValueError: If it carries no digit, or uses forbidden claim vocabulary.
    """
    if not any(character.isdigit() for character in explanation):
        raise ValueError(
            f"Metric {key!r} has an explanation with no figure in it: {explanation!r}. "
            "An explanation must state the numbers it was built from, or it is an "
            "adjective rather than a reason."
        )
    lowered = explanation.lower()
    forbidden = sorted(word for word in FORBIDDEN_CLAIM_WORDS if word in lowered)
    if forbidden:
        raise ValueError(
            f"Metric {key!r} explains itself with {', '.join(forbidden)!r}. Git "
            "timestamps cannot show working time, focus, productivity or effort, so "
            "no explanation here may claim them."
        )


def _explain(key: MetricKey, template: str, **figures: object) -> str:
    """Render an explanation sentence and check it against the two rules.

    Args:
        key: The metric key, named in the error.
        template: A format string with one placeholder per figure.
        **figures: The values to substitute.

    Returns:
        The rendered sentence, already checked.

    Raises:
        ValueError: If the rendered sentence breaks either rule. Checked here as
            well as in the dataclass so the error names the sentence rather than
            arriving as a constructor failure three frames away.
    """
    rendered = template.format(**figures)
    _check_explanation(key.value, rendered)
    return rendered


def _span_days(window_start: datetime, window_end: datetime) -> int:
    """Whole days in ``[window_start, window_end)``, at least 1.

    One rather than zero for a zero-length window, because every explanation here
    divides by or describes this number. A zero-day window would put "0 of the 0
    days" on a card, which reads as a broken card rather than as an empty
    request, and would make :func:`consistency` divide by zero. Clamping up to 1
    keeps every explanation true and keeps the ratio computable.
    """
    delta = _as_utc(window_end) - _as_utc(window_start)
    return max(1, int(delta.total_seconds() // 86400))


def _in_window(sample: MetricSample, window_start: datetime, window_end: datetime) -> bool:
    """Whether a sample falls in ``[window_start, window_end)``.

    Half-open at the top, because a boundary that belonged to two buckets would
    double-count it: a commit at exactly midnight would be counted by the day that
    ended and the day that began, and :func:`active_days` would report more days
    than the window has.
    """
    instant = _as_utc(sample.committed_at)
    return _as_utc(window_start) <= instant < _as_utc(window_end)


def _selected(
    samples: Sequence[MetricSample], window_start: datetime, window_end: datetime
) -> list[MetricSample]:
    """The samples inside the window, in the order they were given.

    Not sorted: every metric here is order-independent and sorting would imply an
    ordering guarantee the callers do not get. What the window does guarantee is
    that the *same* rows are selected for every metric in one response, which is
    what makes the eight numbers comparable to each other.
    """
    return [sample for sample in samples if _in_window(sample, window_start, window_end)]


def _metric(
    key: MetricKey,
    *,
    label: str,
    value: float,
    definition: str,
    explanation: str,
    window_days: int,
    source: str,
) -> DeveloperMetric:
    """Assemble one measured metric from the shared constants.

    Every measured metric goes through here rather than constructing its own
    :class:`DeveloperMetric`, so the unit comes from :data:`METRIC_UNITS` and a
    metric cannot invent a unit the frontend has no formatter for. The declined
    case is separate, below, because its ``value`` carries no meaning.
    """
    return DeveloperMetric(
        key=key.value,
        label=label,
        value=value,
        unit=METRIC_UNITS[key],
        definition=definition,
        window_days=window_days,
        source=source,
        explanation=explanation,
        available=True,
    )


def _declined(
    key: MetricKey,
    *,
    label: str,
    definition: str,
    window_days: int,
    source: str,
    reason: str = NOT_ENOUGH_DATA,
) -> DeveloperMetric:
    """The one shape every "I cannot measure this" answer takes.

    ``value`` is ``0.0`` together with ``available=False``, and the pairing is
    deliberate rather than lazy. :class:`DeveloperMetric` requires a float, so an
    unavailable metric carries a zero it does not mean, and ``available=False`` is
    what tells every reader to ignore it. The alternative — a nullable value — is a
    better shape, and is a schema decision belonging to whoever owns
    ``app/models``, not something a pure module should invent. What matters is
    that no caller can mistake the zero for a measurement, and the two fields
    together make that impossible.

    The explanation is still figure-bearing on purpose. "Not enough data to
    assess this yet" alone would leave the reader unable to tell which figure was
    missing, so the sentence names the figure that was not computed alongside the
    reason — and stays inside the vocabulary rule while doing it.

    Args:
        key: The metric key.
        label: Human name for the card.
        definition: One sentence saying how it *would* be computed.
        window_days: The window the attempt covered.
        source: Which facts it would have read.
        reason: Why it could not be computed.

    Returns:
        An unavailable metric.
    """
    return DeveloperMetric(
        key=key.value,
        label=label,
        value=0.0,
        unit=METRIC_UNITS[key],
        definition=definition,
        window_days=window_days,
        source=source,
        explanation=_explain(
            key,
            "{reason} The {label} figure for the {window}-day comparison is not "
            "computed, because the records it would be read from are not present.",
            reason=reason,
            label=label,
            window=window_days,
        ),
        available=False,
        reason_if_unavailable=reason,
    )


# ---------------------------------------------------------------------------
# The eight metrics
# ---------------------------------------------------------------------------


def commit_activity(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """How many commits were recorded in the window.

    The plainest metric in the set and the one everything else is read against.
    It is a count of rows, so it is always available: a window with no commits is
    a *measured* zero, not a missing measurement.

    Merges are excluded upstream — the scanner reads with ``--no-merges`` — so
    this counts commits that represent work rather than commits replaying another
    branch's work into this one.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    count = len(windowed)
    span = _span_days(window_start, window_end)
    return _metric(
        MetricKey.COMMIT_ACTIVITY,
        label="Commit activity",
        value=float(count),
        definition="The number of commits recorded inside the window.",
        explanation=_explain(
            MetricKey.COMMIT_ACTIVITY,
            "{count} commit(s) were recorded in the {span}-day window.",
            count=count,
            span=span,
        ),
        window_days=span,
        source="git_commits.committed_at",
    )


def repository_activity(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """How many distinct repositories carried a commit in the window.

    Distinct repositories rather than commits, so a window spread across four
    projects reads as breadth rather than as volume — which is the distinction a
    commit count cannot make, and the entire reason this metric exists alongside
    :func:`commit_activity`.

    Always available, for the same reason: no repository with a commit is a
    measured zero, and that is a useful thing to tell someone who has registered a
    repository and not committed to it yet.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    count = len({sample.repository_id for sample in windowed})
    span = _span_days(window_start, window_end)
    return _metric(
        MetricKey.REPOSITORY_ACTIVITY,
        label="Repository activity",
        value=float(count),
        definition="Distinct repositories with at least one commit inside the window.",
        explanation=_explain(
            MetricKey.REPOSITORY_ACTIVITY,
            "{count} of the tracked repositories recorded at least one commit in the "
            "{span}-day window.",
            count=count,
            span=span,
        ),
        window_days=span,
        source="git_commits.repository_id, git_commits.committed_at",
    )


def change_volume(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """How many lines were added and removed in the window.

    Additions and deletions **summed**, not netted. The net figure can go
    negative — a large deletion reads as "-900 lines", which looks like a loss —
    and the sum answers the question a reader actually has, which is how much of
    the repository was touched. A wholesale rewrite is the busiest thing that
    happened in the window, and a net-zero metric would call it quiet.

    Always available: no changed lines is a measured zero.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    additions = sum(max(0, sample.additions) for sample in windowed)
    deletions = sum(max(0, sample.deletions) for sample in windowed)
    span = _span_days(window_start, window_end)
    return _metric(
        MetricKey.CHANGE_VOLUME,
        label="Change volume",
        value=float(additions + deletions),
        definition=(
            "Lines added plus lines deleted across commits recorded in the window, "
            "summed rather than netted."
        ),
        explanation=_explain(
            MetricKey.CHANGE_VOLUME,
            "{total} changed line(s) were recorded in the {span}-day window: "
            "{additions} added and {deletions} deleted.",
            total=additions + deletions,
            span=span,
            additions=additions,
            deletions=deletions,
        ),
        window_days=span,
        source="git_commits.additions, git_commits.deletions",
    )


def active_days(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """How many distinct UTC dates in the window carried a commit.

    One commit on a day counts once, however many. Counting commits instead —
    which is what a naive bucket total gives — would make a single busy day read
    as a week of activity, and would turn this into a restatement of
    :func:`commit_activity` wearing a different name.

    Dates are UTC because the commit timestamps carry a real offset, and a local
    zone would move a 23:30 UTC commit onto the following day depending on where
    the server runs. The contracts name UTC dates for this metric too.

    Always available.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    count = len({_as_utc(sample.committed_at).date() for sample in windowed})
    span = _span_days(window_start, window_end)
    return _metric(
        MetricKey.ACTIVE_DAYS,
        label="Active days",
        value=float(count),
        definition="Distinct UTC calendar dates carrying at least one commit in the window.",
        explanation=_explain(
            MetricKey.ACTIVE_DAYS,
            "{count} of the {span} days in the window carried at least one commit.",
            count=count,
            span=span,
        ),
        window_days=span,
        source="git_commits.committed_at",
    )


def consistency(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """What share of the window's days carried a commit.

    ``active_days / window_days``, clamped to 0-1. A ratio rather than a
    percentage because the value is used in arithmetic by a later phase, and a
    metric whose unit changed with its caller is a metric that cannot be summed.

    Clamped rather than trusted, because ``window_days`` is a caller-supplied
    number and a zero would make this divide by zero. :func:`_span_days` already
    floors the window at one day; the clamp is the second half of that guarantee,
    and it matters because a zero-day window should render as "0 of the 1 day"
    rather than as a crash in the one metric here that divides.

    **What this is not.** It is a count of days with commits over a count of days
    in the window. It is not a claim about habit, discipline or reliability, and
    the explanation says exactly that much: an empty window reads "0 of 30 days",
    which is a fact about the record and nothing about the person.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    span = _span_days(window_start, window_end)
    count = len({_as_utc(sample.committed_at).date() for sample in windowed})
    ratio = min(1.0, max(0.0, count / span))
    return _metric(
        MetricKey.CONSISTENCY,
        label="Consistency",
        value=round(ratio, 4),
        definition=(
            "Active days in the window divided by the days in the window, as a ratio "
            "between 0 and 1."
        ),
        explanation=_explain(
            MetricKey.CONSISTENCY,
            "{count} of the {span} days in the window carried a commit, which is "
            "{percentage}% of them.",
            count=count,
            span=span,
            percentage=round(ratio * 100),
        ),
        window_days=span,
        source="git_commits.committed_at",
    )


def repository_growth(
    samples: Sequence[MetricSample], *, window_start: datetime, window_end: datetime
) -> DeveloperMetric:
    """Branches first recorded inside the window.

    "Repository growth" here means **branch growth**: the number of branches the
    record had never seen before the window opened, counted once each. It is the
    closest thing git records to "a new line of work started", and even that is a
    statement about branches appearing rather than about ideas arriving.

    Counting each new branch **once** rather than counting the commits that land
    on it is the whole distinction. Counting commits would make one long-lived
    feature branch read as continuous growth for as long as it stays open, which
    is a claim about someone still working rather than about anything starting.

    The set comes from :attr:`MetricSample.first_seen_branches` rather than being
    derived here, because only the service knows what the record held *before* the
    window opened. Deriving it inside this module would mean assuming the caller
    passed every commit ever recorded — and a caller who passed one window's worth
    would count every branch as new.

    Always available, and a measured zero is the *normal* answer rather than an
    alarming one: most windows add no branches, and "0 branches were first
    recorded" is a true sentence about a steady fortnight.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    new_branches: set[str] = set()
    for sample in windowed:
        new_branches.update(sample.first_seen_branches)
    span = _span_days(window_start, window_end)
    count = len(new_branches)
    return _metric(
        MetricKey.REPOSITORY_GROWTH,
        label="Repository growth",
        value=float(count),
        definition="Branches first recorded inside the window, counted once each.",
        explanation=_explain(
            MetricKey.REPOSITORY_GROWTH,
            "{count} branch(es) were first recorded inside the {span}-day window.",
            count=count,
            span=span,
        ),
        window_days=span,
        source="git_branches.created_at, git_commits.branch",
    )


def maintenance_activity(
    samples: Sequence[MetricSample],
    *,
    window_start: datetime,
    window_end: datetime,
    last_touched_before: Mapping[tuple[str, str], datetime] | None = None,
    lookback_days: int = DEFAULT_MAINTENANCE_LOOKBACK_DAYS,
) -> DeveloperMetric:
    """Commits that reached a file the record had not seen touched recently.

    A commit counts when **any** of its files was last recorded as changed more
    than ``lookback_days`` before the window opened — or had no recorded change
    before the window at all. The second half is the same condition: a file this
    record has never seen touched *was* not modified in the preceding ninety days,
    and calling a first-time file "current" would classify every new file in a
    repository as maintenance and turn the metric into a measure of newness.

    ``last_touched_before`` maps ``(repository_id, file path)`` to the instant of
    the last recorded change to that file *before* the window opened. It is passed
    in rather than derived for the reason stated in the module docstring: only the
    service knows what the record held before the window started.

    **Comparing against this account's record, not against the file's real
    history.** That is what keeps the metric honest: it reads the commits this
    account recorded, so a file somebody else last touched six months ago reads as
    quiet here — which is a true statement about *this record*, and is what the
    window means everywhere else in this module.

    A commit with no recorded file paths cannot be classified and does not count.
    That is a limitation of the record rather than a decision, and it is stated
    here because it means the metric reads low on a repository scanned before
    per-commit paths were stored.

    Always available.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.
        last_touched_before: ``{(repository_id, path): last recorded change}`` for
            changes *before* the window. ``None`` means no history was supplied,
            which makes every path with files count — the caller is told so in the
            explanation rather than silently handed a number about a different
            question.
        lookback_days: How long a file may be quiet and still count as current.

    Returns:
        The metric.
    """
    windowed = _selected(samples, window_start, window_end)
    span = _span_days(window_start, window_end)
    span_days = max(0, lookback_days)
    cutoff = _as_utc(window_start) - timedelta(days=span_days)
    history = last_touched_before or {}

    count = sum(1 for sample in windowed if _reaches_quiet_file(sample, history, cutoff))
    explanation = _explain(
        MetricKey.MAINTENANCE_ACTIVITY,
        "{count} of the {total} commit(s) in the {span}-day window reached a file with "
        "no recorded change in the {lookback} days before it.",
        count=count,
        total=len(windowed),
        span=span,
        lookback=span_days,
    )
    if last_touched_before is None:
        explanation = _explain(
            MetricKey.MAINTENANCE_ACTIVITY,
            "{count} of the {total} commit(s) in the {span}-day window reached a file, "
            "measured without the preceding {lookback} days of file history, so every "
            "touched file counts as quiet.",
            count=count,
            total=len(windowed),
            span=span,
            lookback=span_days,
        )
    return _metric(
        MetricKey.MAINTENANCE_ACTIVITY,
        label="Maintenance activity",
        value=float(count),
        definition=(
            "Commits in the window reaching a file with no recorded change in the "
            f"{span_days} days before it."
        ),
        explanation=explanation,
        window_days=span,
        source="git_commits.files_changed, git_commits.committed_at",
    )


def _reaches_quiet_file(
    sample: MetricSample, history: Mapping[tuple[str, str], datetime], cutoff: datetime
) -> bool:
    """Whether a commit reached a file that was quiet before ``cutoff``.

    Absent from :attr:`MetricSample.file_paths` means an unclassifiable commit —
    a scan that stored counts but not paths — and it does not count. Counting it
    would report a figure about commits whose contents were never recorded, which
    is the specific invention this module exists to avoid.

    Args:
        sample: The commit.
        history: ``{(repository_id, path): last recorded change before the window}``.
        cutoff: The instant before which a file counts as quiet.

    Returns:
        ``True`` when at least one of the commit's files was quiet.
    """
    for path in sample.file_paths:
        previous = history.get((sample.repository_id, path))
        if previous is None or _as_utc(previous) < cutoff:
            return True
    return False


def recent_momentum(
    samples: Sequence[MetricSample],
    *,
    window_end: datetime,
    recent_days: int = MOMENTUM_COMPARISON_DAYS,
) -> DeveloperMetric:
    """Commits in the last ``recent_days`` against the ``recent_days`` before.

    A ratio, and the only metric here that can decline. When the earlier window
    holds no commits the denominator is zero and there is no ratio: ``18 / 0``
    would be infinity and ``0 / 0`` would be a number, and both would be
    inventions. The answer is :data:`NOT_ENOUGH_DATA`.

    That decline is worth its cost. It is the one place this module could produce
    a confidently wrong growth claim — a new account has no earlier window at all,
    and "momentum is up 400%" for someone who made their first commit is exactly
    the fabricated progress story the brief rules out.

    Both halves are ``recent_days`` long so the ratio compares activity and not
    window length, and the window is anchored on ``window_end`` rather than on
    "now": this module reads no clock, so a caller asking about "the last 7 days"
    must say what the last 7 days *are*, and the service takes that instant from
    the database's ``now()``.

    The window is read independently of any ``window_start``, so a caller asking
    for a 30-day summary still gets an answer about the most recent fortnight.
    That is intentional — momentum is a trailing signal, not a property of
    whichever window the summary happens to use.

    Args:
        samples: Every recorded commit the caller has.
        window_end: The instant the comparison ends, exclusive.
        recent_days: Length of both halves of the comparison.

    Returns:
        The metric, or an unavailable one when the earlier window is empty.
    """
    end = _as_utc(window_end)
    span = max(1, recent_days)
    split = end - timedelta(days=span)
    start = end - timedelta(days=2 * span)

    recent = sum(1 for sample in samples if split <= _as_utc(sample.committed_at) < end)
    earlier = sum(1 for sample in samples if start <= _as_utc(sample.committed_at) < split)

    label = "Recent momentum"
    definition = (
        f"Commits in the most recent {span} days divided by commits in the {span} days before that."
    )
    if earlier == 0:
        return _declined(
            MetricKey.RECENT_MOMENTUM,
            label=label,
            definition=definition,
            window_days=span * 2,
            source="git_commits.committed_at",
        )

    ratio = recent / earlier
    return _metric(
        MetricKey.RECENT_MOMENTUM,
        label=label,
        value=round(ratio, 4),
        definition=definition,
        explanation=_explain(
            MetricKey.RECENT_MOMENTUM,
            "{recent} commit(s) were recorded in the last {span} days against {earlier} "
            "in the {span} days before, a ratio of {ratio}.",
            recent=recent,
            earlier=earlier,
            span=span,
            ratio=round(ratio, 2),
        ),
        window_days=span * 2,
        source="git_commits.committed_at",
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_metrics(
    samples: Sequence[MetricSample],
    *,
    window_start: datetime,
    window_end: datetime,
    last_touched_before: Mapping[tuple[str, str], datetime] | None = None,
    lookback_days: int = DEFAULT_MAINTENANCE_LOOKBACK_DAYS,
) -> tuple[DeveloperMetric, ...]:
    """Compute all eight metrics over one window, in contract order.

    One function so a caller cannot assemble a response from a mixture of two
    different windows, which would produce cards whose figures disagree with each
    other and nothing on the page to say why. Every metric receives the same
    ``samples`` and the same boundaries.

    Args:
        samples: Every recorded commit the caller has, from any repository.
        window_start: Inclusive start of the window.
        window_end: Exclusive end of the window.
        last_touched_before: Passed through to :func:`maintenance_activity`.
        lookback_days: Passed through to :func:`maintenance_activity`.

    Returns:
        Eight metrics, in :data:`DEVELOPER_METRICS` order. Exactly eight always —
        a metric that cannot be computed comes back unavailable rather than
        omitted, because a client that indexes by key would otherwise render a
        hole where a card belongs.
    """
    return (
        commit_activity(samples, window_start=window_start, window_end=window_end),
        repository_activity(samples, window_start=window_start, window_end=window_end),
        change_volume(samples, window_start=window_start, window_end=window_end),
        active_days(samples, window_start=window_start, window_end=window_end),
        consistency(samples, window_start=window_start, window_end=window_end),
        repository_growth(samples, window_start=window_start, window_end=window_end),
        maintenance_activity(
            samples,
            window_start=window_start,
            window_end=window_end,
            last_touched_before=last_touched_before,
            lookback_days=lookback_days,
        ),
        recent_momentum(samples, window_end=window_end),
    )


# ---------------------------------------------------------------------------
# Activity series
# ---------------------------------------------------------------------------


def _floor(value: datetime, granularity: ActivityGranularity) -> datetime:
    """The UTC start of the bucket ``value`` falls in.

    Weeks start on Monday, because that is the ISO convention and because a week
    starting on Sunday makes "last week" mean two different things depending on
    who is reading it. Months floor to the first of the month. The result is
    always midnight UTC, so two buckets are exactly adjacent and a commit at
    00:00:00 belongs to exactly one of them.
    """
    moment = _as_utc(value)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity is ActivityGranularity.DAY:
        return midnight
    if granularity is ActivityGranularity.WEEK:
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


def _advance(value: datetime, granularity: ActivityGranularity) -> datetime:
    """The start of the bucket after the one starting at ``value``.

    Months step by adding 32 days and re-flooring rather than by arithmetic on
    the month number: a month with 31 days would make ``day + 1`` overflow, and a
    naive "same day next month" would skip February entirely.
    """
    if granularity is ActivityGranularity.DAY:
        return value + timedelta(days=1)
    if granularity is ActivityGranularity.WEEK:
        return value + timedelta(days=7)
    return _floor(value + timedelta(days=32), ActivityGranularity.MONTH)


def _bucket_label(value: datetime, granularity: ActivityGranularity) -> str:
    """A stable, sortable label for one bucket.

    ISO dates throughout (``2026-07-01`` for a day and for the Monday starting a
    week, ``2026-07`` for a month). Formatted rather than derived from a locale,
    because a label a client re-parses is a label that breaks when the server's
    locale does.
    """
    if granularity is ActivityGranularity.MONTH:
        return value.strftime("%Y-%m")
    return value.strftime("%Y-%m-%d")


def _parse_granularity(granularity: str) -> ActivityGranularity:
    """Coerce a caller's granularity string, refusing anything unknown.

    Args:
        granularity: The requested bucket size.

    Returns:
        The matching member.

    Raises:
        ValueError: If it is not one of the three known values. An unknown
            granularity is a request the caller can be told is wrong; guessing one
            would silently return a chart nobody asked for.
    """
    try:
        return ActivityGranularity(granularity)
    except ValueError as exc:
        raise ValueError(
            f"{granularity!r} is not a known activity granularity. Use one of "
            f"{', '.join(ACTIVITY_GRANULARITIES)}."
        ) from exc


def activity_series(
    samples: Sequence[MetricSample],
    *,
    window_start: datetime,
    window_end: datetime,
    granularity: str = ActivityGranularity.DAY.value,
    repository_id: str | None = None,
) -> ActivitySeries:
    """Bucket commits across a range, zero-filling every gap.

    **The zero-fill is the point of this function.** A chart built only from the
    buckets that have commits skips the quiet ones, and a reader counting the bars
    sees five consecutive active days when the truth is five active days with gaps
    between them. So every bucket from the first to the last is emitted, including
    the empty ones, and the caller cannot accidentally omit them.

    Buckets are aligned to the calendar, not to the window's start. A window
    opening mid-week gets that week's Monday as its first bucket rather than a
    partial day that would sit beside a full one on the same chart and read as a
    real dip.

    Args:
        samples: Every recorded commit the caller has.
        window_start: Inclusive start of the range.
        window_end: Exclusive end of the range.
        granularity: ``day``, ``week`` or ``month``.
        repository_id: Restrict to one repository, or ``None`` for all of them.

    Returns:
        The dense series.

    Raises:
        ValueError: If ``granularity`` is not one of the three known values, or
            the window does not end after it starts. Both are facts about the
            request rather than about the data, and both are better caught here
            than rendered as an empty chart.
    """
    step = _parse_granularity(granularity)
    start = _as_utc(window_start)
    end = _as_utc(window_end)
    if end <= start:
        raise ValueError(
            "The activity window must end after it starts; got "
            f"{start.isoformat()} to {end.isoformat()}."
        )

    grouped: dict[datetime, list[MetricSample]] = defaultdict(list)
    for sample in samples:
        if repository_id is not None and sample.repository_id != repository_id:
            continue
        if _in_window(sample, start, end):
            grouped[_floor(sample.committed_at, step)].append(sample)

    buckets: list[ActivityBucket] = []
    cursor = _floor(start, step)
    while cursor < end:
        inside = grouped.get(cursor, [])
        buckets.append(
            ActivityBucket(
                start=cursor,
                label=_bucket_label(cursor, step),
                commit_count=len(inside),
                line_change_count=sum(
                    max(0, sample.additions) + max(0, sample.deletions) for sample in inside
                ),
                repository_ids=tuple(sorted({s.repository_id for s in inside})),
            )
        )
        cursor = _advance(cursor, step)

    return ActivitySeries(
        granularity=step.value,
        start=_floor(start, step),
        end=end,
        buckets=tuple(buckets),
    )


def bucket_for(value: datetime, granularity: str = ActivityGranularity.DAY.value) -> str:
    """The label of the bucket ``value`` falls in.

    Exposed on its own so the service can bucket a *query* the same way the series
    buckets its rows. A caller grouping its SQL by a different definition of "a
    day" would produce a heatmap that disagrees with the line chart above it, and
    nothing on the page would say which one is right.

    Args:
        value: The instant to bucket.
        granularity: ``day``, ``week`` or ``month``.

    Returns:
        The bucket's label, in the format :func:`activity_series` emits.

    Raises:
        ValueError: If ``granularity`` is not a known value.
    """
    step = _parse_granularity(granularity)
    return _bucket_label(_floor(value, step), step)
