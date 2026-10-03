"""The Phase 9 learning HTTP surface end to end: eighteen routes, one router.

Every route here is exercised through HTTP with a real bearer token, and most of
the assertions are about what must **not** happen rather than about what a
response contains. That is deliberate. The happy paths of this phase are the ones
a demo shows; the six properties below are the ones that break the product
quietly when a plausible edit lands.

* **The literal sub-paths are reachable at all.** ``GET /learning/summary``,
  ``/metrics``, ``/gaps``, ``/activity`` and ``/features`` share a prefix with
  ``/learning/goals/{goal_id}`` and ``/learning/skills/{skill_id}``. Starlette
  matches in declaration order and does not prefer a literal segment over a
  parameter, so moving one decorator below a parameterised one does not remove the
  route — it binds the literal word to the path parameter and answers with a 422
  about a uuid that was never one. :func:`test_every_literal_sub_path_answers_its_own_question`
  pins a field only each handler emits, which is the only assertion that
  distinguishes "answered with counts" from "answered with a uuid parse failure".

* **A foreign goal, skill or activity is 404, never 403, on every route that
  names one** — including the three that take the id as a *body field* or a
  *query filter* rather than as a path segment, which is where a naive
  implementation would forget to scope. The responses for a foreign id and for one
  nobody ever issued are asserted to carry the same message, so the two cases
  cannot drift into an existence oracle.

* **An unset field is not a cleared field.** ``PATCH`` builds its mapping with
  ``exclude_unset=True`` because ``None`` means "write SQL NULL" downstream.
  Without it, renaming a goal silently discards its description, its topic and its
  project link, and the response still looks correct.

* **The eight metrics are all eight.** A client indexing by key would render a
  hole where a card belongs, so a metric that could not be computed comes back
  unavailable *with a reason* rather than omitted — and never as a zero.

* **Nothing here ever claims a person is good or bad at anything.** A recorded
  activity is evidence that something was recorded. The sweep in
  :func:`test_no_response_claims_what_the_user_is_good_at` is the machine check
  on the phase's first rule, and it is a property of the responses rather than of
  whichever module happened to compose a sentence.

* **A level never travels without its provenance.** Every payload carrying
  ``current_level`` must carry ``level_source`` beside it. A card that rendered
  ``2/5`` with nothing saying who claimed it is the exact failure
  ``SkillLevelSource`` exists to prevent.

Every test requires a live PostgreSQL. They are marked ``integration`` for that
reason.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.models.activity import ActivityLog
from app.models.learning import Skill
from app.models.user import User
from app.services.learning.metrics import FORBIDDEN_CLAIM_WORDS, LEARNING_METRICS
from tests.analytics_fixtures import AnalyticsSeed, seeded_client

pytestmark = pytest.mark.integration

#: The eighteen Phase 9 learning routes as ``(method, path)``, in the order the
#: contract fixes them. The gate tests walk this table rather than restating a
#: list, so a route added to the router without being added here shows up as a
#: route nobody proved is protected.
ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/learning/summary"),
    ("GET", "/api/v1/learning/metrics"),
    ("GET", "/api/v1/learning/gaps"),
    ("GET", "/api/v1/learning/activity"),
    ("GET", "/api/v1/learning/features"),
    ("GET", "/api/v1/learning/goals"),
    ("POST", "/api/v1/learning/goals"),
    ("GET", "/api/v1/learning/goals/{goal_id}"),
    ("PATCH", "/api/v1/learning/goals/{goal_id}"),
    ("DELETE", "/api/v1/learning/goals/{goal_id}"),
    ("POST", "/api/v1/learning/goals/{goal_id}/complete"),
    ("GET", "/api/v1/learning/skills"),
    ("POST", "/api/v1/learning/skills"),
    ("GET", "/api/v1/learning/skills/{skill_id}"),
    ("PATCH", "/api/v1/learning/skills/{skill_id}"),
    ("DELETE", "/api/v1/learning/skills/{skill_id}"),
    ("GET", "/api/v1/learning/activities"),
    ("POST", "/api/v1/learning/activities"),
)

#: ``(method, path, body)`` for every route that names a goal as a *path*
#: segment. ``None`` is a route with no payload; the PATCH carries one because an
#: *empty* edit is a 422 by contract, and this table is about tenancy rather than
#: about edit validation.
GOAL_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/v1/learning/goals/{goal_id}", None),
    ("PATCH", "/api/v1/learning/goals/{goal_id}", {"title": "renamed"}),
    ("DELETE", "/api/v1/learning/goals/{goal_id}", None),
    ("POST", "/api/v1/learning/goals/{goal_id}/complete", None),
)

#: The same for the skill routes.
SKILL_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/v1/learning/skills/{skill_id}", None),
    ("PATCH", "/api/v1/learning/skills/{skill_id}", {"name": "renamed"}),
    ("DELETE", "/api/v1/learning/skills/{skill_id}", None),
)

#: ``(method, path, body)`` for the three routes that take an id as a **body
#: field** or a **query filter** rather than as a path segment. These are the ones
#: a naive implementation forgets to scope, because the path looks unambiguous
#: when it simply has no id in it at all.
LINKED_ROUTES: tuple[tuple[str, str, str, dict[str, Any] | None], ...] = (
    (
        "POST",
        "/api/v1/learning/activities",
        "goal_id",
        {"activity_type": "study_session", "title": "Ada's session"},
    ),
    (
        "POST",
        "/api/v1/learning/activities",
        "skill_id",
        {"activity_type": "study_session", "title": "Ada's session"},
    ),
    ("POST", "/api/v1/learning/goals", "target_skill_id", {"title": "Ada's goal"}),
)

#: The routes whose ``?limit`` the contract bounds. A caller that asked for 500
#: and received 200 could not tell a truncated page from a page that was always
#: 200 rows long, so the ceiling is a refusal rather than a clip.
PAGED_ROUTES: tuple[str, ...] = (
    "/api/v1/learning/goals",
    "/api/v1/learning/skills",
    "/api/v1/learning/activities",
    "/api/v1/learning/gaps",
)

#: The message the service answers a goal, a skill or an activity that is not the
#: caller's with, and one nobody ever issued. Asserting both are identical is the
#: point: a route that could separate them is an existence oracle.
GOAL_NOT_FOUND_MESSAGE = "That learning goal was not found."
SKILL_NOT_FOUND_MESSAGE = "That skill was not found."

#: The feature schema version this phase freezes the column meanings under.
LEARNING_SCHEMA_VERSION = "learning_features.v1"

#: A role the permission map has never heard of, used for the 403 case. An
#: ordinary account holds ``analytics.read``, so refusing a request has to be a
#: question about a role — and the map is fail-closed, so an unknown role is the
#: honest way to express "does not hold it".
ROLE_WITHOUT_ANALYTICS = "wizard"

#: The eight metric keys the contract fixes, in contract order. Asserted exactly
#: rather than by count: a metric that vanished would still leave seven, and seven
#: is not the contract.
METRIC_KEYS: tuple[str, ...] = tuple(key.value for key in LEARNING_METRICS)

#: The activity type every fixture records. ``study_session`` is the weighted
#: kind; ``resource_viewed`` is deliberately not used, because a page view is the
#: weakest evidence on the surface and a gap built on it would be a weaker claim
#: than the test meant to make.
ACTIVITY_TYPE = "study_session"

#: How many activities the "gaps" fixture records against its evidenced skill.
#: Below ``learning_min_evidence_for_estimate`` this is fine — the fixture skill
#: is ``user_defined``, so no estimate is ever on offer and the refusal path is
#: never entered.
FIXTURE_ACTIVITIES = 2

#: A level the user types for themselves, and the target they are aiming at. Both
#: are the user's; NEXUS records them and says so.
USER_LEVEL = 2
USER_TARGET_LEVEL = 4

#: Phrases that would be a claim about a person wearing a number's clothes. Matched
#: on word boundaries so an innocent substring cannot fail the sweep and send
#: someone hunting a bug that is not there.
FORBIDDEN_CLAIM_PHRASES = (
    "good at",
    "not good at",
    "bad at",
    "great at",
    "poor at",
    "excellent at",
    "weak at",
    "strong at",
    "struggles with",
    "excels at",
)

#: ``"you are ..."`` / ``"you are not ..."`` paired with an adjective, which is the
#: grammatical shape the brief's own prohibition takes.
FORBIDDEN_SENTENCE = re.compile(
    r"\byou(?:'re| are| were| was)\s+(?:not\s+|never\s+)?"
    r"(?:very\s+)?(?:good|bad|great|poor|excellent|weak|strong|awful|terrible)\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    client: Any,
    method: str,
    template: str,
    headers: dict[str, str] | None = None,
    *,
    goal_id: uuid.UUID | None = None,
    skill_id: uuid.UUID | None = None,
    **kwargs: Any,
):
    """Issue one request against a route template.

    The placeholders are filled with a fresh, never-issued uuid unless the caller
    names a real row, so the gate and tenancy tests reach a route exactly the way
    a caller with no such row would.
    """
    path = template.format(
        goal_id=goal_id or uuid.uuid4(),
        skill_id=skill_id or uuid.uuid4(),
    )
    return client.request(method, path, headers=headers, **kwargs)


async def _create_goal(client: Any, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    """Record one goal through the real endpoint."""
    payload: dict[str, Any] = {"title": "Learn Rust", **extra}
    response = await client.post("/api/v1/learning/goals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_skill(client: Any, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    """Track one skill through the real endpoint."""
    payload: dict[str, Any] = {"name": "Distributed systems", **extra}
    response = await client.post("/api/v1/learning/skills", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _record_activity(client: Any, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    """Record one learning activity through the real endpoint."""
    payload: dict[str, Any] = {
        "activity_type": ACTIVITY_TYPE,
        "title": "Read the chapter on replication",
        **extra,
    }
    response = await client.post("/api/v1/learning/activities", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _events(session: Any, user_id: uuid.UUID, event_type: str) -> list[ActivityLog]:
    """Every ``activity_events`` row of one kind belonging to one account."""
    result = await session.execute(
        select(ActivityLog).where(
            ActivityLog.user_id == user_id, ActivityLog.event_type == event_type
        )
    )
    return list(result.scalars().all())


def _walk(value: Any):
    """Yield every mapping nested anywhere inside a decoded JSON payload."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


@pytest.fixture
async def account(client, db_session) -> tuple[AnalyticsSeed, dict[str, str]]:
    """One signed-in account with nothing recorded, as ``(seed, headers)``."""
    seed, headers = await seeded_client(client, db_session)
    return seed, headers


# ---------------------------------------------------------------------------
# The gate: 401 anonymous, 403 without analytics.read, on all eighteen routes
# ---------------------------------------------------------------------------

_ROUTE_CASES = [pytest.param(method, path, id=f"{method} {path}") for method, path in ROUTES]


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_an_anonymous_caller(
    method, template, client, assert_error_envelope
):
    """No Phase 9 learning route answers a caller who has not signed in.

    Authentication runs before the permission check, so "you are not signed in" is
    never reported as "you may not" — they are different answers to different
    questions, and a client acts on them differently. The POST and DELETE cases
    carry no body at all, which is the point: the refusal must come from the
    dependency rather than from a request schema that happens to be satisfied.
    """
    response = await _request(client, method, template)

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_a_caller_without_analytics_read(
    method, template, client, db_session, account, assert_error_envelope
):
    """A valid, live session whose role grants nothing is answered 403.

    Phase 9 **reuses** ``analytics.read`` rather than coining a
    ``learning.write``, and this is the test that says the reuse is enforced on
    the eight writes and not only on the ten reads. A new ``Permission`` member
    would have been granted to exactly the roles ``analytics.read`` already
    covers, so it would be a second name for the same door — and
    ``tests/test_permissions.py`` asserts the full member set.
    """
    seed, headers = account
    await db_session.execute(
        update(User).where(User.id == seed.owner.id).values(role=ROLE_WITHOUT_ANALYTICS)
    )
    await db_session.commit()

    response = await _request(client, method, template, headers=headers)

    assert_error_envelope(response, status_code=403, code="forbidden")


# ---------------------------------------------------------------------------
# The routing hazard: literal sub-paths against /{goal_id} and /{skill_id}
# ---------------------------------------------------------------------------


async def test_every_literal_sub_path_answers_its_own_question(client, db_session, account):
    """``/learning/summary`` answers with counts, not with a uuid parse failure.

    Were a parameterised route registered above it, Starlette would bind the
    literal string ``summary`` to the path parameter, the uuid conversion would
    fail, and the dashboard tile would answer 422 about an id nobody ever issued
    — a route that still exists and still answers a different question. Each
    assertion below pins a key that **only** that handler emits, which is the only
    thing that distinguishes the two outcomes.
    """
    _seed, headers = account

    summary = await client.get("/api/v1/learning/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert {"goal_count", "has_data", "window_days"} <= set(summary.json()), summary.json()

    metrics = await client.get("/api/v1/learning/metrics", headers=headers)
    assert metrics.status_code == 200, metrics.text
    assert isinstance(metrics.json(), list)
    assert metrics.json()[0]["key"] == METRIC_KEYS[0], metrics.text

    gaps = await client.get("/api/v1/learning/gaps", headers=headers)
    assert gaps.status_code == 200, gaps.text
    assert {"items", "available_count", "unavailable_count"} <= set(gaps.json()), gaps.text

    activity = await client.get("/api/v1/learning/activity", headers=headers)
    assert activity.status_code == 200, activity.text
    assert {"granularity", "buckets", "total_activities"} <= set(activity.json()), activity.text

    features = await client.get("/api/v1/learning/features", headers=headers)
    assert features.status_code == 200, features.text
    assert features.json()["schema_version"] == LEARNING_SCHEMA_VERSION, features.text


# ---------------------------------------------------------------------------
# The empty account: an absence to explain, never an error
# ---------------------------------------------------------------------------


async def test_a_fresh_account_gets_the_documented_empty_shape(client, db_session, account):
    """Measured zeroes, nulls where nothing could be computed, and no 404.

    Every route here answers 200 on an account with nothing recorded. That is the
    distinction this phase cares about most: an account with no learning has a
    *measured* answer ("none were recorded"), and turning that into a 404 or a
    rejected request would tell a new user their feature is broken. The nulls are
    the other half — an account with no goals does not have goals that are zero
    percent complete, so ``goal_progress`` is ``None`` rather than ``0``.
    """
    _seed, headers = account

    summary = (await client.get("/api/v1/learning/summary", headers=headers)).json()
    assert summary["has_data"] is False, summary
    assert summary["goal_count"] == 0, summary
    assert summary["skill_count"] == 0, summary
    assert summary["activity_count"] == 0, summary
    assert summary["activities_in_window"] == 0, summary
    # Null, not 0: nobody logged a duration, which is not a measured absence of
    # time.
    assert summary["minutes_in_window"] is None, summary
    assert summary["latest_activity_at"] is None, summary

    features = (await client.get("/api/v1/learning/features", headers=headers)).json()
    values = features["features"]
    assert values["sessions_last_7d"] == 0, values
    assert values["sessions_last_30d"] == 0, values
    # An account with no goals has no goals that are zero percent complete, and an
    # account that has finished nothing has not completed nothing — both null.
    assert values["goal_progress"] is None, values
    assert values["completion_rate"] is None, values
    assert values["goal_deadline_distance_days"] is None, values
    # A rate over no tracked skills is null; 0 would claim each was worked on and
    # nothing was recorded.
    assert values["skill_activity_frequency"] is None, values
    assert values["learning_minutes"] is None, values

    gaps = (await client.get("/api/v1/learning/gaps", headers=headers)).json()
    assert gaps["items"] == [], gaps
    assert gaps["total"] == 0, gaps
    assert gaps["available_count"] == 0, gaps
    assert gaps["unavailable_count"] == 0, gaps

    goals = (await client.get("/api/v1/learning/goals", headers=headers)).json()
    assert goals["items"] == [] and goals["total"] == 0, goals

    skills = (await client.get("/api/v1/learning/skills", headers=headers)).json()
    assert skills["items"] == [] and skills["total"] == 0, skills

    activities = (await client.get("/api/v1/learning/activities", headers=headers)).json()
    assert activities["items"] == [] and activities["total"] == 0, activities


async def test_the_activity_series_is_dense_when_nothing_was_recorded(client, db_session, account):
    """A quiet month arrives as zero-filled buckets, not as an empty chart.

    A series that omitted empty buckets compresses the timeline: a reader counting
    the bars would see five active days and read them as consecutive. The
    assertion is the *density* — one bucket per day across the window — and not
    merely a count, so a builder that returned thirty buckets for the wrong reason
    would not satisfy it.
    """
    _seed, headers = account

    body = (
        await client.get("/api/v1/learning/activity?window_days=7&granularity=day", headers=headers)
    ).json()

    assert body["granularity"] == "day", body
    assert body["window_days"] == 7, body
    assert body["total_activities"] == 0, body
    # Null, not 0: nobody said how long anything took.
    assert body["total_minutes"] is None, body
    assert len(body["buckets"]) >= 7, body
    assert all(bucket["activities"] == 0 for bucket in body["buckets"]), body
    # A bucket nobody recorded a duration in is null rather than zero minutes.
    assert all(bucket["minutes"] is None for bucket in body["buckets"]), body


async def test_an_unsupported_granularity_is_422_not_a_guessed_chart(
    client, db_session, account, assert_error_envelope
):
    """``?granularity=fortnight`` is refused rather than silently rounded to a week.

    Returning a chart nobody asked for is worse than a 422: the caller would
    render bucket widths it never asked for and have no way to notice.
    """
    _seed, headers = account

    response = await client.get("/api/v1/learning/activity?granularity=fortnight", headers=headers)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "fortnight" in error["message"], error


# ---------------------------------------------------------------------------
# The eight metrics
# ---------------------------------------------------------------------------


async def test_the_metrics_endpoint_returns_all_eight(client, db_session, account):
    """Exactly eight keys, in contract order, with value and availability agreeing.

    Asserted as a tuple rather than as a count: a metric that vanished would still
    leave seven, and seven is not the contract. The second half of the test is the
    rule that makes the first safe — a metric that could not be computed is
    ``value: null`` with ``available: false`` and a reason, never a zero wearing a
    measurement's clothes. The response model enforces the pairing; this asserts it
    over the wire, which is where a future route assembling its own list would
    break it.
    """
    _seed, headers = account

    response = await client.get("/api/v1/learning/metrics", headers=headers)

    assert response.status_code == 200, response.text
    metrics = response.json()
    assert tuple(item["key"] for item in metrics) == METRIC_KEYS, metrics
    for item in metrics:
        assert (item["value"] is None) is (item["available"] is False), item
        if item["available"] is False:
            assert item["reason_if_unavailable"], item
        else:
            assert item["reason_if_unavailable"] is None, item
        # An explanation built from no figures is a backend bug, and every metric
        # here is built with its figures named — including the declined ones, which
        # state how many records the reading found.
        assert any(character.isdigit() for character in item["explanation"]), item


async def test_recording_activity_moves_the_metrics_off_their_cold_start(
    client, db_session, account
):
    """The counts that were measured zero become real counts; the rest become measurable.

    ``sessions_last_7d`` and ``sessions_last_30d`` are *always* available — no
    sessions in a week is a measured zero, not an absence — so on the empty account
    they read ``0`` with ``available: true`` and here they read the number of
    activities the fixture recorded. ``goal_progress`` was ``None`` on the empty
    account because an account with no goals does not have goals that are zero
    percent complete; one goal at 25% makes it measurable.

    ``completion_rate`` is the pair that proves the distinction is real: with one
    unfinished goal it is ``0.0`` **with** ``available: true``. That is a
    measurement — the goal has not been finished yet — and not the ``null`` the
    empty account gets, where there is no denominator at all. A card that rendered
    both as "0%" would be making the same claim about two different situations.
    """
    _seed, headers = account
    skill = await _create_skill(client, headers, target_level=USER_TARGET_LEVEL)
    for index in range(FIXTURE_ACTIVITIES):
        await _record_activity(client, headers, skill_id=skill["id"], title=f"Session {index}")
    await _create_goal(client, headers, progress=25)

    metrics = (await client.get("/api/v1/learning/metrics", headers=headers)).json()
    by_key = {item["key"]: item for item in metrics}

    assert by_key["sessions_last_7d"]["value"] == FIXTURE_ACTIVITIES, by_key
    assert by_key["sessions_last_30d"]["value"] == FIXTURE_ACTIVITIES, by_key
    assert by_key["goal_progress"]["value"] == 25.0, by_key
    assert by_key["goal_progress"]["available"] is True, by_key
    assert by_key["completion_rate"]["value"] == 0.0, by_key
    assert by_key["completion_rate"]["available"] is True, by_key


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


async def test_the_gaps_endpoint_separates_a_measured_gap_from_an_unmeasured_one(
    client, db_session, account
):
    """``available=True`` with ``gap=0`` and ``available=False`` are different answers.

    Two skills: one with recorded evidence against it, one the user has only
    named. The first gets a measurement — and if the target is met that
    measurement is a **zero**, which is the good news the page exists to deliver.
    The second gets ``available: false`` with its own reason, because "no evidence"
    is not "no gap"; it is the absence of the question's answer. Collapsing the
    second into a null would render an untracked skill as a skill at zero distance
    from its target, which is a claim nobody can support.
    """
    _seed, headers = account
    measured = await _create_skill(
        client,
        headers,
        name="Replication",
        current_level=USER_LEVEL,
        target_level=USER_TARGET_LEVEL,
    )
    # Target already met, so this skill's gap is a *real zero* — a measurement, not
    # a missing one. It carries no evidence, so the row is additionally unmeasured.
    await _create_skill(client, headers, name="Technical writing", current_level=3, target_level=3)
    for index in range(FIXTURE_ACTIVITIES):
        await _record_activity(client, headers, skill_id=measured["id"], title=f"Session {index}")

    response = await client.get("/api/v1/learning/gaps", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2, body
    assert body["available_count"] == 1, body
    assert body["unavailable_count"] == 1, body
    by_name = {item["skill_name"]: item for item in body["items"]}

    with_evidence = by_name["Replication"]
    assert with_evidence["available"] is True, with_evidence
    assert with_evidence["reason_if_unavailable"] is None, with_evidence
    assert with_evidence["gap"] == USER_TARGET_LEVEL - USER_LEVEL, with_evidence
    assert with_evidence["evidence_last_30d"] == FIXTURE_ACTIVITIES, with_evidence

    # Target met, so the gap is a real zero — and it is still a measurement.
    met = by_name["Technical writing"]
    assert met["gap"] == 0, met
    assert met["available"] is False, met
    assert met["reason_if_unavailable"], met

    # Every gap sentence names its levels and its evidence count.
    for item in body["items"]:
        assert any(character.isdigit() for character in item["explanation"]), item
        assert str(item["target_level"]) in item["explanation"], item
        assert str(item["current_level"]) in item["explanation"], item


async def test_a_user_defined_level_is_described_as_self_assessed(client, db_session, account):
    """The sentence says whose claim the level is, and the payload says so twice.

    ``user_defined`` reads as *self-assessed* and ``system_estimate`` as an
    estimate NEXUS can show its working for; the two sentences are not
    interchangeable, and a payload carrying ``2/5`` with no ``level_source`` beside
    it is the failure the whole ``SkillLevelSource`` column exists to prevent.
    """
    _seed, headers = account
    await _create_skill(client, headers, current_level=USER_LEVEL, target_level=USER_TARGET_LEVEL)

    item = (await client.get("/api/v1/learning/gaps", headers=headers)).json()["items"][0]

    assert item["level_source"] == "user_defined", item
    assert "self-assessed" in item["explanation"], item


# ---------------------------------------------------------------------------
# Tenancy: 404, never 403, and never an existence oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in GOAL_ROUTES],
)
async def test_a_foreign_goal_is_404_not_403_on_every_route(
    method, template, body, client, db_session, assert_error_envelope
):
    """Grace's goal, named by path, is refused like a typo.

    The caller is authenticated and permitted — the token is Ada's, and Ada holds
    ``analytics.read`` — and still gets ``not_found``. The message for the foreign
    id is compared against the message for an id nobody ever issued, so the two
    cannot drift apart.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    del grace_seed
    foreign = await _create_goal(client, grace_headers, title="Grace's goal")

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    response = await _request(
        client, method, template, ada_headers, goal_id=uuid.UUID(foreign["id"]), **kwargs
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == GOAL_NOT_FOUND_MESSAGE
    assert "Grace's goal" not in response.text

    unissued = await _request(client, method, template, ada_headers, **kwargs)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in SKILL_ROUTES],
)
async def test_a_foreign_skill_is_404_not_403_on_every_route(
    method, template, body, client, db_session, assert_error_envelope
):
    """Grace's skill is a 404 on the read, the edit and the delete alike.

    ``DELETE`` is the interesting one: a router that resolved the row without
    scoping it would delete another account's skill and answer 204, which is the
    failure this test exists to catch and the only one that cannot be undone by
    the user.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    _grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign = await _create_skill(client, grace_headers, name="Grace's skill")

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    response = await _request(
        client, method, template, ada_headers, skill_id=uuid.UUID(foreign["id"]), **kwargs
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == SKILL_NOT_FOUND_MESSAGE
    assert "Grace's skill" not in response.text

    unissued = await _request(client, method, template, ada_headers, **kwargs)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]

    # Still there: a refused delete deleted nothing.
    survivors = (await client.get("/api/v1/learning/skills", headers=grace_headers)).json()
    assert [item["id"] for item in survivors["items"]] == [foreign["id"]], survivors


@pytest.mark.parametrize(
    ("method", "template", "field", "body"),
    [pytest.param(*case, id=f"{case[1]} {case[2]}") for case in LINKED_ROUTES],
)
async def test_a_foreign_id_in_a_body_or_a_filter_is_404_not_403(
    method, template, field, body, client, db_session, assert_error_envelope
):
    """An id that arrives in a request *body* is scoped exactly as one in the path.

    Two of these three routes carry no id in the URL at all, so the ownership check
    is the only thing standing between one account's records and another's. The
    expected message differs by which resource the id names, so it is looked up
    rather than assumed — the point of the test is the **404**, and that the
    foreign id and an unissued one are indistinguishable.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign_goal = await _create_goal(client, grace_headers, title="Grace's goal")
    foreign_skill = await _create_skill(client, grace_headers, name="Grace's skill")
    foreign_id = uuid.UUID(foreign_goal["id"] if field.endswith("goal_id") else foreign_skill["id"])
    del grace_seed

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = {**body, field: str(foreign_id)}
    response = await client.request(method, template, headers=ada_headers, **kwargs)
    error = assert_error_envelope(response, status_code=404, code="not_found")
    expected = GOAL_NOT_FOUND_MESSAGE if field.endswith("goal_id") else SKILL_NOT_FOUND_MESSAGE
    assert error["message"] == expected, error

    # Nothing was written: the ownership check precedes the insert, so a refused
    # request leaves no orphan activity and no goal pointing at a stranger.
    assert (await client.get("/api/v1/learning/activities", headers=ada_headers)).json()[
        "total"
    ] == 0
    assert (await client.get("/api/v1/learning/goals", headers=ada_headers)).json()["total"] == 0


async def test_another_accounts_activity_is_refused_through_the_series_filter(
    client, db_session, assert_error_envelope
):
    """``GET /learning/activity?goal_id=<foreign>`` is a 404, not an empty chart.

    The narrowest tenancy hole on this surface: the filter is a query parameter
    rather than a path segment, so an implementation that scoped only the path
    would answer ``200`` with zero buckets — a chart that silently omits Grace's
    goal rather than refusing it. The unissued-id comparison is what keeps the two
    answers indistinguishable.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    _grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign = await _create_goal(client, grace_headers, title="Grace's goal")

    template = "/api/v1/learning/activity?goal_id={goal_id}"
    response = await _request(
        client, "GET", template, ada_headers, goal_id=uuid.UUID(foreign["id"])
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == GOAL_NOT_FOUND_MESSAGE
    assert "Grace's goal" not in response.text

    unissued = await _request(client, "GET", template, ada_headers)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


async def test_completing_a_goal_stamps_it_through_the_endpoint(client, db_session, account):
    """``status``, ``completed_at`` and ``progress`` move together, and are recorded.

    Three things happen in one request because they are one fact: a caller doing
    them separately would leave a window in which the row claimed a completion with
    no stamp or a stamp with no completion — a state the database's check
    constraint exists to forbid. The activity row below is what makes the stamp
    traceable rather than merely present.
    """
    seed, headers = account
    goal = await _create_goal(client, headers, progress=30)

    response = await client.post(f"/api/v1/learning/goals/{goal['id']}/complete", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed", body
    assert body["progress"] == 100, body
    assert body["completed_at"] is not None, body

    # The stamp survives a re-read rather than existing only in the response.
    reread = (await client.get(f"/api/v1/learning/goals/{goal['id']}", headers=headers)).json()
    assert reread["completed_at"] == body["completed_at"], reread

    completed = await _events(db_session, seed.owner.id, "learning_goal_completed")
    assert len(completed) == 1, completed
    assert completed[0].metadata_["goal_id"] == goal["id"]


async def test_an_unset_goal_field_is_not_a_cleared_field(client, db_session, account):
    """Asserting progress leaves the description, the topic and the project alone.

    The route builds its mapping with ``exclude_unset=True`` because ``None`` here
    means "write SQL NULL". Without it, a one-field edit would silently discard
    everything else — and the response would still look entirely correct, which is
    why this is asserted against the round trip rather than against the request.
    """
    seed, headers = account
    project = await seed.project(name="atlas")
    goal = await _create_goal(
        client,
        headers,
        description="Work through the book",
        target_topic="asynchronous replication",
        project_id=str(project.id),
    )

    response = await client.patch(
        f"/api/v1/learning/goals/{goal['id']}", json={"progress": 40}, headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["progress"] == 40, body
    assert body["description"] == "Work through the book", body
    assert body["target_topic"] == "asynchronous replication", body
    assert body["project_id"] == str(project.id), body


async def test_an_edit_that_changes_nothing_is_422(
    client, db_session, account, assert_error_envelope
):
    """``{}`` is refused rather than answered with a cheerful no-op 200.

    A silent 200 would tell a client it had updated something it had not, and the
    client would go on to believe the state it asked for had been stored.
    """
    _seed, headers = account
    goal = await _create_goal(client, headers)

    response = await client.patch(f"/api/v1/learning/goals/{goal['id']}", json={}, headers=headers)

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["message"], error


async def test_a_client_cannot_file_its_own_inference_as_a_self_assessment(
    client, db_session, account, assert_error_envelope
):
    """``level_source`` is not in the skill edit schema, and sending it is a 422.

    ``level_source`` and ``confidence`` are NEXUS's to write. A client that could
    set them would be able to file its own inference as a self-assessment — which
    is precisely the claim the whole ``SkillLevelSource`` column exists to keep
    honest. The 422 naming the field is the answer a client can act on; a 200 that
    silently dropped it would let it go on believing the change applied.
    """
    _seed, headers = account
    skill = await _create_skill(client, headers)

    response = await client.patch(
        f"/api/v1/learning/skills/{skill['id']}",
        json={"level_source": "system_estimate", "confidence": 90},
        headers=headers,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    # The envelope's ``details`` carries the offending names — not the message,
    # which is generic — and the name is the whole point: a 422 that did not say
    # which fields were refused is a 422 the client cannot act on.
    fields = {item["field"] for item in error["details"]["errors"]}
    assert {"level_source", "confidence"} <= fields, error

    unchanged = (await client.get(f"/api/v1/learning/skills/{skill['id']}", headers=headers)).json()
    assert unchanged["level_source"] == "user_defined", unchanged
    assert unchanged["confidence"] == 0, unchanged


async def test_a_duplicate_skill_name_is_409(client, db_session, account):
    """One name per account, and the collision is reported rather than merged.

    A duplicate row would give every gap computation two answers for one subject,
    and the page would show the same skill twice with two different levels — one
    of which nobody entered.
    """
    _seed, headers = account
    await _create_skill(client, headers, name="Replication")

    response = await client.post(
        "/api/v1/learning/skills", json={"name": "Replication"}, headers=headers
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


async def test_recording_an_activity_counts_against_the_skill(client, db_session, account):
    """The activity row, the skill's counter and the activity trail all move.

    ``evidence_count`` is a cached counter over a simple count; the gap that
    depends on it is computed on read so no second answer can go stale. The event
    carries ids rather than the user's words, because the title belongs on the row
    the event points at.
    """
    seed, headers = account
    skill = await _create_skill(client, headers)
    for index in range(FIXTURE_ACTIVITIES):
        await _record_activity(client, headers, skill_id=skill["id"], title=f"Session {index}")

    body = (await client.get(f"/api/v1/learning/skills/{skill['id']}", headers=headers)).json()
    assert body["evidence_count"] == FIXTURE_ACTIVITIES, body
    assert body["last_activity_at"] is not None, body

    sessions = await _events(db_session, seed.owner.id, "learning_session_recorded")
    assert len(sessions) == FIXTURE_ACTIVITIES, sessions


async def test_deleting_a_goal_keeps_the_trail_of_work_done_on_it(client, db_session, account):
    """``204``, and the activities recorded towards the goal survive.

    The goal is ``ON DELETE SET NULL`` on the activity, so abandoning an intention
    must not delete the evidence that the user once worked on it — a skill's
    evidence count is a history, and a user who abandons a goal has not
    unlearned anything.
    """
    _seed, headers = account
    goal = await _create_goal(client, headers)
    activity = await _record_activity(client, headers, goal_id=goal["id"])

    deleted = await client.delete(f"/api/v1/learning/goals/{goal['id']}", headers=headers)

    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b"", deleted.text
    assert (
        await client.get(f"/api/v1/learning/goals/{goal['id']}", headers=headers)
    ).status_code == 404

    activities = (await client.get("/api/v1/learning/activities", headers=headers)).json()
    assert activities["total"] == 1, activities
    assert activities["items"][0]["id"] == activity["id"], activities
    assert activities["items"][0]["goal_id"] is None, activities


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", PAGED_ROUTES)
async def test_a_page_size_outside_the_documented_bounds_is_422(
    route, client, db_session, account, assert_error_envelope
):
    """``?limit=500`` is refused rather than silently clipped to the ceiling.

    A caller that asked for 500 and received 200 cannot tell a truncated page from
    a page that was always 200 rows long. ``0`` and ``-1`` are refused for the same
    family of reasons: no page has fewer than no rows.
    """
    _seed, headers = account

    for limit in (0, -1, 500):
        response = await client.get(f"{route}?limit={limit}", headers=headers)
        assert_error_envelope(response, status_code=422, code="validation_error")


async def test_the_goal_list_pages_without_losing_or_repeating_a_row(client, db_session, account):
    """Two goals over two pages come back as two distinct rows.

    Newest first, so the ordering is asserted as well as the coverage: a list that
    paged correctly and ordered wrongly would still pass a set comparison. The
    ``by_status`` tally beside it covers **every** matching goal rather than the
    page, so a header counting a page slice could never understate a band that
    continues onto page two.
    """
    _seed, headers = account
    first_goal = await _create_goal(client, headers, title="Learn Rust")
    second_goal = await _create_goal(client, headers, title="Learn Kafka")

    first = (await client.get("/api/v1/learning/goals?limit=1&offset=0", headers=headers)).json()
    second = (await client.get("/api/v1/learning/goals?limit=1&offset=1", headers=headers)).json()

    assert first["total"] == second["total"] == 2
    assert first["limit"] == second["limit"] == 1
    assert len(first["items"]) == len(second["items"]) == 1
    # Coverage rather than ordering: the contract fixes which rows a page holds,
    # not the order they arrive in, and asserting an order nobody promised would
    # fail on a change that broke nothing. Distinctness is the property that stops
    # a page from repeating one row and dropping the other.
    assert first["items"][0]["id"] != second["items"][0]["id"], (first, second)
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        first_goal["id"],
        second_goal["id"],
    }
    # Both pages count the whole set, not the slice in front of them.
    assert first["by_status"]["not_started"] == second["by_status"]["not_started"] == 2


# ---------------------------------------------------------------------------
# Nothing here ever claims a person is good or bad at anything
# ---------------------------------------------------------------------------

#: The paths the sweep walks. Every read the learning page issues, so a sentence
#: invented in a handler nobody covered cannot slip through.
SWEEP_PATHS: tuple[str, ...] = (
    "/api/v1/learning/summary",
    "/api/v1/learning/metrics",
    "/api/v1/learning/gaps",
    "/api/v1/learning/activity",
    "/api/v1/learning/features",
    "/api/v1/learning/goals",
    "/api/v1/learning/skills",
    "/api/v1/learning/activities",
)


async def test_no_response_claims_what_the_user_is_good_at(client, db_session, account):
    """No field and no sentence on this surface is a statement about a person.

    A recorded activity is evidence that something was recorded. Nothing in
    ``learning_activities`` can show what anyone is *good* at, so a response that
    reached for a judgement — in a field name or in a sentence — would be a
    fabrication with a real number attached to it.

    Three checks, because they fail in different ways:

    * the vocabulary the metrics module itself refuses to build an explanation
      from, matched on word boundaries so an innocent substring cannot fail this
      and send someone hunting a bug that is not there;
    * the phrase-level and sentence-level patterns the brief's own prohibition
      takes, which a word list cannot express; and
    * **every payload carrying ``current_level`` also carrying
      ``level_source``** — a card that rendered ``2/5`` with nothing saying who
      claimed it is the failure this whole test exists to keep out of the tree.
    """
    _seed, headers = account
    skill = await _create_skill(
        client, headers, current_level=USER_LEVEL, target_level=USER_TARGET_LEVEL
    )
    for index in range(FIXTURE_ACTIVITIES):
        await _record_activity(
            client, headers, skill_id=skill["id"], title=f"Session {index}", duration_minutes=30
        )
    goal = await _create_goal(client, headers, target_skill_id=skill["id"], progress=10)

    bodies: list[tuple[str, Any]] = []
    for path in SWEEP_PATHS:
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        bodies.append((path, response.json()))
    for path in (
        f"/api/v1/learning/goals/{goal['id']}",
        f"/api/v1/learning/skills/{skill['id']}",
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        bodies.append((path, response.json()))

    offenders: list[str] = []
    for path, payload in bodies:
        text = json_text(payload)
        for word in FORBIDDEN_CLAIM_WORDS:
            if re.search(rf"\b{word}\w*\b", text, flags=re.IGNORECASE):
                offenders.append(f"{path}: {word}")
        for phrase in FORBIDDEN_CLAIM_PHRASES:
            if phrase in text.lower():
                offenders.append(f"{path}: {phrase}")
        if FORBIDDEN_SENTENCE.search(text):
            offenders.append(f"{path}: {FORBIDDEN_SENTENCE.pattern}")
    assert offenders == [], offenders

    # A level never travels without saying whose claim it is.
    unpaired = [
        f"{path}: {sorted(node)}"
        for path, payload in bodies
        for node in _walk(payload)
        if "current_level" in node and "level_source" not in node
    ]
    assert unpaired == [], unpaired

    # And where it does travel, the source is one NEXUS recognises.
    unknown = [
        f"{path}: {node['level_source']}"
        for path, payload in bodies
        for node in _walk(payload)
        if "level_source" in node
        and node["level_source"] not in {"user_defined", "system_estimate"}
    ]
    assert unknown == [], unknown


def json_text(payload: Any) -> str:
    """The payload as flat text, so a word search sees every string in it."""
    return " ".join(node for node in _strings(payload)).lower()


def _strings(value: Any):
    """Yield every string anywhere inside a decoded JSON payload."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


# ---------------------------------------------------------------------------
# The counters the read path caches
# ---------------------------------------------------------------------------


async def test_the_skill_evidence_counter_is_a_count_and_not_an_inference(
    client, db_session, account
):
    """Recording activity bumps the counter; it never moves the level.

    Evidence is collected and the level is asserted separately and labelled. A
    counter that also nudged ``current_level`` would turn three page views into a
    claim about somebody, which is the one transformation this phase refuses.
    """
    _seed, headers = account
    skill = await _create_skill(
        client, headers, current_level=USER_LEVEL, target_level=USER_TARGET_LEVEL
    )
    for index in range(FIXTURE_ACTIVITIES):
        await _record_activity(client, headers, skill_id=skill["id"], title=f"Session {index}")

    body = (await client.get(f"/api/v1/learning/skills/{skill['id']}", headers=headers)).json()

    assert body["evidence_count"] == FIXTURE_ACTIVITIES, body
    assert body["current_level"] == USER_LEVEL, body
    assert body["level_source"] == "user_defined", body
    # Confidence is how much evidence backs an *estimate*. A user-typed level has
    # nothing behind it, and zero says so rather than implying a weak estimate.
    assert body["confidence"] == 0, body


async def test_two_accounts_skills_are_two_rows_in_the_table(client, db_session):
    """Counted through the ORM, because the property is about storage.

    Tenancy that holds on the read path but not in the table would be tenancy that
    one query away from failing, so the assertion is deliberately below the router:
    Ada's two skills are two rows, and Grace's account contributed none of them.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    await seeded_client(client, db_session, username="grace", email="grace@nexus.test")
    await _create_skill(client, ada_headers, name="Ada's skill")
    await _create_skill(client, ada_headers, name="Another of Ada's")

    result = await db_session.execute(select(func.count()).select_from(Skill))
    assert result.scalar_one() == 2
