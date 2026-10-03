"""The Phase 9 HTTP surface end to end: two routers, twenty-eight routes.

Every route here is exercised through HTTP with a real bearer token, and most of
the assertions are about what must **not** happen rather than about what a
response contains. The happy paths of this phase are the ones a demo shows; the
six properties below are the ones that break the product quietly when a plausible
edit lands.

* **The literal sub-paths are reachable at all.** ``GET /learning/summary``,
  ``/metrics``, ``/gaps``, ``/activity``, ``/features`` and ``GET /career/summary``,
  ``/profile``, ``/features`` share a prefix with ``/learning/goals/{goal_id}``,
  ``/learning/skills/{skill_id}``, ``/career/experience/{experience_id}`` and
  ``/career/evidence/{evidence_id}``. Starlette matches in declaration order and
  does not prefer a literal segment over a parameter, so moving one decorator
  below a parameterised one does not remove the route — it binds the literal word
  to the path parameter and answers with a 422 about a uuid that was never one.
  :func:`test_every_literal_sub_path_answers_its_own_question` pins a field only
  the right handler emits, which is the only assertion that distinguishes
  "answered" from "answered with a uuid parse failure".

* **A foreign row is 404, never 403, on every route that can name one** —
  including the creations, where the id arrives in the *body* as a
  ``target_skill_id`` or a ``project_id``. Those are the routes where a naive
  implementation would store the pointer first and check afterwards. The message
  for a foreign id is compared against the message for one nobody ever issued, so
  the two cases cannot drift into an existence oracle.

* **A cold start is a 200, not a 404 and not an invented row.**
  ``GET /career/profile`` answers ``200`` with a ``null`` body for an account
  that has never written one, and ``GET /learning/summary`` answers ``200`` with
  ``has_data: false``. Both are states the UI has to render rather than errors,
  and a server that filled either gap would be writing a career row or a finding
  about somebody.

* **An unset field is not a cleared field.** Every ``PATCH`` and the profile
  ``PUT`` build their mapping with ``exclude_unset=True`` because ``None`` means
  "write SQL NULL" downstream. Without it, renaming a goal silently discards its
  description and the response still looks correct.

* **Absence is not zero.** On an empty account the feature vectors return
  ``null`` for every figure that could not be computed, and the gap list returns
  an empty page rather than a page of fabricated gaps. Inside a training matrix a
  fabricated zero is indistinguishable from an observed one.

* **A delete is a 204 with no body.** FastAPI forbids a body there, so a route
  that kept its ``response_model`` would answer with an error rather than a
  delete.

Counts here are **derived, never recorded from a run**: every figure asserted
below is read off what this file itself wrote.

Every test requires a live PostgreSQL and is marked ``integration`` for that
reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import pytest
from sqlalchemy import update

from app.models.user import User
from tests.analytics_fixtures import seeded_client

pytestmark = pytest.mark.integration

#: Every Phase 9 route as ``(method, path)``. The gate tests walk this table
#: rather than restating a list, so a route added to a router without being added
#: here shows up as a route nobody proved is protected.
LEARNING_ROUTES: tuple[tuple[str, str], ...] = (
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

CAREER_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/career/summary"),
    ("GET", "/api/v1/career/profile"),
    ("PUT", "/api/v1/career/profile"),
    ("GET", "/api/v1/career/experience"),
    ("POST", "/api/v1/career/experience"),
    ("PATCH", "/api/v1/career/experience/{experience_id}"),
    ("DELETE", "/api/v1/career/experience/{experience_id}"),
    ("GET", "/api/v1/career/evidence"),
    ("POST", "/api/v1/career/evidence"),
    ("PATCH", "/api/v1/career/evidence/{evidence_id}"),
    ("DELETE", "/api/v1/career/evidence/{evidence_id}"),
    ("GET", "/api/v1/career/features"),
)

ROUTES: tuple[tuple[str, str], ...] = LEARNING_ROUTES + CAREER_ROUTES

#: The paths that must answer their own question rather than a uuid parse error,
#: as ``(path, field only the right handler emits)``. A route bound to
#: ``{goal_id}``/``{skill_id}``/``{experience_id}``/``{evidence_id}`` fails its
#: uuid conversion on the literal word and answers 422 with no such field at all,
#: so the presence of the field is the whole assertion.
LITERAL_SUB_PATHS: tuple[tuple[str, str], ...] = (
    ("/api/v1/learning/summary", "has_data"),
    ("/api/v1/learning/metrics", "learning_consistency"),
    ("/api/v1/learning/gaps", "available_count"),
    ("/api/v1/learning/activity", "granularity"),
    ("/api/v1/learning/features", "schema_version"),
    ("/api/v1/career/summary", "has_profile"),
    ("/api/v1/career/features", "schema_version"),
)

#: ``(method, path, body)`` for every route that names a row that already exists.
#: ``None`` is a route with no payload; the PATCH carries one because an *empty*
#: edit is a 422 by contract, and this table is about tenancy, not edit
#: validation.
NAMED_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/v1/learning/goals/{goal_id}", None),
    ("PATCH", "/api/v1/learning/goals/{goal_id}", {"title": "renamed"}),
    ("DELETE", "/api/v1/learning/goals/{goal_id}", None),
    ("POST", "/api/v1/learning/goals/{goal_id}/complete", None),
    ("GET", "/api/v1/learning/skills/{skill_id}", None),
    ("PATCH", "/api/v1/learning/skills/{skill_id}", {"name": "renamed"}),
    ("DELETE", "/api/v1/learning/skills/{skill_id}", None),
    ("PATCH", "/api/v1/career/experience/{experience_id}", {"title": "corrected"}),
    ("DELETE", "/api/v1/career/experience/{experience_id}", None),
    ("PATCH", "/api/v1/career/evidence/{evidence_id}", {"title": "corrected"}),
    ("DELETE", "/api/v1/career/evidence/{evidence_id}", None),
)

#: The messages the services answer a foreign row with, and a row nobody ever
#: issued. Asserting both are identical is the point: a route that could separate
#: them is an existence oracle.
GOAL_NOT_FOUND_MESSAGE = "That learning goal was not found."
SKILL_NOT_FOUND_MESSAGE = "That skill was not found."
EXPERIENCE_NOT_FOUND_MESSAGE = "That career record was not found."
EVIDENCE_NOT_FOUND_MESSAGE = "That career evidence was not found."
PROJECT_NOT_FOUND_MESSAGE = "That project was not found."

#: A role the permission map has never heard of, used for the 403 case. An
#: ordinary account holds ``analytics.read``, so refusing a request has to be a
#: question about a role — and the map is fail-closed, so an unknown role is the
#: honest way to express "does not hold it".
ROLE_WITHOUT_ANALYTICS = "wizard"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    client: Any,
    method: str,
    template: str,
    headers: dict[str, str] | None = None,
    *,
    body: dict[str, Any] | None = None,
    **ids: uuid.UUID,
) -> Any:
    """Issue one request against a route template.

    The placeholders are filled with a fresh, never-issued uuid unless the caller
    names a real row, so the gate and tenancy tests reach a route exactly the way
    a caller with no such row would. ``body`` is forwarded so a PATCH reaches the
    same validation a caller's would.
    """
    path = template.format(
        goal_id=ids.get("goal_id", uuid.uuid4()),
        skill_id=ids.get("skill_id", uuid.uuid4()),
        experience_id=ids.get("experience_id", uuid.uuid4()),
        evidence_id=ids.get("evidence_id", uuid.uuid4()),
    )
    if body is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, headers=headers, json=body)


@pytest.fixture
async def account(client, db_session) -> dict[str, str]:
    """One signed-in account with nothing recorded anywhere."""
    _seed, headers = await seeded_client(client, db_session)
    return headers


@pytest.fixture
async def other(client, db_session) -> dict[str, str]:
    """A second signed-in account, for the tenancy tests."""
    _seed, headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    return headers


async def _create_goal(client: Any, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    """Create one goal through the real endpoint."""
    payload: dict[str, Any] = {"title": "Learn Rust"}
    payload.update(overrides)
    response = await client.post("/api/v1/learning/goals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_skill(client: Any, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    """Create one skill through the real endpoint."""
    payload: dict[str, Any] = {"name": "Rust"}
    payload.update(overrides)
    response = await client.post("/api/v1/learning/skills", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_record(client: Any, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    """Create one dated career record through the real endpoint."""
    payload: dict[str, Any] = {"kind": "experience", "title": "Engineer"}
    payload.update(overrides)
    response = await client.post("/api/v1/career/experience", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_evidence(
    client: Any, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    """Create one piece of career evidence through the real endpoint."""
    payload: dict[str, Any] = {
        "evidence_type": "achievement",
        "title": "Shipped the thing",
        "occurred_on": datetime.now(UTC).date().isoformat(),
    }
    payload.update(overrides)
    response = await client.post("/api/v1/career/evidence", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The gate: 401 anonymous, 403 without analytics.read, on every route
# ---------------------------------------------------------------------------

_ROUTE_CASES = [pytest.param(method, path, id=f"{method} {path}") for method, path in ROUTES]


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_an_anonymous_caller(
    method, template, client, assert_error_envelope
):
    """No Phase 9 route answers a caller who has not signed in.

    Authentication runs before the permission check, so "you are not signed in" is
    never reported as "you may not" — they are different answers to different
    questions, and a client acts on them differently.
    """
    response = await _request(client, method, template)

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_a_caller_without_analytics_read(
    method, template, client, db_session, account, assert_error_envelope
):
    """A valid, live session whose role grants nothing is answered 403.

    Phase 9 **reuses** ``analytics.read`` rather than coining a ``learning.write``
    or a ``career.write``, and this is the test that says the reuse is enforced on
    the writes and not only on the reads. A new ``Permission`` member would have
    been granted to exactly the roles ``analytics.read`` already covers, so it
    would be a second name for the same door — and one the permission test asserts
    the complete membership of.
    """
    await db_session.execute(
        update(User).where(User.email == "ada@nexus.test").values(role=ROLE_WITHOUT_ANALYTICS)
    )
    await db_session.commit()

    response = await _request(client, method, template, account)

    assert_error_envelope(response, status_code=403, code="forbidden")


# ---------------------------------------------------------------------------
# Route order: every literal sub-path answers its own question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "field"),
    [pytest.param(path, field, id=path) for path, field in LITERAL_SUB_PATHS],
)
async def test_every_literal_sub_path_answers_its_own_question(path, field, client, account):
    """Each literal sub-path resolves to its handler, not to a path parameter.

    A route declared below ``/learning/goals/{goal_id}`` would not disappear — it
    would bind ``summary`` to the path parameter, fail its uuid conversion and
    answer 422 with no body field at all. The assertion is therefore the presence
    of one field only the right handler emits.
    """
    response = await client.get(path, headers=account)

    assert response.status_code == 200, response.text
    payload = response.json()
    if isinstance(payload, dict):
        assert field in payload, response.text
    else:
        assert any(field in entry or field in entry.values() for entry in payload), response.text


async def test_career_profile_is_reachable_on_a_cold_account(client, account):
    """``GET /career/profile`` answers 200 with ``null`` before any profile exists.

    The profile is addressed by nothing the caller supplies, so a miss is a state
    to render rather than an error to report — and the server must not fill it
    with an invented row.
    """
    response = await client.get("/api/v1/career/profile", headers=account)

    assert response.status_code == 200, response.text
    assert response.json() is None


# ---------------------------------------------------------------------------
# Cold start: absence, not zero and not a finding
# ---------------------------------------------------------------------------


async def test_an_empty_learning_account_reports_an_absence(client, account):
    """A summary of zeroes is published with ``has_data: false`` beside them.

    Without the flag a dashboard of zeroes is a finding about the account, and the
    six numbers beside it are the evidence for a claim nobody made.
    """
    response = await client.get("/api/v1/learning/summary", headers=account)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_data"] is False
    assert body["goal_count"] == 0
    assert body["activity_count"] == 0
    assert body["minutes_in_window"] is None, "no duration recorded is not zero minutes"
    assert "No learning activity has been recorded" in body["summary"]


async def test_an_empty_career_account_reports_an_absence(client, account):
    """A career summary of zeroes carries ``has_data: false`` and says so in words."""
    response = await client.get("/api/v1/career/summary", headers=account)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_data"] is False
    assert body["has_profile"] is False
    assert body["evidence_count"] == 0
    assert "No career profile has been written" in body["summary"]


async def test_unmeasurable_features_are_null_never_zero(client, account):
    """Every feature that could not be computed is ``null``, not ``0``.

    An account with no goals does not have goals that are zero percent complete,
    an empty completion-rate denominator is not a rate of 0.0, and nothing recorded
    in the window is not a measured consistency of zero. Inside a training matrix a
    fabricated zero is indistinguishable from an observed one.
    """
    learning = await client.get("/api/v1/learning/features", headers=account)
    career = await client.get("/api/v1/career/features", headers=account)

    assert learning.status_code == 200, learning.text
    values = learning.json()["features"]
    assert learning.json()["schema_version"] == "learning_features.v1"
    assert values["goal_progress"] is None
    assert values["completion_rate"] is None
    assert values["learning_consistency"] is None
    assert values["goal_deadline_distance_days"] is None
    assert values["learning_minutes"] is None

    assert career.status_code == 200, career.text
    career_values = career.json()["features"]
    assert career.json()["schema_version"] == "career_features.v1"
    assert career_values["project_activity"] is None, (
        "no repository has been scanned, so the ratio was never computed"
    )
    assert career_values["repositories"] == 0, "a genuine count of zero survives"


async def test_an_empty_account_has_no_gaps_rather_than_fabricated_ones(client, account):
    """The gap page is empty on a cold account, with the split beside it at zero.

    A page of ``available: false`` rows would be inventing skills nobody tracked;
    an empty page with the honest counts is the answer.
    """
    response = await client.get("/api/v1/learning/gaps", headers=account)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["available_count"] == 0
    assert body["unavailable_count"] == 0


async def test_every_metric_is_present_and_in_one_of_the_two_honest_shapes(client, account):
    """``GET /learning/metrics`` always returns eight entries, each honestly shaped.

    A metric that was dropped rather than marked unavailable would leave a client
    indexing by ``key`` with a hole where a card belongs and no way to tell an
    absent card from an unmeasured one. What each entry must *not* do is publish a
    number beside a sentence saying there is none — so the pair is asserted rather
    than the flag alone.
    """
    response = await client.get("/api/v1/learning/metrics", headers=account)

    assert response.status_code == 200, response.text
    metrics = response.json()
    assert len(metrics) == 8
    for metric in metrics:
        if metric["available"]:
            assert metric["value"] is not None, metric
            assert metric["reason_if_unavailable"] is None, metric
        else:
            assert metric["value"] is None, metric
            assert metric["reason_if_unavailable"], metric

    by_key = {metric["key"]: metric for metric in metrics}
    assert by_key["sessions_last_7d"]["available"] is True, (
        "no session recorded in seven days is a measured zero, not an absence"
    )
    assert by_key["sessions_last_7d"]["value"] == 0.0
    assert by_key["goal_progress"]["available"] is False, (
        "an account with no goals does not have goals that are zero percent complete"
    )
    assert by_key["completion_rate"]["available"] is False


# ---------------------------------------------------------------------------
# Tenancy: a foreign row is 404, never 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in NAMED_ROUTES],
)
async def test_a_foreign_row_is_404_not_403_on_every_route_that_names_one(
    method, template, body, client, account, other, assert_error_envelope
):
    """Grace's rows, named by path, are refused exactly like a typo.

    The caller is Ada — authenticated and permitted, holding ``analytics.read`` —
    and still gets ``not_found``. The message for the foreign id is compared
    against the message for an id nobody ever issued, so the two cannot drift
    apart into an existence oracle.
    """
    goal = await _create_goal(client, other, title="Grace's goal")
    skill = await _create_skill(client, other, name="Grace's skill")
    record = await _create_record(client, other, title="Grace's job")
    evidence = await _create_evidence(client, other, title="Grace's achievement")
    ids_for_the_caller: dict[str, uuid.UUID] = {
        "goal_id": uuid.UUID(goal["id"]),
        "skill_id": uuid.UUID(skill["id"]),
        "experience_id": uuid.UUID(record["id"]),
        "evidence_id": uuid.UUID(evidence["id"]),
    }

    response = await _request(client, method, template, account, body=body, **ids_for_the_caller)

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert "Grace's" not in response.text

    unissued = await _request(client, method, template, account, body=body)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]


async def test_a_goal_naming_another_accounts_skill_is_404(
    client, account, other, assert_error_envelope
):
    """A ``target_skill_id`` from somebody else is refused **before** the write.

    Storing the pointer first and checking afterwards would leave a row pointing
    at another account's record and then report success — and a 403 would confirm
    the skill id exists.
    """
    skill = await _create_skill(client, other, name="Grace's skill")

    response = await client.post(
        "/api/v1/learning/goals",
        json={"title": "Learn from it", "target_skill_id": skill["id"]},
        headers=account,
    )

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == SKILL_NOT_FOUND_MESSAGE
    assert "Grace's skill" not in response.text

    unissued = await client.post(
        "/api/v1/learning/goals",
        json={"title": "Learn from it", "target_skill_id": str(uuid.uuid4())},
        headers=account,
    )
    assert_error_envelope(unissued, status_code=404, code="not_found")


async def test_evidence_naming_another_accounts_project_is_404(
    client, db_session, account, assert_error_envelope
):
    """A ``project_id`` from another account is refused, and nothing is written."""
    grace_seed, _grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    project = await grace_seed.project(name="Grace's project")

    response = await client.post(
        "/api/v1/career/evidence",
        json={
            "evidence_type": "project_completed",
            "title": "It was mine",
            "occurred_on": datetime.now(UTC).date().isoformat(),
            "project_id": str(project.id),
        },
        headers=account,
    )

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == PROJECT_NOT_FOUND_MESSAGE
    assert "Grace's project" not in response.text


async def test_a_goal_naming_another_accounts_goal_from_an_activity_is_404(
    client, account, other, assert_error_envelope
):
    """An activity may not count towards somebody else's goal either."""
    goal = await _create_goal(client, other, title="Grace's goal")

    response = await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "A quiet hour",
            "goal_id": goal["id"],
        },
        headers=account,
    )

    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == GOAL_NOT_FOUND_MESSAGE


# ---------------------------------------------------------------------------
# PATCH is a partial edit, never a replacement
# ---------------------------------------------------------------------------


async def test_renaming_a_goal_leaves_every_field_it_did_not_name(client, account):
    """A one-field PATCH clears nothing, because ``None`` means SQL NULL.

    Without ``exclude_unset=True`` the service receives a mapping of every field
    and writes NULL over the description, the project link and the target skill —
    and the response still looks like a successful rename.
    """
    created = await _create_goal(
        client,
        account,
        description="Because I want to",
        target_topic="systems",
        estimated_effort_minutes=600,
        progress=20,
    )

    response = await client.patch(
        f"/api/v1/learning/goals/{created['id']}", json={"title": "Learn Go"}, headers=account
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Learn Go"
    assert body["description"] == "Because I want to"
    assert body["target_topic"] == "systems"
    assert body["estimated_effort_minutes"] == 600
    assert body["progress"] == 20


async def test_editing_a_profile_headline_leaves_the_summary_alone(client, account):
    """The profile ``PUT`` is a partial edit too, for the same reason."""
    written = await client.put(
        "/api/v1/career/profile",
        json={"headline": "Backend engineer", "summary": "A paragraph in my own words."},
        headers=account,
    )
    assert written.status_code == 200, written.text

    response = await client.put(
        "/api/v1/career/profile", json={"headline": "Staff backend engineer"}, headers=account
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["headline"] == "Staff backend engineer"
    assert body["summary"] == "A paragraph in my own words."
    assert body["id"] == written.json()["id"], "a PUT revises the one profile, never a second"


async def test_an_explicit_null_does_clear_a_field(client, account):
    """The other half of the rule: a null the user *did* send is honoured."""
    created = await _create_goal(client, account, description="Because I want to")

    response = await client.patch(
        f"/api/v1/learning/goals/{created['id']}", json={"description": None}, headers=account
    )

    assert response.status_code == 200, response.text
    assert response.json()["description"] is None


async def test_an_empty_edit_is_a_422(client, account):
    """A PATCH that changes nothing is refused rather than answered with a no-op 200."""
    created = await _create_goal(client, account)

    response = await client.patch(
        f"/api/v1/learning/goals/{created['id']}", json={}, headers=account
    )

    assert response.status_code == 422, response.text


async def test_a_completed_at_cannot_be_written_by_a_patch(client, account):
    """``completed_at`` has exactly one producer, and it is not this route."""
    created = await _create_goal(client, account)

    response = await client.patch(
        f"/api/v1/learning/goals/{created['id']}",
        json={"completed_at": datetime.now(UTC).isoformat()},
        headers=account,
    )

    assert response.status_code == 422, response.text


async def test_a_skill_level_the_user_types_is_recorded_as_theirs(client, account):
    """A client cannot file its own inference as a self-assessment.

    ``level_source`` is what decides whether the sentence above a level may say
    "self-assessed" or "estimate", so it travels with the number and is written as
    ``user_defined`` for anything a client supplied.
    """
    created = await _create_skill(client, account, current_level=4, target_level=5)

    assert created["level_source"] == "user_defined"
    assert created["current_level"] == 4

    patched = await client.patch(
        f"/api/v1/learning/skills/{created['id']}",
        json={"current_level": 2},
        headers=account,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["level_source"] == "user_defined"


async def test_a_client_cannot_edit_a_skills_evidence_counters(client, account):
    """``evidence_count`` is NEXUS's own observation and is not client-writable.

    A skill that could claim six recorded study sessions that do not exist would
    have that claim quoted back as the evidence behind a level.
    """
    skill = await _create_skill(client, account)

    response = await client.patch(
        f"/api/v1/learning/skills/{skill['id']}",
        json={"evidence_count": 6, "last_activity_at": datetime.now(UTC).isoformat()},
        headers=account,
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Recording: the writes land and the counts follow
# ---------------------------------------------------------------------------


async def test_recording_an_activity_bumps_the_skill_evidence_and_not_its_level(client, account):
    """Evidence is collected; the level is asserted separately and labelled.

    The count moves and ``current_level`` does not, which is the whole of what
    this phase is allowed to claim about anybody.
    """
    skill = await _create_skill(client, account, current_level=2, target_level=5)

    recorded = await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "Read the ownership chapter",
            "skill_id": skill["id"],
            "duration_minutes": 45,
        },
        headers=account,
    )
    assert recorded.status_code == 201, recorded.text

    fetched = await client.get(f"/api/v1/learning/skills/{skill['id']}", headers=account)
    body = fetched.json()
    assert body["evidence_count"] == 1
    assert body["last_activity_at"] is not None
    assert body["current_level"] == 2, "a recorded session is not a competence claim"
    assert body["level_source"] == "user_defined"


async def test_the_gap_page_names_the_levels_and_the_evidence_count(client, account):
    """A gap sentence with no figure in it is the judgement this phase refuses.

    The validator that enforces it lives on the wire shape, so the assertion is
    made over HTTP where a client would render it.
    """
    skill = await _create_skill(client, account, current_level=2, target_level=4)
    await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "A quiet hour",
            "skill_id": skill["id"],
        },
        headers=account,
    )

    response = await client.get("/api/v1/learning/gaps", headers=account)

    assert response.status_code == 200, response.text
    gap = response.json()["items"][0]
    assert gap["gap"] == 2
    assert gap["level_source"] == "user_defined"
    assert "self-assessed" in gap["explanation"]
    assert any(character.isdigit() for character in gap["explanation"])


async def test_a_met_target_is_a_measured_zero_only_once_there_is_evidence(client, account):
    """``gap=0`` is a measurement only once something has been recorded against it.

    A skill sitting at its target with nothing behind it carries ``gap=0`` **and**
    ``available: false`` with a reason — the zero is real arithmetic, but nobody
    has looked. Recording one activity turns the same row into the measured zero,
    which is the most reassuring thing the page can say.
    """
    skill = await _create_skill(client, account, current_level=4, target_level=4)

    before = await client.get("/api/v1/learning/gaps", headers=account)
    body = before.json()
    assert body["total"] == 1
    assert body["available_count"] == 0
    assert body["unavailable_count"] == 1
    assert body["items"][0]["gap"] == 0
    assert body["items"][0]["available"] is False
    assert body["items"][0]["reason_if_unavailable"], body["items"][0]

    await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "A quiet hour",
            "skill_id": skill["id"],
        },
        headers=account,
    )

    after = await client.get("/api/v1/learning/gaps", headers=account)
    measured = after.json()
    assert measured["available_count"] == 1
    assert measured["unavailable_count"] == 0
    assert measured["items"][0]["gap"] == 0
    assert measured["items"][0]["available"] is True
    assert measured["items"][0]["reason_if_unavailable"] is None


async def test_completing_a_goal_stamps_it_and_raises_it_to_a_hundred(client, account):
    """Three things happen together, because they are one fact.

    The database's constraint holds the status and the stamp together, and a
    client doing them in two requests would leave a row claiming neither or both.
    """
    created = await _create_goal(client, account, progress=40)

    response = await client.post(
        f"/api/v1/learning/goals/{created['id']}/complete", headers=account
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None
    assert body["progress"] == 100


async def test_a_goal_may_name_a_topic_before_the_skill_exists(client, account):
    """A goal may name a topic before any skill row exists for it."""
    created = await _create_goal(client, account, target_topic="Rust")

    assert created["target_skill_id"] is None
    assert created["target_topic"] == "Rust"
    assert created["status"] == "not_started"
    assert created["progress"] == 0


async def test_a_duplicate_skill_name_is_a_conflict_not_a_crash(client, account):
    """``uq_skills_owner_name`` reaches the client as a sentence, not a constraint name."""
    await _create_skill(client, account, name="Rust")

    response = await client.post("/api/v1/learning/skills", json={"name": "Rust"}, headers=account)

    assert response.status_code == 409, response.text
    assert "already tracked" in response.json()["error"]["message"]


async def test_evidence_is_required_to_carry_the_date_the_user_gave(client, account):
    """Undated evidence cannot be placed in a timeline, so it is refused.

    The placeholder that would make it renderable would be a date nobody supplied.
    """
    response = await client.post(
        "/api/v1/career/evidence",
        json={"evidence_type": "achievement", "title": "Shipped it"},
        headers=account,
    )

    assert response.status_code == 422, response.text
    assert "date" in response.json()["error"]["message"]


async def test_a_career_record_may_not_end_before_it_began(client, account):
    """A backwards range is a typo, and is caught before the write."""
    response = await client.post(
        "/api/v1/career/experience",
        json={
            "kind": "experience",
            "title": "Engineer",
            "started_on": "2024-06-01",
            "ended_on": "2023-01-01",
        },
        headers=account,
    )

    assert response.status_code == 422, response.text
    assert "ended date" in response.json()["error"]["message"]


async def test_an_empty_profile_write_is_refused(client, account):
    """A profile created by an empty body is the one way to end up with one nobody asked for."""
    response = await client.put("/api/v1/career/profile", json={}, headers=account)

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Deletes answer 204 with no body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("goal", id="learning goal"),
        pytest.param("skill", id="learning skill"),
        pytest.param("experience", id="career record"),
        pytest.param("evidence", id="career evidence"),
    ],
)
async def test_every_delete_is_a_204_with_no_body(path, client, account, assert_error_envelope):
    """FastAPI forbids a body on a 204, so the route must not declare a model.

    A delete route that kept its ``response_model`` would answer with an error
    rather than with a delete, which is the cheapest way for a client to believe
    a row is gone when it is not.
    """
    makers = {
        "goal": _create_goal,
        "skill": _create_skill,
        "experience": _create_record,
        "evidence": _create_evidence,
    }
    routes = {
        "goal": "/api/v1/learning/goals/{identifier}",
        "skill": "/api/v1/learning/skills/{identifier}",
        "experience": "/api/v1/career/experience/{identifier}",
        "evidence": "/api/v1/career/evidence/{identifier}",
    }
    created = await makers[path](client, account)

    response = await client.delete(routes[path].format(identifier=created["id"]), headers=account)

    assert response.status_code == 204, response.text
    assert response.content == b""

    gone = await client.delete(routes[path].format(identifier=created["id"]), headers=account)
    assert_error_envelope(gone, status_code=404, code="not_found")


async def test_deleting_a_goal_keeps_the_recorded_evidence(client, account):
    """Deleting the intention must not delete the evidence.

    A skill's evidence count is a history, and a user who abandons a goal has not
    unlearned anything.
    """
    skill = await _create_skill(client, account)
    goal = await _create_goal(client, account, target_skill_id=skill["id"])
    await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "Worked on it",
            "skill_id": skill["id"],
            "goal_id": goal["id"],
        },
        headers=account,
    )

    deleted = await client.delete(f"/api/v1/learning/goals/{goal['id']}", headers=account)
    assert deleted.status_code == 204, deleted.text

    activities = await client.get(
        f"/api/v1/learning/activities?skill_id={skill['id']}", headers=account
    )
    assert activities.status_code == 200, activities.text
    body = activities.json()
    assert body["total"] == 1
    assert body["items"][0]["goal_id"] is None, "the trail outlives the goal"


async def test_deleting_a_skill_keeps_the_activities_recorded_against_it(client, account):
    """Deleting the subject must not delete the record of working on it.

    ``learning_activities.skill_id`` is ``ON DELETE SET NULL``, and the activity
    feed reads it exactly as it reads the goal pointer: the row survives the
    thing it points at and reports ``null`` there. Deleting a skill used to
    ``CASCADE``, which meant an edit to the skills table silently erased a
    person's record that they had studied — from another table, without a word.

    Both halves of the answer are asserted, because "shows nowhere" was the
    stated reason for the old rule and it is not true of this one:

    * The **account's** feed still lists the activity — ``total`` 1 — carrying its
      title and a null ``skill_id``. Nothing was destroyed.
    * The **skill-scoped** feed lists nothing, because there is no skill left to
      scope to. That is a narrowing to a skill that does not exist, not a
      deletion.
    """
    skill = await _create_skill(client, account)
    await client.post(
        "/api/v1/learning/activities",
        json={
            "activity_type": "study_session",
            "title": "Worked on it",
            "skill_id": skill["id"],
        },
        headers=account,
    )

    deleted = await client.delete(f"/api/v1/learning/skills/{skill['id']}", headers=account)
    assert deleted.status_code == 204, deleted.text

    activities = await client.get("/api/v1/learning/activities", headers=account)
    assert activities.status_code == 200, activities.text
    body = activities.json()
    assert body["total"] == 1, "the record outlives the skill it was recorded against"
    assert body["items"][0]["title"] == "Worked on it"
    assert body["items"][0]["skill_id"] is None

    scoped = await client.get(
        f"/api/v1/learning/activities?skill_id={skill['id']}", headers=account
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["total"] == 0, "a filter naming a deleted skill matches nothing"


# ---------------------------------------------------------------------------
# Pagination is a bound, not a silent truncation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/learning/goals",
        "/api/v1/learning/skills",
        "/api/v1/learning/activities",
        "/api/v1/learning/gaps",
        "/api/v1/career/experience",
        "/api/v1/career/evidence",
    ],
)
async def test_an_oversized_page_is_a_422_not_a_silent_clip(path, client, account):
    """A caller that asked for 500 must be told, or it cannot tell a clipped page.

    ``?limit=500`` returning 200 with 200 rows is indistinguishable from a
    repository that only ever held 200, and the pager built on top of it would
    quietly never offer a second page.
    """
    response = await client.get(f"{path}?limit=500", headers=account)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/learning/goals",
        "/api/v1/learning/skills",
        "/api/v1/learning/activities",
        "/api/v1/learning/gaps",
        "/api/v1/career/experience",
        "/api/v1/career/evidence",
    ],
)
async def test_a_page_limit_is_accepted_and_described_by_the_response(path, client, account):
    """A legal page size is honoured and echoed back, so the pager can trust it."""
    response = await client.get(f"{path}?limit=10&offset=0", headers=account)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 10
    assert body["offset"] == 0


# ---------------------------------------------------------------------------
# Window bounds
# ---------------------------------------------------------------------------


async def test_an_over_long_window_is_refused_rather_than_silently_shortened(client, account):
    """The returned ``window_days`` must not disagree with what the caller asked for."""
    response = await client.get("/api/v1/learning/summary?window_days=5000", headers=account)

    assert response.status_code == 422, response.text
    assert "days" in response.json()["error"]["message"]


async def test_the_same_window_bound_applies_to_the_career_page(client, account):
    """Both pages share one window vocabulary rather than declaring two ceilings."""
    response = await client.get("/api/v1/career/summary?window_days=5000", headers=account)

    assert response.status_code == 422, response.text


async def test_the_activity_series_is_dense_and_refuses_an_unknown_granularity(client, account):
    """A quiet day arrives with a zero rather than being skipped.

    A series that omitted empty buckets would make a sparse fortnight read as
    dense as a busy one, and returning a chart nobody asked for is worse than a
    422.
    """
    response = await client.get("/api/v1/learning/activity?window_days=7", headers=account)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granularity"] == "day"
    buckets = body["buckets"]
    assert len(buckets) >= 7, buckets
    assert all(bucket["activities"] == 0 for bucket in buckets), (
        "a quiet day arrives carrying a zero rather than being skipped"
    )
    for earlier, later in pairwise(buckets):
        assert earlier["bucket_end"] == later["bucket_start"], (
            "consecutive buckets are exactly adjacent, which is what makes the series dense"
        )
    assert body["total_minutes"] is None, "no duration recorded is not zero minutes"

    refused = await client.get("/api/v1/learning/activity?granularity=fortnight", headers=account)
    assert refused.status_code == 422, refused.text
