"""Extraction tiers (§34).

Not every invoice needs a vision model, and calling one anyway is where the
cost goes. Each tier is a cheaper way of answering the same question:

    0  qr          the IRP's own signed record, where the document carries it
    1  text_layer  labelled fields read straight out of a digital PDF
    2  model       a vision/language model on the page images

The router tries them in order and escalates only when what it has is not
good enough — and "good enough" is decided by the validation engine that
already exists, not by a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.pipelines.stages.parse import FieldEvidence
from app.schemas.invoice import InvoiceData


class ExtractionTier(StrEnum):
    QR = "qr"
    TEXT_LAYER = "text_layer"
    MODEL = "model"


#: Rough relative cost, for logging and for reasoning about routing. Not money.
TIER_COST: dict[ExtractionTier, int] = {
    ExtractionTier.QR: 1,
    ExtractionTier.TEXT_LAYER: 2,
    ExtractionTier.MODEL: 100,
}


@dataclass
class TierOutput:
    """What one tier managed to read. Always partial; never padded out."""

    tier: ExtractionTier
    invoice: InvoiceData
    evidence: dict[str, FieldEvidence] = field(default_factory=dict)
    populated_paths: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def field_count(self) -> int:
        return len(self.populated_paths)
