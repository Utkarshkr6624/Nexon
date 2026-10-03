"""Wire shapes for Phase 9 career intelligence.

The profile, the dated records it is made of, the evidence beside it, and the
summary the two produce.

Every column on this surface is a transcription
------------------------------------------------
The single rule this module enforces is a *negative* one: **there is no field
here NEXUS could fill in that the user did not supply.** A certification, an
employer, a date, a title — all of them arrive from the request body and are
stored verbatim. There is no generator, no importer, no enrichment pass, and no
route that writes a career row the user did not ask for. A schema wide enough to
describe every possible qualification is a schema that will eventually fill one
in, so the vocabulary stays at the seven evidence types and three record kinds
the phase defines.

The wire models are where that has teeth. ``CareerProfileWrite``,
``CareerExperienceWrite`` and ``CareerEvidenceWrite`` carry **no** derived, no
scored, no ranked and no "profile strength" member, and no ``source`` a client
can set to impersonate a subsystem: :attr:`CareerEvidenceRead.source` is part of
the row's identity — it is a column of ``uq_career_evidence_source_identity`` —
and the write models therefore do not expose it. A client that could write
``source='project'`` on a hand-typed achievement would defeat the deduplication
constraint that is the entire mechanism for derived evidence.

Evidence points at records; the records outlive them
----------------------------------------------------
Every foreign key on :class:`CareerEvidenceRead` is ``ON DELETE SET NULL``.
Deleting a project must not delete the record that a project was completed, and
a client therefore cannot assume ``project_id`` resolves to anything. Each is a
*claim that something existed*, and null on all three is a legitimate row — a
manually-added achievement — rather than an incomplete one.

Nullability: the two rules, applied to every field below
--------------------------------------------------------
**Every nullable field is required and typed ``T | None``, never
``Optional[T] = None``**, so the key is always present in the JSON. A client that
finds ``ended_on`` missing cannot tell an ongoing role from a payload written by
an older server, and those want different sentences. ``ended_on`` being null is
precisely how "current" is expressed, so the distinction is the whole field.

**A figure that could not be computed is ``None``, never ``0``.** The one place
this bites in this module is :attr:`CareerFeatureValues.project_activity`, which
is null for an account with no repository that has ever been scanned. Zero there
would be the contract's own worked example of the failure: *a claim about a
repository with no commits, when the truth is that it has never been scanned.*

Counts of the user's own rows are a different matter and stay plain integers.
``evidence_count = 0`` means "you have entered no evidence yet", which is a true
and slightly uncomfortable fact about the account rather than a missing
measurement — and it is exactly the count an empty-evidence state has to be able
to render.

Tallies sit beside the rows, and are complete
---------------------------------------------
Every list carries ``items``/``total``/``limit``/``offset`` flat rather than in
a ``meta`` envelope, matching :class:`app.schemas.risk.RiskListRead`. The totals
are read on screen next to the rows, and burying them under ``meta`` invites a
header to be written against a page slice and quoted as though it described the
whole set.

:class:`CareerEvidenceListRead.by_type` always carries all seven
:class:`~app.models.enums.CareerEvidenceType` members, zeroed where nothing was
found. That is the ``RiskListRead.by_severity`` rule applied to the obvious
case: a client reading ``by_type.certification`` should never need a fallback
default that quietly turns a missing key into the same number as an empty band,
which is how a page ends up reporting no certifications because the key
disappeared rather than because the count is zero.
:attr:`CareerExperienceListRead.by_kind` is completed the same way over the three
record shapes.

Enum columns arrive as ``str``
------------------------------
:attr:`CareerExperienceRead.kind` and :attr:`CareerEvidenceRead.evidence_type`
are typed ``str`` even though the backend holds a ``StrEnum``. The vocabulary is
a closed set and each field enumerates it, but declaring it in a response model
would make the wire a second place the vocabulary lives — and the one that falls
out of step with the enum. The service returns ``.value``; the client checks it
against the same closed union it already imports.

Nothing here is a model
-----------------------
:class:`CareerFeatureVectorRead` carries a ``schema_version`` and named numbers,
and that is all. Phase 9 extracts features; it does not train, load, serve or
register anything, and nothing in this file may be joined with ``features`` and
rendered as a forecast about someone's employability.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import CareerEvidenceType, CareerRecordKind
from app.schemas.recommendation import band_count_sentence

__all__ = [
    "CAREER_FEATURE_SCHEMA_VERSION",
    "CareerEvidenceListRead",
    "CareerEvidenceRead",
    "CareerEvidenceUpdate",
    "CareerEvidenceWrite",
    "CareerExperienceListRead",
    "CareerExperienceRead",
    "CareerExperienceUpdate",
    "CareerExperienceWrite",
    "CareerFeatureValues",
    "CareerFeatureVectorRead",
    "CareerProfileRead",
    "CareerProfileWrite",
    "CareerSummaryRead",
]

#: The version stamped on a feature vector. A single string rather than an enum
#: because this is the *contract* with whatever trains on it: a v2 must not be
#: able to typecheck against v1 column meanings, and the frontend carries the
#: closed union that makes that a compile error there.
CAREER_FEATURE_SCHEMA_VERSION = "career_features.v1"

#: Evidence types, in the order a tally is filled: the five a subsystem may
#: derive first, then the two only a person can supply. A header that walks this
#: order cannot put a certificate the user typed above a shipped feature.
_EVIDENCE_TYPE_ORDER: tuple[str, ...] = tuple(kind.value for kind in CareerEvidenceType)
#: The three record shapes. A closed set with no ``other``: this column picks
#: which shape is being written, so a fourth kind would need a fourth shape to go
#: with it.
_RECORD_KIND_ORDER: tuple[str, ...] = tuple(kind.value for kind in CareerRecordKind)
#: The plural nouns the list headers count.
_EVIDENCE_LIST_SUBJECT = "career evidence records"
_RECORD_LIST_SUBJECT = "career records"


def _complete_bands(provided: Mapping[str, int], order: tuple[str, ...]) -> dict[str, int]:
    """Zero-fill a band tally with every member of ``order``, keeping extras.

    Args:
        provided: The counts the caller supplied. May be partial, may be empty,
            and may carry a key outside ``order`` if a future enum member reached
            a persisted row first.
        order: Every band that must be present in the result, in display order.

    Returns:
        A **new** dictionary holding every band from ``order`` and every extra
        key the caller passed. A new object rather than an in-place fill, so a
        tally the caller still holds is not rewritten underneath them.
    """
    filled = {key: int(provided.get(key, 0)) for key in order}
    for key, value in provided.items():
        if key not in filled:
            filled[key] = int(value)
    return filled


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------


class CareerProfileRead(BaseModel):
    """The one career summary this account has.

    Every descriptive field is nullable and that is the intended shape, not a
    half-built one: a new account has a profile with nothing on it yet, and an
    empty profile is a real state the UI has to render — ``"You have not written
    a profile yet"`` is a sentence this model exists to make possible.

    :attr:`links` is the exception and is never null, because there is no
    difference between "no links" and "links is null" that a reader should have
    to make. The array is exactly what the user supplied, in the order they listed
    them; nothing here discovers, fetches or validates a URL.

    **NEXUS never summarises this.** :attr:`summary` is the user's own paragraph
    and is not generated from the columns above it — a generated summary is a
    claim about a person that nothing in this schema can support.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the profile row.")
    target_role: str | None = Field(
        description="What the user is aiming at, in the words they would use in a "
        "sentence — 'Senior Backend Engineer', not an internal requisition title. "
        "Never inferred from activity and never suggested as a completion: NEXUS "
        "does not know what the user wants next."
    )
    target_domain: str | None = Field(
        description="A grouping label for the target — 'backend', 'data', 'design'. "
        "Deliberately not a controlled vocabulary: a closed set would make a "
        "legitimate answer unenterable, and the only thing this field is for is "
        "letting the user filter their own evidence by direction."
    )
    headline: str | None = Field(
        description="The one-line version, the length a list index or a search result "
        "would show. The long version is `summary`."
    )
    summary: str | None = Field(
        description="The long version, in the user's own words. Null means none was "
        "written. Never generated, never summarised from the other columns."
    )
    location: str | None = Field(
        description="Free text as written — 'Remote (EU)' is a perfectly good answer. "
        "Not split into country and city: nothing here consumes it, and a parsed "
        "location that disagreed with the string the user typed would be a second "
        "answer to 'where are they'."
    )
    links: list[str] = Field(
        description="Portfolio URLs, in the order the user listed them. Always a list "
        "and never null — an empty one is the answer 'no links supplied'. NEXUS does "
        "not fetch them, check them, or add any."
    )
    created_at: datetime = Field(description="When the profile row was first written.")
    updated_at: datetime = Field(
        description="When the row was last revised. `PUT /career/profile` upserts on "
        "the unique `user_id`, so this account can only ever have one profile and a "
        "second PUT revises this one rather than accumulating a second."
    )


class CareerProfileWrite(BaseModel):
    """Write the account's one career profile.

    ``PUT`` rather than ``POST``: ``career_profiles.user_id`` is unique, so this
    route upserts. There is deliberately no create-and-keep-the-old path, because
    a second profile would be a second *answer* to every question about this
    person's career, and the read would then have to decide which one wins.

    Every field is optional and the account always ends up with exactly one row —
    an empty profile is a legitimate state a user can reach by sending ``{}``, and
    forcing content before the row exists would put a form in front of a blank
    page. The one field a client cannot send is a ``user_id``: ownership is never
    trusted from the request body, and a body carrying another account's id is
    ignored rather than honoured.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_role: str | None = Field(
        default=None,
        max_length=200,
        description="What the user is aiming at, in their own words. Null clears it.",
    )
    target_domain: str | None = Field(
        default=None,
        max_length=120,
        description="A grouping label for the target, free text rather than a closed set.",
    )
    headline: str | None = Field(
        default=None,
        max_length=200,
        description="The one-line version of the profile.",
    )
    summary: str | None = Field(
        default=None,
        max_length=20000,
        description="The long version, in the user's own words. Stored verbatim: "
        "there is no generator anywhere in this module and no future one is "
        "planned, because a generated summary is a claim about a person that "
        "nothing here can support.",
    )
    location: str | None = Field(
        default=None,
        max_length=200,
        description="Free text as written. NEXUS does not parse, geocode or normalise it.",
    )
    links: list[str] | None = Field(
        default=None,
        description="Portfolio URLs, stored in the order given and replaced wholesale. "
        "Omit to leave the existing list alone; send a list to replace it, including "
        "an empty list to clear it. NEXUS never adds, fetches or validates a URL.",
    )


# ---------------------------------------------------------------------------
# The dated records
# ---------------------------------------------------------------------------


class CareerExperienceRead(BaseModel):
    """One dated line on the profile: a course, a job, or a certification.

    :attr:`kind` is what separates the three, and nothing else does. They share a
    table because they do not differ in *shape* — all three are "a titled thing, "
    "at an organisation, over a span of dates, with a description and a link" —
    and splitting them would have tripled the router for no read this product
    makes.

    :attr:`ended_on` being null is how a **current** role or an ongoing degree
    says so. It does not mean unknown; the UI asks, and an ongoing record is the
    common case. That null is the reason the field is required and nullable rather
    than optional with a default: a client has to be able to say "I don't know
    whether this ended" only by having chosen to.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the record.")
    kind: str = Field(
        description="Which of the three record shapes this is: one of the "
        f"{_RECORD_KIND_ORDER} `CareerRecordKind` values. A closed set with no "
        "`other`, because this column chooses a shape rather than characterising "
        "the record — education was studied, experience was worked, a certification "
        "was passed, and those are not the same claim."
    )
    title: str = Field(
        description="The user's own name for it — 'BSc Computer Science', 'Senior "
        "Engineer', 'AWS Solutions Architect Associate'. Never generated, never "
        "expanded, never fetched from an issuing body."
    )
    organisation: str | None = Field(
        description="Who issued it or where it was done. Null is normal and is not a "
        "gap: a self-directed project or an open-source contribution has no "
        "organisation, and inventing one is exactly the failure this phase forbids."
    )
    started_on: date | None = Field(
        description="When it began, as the user gave it. Null when they did not say, "
        "and never filled in from anything NEXUS can observe."
    )
    ended_on: date | None = Field(
        description="When it ended, or null to say it is *current* — an ongoing "
        "degree, a current role. Null is a real answer, not an unrendered empty "
        "date."
    )
    description: str | None = Field(
        description="The user's own description of what it involved. This is where a "
        "difference between a certification and a degree belongs — what a "
        "certification has that an education does not, the user can say here."
    )
    url: str | None = Field(
        description="A link the user supplied — the certificate, the programme, the "
        "company. Never discovered and never fetched."
    )
    created_at: datetime = Field(description="When the record was added.")
    updated_at: datetime = Field(description="When the record was last revised.")


class CareerExperienceListRead(BaseModel):
    """One page of career records, with the shape tally beside them.

    ``by_kind`` always carries all three record kinds, zeroed where nothing was
    found, so a client reading ``by_kind.certification`` never meets a missing key
    and quietly reports the same number as an empty section.

    ``current_count`` is the other half of the date story: records with no end
    date, which is how the profile says "this is ongoing". A timeline that showed
    an open-ended role as one with no dates would be making the same claim.
    """

    items: list[CareerExperienceRead] = Field(
        description="The records on this page, newest span first. Empty when the "
        "filters match nothing, which is a measurement and not an error."
    )
    total: int = Field(
        ge=0, description="How many records match the filters, not the length of this page."
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    by_kind: dict[str, int] = Field(
        description="Counts across every matching record, not just this page. Always "
        "carries all three kinds, zeroed where nothing was found."
    )
    current_count: int = Field(
        ge=0,
        description="Matching records with no end date — the ones that are ongoing. "
        "Across every matching row, not this page.",
    )
    summary: str = Field(
        description="One factual sentence describing the counts, for the list header."
    )

    @model_validator(mode="after")
    def _fill_bands_and_summary(self) -> Self:
        """Complete the tally and compose the header sentence.

        The counts are copied rather than mutated, so a dictionary the caller
        still holds is not rewritten underneath them.
        """
        self.by_kind = _complete_bands(self.by_kind, _RECORD_KIND_ORDER)
        if not self.summary:
            self.summary = band_count_sentence(
                self.by_kind, self.total, _RECORD_LIST_SUBJECT, _RECORD_KIND_ORDER
            )
        return self


class CareerExperienceWrite(BaseModel):
    """Add one dated record to the profile.

    ``kind`` and ``title`` are required and everything else is optional, because
    the honest minimum for a real career record is a name and which of the three
    shapes it is. Requiring dates would exclude the open-ended degree and the
    self-directed course, which are both common and both true.

    Every date here is the user's. There is no import that could fill one, so an
    empty body beyond ``title`` produces a record the profile renders honestly as
    an undated entry rather than one NEXUS completed on the user's behalf.
    """

    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(
        description="Which of the three record shapes this is: one of the "
        f"{_RECORD_KIND_ORDER} `CareerRecordKind` values, validated against the enum "
        "rather than trusted as a bare string — an unrecognised value would file "
        "the row under no section of the timeline it belongs to."
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="The user's own name for it. Required even for a bare "
        "certification: an untitled row on a profile is a record nobody can act on.",
    )
    organisation: str | None = Field(
        default=None,
        max_length=200,
        description="Who issued it or where it was done. Omit when there is no such "
        "body — that is a real answer and not a field to be filled in.",
    )
    started_on: date | None = Field(default=None, description="When it began, as the user gave it.")
    ended_on: date | None = Field(
        default=None,
        description="When it ended. Omit to say it is current; the database asserts "
        "that an end date is not before a start date when both are present, and "
        "deliberately says nothing about the single-date cases.",
    )
    description: str | None = Field(
        default=None, max_length=20000, description="The user's description of what it involved."
    )
    url: str | None = Field(default=None, max_length=500, description="A link the user supplied.")


class CareerExperienceUpdate(BaseModel):
    """Patch one dated record.

    ``kind`` is inherited as editable but ``created_at`` is not present at all: a
    record is not re-filed, it is corrected. ``extra="forbid"`` turns "that field
    is not editable here" into a 422 naming the field rather than a cheerful 200
    that dropped it.

    As everywhere in this codebase, the service applies this payload with
    ``exclude_unset``: an omitted key leaves the column alone and an explicit null
    clears it.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="A corrected name. Omit to leave it alone.",
    )
    organisation: str | None = Field(
        default=None,
        max_length=200,
        description="A corrected issuer or employer. An explicit null clears it — "
        "which is how a self-directed entry stays honest.",
    )
    started_on: date | None = Field(default=None, description="A corrected start date.")
    ended_on: date | None = Field(
        default=None,
        description="A corrected end date, or an explicit null to say the record is current again.",
    )
    description: str | None = Field(
        default=None,
        max_length=20000,
        description="A corrected description. An explicit null clears it.",
    )
    url: str | None = Field(
        default=None, max_length=500, description="A corrected link. An explicit null clears it."
    )


# ---------------------------------------------------------------------------
# The evidence
# ---------------------------------------------------------------------------


class CareerEvidenceRead(BaseModel):
    """One thing the user wants to be able to point at.

    A *claim with a provenance*. :attr:`source` says whether the user wrote it or
    a subsystem derived it, and the three nullable foreign keys say what it was
    derived from. **All three null is a legitimate row** — a manually-added
    achievement — and not an incomplete one.

    Because every foreign key is ``ON DELETE SET NULL``, no ``*_id`` here is
    guaranteed to resolve: each says "something of that kind existed", not "you can
    still open it". Deleting a project must not delete the record that a project
    was completed — the evidence is the trail, and a trail that vanishes when you
    stop watching the place it happened is a view.

    :attr:`evidence_type` is a closed set and the vocabulary is the guarantee.
    ``repository_activity`` means commits were observed in a repository; it does
    not mean a task was completed, and it does not mean anything was finished.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the evidence row.")
    evidence_type: str = Field(
        description="What kind of thing this is: one of the "
        f"{_EVIDENCE_TYPE_ORDER} `CareerEvidenceType` values. `certification` and "
        "`achievement` are user-entered rows and nothing else — NEXUS never creates "
        "a certification, never dates one, and never infers one from activity."
    )
    title: str = Field(
        description="The user's line for it, kept as written rather than generated "
        "from the linked record. A derived title would be a sentence about work "
        "nobody described."
    )
    description: str | None = Field(
        description="A longer note in the user's words. Null means none was written."
    )
    occurred_on: date = Field(
        description="When it happened, as a **date** and not a timestamp: evidence is "
        "a thing that happened on a day. Never null — undated evidence cannot be put "
        "in a timeline, and a placeholder date would be a fabricated one."
    )
    project_id: uuid.UUID | None = Field(
        description="The project this was drawn from, or null when it was not drawn "
        "from one. `ON DELETE SET NULL`, so this may name something that no longer "
        "exists — which is the point: the record of having finished it outlives it."
    )
    skill_id: uuid.UUID | None = Field(
        description="The skill this was drawn from, or null. `ON DELETE SET NULL`, for "
        "the same reason."
    )
    repository_id: uuid.UUID | None = Field(
        description="The repository this was drawn from, or null. `ON DELETE SET "
        "NULL`. Its presence is a claim that activity was observed there, not that "
        "anything was completed."
    )
    source: str = Field(
        description="`manual`, or the subsystem that derived the row — `project`, "
        "`skill`, `repository`. Part of the row's identity in "
        "`uq_career_evidence_source_identity`, which is the entire deduplication "
        "mechanism: a derived row cannot be inserted twice, while any number of "
        "manual rows can. Never client-writable."
    )
    created_at: datetime = Field(description="When the row was added.")
    updated_at: datetime = Field(description="When the row was last revised.")


class CareerEvidenceListRead(BaseModel):
    """One page of evidence, with the type tally beside the rows.

    ``by_type`` carries all seven :class:`~app.models.enums.CareerEvidenceType`
    members, zeroed where nothing was found, in the order derived types come before
    user-entered ones. A client reading ``by_type.certification`` therefore never
    needs a fallback default that would turn a missing key into the same number as
    an empty band — which is how a profile ends up reporting no certifications
    because the key disappeared rather than because the count is genuinely zero.

    ``by_source`` cannot be completed the same way, because the source vocabulary
    is open: a new subsystem may derive evidence without a migration, so only the
    sources this account has actually used appear. ``manual_count`` is filled in
    explicitly, because that one is the count the design cares about being
    honest — how much of the profile the user wrote themselves.
    """

    items: list[CareerEvidenceRead] = Field(
        description="The evidence on this page, newest first. Empty when the filters "
        "match nothing, which is a measurement and not an error."
    )
    total: int = Field(
        ge=0,
        description="How many rows of evidence match the filters, not the length of this page.",
    )
    limit: int = Field(ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(ge=0, description="How many matching rows were skipped.")
    by_type: dict[str, int] = Field(
        description="Counts across every matching row, not just this page. Always "
        "carries all seven evidence types, zeroed where nothing was found."
    )
    by_source: dict[str, int] = Field(
        description="Counts by the subsystem each row came from. Only the sources "
        "this account has used appear — the vocabulary is deliberately open."
    )
    manual_count: int = Field(
        ge=0,
        description="Matching rows the user entered themselves, across every matching "
        "row rather than this page. The one provenance figure worth stating next to "
        "a career profile, because it separates what the person wrote from what the "
        "system observed.",
    )
    summary: str = Field(
        description="One factual sentence describing the counts, for the list header. "
        "Counts records; never ranks them, because ranking a person's evidence is a "
        "judgement about them."
    )

    @model_validator(mode="after")
    def _fill_bands_and_summary(self) -> Self:
        """Complete the type tally and compose the header sentence.

        ``by_source`` is left exactly as supplied — its vocabulary is open, so
        zero-filling it would invent sources that never ran. The counts are copied
        rather than mutated, so a dictionary the caller still holds is not rewritten
        underneath them.
        """
        self.by_type = _complete_bands(self.by_type, _EVIDENCE_TYPE_ORDER)
        if not self.summary:
            self.summary = band_count_sentence(
                self.by_type, self.total, _EVIDENCE_LIST_SUBJECT, _EVIDENCE_TYPE_ORDER
            )
        return self


class CareerEvidenceWrite(BaseModel):
    """Enter one piece of evidence.

    Every word of this payload is the user's: NEXUS supplies no title, no date, no
    employer and no credential. The three ``*_id`` pointers name records in this
    account that already exist — NEXUS does not create them — and all three may be
    omitted, which is what a hand-written achievement looks like.

    :attr:`source` and :attr:`project_id`/``skill_id``/``repository_id`` together
    form the row's uniqueness key, so a second row carrying the same identity is a
    409 rather than a duplicate. That is why the write models take no ``source``:
    a client that could write ``source='project'`` on a hand-typed achievement
    would be able to make two rows collide or to impersonate a subsystem.
    """

    model_config = ConfigDict(populate_by_name=True)

    evidence_type: str = Field(
        description="What kind of thing this is: one of the "
        f"{_EVIDENCE_TYPE_ORDER} `CareerEvidenceType` values, validated against the "
        "enum rather than trusted as a bare string — this column is one of the six "
        "in the deduplication key."
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="The user's one-line description of what they did. Required: NEXUS "
        "will not write it.",
    )
    description: str | None = Field(
        default=None, max_length=20000, description="A longer note in the user's words."
    )
    occurred_on: date | None = Field(
        default=None,
        description="The day it happened. Optional here because a dated record is not "
        "the only thing a user may be entering; a response without it is "
        "unrenderable in a timeline, so the service may require it for the types "
        "that need one.",
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description="A project in this account this was drawn from. Another account's "
        "project is a 404, never a 403.",
    )
    skill_id: uuid.UUID | None = Field(
        default=None, description="A skill in this account this was drawn from."
    )
    repository_id: uuid.UUID | None = Field(
        default=None, description="A repository in this account this was drawn from."
    )


class CareerEvidenceUpdate(BaseModel):
    """Patch one piece of evidence.

    :attr:`CareerEvidenceWrite.source` and the three ``*_id`` pointers are
    **absent**: the provenance of a row is part of its identity and of the
    deduplication constraint that keeps derived evidence from being inserted
    twice. Re-pointing a row at a different project would let a rename become a
    second record, which is the exact failure the constraint exists to prevent.
    ``extra="forbid"`` makes the attempt a 422 naming the field.

    Only the words may be corrected, and only the date may be corrected — the
    person is allowed to be wrong about what they wrote, and not about where it
    came from.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="A corrected one-liner. Omit to leave it alone.",
    )
    description: str | None = Field(
        default=None, max_length=20000, description="A corrected note. An explicit null clears it."
    )
    occurred_on: date | None = Field(
        default=None,
        description="A corrected date. An explicit null is rejected by the service "
        "rather than stored: undated evidence cannot be placed in a timeline, and a "
        "placeholder date would be a fabricated one.",
    )


# ---------------------------------------------------------------------------
# The summary and the features
# ---------------------------------------------------------------------------


class CareerSummaryRead(BaseModel):
    """The account-wide headline figures for the career page.

    Counts only, and every one of them is a count of something the user supplied
    or a subsystem recorded. There is no score, no rank and no "profile strength"
    here, and there is no ordering of evidence by importance: anything that
    ordered a person's evidence by weight would be a judgement about them that no
    column here could justify.

    ``has_data`` is the cold-start flag. False means the counts are legitimately
    zero **and** the page must say why — an account with no profile, no records
    and no evidence is not a finding about the person who owns it.

    The window is carried because three of these figures are date-bounded, and a
    sentence printed above them that omits the range would not be true.
    """

    has_profile: bool = Field(
        description="Whether this account has a profile row at all. False on an "
        "account that has never written one, which is a different state from a "
        "profile that exists and is blank — the first has no row to render, the "
        "second renders as empty fields."
    )
    target_role: str | None = Field(
        description="What the user is aiming at, echoed from the profile so the "
        "header can filter the page by direction. Null when they have not said."
    )
    link_count: int = Field(
        ge=0,
        description="Portfolio URLs on the profile. A real count: zero means the user "
        "supplied none, which is an answer rather than a missing measurement.",
    )
    record_count: int = Field(
        ge=0,
        description="Dated records — education, work experience and certifications "
        "together, since they share one table.",
    )
    evidence_count: int = Field(
        ge=0,
        description="Evidence rows across the whole history. Zero on a new account is "
        "the true state the page has to render.",
    )
    evidence_in_window: int = Field(ge=0, description="Evidence dated inside the window below.")
    manual_evidence_count: int = Field(
        ge=0,
        description="Of the evidence, how much the user entered themselves. The one "
        "provenance figure the summary states, because it separates what the person "
        "wrote from what the system observed.",
    )
    linked_project_count: int = Field(
        ge=0,
        description="Distinct projects this account's evidence points at. Every "
        "foreign key is `ON DELETE SET NULL`, so this counts rows that name a "
        "project whether or not that project still exists.",
    )
    project_count: int = Field(
        ge=0,
        description="Projects this account has in the work manager. A real count, "
        "including completed ones, which is why `completed_project_count` is carried "
        "beside it.",
    )
    completed_project_count: int = Field(
        ge=0,
        description="Of those, the ones that reached `completed`. Read from the "
        "project's own status column, never inferred from the evidence table.",
    )
    repository_count: int = Field(
        ge=0,
        description="Repositories registered for this account, whether or not any has "
        "been scanned. A real count; `0` means none were registered, which is a "
        "different fact from 'registered but never read'.",
    )
    skills_with_evidence: int = Field(
        ge=0,
        description="Skills in this account that carry at least one career evidence "
        "row pointing at them.",
    )
    learning_activity_count: int = Field(
        ge=0,
        description="Learning activities recorded across the account, whole history. "
        "Read from the learning tables so the career page and the learning page "
        "cannot quote different totals for the same rows.",
    )
    window_days: int = Field(
        ge=1,
        description="The length of the window the in-window figure covers. Carried so "
        "no sentence about it can omit the range it describes.",
    )
    window_start: datetime = Field(description="Inclusive start of that window.")
    window_end: datetime = Field(description="Exclusive end of that window.")
    latest_evidence_on: date | None = Field(
        description="The most recent day any evidence was dated, or null when there is none."
    )
    has_data: bool = Field(
        description="False when there is nothing to summarise, so the counts read as "
        "an absence rather than as a finding about the person."
    )
    summary: str = Field(
        description="One factual sentence describing the counts, composed server-side "
        "so the header has one owner rather than one per page. Counts what exists "
        "and never says what it means about the user's prospects."
    )


class CareerFeatureValues(BaseModel):
    """The feature names, and only the feature names.

    An **extractor**, not a model: named numbers under a schema version so a later
    phase knows what each column meant. Nothing here is a prediction, a probability
    or a fitted parameter, and ``repositories`` is a count of work trees someone
    registered — not a statement about what they can build.

    ``project_activity`` is the contract's own worked example of the null-not-zero
    rule: it is **null** for an account with no repository that has ever been
    scanned, because ``0`` would assert that a repository exists and carries no
    commits when the truth is that nobody has looked.
    """

    projects_completed: int = Field(
        ge=0,
        description="Projects that reached `completed`, read from the project status "
        "column rather than inferred from the evidence table.",
    )
    project_activity: float | None = Field(
        description="Recorded commits per completed project across the window, or null "
        "when no repository has ever been scanned. Null rather than 0 — see the class "
        "docstring."
    )
    repositories: int = Field(
        ge=0,
        description="Repositories registered for this account. Zero is a real count: "
        "nobody registered one.",
    )
    relevant_skill_evidence: int = Field(
        ge=0,
        description="Career evidence rows pointing at a tracked skill, across every "
        "match. A count of claims the user made about their own skills.",
    )
    learning_activity: int = Field(
        ge=0,
        description="Learning activities recorded across the account. Read from the "
        "same rows the learning page counts, so the two cannot disagree.",
    )
    portfolio_evidence_count: int = Field(
        ge=0,
        description="Evidence rows the user entered by hand, plus the profile's own "
        "links. The provenance figure: how much of the career page was written by "
        "the person rather than derived by the system.",
    )


class CareerFeatureVectorRead(BaseModel):
    """``GET /career/features``: the account-level feature row.

    ``schema_version`` is what makes the vector usable later: a trainer that sees
    ``career_features.v1`` knows the column meanings without having to trust that
    the client did not reorder them. There is no model, no inference and no
    registry behind this shape — Phase 10 does that, and this phase does not.
    """

    schema_version: str = Field(
        default=CAREER_FEATURE_SCHEMA_VERSION,
        description="The version of the column meanings below. A v2 must not "
        "typecheck against v1, which is why the frontend carries this as a closed "
        "union rather than a bare string.",
    )
    generated_at: datetime = Field(
        description="When the vector was extracted, from the database clock rather "
        "than the host's, so it belongs on the same timeline as the rows it reads."
    )
    window_days: int = Field(
        ge=1, description="The window the date-bounded features above were computed over."
    )
    features: CareerFeatureValues = Field(
        description="The account-level row: the same features aggregated across every "
        "profile, record, evidence row and linked project this account owns."
    )
