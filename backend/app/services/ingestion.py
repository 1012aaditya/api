"""Turning a file a client sent into a settled requirement, or an exception.

This is the spine of the product. A document arrives — over WhatsApp, or
dropped into the dashboard — and by the time this module is finished one of
two things is true:

* a requirement moved to ``VALID`` and the client owes one less thing, or
* an exception exists with a sentence a CA can act on.

There is no third outcome. Nothing is left in a state where the dashboard says
"received" and nobody knows whether it was any good.

Three rules shape everything here, and all three come from the brief:

**AI proposes, rules decide (§G).** The classifier's answer is a suggestion.
Whether a document satisfies a requirement is settled by deterministic checks
— does the GSTIN match the client, does the period match the case — because
those have right answers and a model guessing at them is a liability.

**Never claim what did not happen (§28).** A requirement reaches ``VALID``
because bytes were stored and rules passed. It never reaches it because
something was confident.

**When uncertain, escalate (§16).** Every branch below that cannot decide ends
in ``ExceptionRepository.raise_exception``, not in a default.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    ActorType,
    Client,
    ComplianceCase,
    Document,
    DocumentRequirement,
    DocumentType,
    ExceptionType,
    RequirementStatus,
    ReviewException,
    Severity,
)
from app.pipelines.stages.preprocess import PreparedDocument
from app.repositories.clients import RequirementRepository
from app.repositories.operations import (
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
)
from app.services.classification import Classification, get_classifier
from app.services.file_validation import ValidatedFile
from app.services.tally.matching import normalize_gstin

logger = get_logger("docuparse.ingestion")


@dataclass
class IngestionOutcome:
    """What happened to one document, in terms the dashboard can render."""

    document: Document
    classification: Classification
    requirement: DocumentRequirement | None = None
    exceptions: list[ReviewException] = field(default_factory=list)
    #: Plain sentences, in order, for the agent activity timeline.
    steps: list[str] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return (
            self.requirement is not None
            and self.requirement.status in RequirementStatus.SETTLED
        )

    @property
    def needs_human(self) -> bool:
        return bool(self.exceptions)


def _period_matches(period: str, invoice_date: dt.date | None) -> bool | None:
    """Whether a document's date falls in the case's period.

    Returns ``None`` when the period label is not one this understands, rather
    than guessing. Case periods are free-form on purpose — "2026-09",
    "FY 2025-26", "Q2 FY 2026-27" — and a wrong "period mismatch" exception
    wastes more of a CA's time than no exception at all.
    """
    if invoice_date is None:
        return None
    label = period.strip()
    if len(label) == 7 and label[4] == "-" and label[:4].isdigit():
        try:
            year, month = int(label[:4]), int(label[5:])
        except ValueError:
            return None
        return invoice_date.year == year and invoice_date.month == month
    return None


class IngestionService:
    """Runs one document through classification, extraction and validation."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._exceptions = ExceptionRepository(db)
        self._events = AgentEventRepository(db)
        self._requirements = RequirementRepository(db)

    # -- classification ------------------------------------------------

    async def classify(
        self,
        document: Document,
        prepared: PreparedDocument,
        *,
        threshold: float | None = None,
    ) -> Classification:
        """Propose a type and record it on the document.

        The proposal is stored whatever its confidence — a low-confidence guess
        is still evidence a reviewer wants to see. What the confidence controls
        is whether anything is done with it.
        """
        classification = get_classifier().classify(prepared, filename=document.filename)
        document.classified_type = classification.document_type
        document.classification_confidence = f"{classification.confidence:.2f}"

        limit = threshold
        if limit is None:
            policy = await AgentPolicyRepository(self._db).get_or_create(
                document.organization_id
            )
            limit = policy.threshold()

        await self._events.record(
            organization_id=document.organization_id,
            client_id=document.client_id,
            case_id=document.case_id,
            action="document.classified",
            summary=(
                f"Classified {document.filename} as "
                f"{DocumentType.label(classification.document_type)} "
                f"({classification.confidence:.0%} confident)"
            ),
            entity_type="document",
            entity_id=document.id,
            details={
                "document_type": classification.document_type,
                "confidence": classification.confidence,
                "evidence": list(classification.evidence),
                "source": classification.source,
                "threshold": limit,
            },
        )
        return classification

    # -- direction: purchase or sales ----------------------------------

    def resolve_invoice_direction(
        self, classification: Classification, invoice: dict, client: Client | None
    ) -> tuple[str, str | None]:
        """Decide whether an invoice is a purchase or a sale, from the GSTINs.

        Nothing printed on an invoice says which side you are on — it depends
        entirely on whose books it is going into. So the classifier's guess is
        overruled here by a fact: if the *buyer* is our client, the client
        bought it, and it is a purchase invoice for them.

        Returns the type and the reason, or the unchanged type and ``None``
        when the GSTINs cannot settle it.
        """
        if classification.document_type not in DocumentType.INVOICE_LIKE:
            return classification.document_type, None
        if classification.document_type in (
            DocumentType.CREDIT_NOTE,
            DocumentType.DEBIT_NOTE,
        ):
            return classification.document_type, None
        if client is None or not client.gstin:
            return classification.document_type, None

        client_gstin = normalize_gstin(client.gstin)
        buyer = normalize_gstin((invoice.get("buyer") or {}).get("gstin"))
        seller = normalize_gstin((invoice.get("supplier") or {}).get("gstin"))

        if buyer and buyer == client_gstin:
            return (
                DocumentType.PURCHASE_INVOICE,
                "the buyer GSTIN on the invoice is the client's, so they bought it",
            )
        if seller and seller == client_gstin:
            return (
                DocumentType.SALES_INVOICE,
                "the supplier GSTIN on the invoice is the client's, so they sold it",
            )
        return classification.document_type, None

    # -- validation against the client and the case --------------------

    async def check_against_case(
        self,
        *,
        document: Document,
        invoice: dict,
        client: Client | None,
        case: ComplianceCase | None,
        requirement: DocumentRequirement | None,
    ) -> list[ReviewException]:
        """Deterministic checks that only make sense once a client is known.

        The existing validators already checked the invoice against itself —
        GSTIN checksums, whether the arithmetic adds up. These are the checks
        that need context: is this the right client, is this the right month.
        """
        raised: list[ReviewException] = []

        if client is not None and client.gstin:
            client_gstin = normalize_gstin(client.gstin)
            buyer = normalize_gstin((invoice.get("buyer") or {}).get("gstin"))
            seller = normalize_gstin((invoice.get("supplier") or {}).get("gstin"))
            present = {value for value in (buyer, seller) if value}
            if present and client_gstin not in present:
                raised.append(
                    await self._exceptions.raise_exception(
                        organization_id=document.organization_id,
                        client_id=document.client_id,
                        case_id=document.case_id,
                        document_id=document.id,
                        requirement_id=requirement.id if requirement else None,
                        type=ExceptionType.GSTIN_MISMATCH,
                        severity=Severity.HIGH,
                        message=(
                            f"Neither GSTIN on {document.filename} belongs to "
                            f"{client.display_name}. This may be another client's "
                            "document, or the client's GSTIN may be wrong in their record."
                        ),
                        dedupe_key=f"gstin_mismatch:{document.id}",
                        details={
                            "client_gstin": client_gstin,
                            "supplier_gstin": seller or None,
                            "buyer_gstin": buyer or None,
                        },
                    )
                )

        if case is not None:
            raw_date = invoice.get("invoice_date")
            invoice_date: dt.date | None = None
            if raw_date:
                try:
                    invoice_date = dt.date.fromisoformat(str(raw_date)[:10])
                except ValueError:
                    invoice_date = None
            verdict = _period_matches(case.period, invoice_date)
            if verdict is False:
                raised.append(
                    await self._exceptions.raise_exception(
                        organization_id=document.organization_id,
                        client_id=document.client_id,
                        case_id=case.id,
                        document_id=document.id,
                        requirement_id=requirement.id if requirement else None,
                        type=ExceptionType.PERIOD_MISMATCH,
                        severity=Severity.WARNING,
                        message=(
                            f"{document.filename} is dated {invoice_date:%d %b %Y}, "
                            f"which is outside {case.label}."
                        ),
                        dedupe_key=f"period_mismatch:{document.id}",
                        details={
                            "invoice_date": invoice_date.isoformat() if invoice_date else None,
                            "period": case.period,
                        },
                    )
                )

        return raised

    # -- the requirement this document answers -------------------------

    async def attach_to_requirement(
        self,
        *,
        document: Document,
        case: ComplianceCase,
        document_type: str,
        classification: Classification,
    ) -> tuple[DocumentRequirement | None, list[ReviewException]]:
        """Find the requirement this document satisfies, or say why not."""
        raised: list[ReviewException] = []

        requirement = await self._requirements.match_document_type(
            document.organization_id, case.id, document_type
        )
        if requirement is None:
            wanted = await self._requirements.for_case(document.organization_id, case.id)
            raised.append(
                await self._exceptions.raise_exception(
                    organization_id=document.organization_id,
                    client_id=document.client_id,
                    case_id=case.id,
                    document_id=document.id,
                    type=ExceptionType.WRONG_DOCUMENT_TYPE,
                    severity=Severity.WARNING,
                    message=(
                        f"{document.filename} looks like a "
                        f"{DocumentType.label(document_type)}, which {case.label} "
                        "does not ask for."
                    ),
                    dedupe_key=f"unexpected_type:{document.id}",
                    details={
                        "classified_as": document_type,
                        "confidence": classification.confidence,
                        "expected": [r.document_type for r in wanted],
                    },
                )
            )
            return None, raised

        if requirement.status in (
            RequirementStatus.MISSING,
            RequirementStatus.REQUESTED,
            RequirementStatus.INVALID,
        ):
            requirement.move_to(RequirementStatus.RECEIVED)
            requirement.received_document_id = document.id
        return requirement, raised

    # -- the whole thing -----------------------------------------------

    async def ingest(
        self,
        *,
        document: Document,
        file: ValidatedFile,
        prepared: PreparedDocument,
        client: Client | None,
        case: ComplianceCase | None,
        invoice: dict | None = None,
        validation_passed: bool | None = None,
        validation_summary: str | None = None,
    ) -> IngestionOutcome:
        """Classify, place, check, and settle — or file an exception.

        ``invoice`` and ``validation_passed`` come from the existing extraction
        pipeline when one ran. They are optional because not every document is
        an invoice: a bank statement is classified and attached without any
        invoice extraction at all.
        """
        outcome = IngestionOutcome(document=document, classification=None)  # type: ignore[arg-type]

        classification = await self.classify(document, prepared)
        outcome.classification = classification
        outcome.steps.append(
            f"Read as {DocumentType.label(classification.document_type)} "
            f"({classification.confidence:.0%})"
        )

        policy = await AgentPolicyRepository(self._db).get_or_create(document.organization_id)
        threshold = policy.threshold()

        document_type = classification.document_type
        if invoice:
            resolved, reason = self.resolve_invoice_direction(classification, invoice, client)
            if reason is not None and resolved != document_type:
                document_type = resolved
                document.classified_type = resolved
                outcome.steps.append(
                    f"Treated as {DocumentType.label(resolved)} because {reason}"
                )

        # Too unsure to act on. File it, leave the requirement alone, and let a
        # person say what it is (§G).
        if not classification.is_confident(threshold):
            outcome.exceptions.append(
                await self._exceptions.raise_exception(
                    organization_id=document.organization_id,
                    client_id=document.client_id,
                    case_id=document.case_id,
                    document_id=document.id,
                    type=ExceptionType.CLASSIFICATION_UNCERTAIN,
                    severity=Severity.WARNING,
                    message=(
                        f"Could not tell what {document.filename} is "
                        f"({classification.confidence:.0%} confident it is a "
                        f"{DocumentType.label(classification.document_type)}). "
                        "Please set the type."
                    ),
                    dedupe_key=f"classification:{document.id}",
                    details={
                        "proposed": classification.document_type,
                        "confidence": classification.confidence,
                        "threshold": threshold,
                        "evidence": list(classification.evidence),
                        "alternatives": [
                            {"document_type": name, "score": score}
                            for name, score in classification.alternatives
                        ],
                    },
                )
            )
            outcome.steps.append("Held for review: the document type is unclear")
            await self._db.flush()
            return outcome

        if case is None:
            outcome.steps.append("Stored; not linked to a case")
            await self._db.flush()
            return outcome

        requirement, raised = await self.attach_to_requirement(
            document=document,
            case=case,
            document_type=document_type,
            classification=classification,
        )
        outcome.requirement = requirement
        outcome.exceptions.extend(raised)
        if requirement is None:
            await self._db.flush()
            return outcome

        outcome.steps.append(f"Matched the {requirement.label} requirement")

        if invoice is not None:
            outcome.exceptions.extend(
                await self.check_against_case(
                    document=document,
                    invoice=invoice,
                    client=client,
                    case=case,
                    requirement=requirement,
                )
            )

        blocking = [
            item for item in outcome.exceptions if item.severity in Severity.BLOCKING
        ]

        # Only the document the requirement is actually waiting on may move
        # it. A second file arriving — a stranger's invoice, a duplicate, a
        # re-send after a person settled it by hand — is a question about
        # that file; dragging a settled requirement back into review would
        # ask the firm to re-check work that was already fine, and would try
        # transitions its state machine rightly refuses. What keeps the case
        # off "ready" is the open exception (see refresh_case_status), not a
        # regressed row.
        this_document_owns_it = requirement.received_document_id == document.id

        if not this_document_owns_it:
            # Say which it is. "Already settled" reads as good news when the
            # requirement is really sitting in review because the last
            # document turned out to be somebody else's.
            held = requirement.status in RequirementStatus.WITH_THE_FIRM
            state = "with a person for review" if held else "already settled"
            outcome.steps.append(
                f"{requirement.label} is {state} by another document; "
                "this one is filed against the case"
            )
        elif blocking:
            requirement.move_to(
                RequirementStatus.NEEDS_REVIEW, reason=blocking[0].message
            )
            outcome.steps.append("Held for review")
        elif validation_passed is False:
            requirement.move_to(
                RequirementStatus.NEEDS_REVIEW,
                reason=validation_summary or "Validation did not pass.",
            )
            outcome.exceptions.append(
                await self._exceptions.raise_exception(
                    organization_id=document.organization_id,
                    client_id=document.client_id,
                    case_id=case.id,
                    document_id=document.id,
                    requirement_id=requirement.id,
                    type=ExceptionType.VALIDATION_FAILED,
                    severity=Severity.WARNING,
                    message=(
                        validation_summary
                        or f"{document.filename} did not pass validation."
                    ),
                    dedupe_key=f"validation:{document.id}",
                    details={},
                )
            )
            outcome.steps.append("Held for review: validation did not pass")
        elif invoice is None and document_type in DocumentType.INVOICE_LIKE:
            # An invoice nobody has read is not a satisfied requirement. Every
            # check that stops one client's paperwork landing in another's
            # books — the GSTIN, the period, the arithmetic — needs the fields
            # off the page, and they are not here yet. The document is in; it
            # is not cleared (§28).
            if requirement.status == RequirementStatus.RECEIVED:
                outcome.steps.append(f"{requirement.label} received, waiting to be read")
        else:
            requirement.move_to(RequirementStatus.PROCESSING)
            requirement.move_to(RequirementStatus.VALID)
            outcome.steps.append(f"{requirement.label} is complete")

        await self._events.record(
            organization_id=document.organization_id,
            client_id=document.client_id,
            case_id=case.id,
            actor_type=ActorType.AGENT,
            action="requirement.updated",
            summary=f"{requirement.label} → {requirement.status}",
            entity_type="document_requirement",
            entity_id=requirement.id,
            details={"status": requirement.status, "document_id": document.id},
        )
        await self._db.flush()
        return outcome


async def refresh_case_status(db: AsyncSession, case: ComplianceCase) -> str:
    """Recompute a case's status from its requirements and open exceptions.

    Derived, never set by hand or by a model. A case is BLOCKED because rows
    say documents are outstanding — which means the dashboard and the database
    cannot disagree.
    """
    from app.models import CaseStatus

    requirements = await RequirementRepository(db).for_case(
        case.organization_id, case.id
    )
    required = [r for r in requirements if r.required]
    open_exceptions = await ExceptionRepository(db).open_for_case(
        case.organization_id, case.id
    )

    if not required:
        new_status = CaseStatus.NOT_STARTED
    elif any(r.status in RequirementStatus.OUTSTANDING for r in required):
        new_status = CaseStatus.BLOCKED
    elif any(r.status in RequirementStatus.WITH_THE_FIRM for r in required):
        new_status = CaseStatus.IN_PROGRESS
    elif all(r.status in RequirementStatus.SETTLED for r in required):
        new_status = CaseStatus.READY
    else:
        new_status = CaseStatus.IN_PROGRESS

    # An unresolved high-severity finding keeps a case off READY however tidy
    # the requirement rows look.
    if new_status == CaseStatus.READY and any(
        item.severity in Severity.BLOCKING for item in open_exceptions
    ):
        new_status = CaseStatus.ESCALATED

    if case.status != new_status and case.status != CaseStatus.COMPLETED:
        case.status = new_status
        case.updated_at = utcnow()
        await db.flush()
    return case.status
