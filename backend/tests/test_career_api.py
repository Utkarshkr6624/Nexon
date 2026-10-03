"""The Phase 9 career HTTP surface end to end: eleven routes, one router.

Every route here is exercised through HTTP with a real bearer token, and almost
every assertion is about what must **not** happen rather than about what a
response contains. The career page is the one place in NEXUS where a fabricated
row would be read as a fact about a person, so the properties below are the
phase's, not the module's.

* **Nothing on this surface is invented.** A certification, an employer, a date
  and a title are all transcriptions of something the user typed. The tests
  assert that round trip verbatim — a field nobody supplied comes back ``null``
  rather than filled in with a plausible value — and then sweep every response for
  the sentences a generator would have written.

* **A foreign record is 404, never 403, on every route that names one.** The
  responses for a foreign id and for one nobody ever issued carry the same
  message, so the two cannot drift apart into an existence oracle.

* **``PUT /career/profile`` is an upsert, not an accumulation.** ``user_id`` is
  unique on the table, so a second ``PUT`` revises the row rather than producing a
  second profile — which would be a second *answer* to every question about this
  person's career, and the read would then have to decide which one wins.

* **A cold-start profile is a 200 empty state, not a 404.** The profile is not
  addressed by anything, so there is no id for a miss to be about; an account that
  has never written one is in a state the UI has to render. ``get_profile``
  returns ``None`` precisely so that a generated profile — the first career row
  NEXUS ever wrote — cannot be the answer.

* **A duplicate evidence row is a 409, not a 500.** ``uq_career_evidence_source_identity``
  is the whole deduplication mechanism, and the sentence the service raises when
  the constraint catches a race is the one a person can act on. Letting
  ``IntegrityError`` reach the handler would put a stack trace on someone's
  profile page.

* **A page size outside the documented bounds is a 422**, because a caller that
  asked for 500 and received 200 cannot tell a truncated page from a page that was
  always 200 rows long.

Every test requires a live PostgreSQL. They are marked ``integration`` for that
reason.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.models.activity import ActivityLog
from app.models.career import CareerProfile
from app.models.developer import GitRepository
from app.models.user import User
from app.services.career.service import (
    _DATES_OUT_OF_ORDER,
    _DUPLICATE_EVIDENCE,
    _EVIDENCE_NOT_FOUND,
    _EXPERIENCE_NOT_FOUND,
)
from tests.analytics_fixtures import AnalyticsSeed, seeded_client

pytestmark = pytest.mark.integration

#: The eleven Phase 9 career routes as ``(method, path)``, in the order the
#: contract fixes them. The gate tests walk this table rather than restating a
#: list, so a route added to the router without being added here shows up as a
#: route nobody proved is protected.
ROUTES: tuple[tuple[str, str], ...] = (
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
    # §6 of the contract names ``GET /career/features`` beside its learning
    # counterpart; §5's table omits it. It exists, so it is gated here — the
    # alternative would be a route nothing proved is protected.
    ("GET", "/api/v1/career/features"),
)

#: ``(method, path, body)`` for every route that names a dated record as a *path*
#: segment. ``None`` is a route with no payload; the PATCH carries one because an
#: *empty* edit is a 422 by contract, and this table is about tenancy rather than
#: about edit validation.
EXPERIENCE_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("PATCH", "/api/v1/career/experience/{experience_id}", {"title": "corrected"}),
    ("DELETE", "/api/v1/career/experience/{experience_id}", None),
)

#: The same for evidence.
EVIDENCE_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("PATCH", "/api/v1/career/evidence/{evidence_id}", {"title": "corrected"}),
    ("DELETE", "/api/v1/career/evidence/{evidence_id}", None),
)

#: The routes whose ``?limit`` the contract bounds.
PAGED_ROUTES: tuple[str, ...] = (
    "/api/v1/career/experience",
    "/api/v1/career/evidence",
)

#: The messages the service answers a row that is not the caller's with. They are
#: imported from the service rather than retyped here so a rename in the copy
#: shows up as a failure to update the test rather than as two sentences that
#: drifted apart while both still looked right.
EXPERIENCE_NOT_FOUND_MESSAGE = _EXPERIENCE_NOT_FOUND
EVIDENCE_NOT_FOUND_MESSAGE = _EVIDENCE_NOT_FOUND
DUPLICATE_EVIDENCE_MESSAGE = _DUPLICATE_EVIDENCE
DATES_OUT_OF_ORDER_MESSAGE = _DATES_OUT_OF_ORDER

#: A role the permission map has never heard of, used for the 403 case. An
#: ordinary account holds ``analytics.read``, so refusing a request has to be a
#: question about a role — and the map is fail-closed, so an unknown role is the
#: honest way to express "does not hold it".
ROLE_WITHOUT_ANALYTICS = "wizard"

#: Phrases a generator would reach for. None of them can be a legitimate column
#: value or an enum member on this surface — ``career_evidence.evidence_type`` has
#: a ``certification`` member, which is why the forbidden list carries *certified*
#: rather than *certification* — so a hit here is a fabricated sentence and not a
#: vocabulary accident.
FORBIDDEN_QUALIFICATION_PHRASES = (
    "certified",
    "employer",
    "employed by",
    "graduated from",
    "awarded",
    "salary",
    "employable",
    "employability",
    "job offer",
    "interview",
    "candidate",
    "qualified",
    "recognised by",
    "recognized by",
    "references available",
)

#: The grammatical shape a verdict about a person would take, which a word list
#: cannot express. Matched against the whole flattened payload, keys included.
FORBIDDEN_CLAIM_SENTENCE = re.compile(
    r"\byou(?:'re| are| were| was)\s+(?:not\s+|never\s+)?"
    r"(?:a\s+|an\s+)?(?:strong|weak|excellent|ideal|qualified|perfect)\b",
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
    experience_id: uuid.UUID | None = None,
    evidence_id: uuid.UUID | None = None,
    **kwargs: Any,
):
    """Issue one request against a route template.

    The placeholders are filled with a fresh, never-issued uuid unless the caller
    names a real row, so the gate and tenancy tests reach a route exactly the way
    a caller with no such row would.
    """
    path = template.format(
        experience_id=experience_id or uuid.uuid4(),
        evidence_id=evidence_id or uuid.uuid4(),
    )
    return client.request(method, path, headers=headers, **kwargs)


async def _repository(session: Any, user_id: uuid.UUID, name: str) -> uuid.UUID:
    """One registered work tree, written straight through the ORM.

    The Phase 8 registration route needs a real ``.git`` on disk and a system git
    binary, neither of which this assertion is about; what matters is that a
    ``repository_id`` exists and belongs to the caller. The row is created
    directly so the deduplication fixture does not inherit a dependency on the
    scan engine to prove something about a unique constraint.
    """
    row = GitRepository(
        user_id=user_id,
        name=name,
        local_path=f"C:/nexus-fixtures/{name}",
        is_active=True,
    )
    session.add(row)
    await session.commit()
    return row.id


async def _record_experience(client: Any, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    """Record one dated line through the real endpoint."""
    payload: dict[str, Any] = {"kind": "education", "title": "BSc Computer Science", **extra}
    response = await client.post("/api/v1/career/experience", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _record_evidence(client: Any, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    """Enter one piece of evidence through the real endpoint."""
    payload: dict[str, Any] = {
        "evidence_type": "achievement",
        "title": "Shipped the billing service",
        **extra,
    }
    response = await client.post("/api/v1/career/evidence", json=payload, headers=headers)
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


def _strings(value: Any):
    """Yield every string anywhere inside a decoded JSON payload, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _flatten(payload: Any) -> str:
    """The payload as one lowercase string, so a word search sees all of it."""
    return " ".join(_strings(payload)).lower()


@pytest.fixture
async def account(client, db_session) -> tuple[AnalyticsSeed, dict[str, str]]:
    """One signed-in account with nothing recorded, as ``(seed, headers)``."""
    seed, headers = await seeded_client(client, db_session)
    return seed, headers


# ---------------------------------------------------------------------------
# The gate: 401 anonymous, 403 without analytics.read, on all eleven routes
# ---------------------------------------------------------------------------

_ROUTE_CASES = [pytest.param(method, path, id=f"{method} {path}") for method, path in ROUTES]


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_an_anonymous_caller(
    method, template, client, assert_error_envelope
):
    """No Phase 9 career route answers a caller who has not signed in.

    Authentication runs before the permission check, so "you are not signed in" is
    never reported as "you may not" — they are different answers to different
    questions, and a client acts on them differently. The ``PUT``, ``POST`` and
    ``DELETE`` cases carry no body at all, which is the point: the refusal must
    come from the dependency rather than from a request schema that happens to be
    satisfied.
    """
    response = await _request(client, method, template)

    error = assert_error_envelope(response, status_code=401, code="unauthorized")
    assert error["details"] is None


@pytest.mark.parametrize(("method", "template"), _ROUTE_CASES)
async def test_every_route_refuses_a_caller_without_analytics_read(
    method, template, client, db_session, account, assert_error_envelope
):
    """A valid, live session whose role grants nothing is answered 403.

    Phase 9 **reuses** ``analytics.read`` rather than coining a ``career.write``,
    and this is the test that says the reuse is enforced on the four writes and not
    only on the seven reads. A new ``Permission`` member would have been granted to
    exactly the roles ``analytics.read`` already covers, so it would be a second
    name for the same door — and ``tests/test_permissions.py`` asserts the full
    member set.
    """
    seed, headers = account
    await db_session.execute(
        update(User).where(User.id == seed.owner.id).values(role=ROLE_WITHOUT_ANALYTICS)
    )
    await db_session.commit()

    response = await _request(client, method, template, headers=headers)

    assert_error_envelope(response, status_code=403, code="forbidden")


# ---------------------------------------------------------------------------
# The routing hazard: /career/summary against /career/{id}
# ---------------------------------------------------------------------------


async def test_the_literal_sub_paths_answer_their_own_questions(client, db_session, account):
    """``/career/summary`` answers with counts, and the lists answer with pages.

    Starlette matches routes in declaration order and does not prefer a literal
    segment over a parameter. Were ``/career/profile`` — or any other single
    segment — registered above a parameterised route, the literal word would bind
    to the path parameter instead and the dashboard header would answer a
    different question while still returning a 200. Each assertion pins a key
    **only** that handler emits.
    """
    _seed, headers = account

    summary = await client.get("/api/v1/career/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert {"has_profile", "record_count", "evidence_count"} <= set(summary.json()), summary.json()

    profile = await client.get("/api/v1/career/profile", headers=headers)
    assert profile.status_code == 200, profile.text

    experience = await client.get("/api/v1/career/experience", headers=headers)
    assert experience.status_code == 200, experience.text
    assert {"items", "by_kind", "current_count"} <= set(experience.json()), experience.text

    evidence = await client.get("/api/v1/career/evidence", headers=headers)
    assert evidence.status_code == 200, evidence.text
    assert {"items", "by_type", "manual_count"} <= set(evidence.json()), evidence.text


# ---------------------------------------------------------------------------
# The cold start: an empty state to render, never a 404
# ---------------------------------------------------------------------------


async def test_an_account_with_no_career_data_gets_zeroes_and_an_empty_profile(
    client, db_session, account
):
    """Every count is zero, ``has_profile`` is false, and nothing is a 404.

    Zero is a real count here: ``evidence_count == 0`` means "you have entered no
    evidence yet", which is a true and slightly uncomfortable fact about the
    account rather than a missing measurement. The cold-start ``GET
    /career/profile`` is the load-bearing assertion — the profile is not addressed
    by anything, so there is no id for a miss to be about, and a 404 there would
    tell a new user their page is broken.
    """
    _seed, headers = account

    profile = await client.get("/api/v1/career/profile", headers=headers)
    assert profile.status_code == 200, profile.text
    # ``null``, not an empty object and emphatically not a 404. The service is
    # never asked to fill the gap: an empty profile invented here would be the
    # first career row NEXUS ever wrote.
    assert profile.json() is None, profile.text

    summary = (await client.get("/api/v1/career/summary", headers=headers)).json()
    assert summary["has_profile"] is False, summary
    assert summary["has_data"] is False, summary
    assert summary["record_count"] == 0, summary
    assert summary["evidence_count"] == 0, summary
    assert summary["manual_evidence_count"] == 0, summary
    assert summary["target_role"] is None, summary
    assert summary["latest_evidence_on"] is None, summary
    # The sentence explains the absence rather than rendering zeroes as a finding.
    assert summary["summary"], summary

    records = (await client.get("/api/v1/career/experience", headers=headers)).json()
    assert records["items"] == [] and records["total"] == 0, records
    assert records["current_count"] == 0, records

    evidence = (await client.get("/api/v1/career/evidence", headers=headers)).json()
    assert evidence["items"] == [] and evidence["total"] == 0, evidence
    assert evidence["manual_count"] == 0, evidence


# ---------------------------------------------------------------------------
# The profile: an upsert, and a transcription of what the user wrote
# ---------------------------------------------------------------------------


async def test_putting_the_profile_twice_leaves_exactly_one_row(client, db_session, account):
    """A second ``PUT`` revises the row; it does not accumulate a second profile.

    Counted through the ORM rather than through a response, because the property
    is about storage: ``career_profiles.user_id`` is unique, and two profiles would
    be a second *answer* to every question about this person's career, with the
    read then having to decide which one wins. The id is asserted stable as well,
    because a second row with a fresh id would be invisible to a client that
    cached the first one.
    """
    _seed, headers = account

    first = await client.put(
        "/api/v1/career/profile",
        json={"target_role": "Senior Backend Engineer", "headline": "Ada"},
        headers=headers,
    )
    assert first.status_code in {200, 201}, first.text

    second = await client.put(
        "/api/v1/career/profile",
        json={"target_role": "Senior Backend Engineer", "location": "Remote (EU)"},
        headers=headers,
    )
    assert second.status_code in {200, 201}, second.text

    result = await db_session.execute(select(func.count()).select_from(CareerProfile))
    assert result.scalar_one() == 1
    assert second.json()["id"] == first.json()["id"], (first.json(), second.json())


async def test_the_profile_round_trip_is_verbatim_and_invents_nothing(client, db_session, account):
    """Every word comes back as it was typed, and every unwritten column stays null.

    ``exclude_unset`` on the write is what keeps the second ``PUT`` above from
    clearing a headline the user never mentioned, and ``links`` is exactly the
    array they supplied, in the order they listed it. NEXUS does not fetch, check
    or add a URL, and it does not parse, geocode or normalise a location.
    """
    _seed, headers = account

    response = await client.put(
        "/api/v1/career/profile",
        json={
            "target_role": "Senior Backend Engineer",
            "target_domain": "backend",
            "headline": "Backend engineer who likes boring databases",
            "summary": "A decade of keeping other people's pages up.",
            "location": "Remote (EU)",
            "links": ["https://example.invalid/ada", "https://example.invalid/ada/cv"],
        },
        headers=headers,
    )

    assert response.status_code in {200, 201}, response.text
    body = response.json()
    assert body["target_role"] == "Senior Backend Engineer", body
    assert body["target_domain"] == "backend", body
    assert body["headline"] == "Backend engineer who likes boring databases", body
    assert body["summary"] == "A decade of keeping other people's pages up.", body
    assert body["location"] == "Remote (EU)", body
    assert body["links"] == [
        "https://example.invalid/ada",
        "https://example.invalid/ada/cv",
    ], body


async def test_a_second_profile_put_does_not_clear_what_it_did_not_name(
    client, db_session, account
):
    """An omitted field leaves the column alone; only an explicit null clears it.

    The route builds its mapping with ``model_dump(exclude_unset=True)``, because
    ``None`` means "write SQL NULL" downstream. Without it a one-field revision
    would silently discard everything else, and the response would still look
    entirely correct.
    """
    _seed, headers = account
    await client.put(
        "/api/v1/career/profile",
        json={"headline": "Ada", "location": "Remote (EU)"},
        headers=headers,
    )

    revised = await client.put(
        "/api/v1/career/profile",
        json={"target_role": "Senior Backend Engineer"},
        headers=headers,
    )

    assert revised.status_code in {200, 201}, revised.text
    body = revised.json()
    assert body["target_role"] == "Senior Backend Engineer", body
    assert body["headline"] == "Ada", body
    assert body["location"] == "Remote (EU)", body


# ---------------------------------------------------------------------------
# Dated records: dates the user gave, in the order they gave them
# ---------------------------------------------------------------------------


async def test_an_end_date_before_the_start_is_422(
    client, db_session, account, assert_error_envelope
):
    """A record that ends before it starts is refused, not stored and re-read.

    The database holds a check constraint for this, so an insert that got past the
    edge would answer 500 rather than 422 — a stack trace on someone's profile
    because they mistyped a date. The refusal has to happen before the write.
    """
    _seed, headers = account
    today = date.today()

    response = await client.post(
        "/api/v1/career/experience",
        json={
            "kind": "experience",
            "title": "Senior Backend Engineer",
            "started_on": (today - timedelta(days=400)).isoformat(),
            "ended_on": (today - timedelta(days=800)).isoformat(),
        },
        headers=headers,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert DATES_OUT_OF_ORDER_MESSAGE in error["message"], error

    listed = (await client.get("/api/v1/career/experience", headers=headers)).json()
    assert listed["total"] == 0, listed
    assert listed["items"] == [], listed


async def test_an_undated_record_is_a_legitimate_record(client, db_session, account):
    """``started_on`` and ``ended_on`` null is how "ongoing" and "undated" are said.

    Requiring either date would exclude the self-directed course and the open-ended
    degree, which are both common and both true. What is asserted here is that a
    null comes back as ``null`` rather than as a placeholder — a fabricated date is
    the one thing this table must never contain.
    """
    _seed, headers = account

    created = await _record_experience(
        client, headers, kind="experience", title="Senior Backend Engineer"
    )

    assert created["started_on"] is None, created
    assert created["ended_on"] is None, created
    assert created["organisation"] is None, created
    assert created["url"] is None, created


async def test_the_record_tally_covers_every_matching_row_not_the_page(client, db_session, account):
    """``by_kind`` is complete and counts the whole set, not the slice on screen.

    ``career_experience`` shares one table across education, experience and
    certification, so the tally is the only thing separating the three sections of
    a timeline. A key that disappeared because the band was empty would make a page
    report no certifications when the truth was zero.
    """
    _seed, headers = account
    await _record_experience(client, headers, kind="education")
    await _record_experience(client, headers, kind="experience", title="Senior Engineer")
    await _record_experience(client, headers, kind="certification", title="CompTIA Serverless")

    page = (await client.get("/api/v1/career/experience?limit=1&offset=0", headers=headers)).json()

    assert len(page["items"]) == 1, page
    assert page["total"] == 3, page
    assert page["by_kind"] == {"education": 1, "experience": 1, "certification": 1}, page
    assert page["current_count"] == 3, page


# ---------------------------------------------------------------------------
# Evidence: a claim with a provenance
# ---------------------------------------------------------------------------


async def test_a_duplicate_evidence_row_is_a_409_not_a_500(
    client, db_session, account, assert_error_envelope
):
    """The unique constraint answers in a sentence, not in a stack trace.

    ``uq_career_evidence_source_identity`` is the entire deduplication mechanism:
    ``(user_id, evidence_type, source, project_id, skill_id, repository_id)``. Its
    identity is only complete when **all three** pointers are present — nulls do
    not collide in a btree unique index, which is exactly why several manual
    achievements may coexist — so the fixture links a project, a skill and a
    repository and posts the same body twice. Letting ``IntegrityError`` reach the
    handler instead would put a 500 on someone's profile page for the most ordinary
    mistake available, and the test's second half proves the refusal left exactly
    one row rather than two.
    """
    seed, headers = account
    project = await seed.project(name="atlas")
    skill = await client.post(
        "/api/v1/learning/skills", json={"name": "Replication"}, headers=headers
    )
    assert skill.status_code == 201, skill.text
    repository = await _repository(db_session, seed.owner.id, "ada-widget")
    payload = {
        "evidence_type": "project_completed",
        "title": "Shipping the billing service",
        "occurred_on": date.today().isoformat(),
        "project_id": str(project.id),
        "skill_id": skill.json()["id"],
        "repository_id": str(repository),
    }

    first = await client.post("/api/v1/career/evidence", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    duplicate = await client.post("/api/v1/career/evidence", json=payload, headers=headers)

    error = assert_error_envelope(duplicate, status_code=409, code="conflict")
    assert error["message"] == DUPLICATE_EVIDENCE_MESSAGE, error

    listed = (await client.get("/api/v1/career/evidence", headers=headers)).json()
    assert listed["total"] == 1, listed
    added = await _events(db_session, seed.owner.id, "career_evidence_added")
    assert len(added) == 1, added


async def test_two_hand_written_achievements_coexist(client, db_session, account):
    """Nulls do not collide, so a user may file as many manual rows as they like.

    The counterpart to the duplicate test: deduplication exists so a *derived* row
    cannot be inserted twice, not so a person cannot record two achievements they
    genuinely did. Refusing the second manual row would be the deduplication
    constraint eating a legitimate record.
    """
    _seed, headers = account
    today = date.today().isoformat()

    first = await _record_evidence(
        client, headers, occurred_on=today, title="Shipped the billing service"
    )
    second = await _record_evidence(
        client, headers, occurred_on=today, title="Shipped the notification service"
    )

    assert first["id"] != second["id"], (first, second)
    assert first["source"] == "manual", first
    listed = (await client.get("/api/v1/career/evidence", headers=headers)).json()
    assert listed["total"] == 2, listed
    assert listed["manual_count"] == 2, listed


async def test_evidence_without_a_date_is_refused_rather_than_dated_for_you(
    client, db_session, account, assert_error_envelope
):
    """Undated evidence cannot be placed in a timeline, and a placeholder is a lie.

    Every other word on this surface is optional; this one is not. A row stored
    without a day would render as a fabricated one, which is the single worst thing
    this module could do.
    """
    _seed, headers = account

    response = await client.post(
        "/api/v1/career/evidence",
        json={"evidence_type": "achievement", "title": "Shipped the billing service"},
        headers=headers,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert error["message"], error
    listed = (await client.get("/api/v1/career/evidence", headers=headers)).json()
    assert listed["total"] == 0, listed


async def test_a_client_cannot_re_point_an_evidence_row_at_another_project(
    client, db_session, account, assert_error_envelope
):
    """``project_id`` is absent from the edit schema, and sending it is a 422.

    The three ``*_id`` pointers are part of the row's identity and of the
    constraint that keeps derived evidence from being inserted twice. A client that
    could re-point one could make a rename become a second record — which is the
    exact failure the constraint exists to prevent. The 422 names the field rather
    than dropping it silently.
    """
    _seed, headers = account
    evidence = await _record_evidence(client, headers, occurred_on=date.today().isoformat())

    response = await client.patch(
        f"/api/v1/career/evidence/{evidence['id']}",
        json={"title": "corrected", "project_id": str(uuid.uuid4())},
        headers=headers,
    )

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    # The envelope's ``details`` carries the offending name — not the message, which
    # is generic — and the name is the whole point: a 422 that did not say which
    # field was refused is a 422 the client cannot act on.
    fields = {item["field"] for item in error["details"]["errors"]}
    assert "project_id" in fields, error

    unchanged = (await client.get("/api/v1/career/evidence", headers=headers)).json()["items"][0]
    assert unchanged["title"] == evidence["title"], unchanged


async def test_an_unset_evidence_field_is_not_a_cleared_field(client, db_session, account):
    """Correcting the wording leaves the description and the date alone.

    ``exclude_unset`` on the write is what makes the difference between "the person
    is allowed to be wrong about what they wrote" and "a one-word correction
    silently discards their notes and moves the record on their timeline".
    """
    _seed, headers = account
    evidence = await _record_evidence(
        client,
        headers,
        occurred_on=date.today().isoformat(),
        description="Wrote the migration and the rollback path.",
    )

    response = await client.patch(
        f"/api/v1/career/evidence/{evidence['id']}",
        json={"title": "Shipped the billing service"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Shipped the billing service", body
    assert body["description"] == "Wrote the migration and the rollback path.", body
    assert body["occurred_on"] == evidence["occurred_on"], body


# ---------------------------------------------------------------------------
# Tenancy: 404, never 403, and never an existence oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in EXPERIENCE_ROUTES],
)
async def test_a_foreign_record_is_404_not_403_on_every_route(
    method, template, body, client, db_session, assert_error_envelope
):
    """Grace's dated record, named by path, is refused like a typo.

    The caller is authenticated and permitted — the token is Ada's, and Ada holds
    ``analytics.read`` — and still gets ``not_found``. ``DELETE`` is the
    interesting one: a router that resolved the row without scoping it would delete
    another account's record and answer 204, which is the failure this test exists
    to catch and the only one the user cannot undo.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    _grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign = await _record_experience(client, grace_headers, title="BSc Computer Science")

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    response = await _request(
        client, method, template, ada_headers, experience_id=uuid.UUID(foreign["id"]), **kwargs
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == EXPERIENCE_NOT_FOUND_MESSAGE
    assert "BSc Computer Science" not in response.text

    unissued = await _request(client, method, template, ada_headers, **kwargs)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]

    # A refused delete deleted nothing.
    survivors = (await client.get("/api/v1/career/experience", headers=grace_headers)).json()
    assert [item["id"] for item in survivors["items"]] == [foreign["id"]], survivors


@pytest.mark.parametrize(
    ("method", "template", "body"),
    [pytest.param(*case, id=f"{case[0]} {case[1]}") for case in EVIDENCE_ROUTES],
)
async def test_a_foreign_evidence_row_is_404_not_403_on_every_route(
    method, template, body, client, db_session, assert_error_envelope
):
    """Grace's evidence is a 404 on the edit and the delete alike.

    Evidence is the record a person keeps so they can point at it. Answering 403
    for somebody else's row would confirm the id exists, and answering 204 to an
    unscoped delete would destroy the one thing the table is for.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    _grace_seed, grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    foreign = await _record_evidence(client, grace_headers, occurred_on=date.today().isoformat())

    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    response = await _request(
        client, method, template, ada_headers, evidence_id=uuid.UUID(foreign["id"]), **kwargs
    )
    error = assert_error_envelope(response, status_code=404, code="not_found")
    assert error["message"] == EVIDENCE_NOT_FOUND_MESSAGE
    assert "Shipped the billing service" not in response.text

    unissued = await _request(client, method, template, ada_headers, **kwargs)
    unissued_error = assert_error_envelope(unissued, status_code=404, code="not_found")
    assert unissued_error["message"] == error["message"]

    survivors = (await client.get("/api/v1/career/evidence", headers=grace_headers)).json()
    assert [item["id"] for item in survivors["items"]] == [foreign["id"]], survivors


async def test_another_accounts_project_is_404_on_an_evidence_write(
    client, db_session, assert_error_envelope
):
    """A ``project_id`` in the body is scoped exactly as one in the path.

    The route carries no id in the URL, so the ownership check is the only thing
    standing between one account's evidence and another's project history — and a
    403 here would confirm the project id is real.
    """
    _ada_seed, ada_headers = await seeded_client(client, db_session)
    grace_seed, _grace_headers = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace_seed.project(name="Grace's project")

    response = await client.post(
        "/api/v1/career/evidence",
        json={
            "evidence_type": "project_completed",
            "title": "Shipped the billing service",
            "occurred_on": date.today().isoformat(),
            "project_id": str(grace_project.id),
        },
        headers=ada_headers,
    )

    assert_error_envelope(response, status_code=404, code="not_found")
    assert "Grace's project" not in response.text
    listed = (await client.get("/api/v1/career/evidence", headers=ada_headers)).json()
    assert listed["total"] == 0, listed


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


async def test_the_evidence_list_pages_without_losing_or_repeating_a_row(
    client, db_session, account
):
    """Two rows over two pages come back as two distinct rows, with one total.

    The ``total`` is read on screen beside the rows, so a header written against a
    page slice would quote a number describing the slice. Both pages must agree on
    it, and the two pages must not overlap.
    """
    _seed, headers = account
    today = date.today().isoformat()
    first = await _record_evidence(client, headers, occurred_on=today)
    second = await _record_evidence(
        client, headers, occurred_on=today, title="Shipped the notification service"
    )

    page_one = (
        await client.get("/api/v1/career/evidence?limit=1&offset=0", headers=headers)
    ).json()
    page_two = (
        await client.get("/api/v1/career/evidence?limit=1&offset=1", headers=headers)
    ).json()

    assert page_one["total"] == page_two["total"] == 2
    assert page_one["limit"] == page_two["limit"] == 1
    assert len(page_one["items"]) == len(page_two["items"]) == 1
    assert {page_one["items"][0]["id"], page_two["items"][0]["id"]} == {
        first["id"],
        second["id"],
    }


# ---------------------------------------------------------------------------
# Nothing here invents a qualification
# ---------------------------------------------------------------------------

#: The paths the sweep walks. Every read the career page issues plus the two rows
#: it addresses by id, so a sentence invented in a handler nobody covered cannot
#: slip through.
SWEEP_PATHS: tuple[str, ...] = (
    "/api/v1/career/summary",
    "/api/v1/career/profile",
    "/api/v1/career/experience",
    "/api/v1/career/evidence",
)


async def test_no_response_invents_a_qualification_the_caller_did_not_supply(
    client, db_session, account
):
    """Every sentence on this surface is either the user's or a count of their rows.

    A profile is entirely user-controlled. NEXUS never writes a certification, an
    employer or a date the user did not supply, so the sweep is over a page the
    caller filled in themselves and looks for the sentences a generator would have
    written: *certified by*, *employed by*, *awarded*, a salary, a verdict on
    employability. The vocabulary list carries **certified** rather than
    **certification** deliberately, because ``certification`` is a legitimate
    ``evidence_type`` and a forbidden-word list that included it would fail this
    test on the enum and send someone hunting a bug that is not there.
    """
    _seed, headers = account
    await client.put(
        "/api/v1/career/profile",
        json={
            "target_role": "Senior Backend Engineer",
            "headline": "Backend engineer who likes boring databases",
            "summary": "A decade of keeping other people's pages up.",
            "location": "Remote (EU)",
            "links": ["https://example.invalid/ada"],
        },
        headers=headers,
    )
    record = await _record_experience(
        client,
        headers,
        kind="certification",
        title="CompTIA Serverless",
        organisation="University of Testing",
        started_on="2022-01-10",
    )
    evidence = await _record_evidence(client, headers, occurred_on=date.today().isoformat())

    bodies: list[tuple[str, Any]] = []
    for path in SWEEP_PATHS:
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        bodies.append((path, response.json()))

    offenders: list[str] = []
    for path, payload in bodies:
        text = _flatten(payload)
        for phrase in FORBIDDEN_QUALIFICATION_PHRASES:
            if phrase in text:
                offenders.append(f"{path}: {phrase}")
        if FORBIDDEN_CLAIM_SENTENCE.search(text):
            offenders.append(f"{path}: {FORBIDDEN_CLAIM_SENTENCE.pattern}")
    assert offenders == [], offenders

    # And the half that is not a word search: the record the user typed comes back
    # exactly as they typed it, and the columns they left empty are still empty.
    # Pulled out of the list page rather than from a detail route, because the
    # contract's §5 table gives ``/career/experience/{id}`` and
    # ``/career/evidence/{id}`` only ``PATCH`` and ``DELETE``.
    reread = next(item for item in bodies[2][1]["items"] if item["id"] == record["id"])
    assert reread["title"] == "CompTIA Serverless", reread
    assert reread["organisation"] == "University of Testing", reread
    assert reread["started_on"] == "2022-01-10", reread
    # ``ended_on`` null is how an ongoing record says so; it is never back-filled.
    assert reread["ended_on"] is None, reread
    assert reread["url"] is None, reread
    assert reread["description"] is None, reread

    # The same for the evidence row: the user's line verbatim, a date they gave,
    # and a ``source`` of ``manual`` — never a subsystem name, because ``source``
    # is a column of the deduplication key and is not client-writable.
    recheck = next(item for item in bodies[3][1]["items"] if item["id"] == evidence["id"])
    assert recheck["title"] == "Shipped the billing service", recheck
    assert recheck["occurred_on"] == date.today().isoformat(), recheck
    assert recheck["source"] == "manual", recheck
    assert recheck["description"] is None, recheck
    assert recheck["project_id"] is None, recheck
