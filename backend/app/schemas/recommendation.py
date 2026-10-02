"""Wire shapes for Phase 7 recommendations: a proposed action, and the case for it.

Every field on these models exists because the brief demands four things of a
recommendation — **WHAT** (the title), **SUGGESTED ACTION** (the description),
**WHY** (the reason, with the numbers), and **RELATED DATA** (the entity and the
risk it came from). The model is laid out in that order on purpose: it is the
order the UI reads them in, and a reader of the file should not have to consult
the brief to know which string is the imperative and which one is the evidence.

The engine proposes and never performs
--------------------------------------
Every member of :class:`app.models.enums.RecommendationType` names something a
person does, and none of them is an operation NEXUS carries out. That is
structural rather than a convention somebody has to remember, and these schemas
keep it that way: there is no field anywhere in this module through which a
response could say that a task *has been* rescheduled. ``responded_at`` records
when the user acted, which is the only evidence of an action this module can
carry, and it is set by the user's request — never by the engine.

Why the reason cannot be empty
------------------------------
The brief forbids "recommendations without clear reasoning", and an empty
``reason`` is the failure mode that slips through review: the imperative renders
perfectly well on a card, so nobody notices the sentence justifying it is gone.
:class:`RecommendationRead` therefore rejects a blank reason at validation time
rather than shipping a bare imperative, and
:class:`RecommendationSummaryRead` — the projection nested inside a risk —
**keeps** the reason for the same reason. A summary that dropped it would
reintroduce the exact thing the brief rules out, one screen further in.

Relationship to the risk schemas
--------------------------------
:class:`RecommendationSummaryRead` lives here rather than in
:mod:`app.schemas.risk` because ``RiskRead`` needs it and nothing here needs
anything from ``risk.py``. The import runs one way, which is why the two modules
can both be imported first in any order without a cycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import RecommendationPriority

__all__ = [
    "RecommendationListRead",
    "RecommendationRead",
    "RecommendationSummaryRead",
    "band_count_sentence",
]

#: Priority bands, most urgent first. The same four words as
#: :class:`app.models.enums.RiskSeverity`, and for the same reason: priority is
#: derived from the severity of the risk that raised the recommendation, so the
#: two are two views of one number and cannot disagree.
_PRIORITY_ORDER: tuple[str, ...] = tuple(priority.value for priority in RecommendationPriority)


def band_count_sentence(
    by_band: Mapping[str, int],
    total: int,
    subject: str,
    band_order: Sequence[str],
) -> str:
    """One factual sentence describing a set of counts banded by severity word.

    The shared implementation behind :func:`app.schemas.risk.count_sentence`,
    which is the only caller today: the risk list carries a header sentence and
    the recommendation list does not, but when the service composes one for the
    second it should reach for this rather than writing a second one in a
    different tone. It lives here because :mod:`app.schemas.risk` already imports
    from this module; the reverse would be a cycle.

    The register is the brief's: it states the counts and nothing else. No
    urgency language, no "you have" framing, and no suggestion that a count of
    open items is a count of failures — a user who rejected three suggestions has
    done three things NEXUS asked for.

    Args:
        by_band: Counts keyed by band word, e.g. ``{"high": 2, "low": 1}``. A band
            with no entries is left out of the sentence rather than printed as a
            zero, because "0 low" is noise to a reader. Keys outside `band_order`
            are ignored.
        total: The number the counts add up to. Used for the leading count when
            the two disagree, so a stale breakdown cannot silently change the
            headline.
        subject: The plural noun the counts are of, already in the right voice —
            ``"live risks"``, ``"open recommendations"``. Passed in rather than
            derived because the two surfaces name the same numbers differently on
            purpose: a resolved risk stops being live, and a rejected suggestion
            stops being open.
        band_order: Band words in the order they should appear, most urgent first.

    Returns:
        A sentence such as ``"5 open recommendations: 1 critical, 2 high, 2 low."``
        An empty set returns the subject on its own — ``"No open recommendations."``,
        ``"No live risks."``.
    """
    if total <= 0:
        return f"No {subject}."
    # The band words are adjectives, so "1 critical" needs no plural form.
    named = [f"{by_band[key]} {key}" for key in band_order if by_band.get(key)]
    if not named:
        return f"{total} {subject}."
    return f"{total} {subject}: {', '.join(named)}."


class RecommendationRead(BaseModel):
    """One proposed action, complete with the evidence behind it.

    The requirement that a recommendation is never a bare imperative is enforced
    here rather than documented and left to review: a blank ``reason`` renders
    perfectly well on a card, so a reviewer looking at the screenshot sees a
    working suggestion and not the missing sentence underneath it. A field
    validator is the only thing that still catches it in a service written next
    year.

    The model validates straight from a persisted ``Recommendation`` row. The
    ``metadata`` alias is the only accommodation that needs: the column is
    mapped as ``metadata_`` on the model because ``metadata`` is reserved on a
    SQLAlchemy declarative class, and the wire name is ``metadata`` because that
    is the name the column has in the database and in the JSON.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(description="Identifier of the stored recommendation.")
    recommendation_type: str = Field(
        description="Which rule proposed this action; one of the "
        "``RecommendationType`` values, e.g. `block_time`."
    )
    priority: str = Field(
        description="How soon this wants an answer, as one of the "
        "``RecommendationPriority`` values. Derived from the severity of the "
        "risk that raised it, never chosen independently."
    )
    title: str = Field(
        description="WHAT is being asked for, stated as a fact about the work: "
        "'Block time for the Q3 report' rather than 'You should block time'."
    )
    description: str = Field(
        description="The SUGGESTED ACTION, in the imperative and specific enough "
        "to act on: which entity, and what to do with it."
    )
    reason: str = Field(
        description="WHY this was proposed, with the numbers that drove it. Never "
        "empty — a recommendation with no reason is a bare imperative, which the "
        "brief forbids."
    )
    entity_type: str | None = Field(
        description="What the recommendation is about — `task`, `project` or "
        "`account`. Null for an account-level suggestion that points at no row."
    )
    entity_id: uuid.UUID | None = Field(
        description="The row the recommendation is about. Null together with "
        "`entity_type`, for the same reason."
    )
    risk_id: uuid.UUID | None = Field(
        description="The risk that raised this. Null when a rule fired without a "
        "stored risk — cold start raises suggestions before anything is severe "
        "enough to persist — and also once the risk has been deleted, because "
        "deleting a risk must not delete the record of having acted on it."
    )
    status: str = Field(
        description="Where the suggestion is in its lifecycle; one of the "
        "``RecommendationStatus`` values. The terminal states are the training "
        "signal Phase 10 will read."
    )
    created_at: datetime = Field(description="When the recommendation was first raised.")
    responded_at: datetime | None = Field(
        description="When the user accepted, rejected, completed or viewed it. "
        "Null until they do, which is how 'how many were never answered' is "
        "answered without a second query."
    )
    expires_at: datetime | None = Field(
        description="Set when the risk behind this was resolved. A recommendation "
        "is not merely old, it is moot, and recording that is more useful than "
        "recording how old it is."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata", "metadata_"),
        description="The raw inputs behind the recommendation. Same role as "
        "`Risk.metadata`: what makes a stored suggestion auditable after the "
        "aggregates it was computed from have been rebuilt.",
    )

    @field_validator("reason", mode="after")
    @classmethod
    def _reason_must_carry_a_why(cls, value: str) -> str:
        """Reject a blank reason.

        Stripped before the check rather than after, because a whitespace-only
        reason is the same defect as an empty one and would otherwise survive
        the length validation that ``min_length`` performs.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "A recommendation must carry a reason. The brief requires every "
                "recommendation to explain itself; an imperative with nothing "
                "behind it is the failure being ruled out."
            )
        return stripped


class RecommendationSummaryRead(BaseModel):
    """A recommendation as it appears nested inside a risk.

    The Risk Center shows a risk and, under it, the actions proposed for it. This
    is that inner row: it drops ``description``, ``entity_id``, ``responded_at``
    and ``metadata`` because the parent risk has already said which entity is
    involved and the card has no room for the rest — and it **keeps**
    ``reason``, because the one thing a projection may not drop is the
    explanation. A risk card showing an unexplained suggestion would be the bare
    imperative one level down from where the brief rules it out.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Identifier of the stored recommendation.")
    recommendation_type: str = Field(
        description="Which rule proposed this action; one of the ``RecommendationType`` values."
    )
    priority: str = Field(
        description="How soon this wants an answer, as one of the "
        "``RecommendationPriority`` values."
    )
    title: str = Field(description="WHAT is being asked for, in a few words.")
    reason: str = Field(
        description="WHY this was proposed, kept in the projection for the same "
        "reason the full model cannot omit it."
    )
    status: str = Field(
        description="Where the suggestion is in its lifecycle; one of the "
        "``RecommendationStatus`` values."
    )
    created_at: datetime = Field(description="When the recommendation was first raised.")


class RecommendationListRead(BaseModel):
    """One page of recommendations, with the counts for the header.

    Not built on the shared :class:`app.schemas.common.Page` envelope: this list
    carries its own ``by_priority`` tally, and folding that into a nested ``meta``
    would bury a count the UI shows next to every row inside an object whose only
    other members are pagination bookkeeping.

    No header sentence, unlike :class:`app.schemas.risk.RiskListRead`: the
    contracts freeze this envelope at five fields and the client renders no such
    line here. :func:`band_count_sentence` is available to the service if a
    future screen wants one, rather than a field no consumer asked for.
    """

    items: list[RecommendationRead] = Field(
        default_factory=list,
        description="The recommendations on this page, most urgent first. Empty "
        "when the filters match nothing.",
    )
    total: int = Field(default=0, ge=0, description="How many recommendations match the filters.")
    limit: int = Field(default=0, ge=0, description="Maximum rows the page may hold.")
    offset: int = Field(default=0, ge=0, description="How many matching rows were skipped.")
    by_priority: dict[str, int] = Field(
        default_factory=dict,
        description="Counts across every matching row, not just this page. Always "
        "carries all four priority words set to zero when empty, so the response "
        "shape does not change as the last critical recommendation is closed and no "
        "client needs a fallback default.",
    )

    @model_validator(mode="after")
    def _fill_priority_bands(self) -> Self:
        """Complete the tally with the bands nothing was found under.

        Copied rather than mutated, so a dictionary the caller still holds a
        reference to is not silently rewritten underneath them. A key outside the
        four known bands is carried through untouched: a priority word the engine
        has not shipped yet is still a count the user should see.
        """
        filled = {key: int(self.by_priority.get(key, 0)) for key in _PRIORITY_ORDER}
        for key, value in self.by_priority.items():
            if key not in filled:
                filled[key] = value
        self.by_priority = filled
        return self
