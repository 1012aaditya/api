"""Combining what several tiers read (§11).

Precedence is by directness, not by cost: the e-invoice QR is the IRP's own
record, the text layer is the literal characters in the file, and the model
is a reading of a picture of those characters. Earlier sources fill a field
first; later ones only fill what is still empty.

Disagreements are **recorded, never silently resolved.** "The QR says 118000
and the printed total reads 125000" is exactly the kind of thing an
accounts-payable team needs to see, and burying it would defeat the point of
having two sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.pipelines.stages.parse import FieldEvidence
from app.pipelines.tiers import ExtractionTier, TierOutput
from app.schemas.invoice import InvoiceData

logger = get_logger("docuparse.merge")

#: Most direct first. Anything not listed sorts last.
PRECEDENCE: tuple[ExtractionTier, ...] = (
    ExtractionTier.QR,
    ExtractionTier.TEXT_LAYER,
    ExtractionTier.MODEL,
)

_IGNORED_PATHS = frozenset({"document_type"})


@dataclass(frozen=True)
class SourceConflict:
    """Two tiers read the same field differently."""

    path: str
    kept: Any
    kept_source: ExtractionTier
    discarded: Any
    discarded_source: ExtractionTier

    def to_payload(self) -> dict[str, Any]:
        return {
            "field": self.path,
            "kept": str(self.kept),
            "kept_source": str(self.kept_source),
            "conflicting": str(self.discarded),
            "conflicting_source": str(self.discarded_source),
        }


@dataclass
class MergedExtraction:
    invoice: InvoiceData
    evidence: dict[str, FieldEvidence] = field(default_factory=dict)
    field_sources: dict[str, ExtractionTier] = field(default_factory=dict)
    conflicts: list[SourceConflict] = field(default_factory=list)
    tiers_used: list[ExtractionTier] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def conflicting_paths(self) -> set[str]:
        return {conflict.path for conflict in self.conflicts}


def _comparable(value: Any) -> Any:
    """Compare on meaning, not formatting: 118000 == Decimal('118000.00')."""
    if isinstance(value, str):
        return value.strip().upper()
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return value.normalize()
    except Exception:  # noqa: BLE001
        pass
    return value


def _walk(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else key
            if path in _IGNORED_PATHS:
                continue
            leaves.extend(_walk(value, path))
    elif isinstance(payload, list):
        # Lists are taken whole, by the first source that has a non-empty one:
        # interleaving line items from two readings would invent a third.
        if payload:
            leaves.append((prefix, payload))
    elif payload is not None:
        leaves.append((prefix, payload))
    return leaves


def _assign(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def merge_tiers(outputs: list[TierOutput]) -> MergedExtraction:
    ordered = sorted(
        outputs,
        key=lambda out: PRECEDENCE.index(out.tier)
        if out.tier in PRECEDENCE
        else len(PRECEDENCE),
    )

    merged: dict[str, Any] = {}
    evidence: dict[str, FieldEvidence] = {}
    sources: dict[str, ExtractionTier] = {}
    conflicts: list[SourceConflict] = []
    notes: list[str] = []

    for output in ordered:
        notes.extend(output.notes)
        for path, value in _walk(output.invoice.model_dump(mode="python")):
            if path not in sources:
                _assign(merged, path, value)
                sources[path] = output.tier
                if cited := output.evidence.get(path):
                    evidence[path] = cited
                continue

            if _comparable(merged_value := _read(merged, path)) != _comparable(value):
                conflicts.append(
                    SourceConflict(
                        path=path,
                        kept=merged_value,
                        kept_source=sources[path],
                        discarded=value,
                        discarded_source=output.tier,
                    )
                )
            elif path not in evidence and (cited := output.evidence.get(path)):
                # Same value, but this source cited where it read it.
                evidence[path] = cited

    invoice = InvoiceData.model_validate(merged) if merged else InvoiceData()
    if conflicts:
        logger.info(
            "merge.conflicts",
            count=len(conflicts),
            fields=[c.path for c in conflicts][:10],
        )

    return MergedExtraction(
        invoice=invoice,
        evidence=evidence,
        field_sources=sources,
        conflicts=conflicts,
        tiers_used=[output.tier for output in ordered],
        notes=notes,
    )


def _read(payload: dict[str, Any], path: str) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor
