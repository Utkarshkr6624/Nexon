"""ORM models.

Every model is re-exported here so that Alembic's ``env.py`` and
``Base.metadata`` see the complete schema via a single import. A model missing
from this list is invisible to autogenerate: its table would never be created
and ``alembic check`` would report no drift while the table simply does not
exist.
"""

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.activity import ActivityLog
from app.models.analytics import DailyMetric
from app.models.audit import AuditEvent, AuditLog
from app.models.enums import (
    ActivityEvent,
    CalendarEventType,
    EvidenceStrength,
    KnowledgeEntityType,
    KnowledgeLinkType,
    NoteStatus,
    ProjectPriority,
    ProjectStatus,
    RecommendationPriority,
    RecommendationStatus,
    RecommendationType,
    ResourceType,
    RiskSeverity,
    RiskStatus,
    RiskType,
    TaskPriority,
    TaskStatus,
)
from app.models.knowledge import (
    DEFAULT_KNOWLEDGE_LINK_TYPE,
    DEFAULT_NOTE_STATUS,
    DEFAULT_RESOURCE_TYPE,
    MAX_REVISIONS_PER_NOTE,
    Bookmark,
    Category,
    Concept,
    Document,
    KnowledgeLink,
    Note,
    NoteRevision,
    Resource,
    concept_tags,
    note_tags,
)
from app.models.password_reset import PasswordResetToken
from app.models.planner import (
    DEFAULT_CALENDAR_EVENT_TYPE,
    DEFAULT_WORK_SESSION_STATUS,
    AvailabilityRule,
    CalendarEvent,
    WorkSession,
    WorkSessionStatus,
)
from app.models.project import DEFAULT_PROJECT_PRIORITY, DEFAULT_PROJECT_STATUS, Project
from app.models.risk import (
    DEFAULT_RECOMMENDATION_PRIORITY,
    DEFAULT_RECOMMENDATION_STATUS,
    DEFAULT_RISK_SEVERITY,
    DEFAULT_RISK_STATUS,
    Recommendation,
    Risk,
    RiskEvaluation,
)
from app.models.session import Session
from app.models.tag import Tag, project_tags, task_tags
from app.models.task import (
    DEFAULT_TASK_PRIORITY,
    DEFAULT_TASK_STATUS,
    Task,
    TaskDependency,
)
from app.models.user import DEFAULT_ROLE, User, UserRole

__all__ = [
    "DEFAULT_CALENDAR_EVENT_TYPE",
    "DEFAULT_KNOWLEDGE_LINK_TYPE",
    "DEFAULT_NOTE_STATUS",
    "DEFAULT_PROJECT_PRIORITY",
    "DEFAULT_PROJECT_STATUS",
    "DEFAULT_RECOMMENDATION_PRIORITY",
    "DEFAULT_RECOMMENDATION_STATUS",
    "DEFAULT_RESOURCE_TYPE",
    "DEFAULT_RISK_SEVERITY",
    "DEFAULT_RISK_STATUS",
    "DEFAULT_ROLE",
    "DEFAULT_TASK_PRIORITY",
    "DEFAULT_TASK_STATUS",
    "DEFAULT_WORK_SESSION_STATUS",
    "MAX_REVISIONS_PER_NOTE",
    "ActivityEvent",
    "ActivityLog",
    "AuditEvent",
    "AuditLog",
    "AvailabilityRule",
    "Base",
    "Bookmark",
    "CalendarEvent",
    "CalendarEventType",
    "Category",
    "Concept",
    "DailyMetric",
    "Document",
    "EvidenceStrength",
    "KnowledgeEntityType",
    "KnowledgeLink",
    "KnowledgeLinkType",
    "Note",
    "NoteRevision",
    "NoteStatus",
    "PasswordResetToken",
    "Project",
    "ProjectPriority",
    "ProjectStatus",
    "Recommendation",
    "RecommendationPriority",
    "RecommendationStatus",
    "RecommendationType",
    "Resource",
    "ResourceType",
    "Risk",
    "RiskEvaluation",
    "RiskSeverity",
    "RiskStatus",
    "RiskType",
    "Session",
    "Tag",
    "Task",
    "TaskDependency",
    "TaskPriority",
    "TaskStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserRole",
    "WorkSession",
    "WorkSessionStatus",
    "concept_tags",
    "note_tags",
    "project_tags",
    "task_tags",
]
