"""DocuParse — Indian GST invoices as structured, validated JSON.

    from docuparse import DocuParse

    client = DocuParse()                      # reads DOCUPARSE_API_KEY
    result = client.extract("invoice.pdf")

    print(result.data.invoice_number)         # "INV-2025-0042"
    print(result.data.total)                  # Decimal("118000.00")
    print(result.validation.overall)          # "passed"
    if result.needs_review():
        print(result.confidence.low_confidence_fields)

A field the pipeline could not read is ``None``, never a guess. Check
``validation`` and ``confidence`` before you post anything to a ledger.
"""

from ._files import SUPPORTED_SUFFIXES
from .client import DEFAULT_BASE_URL, DocuParse
from .errors import (
    APIConnectionError,
    AuthenticationError,
    DocuParseError,
    ExtractionFailed,
    InvalidRequest,
    PermissionDenied,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimited,
    ServerError,
    UnsupportedFile,
)
from .models import (
    Batch,
    BatchSubmission,
    Confidence,
    Document,
    Extraction,
    FieldConfidence,
    Invoice,
    Job,
    LineItem,
    Party,
    Processing,
    RejectedFile,
    TaxBreakdown,
    Validation,
    ValidationCheck,
)

__version__ = "0.1.0"

__all__ = [
    "DocuParse",
    "DEFAULT_BASE_URL",
    "SUPPORTED_SUFFIXES",
    "__version__",
    # errors
    "DocuParseError",
    "APIConnectionError",
    "InvalidRequest",
    "AuthenticationError",
    "PermissionDenied",
    "QuotaExceeded",
    "UnsupportedFile",
    "ExtractionFailed",
    "RateLimited",
    "ProviderUnavailable",
    "ServerError",
    # models
    "Invoice",
    "Party",
    "LineItem",
    "TaxBreakdown",
    "Extraction",
    "Confidence",
    "FieldConfidence",
    "Validation",
    "ValidationCheck",
    "Processing",
    "Job",
    "Document",
    "Batch",
    "BatchSubmission",
    "RejectedFile",
]
