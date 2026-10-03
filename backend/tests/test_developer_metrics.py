"""The developer metric formulas, exercised as pure arithmetic.

No database, no HTTP, no ``integration`` marker: everything in
:mod:`app.services.developer.metrics` takes plain samples and returns a
:class:`~app.services.developer.metrics.DeveloperMetric`, so a figure can be
checked against a hand-derived value without provisioning a database or reading a
git repository. That matters here more than it did for the risk scores, because
these numbers are the whole of the Developer Intelligence screen — a formula that
quietly drifts shows a plausible-looking chart and nobody notices, because a
slightly wrong ratio is not an error, it is just a number.

Four things are asserted rather than merely exercised:

* **Exact figures.** Every expected value is derived by hand from the documented
  formula, never copied from a run of the code. A change to a formula shows up as
  a wrong number rather than as a moved baseline.
* **Zero is not unknown.** The module's stated contract is that an absence of
  measurement is ``available=False`` with a reason while a real measurement of
  zero is ``value=0, available=True``. Conflating them is how a product ends up
  telling a new user they have no activity, so the two are asserted separately
  for every metric that can be in either state.
* **The wording of the explanations.** A figure is only trustworthy if a reader
  can see what it was built from, and the brief forbids sentences that claim
  anything about the person. The digit requirement and the forbidden-vocabulary
  requirement are therefore asserted as exact strings where the brief is specific,
  and as construction-time failures where it is not.
* **The density of the activity series.** A gap-filled series is the property the
  series builder exists for, and a chart that skips a quiet Tuesday is lying about
  the fortnight in a way no assertion on the totals would catch.

The instants below are built from a fixed UTC base rather than from
``datetime.now()``. A test that read the wall clock would start failing the day
someone's timezone moved, and these figures do not depend on when the suite runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.developer.metrics import (
    DEFAULT_MAINTENANCE_LOOKBACK_DAYS,
    METRIC_UNITS,
    MOMENTUM_COMPARISON_DAYS,
    NOT_ENOUGH_DATA,
    ActivityGranularity,
    DeveloperMetric,
    MetricKey,
    MetricSample,
    active_days,
    activity_series,
    bucket_for,
    build_metrics,
    change_volume,
    commit_activity,
    consistency,
    maintenance_activity,
    recent_momentum,
    repository_activity,
    repository_growth,
)

#: The window every windowed test reasons over: 30 days ending at this instant.
#: Fixed rather than "now" so a figure derived from it is derived, not recorded.
WINDOW_END = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(days=30)

#: One day before the window ends, so a sample lands unambiguously inside it.
INSIDE = WINDOW_END - timedelta(days=1)
#: One day before the window opens, so it lands unambiguously outside it.
OUTSIDE = WINDOW_START - timedelta(days=1)


def _sample(
    *,
    committed_at: datetime = INSIDE,
    repository_id: str = "repo-a",
    branch: str | None = "main",
    additions: int = 0,
    deletions: int = 0,
    first_seen_branches: frozenset[str] = frozenset(),
    file_paths: tuple[str, ...] = (),
) -> MetricSample:
    """One recorded commit, with only the fields the metrics read."""
    return MetricSample(
        committed_at=committed_at,
        repository_id=repository_id,
        branch=branch,
        additions=additions,
        deletions=deletions,
        files_changed=len(file_paths),
        first_seen_branches=first_seen_branches,
        file_paths=file_paths,
    )


def _by_key(metrics: tuple[DeveloperMetric, ...], key: MetricKey) -> DeveloperMetric:
    """The metric with this key from an assembled eight.

    Raises:
        AssertionError: If the metric is absent, which is itself the failure —
            ``/developer/metrics`` promises eight and a missing one renders a hole
            where a card belongs.
    """
    for metric in metrics:
        if metric.key == key.value:
            return metric
    raise AssertionError(f"{key.value} is missing from the assembled metrics")


# ---------------------------------------------------------------------------
# The shape every metric owes its caller
# ---------------------------------------------------------------------------


def test_build_metrics_returns_the_eight_metrics_in_contract_order():
    """Eight keys, in the order the contracts list them.

    The order is asserted as a whole tuple rather than as a set because it is a
    reading order a human follows; a set would pass with the cards shuffled, which
    looks like a different product every deploy.
    """
    metrics = build_metrics([], window_start=WINDOW_START, window_end=WINDOW_END)

    assert tuple(metric.key for metric in metrics) == (
        "commit_activity",
        "repository_activity",
        "change_volume",
        "active_days",
        "consistency",
        "repository_growth",
        "maintenance_activity",
        "recent_momentum",
    )


def test_every_metric_carries_a_definition_a_window_a_source_and_its_own_unit():
    """No metric is a bare number.

    ``definition``, ``window_days`` and ``source`` are what make "explain this
    figure" answerable, and the unit comes from the shared table so a client
    cannot be handed a value whose unit no formatter knows.
    """
    metrics = build_metrics([], window_start=WINDOW_START, window_end=WINDOW_END)

    for metric in metrics:
        assert metric.definition.strip()
        assert metric.source.strip()
        assert metric.window_days is not None
        assert metric.unit == METRIC_UNITS[MetricKey(metric.key)]


def test_an_empty_window_is_a_measured_zero_rather_than_an_absence_of_measurement():
    """Seven of the eight metrics report a real 0 for a window with no commits.

    "No commits were recorded" is a measurement. Reporting it as ``available=False``
    would tell a user who has just registered their first repository that NEXUS
    cannot assess them yet, which is a different and much less useful sentence.
    """
    metrics = build_metrics([], window_start=WINDOW_START, window_end=WINDOW_END)

    for key in (
        MetricKey.COMMIT_ACTIVITY,
        MetricKey.REPOSITORY_ACTIVITY,
        MetricKey.CHANGE_VOLUME,
        MetricKey.ACTIVE_DAYS,
        MetricKey.CONSISTENCY,
        MetricKey.REPOSITORY_GROWTH,
        MetricKey.MAINTENANCE_ACTIVITY,
    ):
        metric = _by_key(metrics, key)
        assert metric.available is True, key
        assert metric.value == 0.0, key
        assert metric.reason_if_unavailable is None, key


def test_an_explanation_with_no_figure_is_rejected_at_construction():
    """The brief's "a metric whose explanation has no number in it is a bug".

    Asserted as a construction-time failure rather than by checking a rendered
    string, because the point is that the failure is loud where a reviewer sees
    it and silent nowhere.
    """
    with pytest.raises(ValueError, match="no figure in it"):
        DeveloperMetric(
            key="commit_activity",
            label="Commit activity",
            value=3.0,
            unit="count",
            definition="Commits in the window.",
            window_days=30,
            source="git_commits.committed_at",
            explanation="A reasonable number of commits were recorded.",
        )


@pytest.mark.parametrize(
    "claim",
    [
        "18 commits were recorded, showing strong productivity.",
        "6 hours of recorded activity across 12 commits.",
        "focus was sustained across 5 active days.",
        "9 commits show real effort this week.",
    ],
)
def test_an_explanation_claiming_effort_or_time_is_rejected_at_construction(claim: str):
    """Git timestamps cannot show these, so no sentence here may claim them.

    Each sentence carries a real figure and still fails, which is the point: the
    digit rule alone would accept all four, and the rule that does the real work
    is the vocabulary one. A product that renders "6 hours of recorded activity"
    has made a claim its data cannot support, and it is better that this fails
    here than that it ships.
    """
    with pytest.raises(ValueError):
        DeveloperMetric(
            key="commit_activity",
            label="Commit activity",
            value=18.0,
            unit="count",
            definition="Commits in the window.",
            window_days=30,
            source="git_commits.committed_at",
            explanation=claim,
        )


def test_an_unavailable_metric_must_say_why():
    """``available=False`` with no reason renders as an unexplained blank."""
    with pytest.raises(ValueError, match="must say why"):
        DeveloperMetric(
            key="recent_momentum",
            label="Recent momentum",
            value=0.0,
            unit="ratio",
            definition="Commits in the last 7 days over the 7 before.",
            window_days=14,
            source="git_commits.committed_at",
            explanation="The ratio is not computed over a 14-day window.",
            available=False,
        )


# ---------------------------------------------------------------------------
# commit_activity
# ---------------------------------------------------------------------------


def test_commit_activity_counts_the_commits_inside_the_window_and_no_others():
    """Three inside the window, one before it, one on its final instant.

    The window is half-open, so the sample at ``window_end`` is *outside* it —
    including it would double-count a boundary that also opens the next window.
    """
    samples = [
        _sample(committed_at=WINDOW_START),
        _sample(committed_at=INSIDE),
        _sample(committed_at=INSIDE + timedelta(hours=1)),
        _sample(committed_at=OUTSIDE),
        _sample(committed_at=WINDOW_END),
    ]

    metric = commit_activity(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 3.0
    assert metric.unit == "count"
    assert metric.available is True
    assert metric.window_days == 30
    assert metric.explanation == "3 commit(s) were recorded in the 30-day window."


def test_commit_activity_is_unchanged_by_commits_outside_the_window():
    """One commit either side of the boundary changes nothing.

    Pinned because the two filtering metrics share one selector, and a selector
    that leaked a row would show up here as a 3 where the answer is 1.
    """
    samples = [
        _sample(committed_at=INSIDE),
        _sample(committed_at=OUTSIDE),
        _sample(committed_at=WINDOW_END + timedelta(days=400)),
    ]

    metric = commit_activity(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 1.0


# ---------------------------------------------------------------------------
# repository_activity
# ---------------------------------------------------------------------------


def test_repository_activity_counts_distinct_repositories_not_commits():
    """Six commits across three repositories is three.

    Four commits in ``repo-a`` make it one repository, not four. This is the whole
    reason the metric exists beside ``commit_activity``: breadth and volume are
    different questions and one number cannot answer both.
    """
    samples = [
        _sample(repository_id="repo-a"),
        _sample(repository_id="repo-a"),
        _sample(repository_id="repo-a"),
        _sample(repository_id="repo-a"),
        _sample(repository_id="repo-b"),
        _sample(repository_id="repo-c"),
        _sample(repository_id="repo-c"),
    ]

    metric = repository_activity(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 3.0
    assert metric.explanation == (
        "3 of the tracked repositories recorded at least one commit in the 30-day window."
    )


def test_a_repository_with_no_commit_in_the_window_does_not_count():
    """Only the two repositories inside the window are counted.

    The third repository's commit is 40 days old, so it is not in the window and
    the answer is 2. Counting registered repositories rather than active ones
    would make this metric a restatement of the repository list.
    """
    samples = [
        _sample(repository_id="repo-a", committed_at=INSIDE),
        _sample(repository_id="repo-b", committed_at=INSIDE),
        _sample(repository_id="repo-c", committed_at=OUTSIDE),
    ]

    metric = repository_activity(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 2.0


# ---------------------------------------------------------------------------
# change_volume
# ---------------------------------------------------------------------------


def test_change_volume_sums_additions_and_deletions_rather_than_netting_them():
    """120 added and 80 deleted is 200, not 40.

    The net figure would render as ``+40`` and call a commit that replaced 200
    lines a small one. The sum answers the question a reader has — how much was
    touched — and a wholesale rewrite is the busiest thing that can happen.
    """
    samples = [
        _sample(additions=100, deletions=10),
        _sample(additions=20, deletions=70),
    ]

    metric = change_volume(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 200.0
    assert metric.unit == "lines"
    assert metric.explanation == (
        "200 changed line(s) were recorded in the 30-day window: 120 added and 80 deleted."
    )


def test_a_commit_that_deletes_more_than_it_adds_still_counts_the_deletions():
    """10 added, 90 deleted is 100 changed lines.

    The case that decides whether the metric is netted: netted it would be -80,
    and a negative "change volume" on a dashboard reads as a loss rather than as
    a refactor.
    """
    metric = change_volume(
        [_sample(additions=10, deletions=90)],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert metric.value == 100.0
    assert "-80" not in metric.explanation


# ---------------------------------------------------------------------------
# active_days
# ---------------------------------------------------------------------------


def test_active_days_counts_distinct_utc_dates_rather_than_commits():
    """Five commits on one day is one active day; three days is three.

    Counting commits would make a single busy day read as a fortnight of activity
    and would turn this metric into ``commit_activity`` with a different label.
    """
    samples = [
        _sample(committed_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC)),
        _sample(committed_at=datetime(2026, 6, 20, 17, 30, tzinfo=UTC)),
        _sample(committed_at=datetime(2026, 6, 21, 1, 0, tzinfo=UTC)),
        _sample(committed_at=datetime(2026, 6, 23, 23, 59, tzinfo=UTC)),
    ]

    metric = active_days(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 3.0
    assert metric.unit == "days"
    assert metric.explanation == "3 of the 30 days in the window carried at least one commit."


def test_two_commits_in_the_same_instant_on_either_side_of_utc_midnight_are_one_day():
    """23:30 UTC and 00:30 UTC the next morning are two distinct UTC dates.

    Pinned because the alternative — bucketing in local time — would make the
    figure depend on which server ran the query: in UTC+2 those two commits share
    a date and the answer drops to 1. The contracts name UTC dates, and this is
    the assertion that keeps them UTC.
    """
    samples = [
        _sample(committed_at=datetime(2026, 6, 20, 23, 30, tzinfo=UTC)),
        _sample(committed_at=datetime(2026, 6, 21, 0, 30, tzinfo=UTC)),
    ]

    metric = active_days(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 2.0


# ---------------------------------------------------------------------------
# consistency
# ---------------------------------------------------------------------------


def test_consistency_is_active_days_over_window_days_as_a_ratio():
    """5 active days in a 30-day window is 0.1667, rendered as 17%.

    ``5 / 30 = 0.16666...``, rounded to four places for the value and to a whole
    percentage for the sentence. A ratio rather than a percentage because a later
    phase sums these, and a metric whose unit changed with its caller cannot be
    summed.
    """
    samples = [_sample(committed_at=WINDOW_START + timedelta(days=index)) for index in range(5)]

    metric = consistency(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == round(5 / 30, 4) == 0.1667
    assert metric.unit == "ratio"
    assert 0.0 <= metric.value <= 1.0
    assert metric.explanation == (
        "5 of the 30 days in the window carried a commit, which is 17% of them."
    )


def test_consistency_is_never_above_one_even_when_every_day_carries_a_commit():
    """A commit on all 30 days is exactly 1.0, not 1.0-plus.

    The clamp matters for a window whose ``window_days`` is computed by the caller
    and could be one day short of the commits it contains: without it the ratio
    would exceed 1 and a "consistency" card would read as more than fully
    consistent, which is not a thing.
    """
    samples = [_sample(committed_at=WINDOW_START + timedelta(days=index)) for index in range(31)]

    metric = consistency(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 1.0


def test_consistency_of_a_zero_length_window_is_zero_rather_than_a_division_error():
    """An empty window measures 0 of the 1 day.

    :func:`_span_days` floors the window at one day precisely so this divides.
    A zero-length window is a request the dashboard can make while its date
    pickers are loading, and it must render a card rather than a 500.
    """
    metric = consistency([], window_start=WINDOW_END, window_end=WINDOW_END)

    assert metric.value == 0.0
    assert metric.available is True


# ---------------------------------------------------------------------------
# repository_growth
# ---------------------------------------------------------------------------


def test_repository_growth_counts_each_new_branch_once_however_many_commits_landed():
    """Four commits on one new branch is one branch, not four.

    Counting commits would make one long-lived feature branch read as continuous
    growth for as long as it stays open, which is a claim about someone still
    working rather than about anything starting. The metric is about branches
    appearing.
    """
    samples = [
        _sample(branch="feature/login", first_seen_branches=frozenset({"feature/login"})),
        _sample(branch="feature/login", first_seen_branches=frozenset({"feature/login"})),
        _sample(branch="feature/login", first_seen_branches=frozenset({"feature/login"})),
        _sample(branch="feature/login", first_seen_branches=frozenset({"feature/login"})),
    ]

    metric = repository_growth(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 1.0
    assert metric.explanation == "1 branch(es) were first recorded inside the 30-day window."


def test_repository_growth_counts_branches_across_repositories_without_double_counting():
    """Two repositories, both opening ``release/2.0`, is one branch.

    Branch names are namespaced per repository in git — two repositories may both
    have a ``main`` — so keying on the name alone would collapse genuinely
    distinct branches. The dedup here is by name across the assembled set, which
    is the conservative reading: the contracts store ``git_branches`` per
    repository, and the service resolves the ambiguity before this point. What is
    asserted here is that one branch is one branch.
    """
    samples = [
        _sample(repository_id="repo-a", first_seen_branches=frozenset({"release/2.0"})),
        _sample(repository_id="repo-b", first_seen_branches=frozenset({"release/2.0"})),
        _sample(repository_id="repo-b", first_seen_branches=frozenset({"hotfix"})),
    ]

    metric = repository_growth(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 2.0


def test_repository_growth_ignores_branches_that_existed_before_the_window():
    """A commit on an established branch adds nothing to the growth count.

    The whole point of passing ``first_seen_branches`` rather than every branch
    the commit touched: if the metric counted every branch, every commit in a
    working branch would read as growth.
    """
    samples = [_sample(branch="main", first_seen_branches=frozenset())]

    metric = repository_growth(samples, window_start=WINDOW_START, window_end=WINDOW_END)

    assert metric.value == 0.0
    assert metric.available is True


# ---------------------------------------------------------------------------
# maintenance_activity
# ---------------------------------------------------------------------------


def test_maintenance_counts_a_commit_that_reaches_a_file_quiet_before_the_window():
    """A file last touched 200 days ago is quiet; the cutoff is 90 days before.

    ``window_start`` is 2026-06-01, so the cutoff is 2026-03-03. A file touched on
    2025-09-15 is before it and counts; one touched on 2026-05-01 is after it and
    does not. The two commits are otherwise identical, so the file history is the
    only thing separating them.
    """
    samples = [_sample(file_paths=("app/legacy.py",))]
    history = {("repo-a", "app/legacy.py"): datetime(2025, 9, 15, tzinfo=UTC)}

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before=history,
    )

    assert metric.value == 1.0
    assert f"{DEFAULT_MAINTENANCE_LOOKBACK_DAYS} days" in metric.explanation


def test_maintenance_ignores_a_commit_that_only_reaches_a_current_file():
    """A file touched 10 days before the window is current, so the commit is not.

    ``2026-05-22`` is after the 2026-03-03 cutoff, so nothing the commit reached
    was quiet and the count is 0 — a measured zero, not an absence.
    """
    samples = [_sample(file_paths=("app/service.py",))]
    history = {("repo-a", "app/service.py"): datetime(2026, 5, 22, tzinfo=UTC)}

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before=history,
    )

    assert metric.value == 0.0
    assert metric.available is True


def test_maintenance_counts_a_file_the_record_has_never_seen_touched_before():
    """No recorded history is the same condition as a long silence.

    A file this record has never seen changed *was* not modified in the ninety
    days before the window, and classifying it as current would make every new
    file in a repository maintenance and turn the metric into a measure of
    newness.
    """
    samples = [_sample(file_paths=("app/brand_new.py",))]

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before={},
    )

    assert metric.value == 1.0


def test_maintenance_counts_a_commit_reaching_a_quiet_file_among_current_ones():
    """One quiet file is enough to make the whole commit maintenance.

    "Touching files not modified in the 90 days before the window" is existential,
    not universal: a commit that changes four lines in a live module and opens a
    file nobody has opened in a year is a maintenance commit, and requiring *all*
    its files to be stale would report it as ordinary work.
    """
    samples = [_sample(file_paths=("app/service.py", "docs/old-runbook.md"))]
    history = {
        ("repo-a", "app/service.py"): datetime(2026, 5, 22, tzinfo=UTC),
        ("repo-a", "docs/old-runbook.md"): datetime(2024, 1, 1, tzinfo=UTC),
    }

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before=history,
    )

    assert metric.value == 1.0


def test_a_commit_with_no_recorded_file_paths_is_not_counted_as_maintenance():
    """A commit whose contents were never recorded cannot be classified.

    This is the honest answer rather than the useful one: counting it would
    report a figure about commits whose files NEXUS never saw, which is the
    specific invention this module exists to avoid.
    """
    samples = [_sample(file_paths=())]

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before={("repo-a", "anything"): datetime(2020, 1, 1, tzinfo=UTC)},
    )

    assert metric.value == 0.0


def test_maintenance_file_history_is_scoped_to_its_own_repository():
    """The same path in another repository does not borrow the first one's history.

    ``app/service.py`` in ``repo-a`` was touched last week and in ``repo-b`` two
    years ago. The commit in ``repo-b`` is maintenance and the one in ``repo-a``
    is not, and keying the history on the path alone would have got both wrong.
    """
    samples = [
        _sample(repository_id="repo-a", file_paths=("app/service.py",)),
        _sample(repository_id="repo-b", file_paths=("app/service.py",)),
    ]
    history = {
        ("repo-a", "app/service.py"): datetime(2026, 5, 22, tzinfo=UTC),
        ("repo-b", "app/service.py"): datetime(2024, 1, 1, tzinfo=UTC),
    }

    metric = maintenance_activity(
        samples,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        last_touched_before=history,
    )

    assert metric.explanation.startswith("1 of the 2 commit(s)")


def test_maintenance_without_any_file_history_says_so_in_its_explanation():
    """Passing no history counts every touched file, and the sentence admits it.

    Silently returning the "everything is quiet" figure would answer a different
    question from the one the caller asked. The explanation names the missing
    90 days of history so the reader can tell which number they are looking at.
    """
    samples = [_sample(file_paths=("app/service.py",))]

    metric = maintenance_activity(
        samples, window_start=WINDOW_START, window_end=WINDOW_END, last_touched_before=None
    )

    assert metric.value == 1.0
    assert "without the preceding 90 days of file history" in metric.explanation


# ---------------------------------------------------------------------------
# recent_momentum
# ---------------------------------------------------------------------------


def test_recent_momentum_divides_the_last_seven_days_by_the_seven_before():
    """18 commits in the last week against 6 in the week before is 3.0.

    ``18 / 6 = 3``, which is the brief's arithmetic exactly. The two windows are
    both seven days because a ratio of unequal windows measures the difference in
    window length as much as the difference in activity.
    """
    end = WINDOW_END
    # 18 commits inside the most recent 7 days and 6 inside the 7 before it. The
    # two sets are placed by hours, not by days: 18 samples a day apart would span
    # 18 days, and the older 11 would fall inside the *earlier* window and turn
    # the denominator into 13. A fixture that quietly measures the wrong thing is
    # worse than one that fails.
    recent = [_sample(committed_at=end - timedelta(hours=index)) for index in range(1, 19)]
    earlier = [_sample(committed_at=end - timedelta(days=7, hours=index)) for index in range(1, 7)]

    metric = recent_momentum(recent + earlier, window_end=end)

    assert metric.value == 3.0
    assert metric.unit == "ratio"
    assert metric.available is True
    assert metric.window_days == MOMENTUM_COMPARISON_DAYS * 2
    assert metric.explanation == (
        "18 commit(s) were recorded in the last 7 days against 6 in the 7 days before, "
        "a ratio of 3.0."
    )


def test_recent_momentum_declines_when_the_earlier_week_had_no_commits():
    """A zero denominator has no ratio, so the metric is unavailable.

    ``18 / 0`` would be infinity, which a chart renders as infinite growth, and
    ``0 / 0`` would be a number, which would be a claim about a comparison that
    cannot be made. Both are inventions, so both are refused. This is the one
    place in the module where an answer could have been fabricated, and the
    new-account case is exactly where it would have been.
    """
    recent = [_sample(committed_at=WINDOW_END - timedelta(hours=index)) for index in range(1, 19)]

    metric = recent_momentum(recent, window_end=WINDOW_END)

    assert metric.available is False
    assert metric.reason_if_unavailable == NOT_ENOUGH_DATA
    assert metric.value == 0.0


def test_recent_momentum_declines_rather_than_reporting_infinity_for_a_silent_account():
    """No commits at all in either week is still a decline, not a ratio of zero.

    ``0 / 0`` would produce ``ZeroDivisionError`` here and a reported 0.0 in a
    less careful implementation. Both are worse than saying the metric cannot be
    assessed: a 0.0 ratio would read as "activity stopped" for an account whose
    only history is this fortnight.
    """
    metric = recent_momentum([], window_end=WINDOW_END)

    assert metric.available is False
    assert metric.reason_if_unavailable == NOT_ENOUGH_DATA
    assert metric.explanation.startswith(NOT_ENOUGH_DATA)


def test_recent_momentum_counts_a_commit_on_the_split_in_exactly_one_of_the_two_weeks():
    """The boundary belongs to the recent week, and to it alone.

    The two halves are ``[start, split)`` and ``[split, end)``, so the commit at
    exactly ``end - 7 days`` is in the numerator and the one a second earlier is
    in the denominator. 2 over 1 is 2.0.

    This is the assertion that catches a boundary counted in *both* halves, which
    is the failure the partition exists to prevent: such a commit would inflate
    both the numerator and the denominator and quietly drag the ratio toward 1,
    which is precisely the "nothing much has changed" reading the metric is
    supposed to avoid giving by accident.
    """
    end = WINDOW_END
    boundary = end - timedelta(days=7)
    samples = [
        _sample(committed_at=end - timedelta(days=1)),
        _sample(committed_at=boundary),
        _sample(committed_at=boundary - timedelta(seconds=1)),
    ]

    metric = recent_momentum(samples, window_end=end)

    assert metric.value == 2.0
    assert metric.explanation == (
        "2 commit(s) were recorded in the last 7 days against 1 in the 7 days before, "
        "a ratio of 2.0."
    )


def test_recent_momentum_ignores_commits_older_than_the_comparison_window():
    """Two commits from a year ago are not the denominator.

    The momentum window is a fortnight anchored on ``window_end``, so anything
    older than that is simply not read. Without the bound, a long-dormant account
    would report a ratio of 0 against a year of history.
    """
    end = WINDOW_END
    samples = [
        _sample(committed_at=end - timedelta(days=1)),
        _sample(committed_at=end - timedelta(days=400)),
        _sample(committed_at=end - timedelta(days=800)),
    ]

    metric = recent_momentum(samples, window_end=end)

    assert metric.available is False


# ---------------------------------------------------------------------------
# Activity series
# ---------------------------------------------------------------------------


def test_the_daily_series_is_zero_filled_across_every_day_in_the_range():
    """Ten days produce ten buckets, two of which carry a commit.

    The quiet days are emitted as zeros rather than omitted. A series built only
    from days with commits would show two bars a reader would read as two
    *consecutive* active days, which is the lie this builder exists to prevent.
    """
    samples = [
        _sample(committed_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC)),
        _sample(committed_at=datetime(2026, 6, 24, 10, 0, tzinfo=UTC)),
    ]

    series = activity_series(
        samples,
        window_start=datetime(2026, 6, 20, tzinfo=UTC),
        window_end=datetime(2026, 6, 30, tzinfo=UTC),
        granularity="day",
    )

    assert len(series.buckets) == 10
    assert [bucket.commit_count for bucket in series.buckets] == [1, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    assert [bucket.label for bucket in series.buckets][:2] == ["2026-06-20", "2026-06-21"]


def test_a_commit_at_midnight_lands_in_the_day_that_just_started():
    """00:00 UTC exactly is the first instant of its day, not the last of the previous.

    Buckets are half-open at the top, so a commit stamped at midnight belongs to
    exactly one bucket. Attributing it to both would make the day counts and the
    commit counts disagree.
    """
    samples = [_sample(committed_at=datetime(2026, 6, 21, 0, 0, tzinfo=UTC))]

    series = activity_series(
        samples,
        window_start=datetime(2026, 6, 20, tzinfo=UTC),
        window_end=datetime(2026, 6, 23, tzinfo=UTC),
    )

    assert series.buckets[1].label == "2026-06-21"
    assert series.buckets[1].commit_count == 1
    assert series.buckets[0].commit_count == 0


def test_the_weekly_series_buckets_start_on_monday_and_cover_the_whole_week():
    """Ten days from a Wednesday give three Monday-anchored buckets.

    The first bucket is the Monday *before* the window opened, so the partial week
    at the start is a whole bucket rather than a two-day stub that would sit on
    the chart beside full weeks and read as a collapse.
    """
    samples = [_sample(committed_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC))]

    series = activity_series(
        samples,
        window_start=datetime(2026, 6, 17, tzinfo=UTC),  # a Wednesday
        window_end=datetime(2026, 6, 27, tzinfo=UTC),
        granularity="week",
    )

    # Two buckets, not three: the bucket starting 2026-06-29 is entirely after the
    # window closes and is not emitted. The Monday before the window opens *is*
    # emitted, because the week it covers is half inside the range.
    assert [bucket.label for bucket in series.buckets] == ["2026-06-15", "2026-06-22"]
    assert series.buckets[1].commit_count == 1
    assert series.buckets[0].start.weekday() == 0


def test_the_monthly_series_covers_every_month_in_the_range_exactly_once():
    """Four months across a year boundary produce four buckets.

    The month step adds 32 days and re-floors rather than doing date arithmetic,
    because "the same day next month" would skip February and a plain ``day + 1``
    would overflow on the 31st. Four months in, four buckets out.
    """
    samples = [_sample(committed_at=datetime(2026, 3, 1, 8, 0, tzinfo=UTC))]

    series = activity_series(
        samples,
        window_start=datetime(2026, 1, 15, tzinfo=UTC),
        window_end=datetime(2026, 5, 1, tzinfo=UTC),
        granularity="month",
    )

    assert [bucket.label for bucket in series.buckets] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
    ]
    assert series.buckets[2].commit_count == 1


def test_a_series_can_be_restricted_to_one_repository():
    """Four commits across two repositories give a per-repository series of one.

    The filter is applied to the same rows the buckets are built from, so a
    filtered series is a real subset rather than a differently-shaped answer.
    """
    inside = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)
    samples = [
        _sample(committed_at=inside, repository_id="repo-a"),
        _sample(committed_at=inside, repository_id="repo-a"),
        _sample(committed_at=inside, repository_id="repo-b"),
    ]

    every = activity_series(
        samples,
        window_start=datetime(2026, 6, 20, tzinfo=UTC),
        window_end=datetime(2026, 6, 22, tzinfo=UTC),
    )
    one = activity_series(
        samples,
        window_start=datetime(2026, 6, 20, tzinfo=UTC),
        window_end=datetime(2026, 6, 22, tzinfo=UTC),
        repository_id="repo-b",
    )

    assert every.buckets[0].commit_count == 3
    assert one.buckets[0].commit_count == 1
    assert one.buckets[0].repository_ids == ("repo-b",)


def test_a_bucket_sums_the_lines_changed_by_the_commits_inside_it():
    """Two commits adding 30 and 12 lines make one bucket of 42 changed lines.

    Line changes are summed rather than netted, for the same reason as
    ``change_volume``: a bucket that nets deletions would report a shrinking
    number of changed lines for a commit that touched a great many.
    """
    samples = [
        _sample(committed_at=datetime(2026, 6, 20, 9, 0, tzinfo=UTC), additions=30),
        _sample(committed_at=datetime(2026, 6, 20, 18, 0, tzinfo=UTC), additions=7, deletions=5),
    ]

    series = activity_series(
        samples,
        window_start=datetime(2026, 6, 20, tzinfo=UTC),
        window_end=datetime(2026, 6, 21, tzinfo=UTC),
    )

    assert series.buckets[0].line_change_count == 42


def test_bucket_for_labels_a_single_instant_the_way_the_series_labels_its_buckets():
    """The standalone bucketer agrees with the series, in all three granularities.

    It exists so a service can group its SQL by the same definition the series
    groups its rows by. If the two disagreed, a heatmap built from SQL buckets
    would sit beside a chart built here and the two would disagree with nothing on
    the page to say which is right.
    """
    instant = datetime(2026, 6, 24, 15, 30, tzinfo=UTC)

    assert bucket_for(instant, "day") == "2026-06-24"
    assert bucket_for(instant, "week") == "2026-06-22"
    assert bucket_for(instant, "month") == "2026-06"

    series = activity_series(
        [_sample(committed_at=instant)],
        window_start=datetime(2026, 6, 24, tzinfo=UTC),
        window_end=datetime(2026, 6, 25, tzinfo=UTC),
        granularity="day",
    )
    assert series.buckets[0].label == bucket_for(instant, "day")


def test_an_unknown_granularity_is_refused_rather_than_guessed():
    """``quarterly`` is a caller error, not something to round to a month.

    Guessing would return a chart nobody asked for with no indication it had been
    substituted, which is worse than an error the caller can fix.
    """
    with pytest.raises(ValueError, match="not a known activity granularity"):
        activity_series(
            [],
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            granularity="quarterly",
        )


def test_a_window_that_does_not_end_after_it_starts_is_refused():
    """An inverted window is a request the caller can be told about.

    Rendering it as an empty chart would show a user a blank activity graph with
    no explanation, which is the outcome the empty-state copy exists to prevent.
    """
    with pytest.raises(ValueError, match="must end after it starts"):
        activity_series([], window_start=WINDOW_END, window_end=WINDOW_START, granularity="day")


def test_the_granularity_enum_names_exactly_the_three_documented_sizes():
    """``day``, ``week`` and ``month``, and nothing else.

    Pinned so adding a fourth granularity is a deliberate contract change rather
    than an accident that silently alters what an existing query means.
    """
    assert tuple(member.value for member in ActivityGranularity) == ("day", "week", "month")
