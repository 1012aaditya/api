"""Document-type strategies (§35).

A document type is the tuple (schema, prompt, parser, normalizer, validator
set). Binding them in one object is what lets receipts, purchase orders and
bank statements be added later as new strategy modules, with the pipeline,
the routing layer and the persistence layer untouched.

Only ``gst_invoice`` is registered. The others are deliberately absent rather
than stubbed — an endpoint that exists but cannot work is worse than one that
does not exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.errors import InvalidRequestError
from app.pipelines.stages.normalize import NormalizationReport, normalize_invoice
from app.pipelines.stages.parse import ParsedExtraction, parse_provider_output
from app.prompts import gst_invoice as gst_invoice_prompt
from app.schemas.invoice import InvoiceData
from app.validators.base import ValidationOutcome
from app.validators.engine import validate_invoice


@dataclass(frozen=True)
class DocumentTypeStrategy:
    document_type: str
    prompt_version: str
    system_prompt: str
    build_user_prompt: Callable[..., str]
    parse: Callable[[dict[str, Any]], ParsedExtraction]
    normalize: Callable[[Any], tuple[Any, NormalizationReport]]
    validate: Callable[..., ValidationOutcome]
    schema: type[InvoiceData]


GST_INVOICE_STRATEGY = DocumentTypeStrategy(
    document_type="gst_invoice",
    prompt_version=gst_invoice_prompt.PROMPT_VERSION,
    system_prompt=gst_invoice_prompt.SYSTEM_PROMPT,
    build_user_prompt=gst_invoice_prompt.build_user_prompt,
    parse=parse_provider_output,
    normalize=normalize_invoice,
    validate=validate_invoice,
    schema=InvoiceData,
)

_STRATEGIES: dict[str, DocumentTypeStrategy] = {
    GST_INVOICE_STRATEGY.document_type: GST_INVOICE_STRATEGY,
}


def register_strategy(strategy: DocumentTypeStrategy) -> None:
    _STRATEGIES[strategy.document_type] = strategy


def get_strategy(document_type: str) -> DocumentTypeStrategy:
    strategy = _STRATEGIES.get(document_type)
    if strategy is None:
        raise InvalidRequestError(
            f"Unsupported document type {document_type!r}.",
            details={"supported": sorted(_STRATEGIES)},
        )
    return strategy


def supported_document_types() -> list[str]:
    return sorted(_STRATEGIES)


__all__ = [
    "DocumentTypeStrategy",
    "GST_INVOICE_STRATEGY",
    "get_strategy",
    "register_strategy",
    "supported_document_types",
]
