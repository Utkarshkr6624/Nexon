"""Persistence layer: repositories own SQL and nothing else."""

from app.repositories.activity import ActivityRepository
from app.repositories.analytics import AnalyticsRepository
from app.repositories.audit import AuditRepository
from app.repositories.knowledge import (
    BookmarkRepository,
    CategoryRepository,
    ConceptRepository,
    DocumentRepository,
    KnowledgeLinkRepository,
    NoteRepository,
    ResourceRepository,
)
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.planner import (
    AvailabilityRuleRepository,
    CalendarEventRepository,
    WorkSessionRepository,
)
from app.repositories.project import ProjectRepository
from app.repositories.session import SessionRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository

__all__ = [
    "ActivityRepository",
    "AnalyticsRepository",
    "AuditRepository",
    "AvailabilityRuleRepository",
    "BookmarkRepository",
    "CalendarEventRepository",
    "CategoryRepository",
    "ConceptRepository",
    "DocumentRepository",
    "KnowledgeLinkRepository",
    "NoteRepository",
    "PasswordResetRepository",
    "ProjectRepository",
    "ResourceRepository",
    "SessionRepository",
    "TagRepository",
    "TaskRepository",
    "UserRepository",
    "WorkSessionRepository",
]
