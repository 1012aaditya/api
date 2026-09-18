"""SQLAlchemy models. Importing this package registers every table on Base."""

from app.db.base import Base
from app.models.api_key import APIKey
from app.models.document import Document, DocumentStatus
from app.models.extraction import Extraction, ExtractionStatus
from app.models.organization import Organization
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.models.validation_result import ValidationResult

__all__ = [
    "APIKey",
    "Base",
    "Document",
    "DocumentStatus",
    "Extraction",
    "ExtractionStatus",
    "Organization",
    "UsageEvent",
    "User",
    "ValidationResult",
]
