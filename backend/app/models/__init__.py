"""SQLAlchemy models. Importing this package registers every table on Base."""

from app.db.base import Base
from app.models.api_key import APIKey
from app.models.batch import DocumentBatch
from app.models.document import Document, DocumentStatus
from app.models.extraction import Extraction, ExtractionStatus
from app.models.extraction_job import ExtractionJob, JobStatus
from app.models.organization import Organization
from app.models.tally import Ledger, LedgerAlias, TallySettings
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.models.validation_result import ValidationResult
from app.models.webhook import DeliveryStatus, Webhook, WebhookDelivery, WebhookEvent

__all__ = [
    "APIKey",
    "Base",
    "DeliveryStatus",
    "Document",
    "DocumentBatch",
    "DocumentStatus",
    "Extraction",
    "ExtractionJob",
    "ExtractionStatus",
    "JobStatus",
    "Ledger",
    "LedgerAlias",
    "Organization",
    "TallySettings",
    "UsageEvent",
    "User",
    "ValidationResult",
    "Webhook",
    "WebhookDelivery",
    "WebhookEvent",
]
