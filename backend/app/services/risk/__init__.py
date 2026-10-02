"""Phase 7 — deterministic risk detection and recommendation.

Two services, one direction of flow:

* :mod:`app.services.risk.scoring` — pure functions, no I/O. The formulas.
* :mod:`app.services.risk.detection` — reads analytics, applies the formulas,
  and reconciles the result against what is already stored.
* :mod:`app.services.risk.recommendation` — turns risks into proposed actions.

The split exists because the brief forbids risk calculation in route handlers
*and* requires the engine to be testable. Keeping the arithmetic in a pure
module means a rule can be asserted against a fixed tuple of inputs without a
database, and the orchestration that reads the database can be asserted against
a seeded fixture. Neither test needs the other.
"""

from app.services.risk.recommendation import RecommendationService
from app.services.risk.scoring import (
    NOT_ENOUGH_DATA,
    RiskEvidence,
    RiskResult,
    consistency_risk,
    deadline_risk,
    estimation_risk,
    evidence_strength_for,
    project_risk,
    risk_severity_for,
    scheduling_risk,
    workload_risk,
)

__all__ = [
    "NOT_ENOUGH_DATA",
    "RecommendationService",
    "RiskEvidence",
    "RiskResult",
    "consistency_risk",
    "deadline_risk",
    "estimation_risk",
    "evidence_strength_for",
    "project_risk",
    "recommendation_rules",
    "risk_severity_for",
    "scheduling_risk",
    "workload_risk",
]
