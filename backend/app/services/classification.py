"""Deciding what a document is.

The existing extraction pipeline assumes it was handed an invoice. Once
documents arrive over WhatsApp from a client who was asked for four different
things, that assumption stops holding: the file might be a bank statement, a
GSTR-2B, or a photograph of somebody's Aadhaar card.

The rule from the brief (§G) is that **AI proposes and business rules
validate**, and it is enforced structurally here: a classifier returns a
*proposal* with a confidence, and the caller decides what to do with it.
Nothing in this module writes state.

The default classifier reads the document's own text and matches known
phrases. That is deliberate and not a placeholder:

* it works with no AI provider configured, which demo mode needs (§31);
* it costs nothing and takes milliseconds;
* it is inspectable — when it says "bank_statement, 0.86", the evidence that
  produced that number is on the result and can be shown to a reviewer.

A model-backed classifier can be registered alongside it for the documents
this one cannot place. The interface is the same, so the caller does not
change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.models import DocumentType
from app.pipelines.stages.preprocess import PreparedDocument

#: Below this, a proposal is never acted on automatically. The organization's
#: policy can raise it; nothing lowers it silently.
DEFAULT_THRESHOLD = 0.80


@dataclass(frozen=True)
class Classification:
    """What a document appears to be, and why.

    ``evidence`` exists so a reviewer is never asked to trust a bare number.
    "0.86 because it says 'Statement of Account' and has an IFSC code" is a
    claim somebody can check; "0.86" is not.
    """

    document_type: str
    confidence: float
    evidence: tuple[str, ...] = ()
    #: Types that also fit, best first. Shown when a human has to choose.
    alternatives: tuple[tuple[str, float], ...] = ()
    source: str = "rules"

    def is_confident(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.confidence >= threshold and self.document_type != DocumentType.OTHER


class DocumentClassifier(Protocol):
    """Anything that can propose a document type."""

    name: str

    def classify(
        self, prepared: PreparedDocument, *, filename: str | None = None
    ) -> Classification: ...


# --- the evidence table ------------------------------------------------
#
# Each entry is (regex, weight, human-readable reason). Weights are additive
# and capped; they are not probabilities and are not presented as such.


@dataclass(frozen=True)
class Signal:
    pattern: re.Pattern[str]
    weight: float
    reason: str


def _signal(expression: str, weight: float, reason: str) -> Signal:
    return Signal(re.compile(expression, re.IGNORECASE), weight, reason)


_SIGNALS: dict[str, tuple[Signal, ...]] = {
    DocumentType.BANK_STATEMENT: (
        _signal(r"\bstatement of account\b", 0.45, "says 'Statement of Account'"),
        _signal(r"\baccount statement\b", 0.40, "says 'Account Statement'"),
        _signal(r"\b(ifsc|ifs code)\b", 0.25, "carries an IFSC code"),
        _signal(r"\b(opening|closing)\s+balance\b", 0.30, "has opening/closing balance"),
        _signal(r"\b(withdrawal|deposit)s?\b", 0.15, "has withdrawal/deposit columns"),
        _signal(r"\bnarration\b", 0.15, "has a narration column"),
    ),
    DocumentType.GSTR_2B: (
        _signal(r"\bgstr[\s\-_]?2b\b", 0.75, "says GSTR-2B"),
        _signal(r"\binput tax credit\b", 0.15, "mentions input tax credit"),
        _signal(r"\bitc available\b", 0.20, "has an 'ITC available' section"),
        _signal(r"\bauto[\s\-]?drafted\b", 0.15, "says 'auto-drafted'"),
    ),
    DocumentType.CREDIT_NOTE: (
        _signal(r"\bcredit note\b", 0.70, "says 'Credit Note'"),
        _signal(r"\bcr\.?\s?note\b", 0.35, "says 'Cr Note'"),
    ),
    DocumentType.DEBIT_NOTE: (
        _signal(r"\bdebit note\b", 0.70, "says 'Debit Note'"),
        _signal(r"\bdr\.?\s?note\b", 0.35, "says 'Dr Note'"),
    ),
    DocumentType.TDS_CERTIFICATE: (
        _signal(r"\bform\s+16a?\b", 0.60, "says 'Form 16'"),
        _signal(r"\bcertificate under section 203\b", 0.55, "cites section 203"),
        _signal(r"\btds certificate\b", 0.55, "says 'TDS Certificate'"),
        _signal(r"\btraces\b", 0.20, "mentions TRACES"),
    ),
    DocumentType.GST_CERTIFICATE: (
        _signal(r"\bcertificate of registration\b", 0.50, "says 'Certificate of Registration'"),
        _signal(r"\bgoods and services tax\b", 0.20, "mentions GST"),
        _signal(r"\bform gst reg[\s\-]?06\b", 0.60, "is Form GST REG-06"),
    ),
    DocumentType.ITR: (
        _signal(r"\bindian income tax return\b", 0.65, "says 'Income Tax Return'"),
        _signal(r"\backnowledgement number\b", 0.25, "has an acknowledgement number"),
        _signal(r"\bitr[\s\-]?[1-7]\b", 0.45, "names an ITR form"),
    ),
    DocumentType.PAN: (
        _signal(r"\bpermanent account number\b", 0.65, "says 'Permanent Account Number'"),
        _signal(r"\bincome tax department\b", 0.20, "issued by the Income Tax Department"),
    ),
    DocumentType.AADHAAR: (
        _signal(r"\baadhaar\b", 0.65, "says 'Aadhaar'"),
        _signal(r"\bunique identification authority\b", 0.30, "issued by UIDAI"),
    ),
    DocumentType.SALES_INVOICE: (
        _signal(r"\btax invoice\b", 0.35, "says 'Tax Invoice'"),
        _signal(r"\binvoice\s*(no|number|#)\b", 0.20, "has an invoice number"),
    ),
}

#: Words that distinguish a purchase invoice from a sales one once we know it
#: is an invoice at all. Which side you are on is not written on the page —
#: it depends on whose books these are — so this stays a weak hint and the
#: real answer comes from matching the GSTIN against the client (§I).
_INVOICE_DIRECTION = (
    _signal(r"\b(purchase|bill from|vendor|supplier)\b", 0.10, "reads like a purchase"),
)

_ADDITIONAL_INVOICE_EVIDENCE = (
    _signal(r"\bgstin\b", 0.10, "carries a GSTIN"),
    _signal(r"\b(cgst|sgst|igst)\b", 0.15, "has GST tax lines"),
    _signal(r"\bhsn\b", 0.10, "has HSN codes"),
)

#: A filename is a hint from a human, worth a little and never much — clients
#: name files "scan_001.pdf" at least as often as "bank statement.pdf".
_FILENAME_HINTS: tuple[tuple[str, str, float], ...] = (
    (r"bank|statement|passbook", DocumentType.BANK_STATEMENT, 0.15),
    (r"2b|gstr", DocumentType.GSTR_2B, 0.15),
    (r"credit[\s_-]?note|cn[\s_-]", DocumentType.CREDIT_NOTE, 0.15),
    (r"debit[\s_-]?note", DocumentType.DEBIT_NOTE, 0.15),
    (r"purchase|bill", DocumentType.PURCHASE_INVOICE, 0.12),
    (r"sales|invoice", DocumentType.SALES_INVOICE, 0.10),
    (r"tds|form[\s_-]?16", DocumentType.TDS_CERTIFICATE, 0.15),
    (r"\bpan\b", DocumentType.PAN, 0.15),
    (r"aadha?ar", DocumentType.AADHAAR, 0.15),
    (r"itr", DocumentType.ITR, 0.12),
)


@dataclass
class _Score:
    total: float = 0.0
    reasons: list[str] = field(default_factory=list)


class RulesClassifier:
    """Matches known phrases in the document's own text.

    Needs no AI and no network. Returns ``OTHER`` with a low confidence when
    it cannot tell — which is the honest answer, and routes the document to a
    human rather than guessing.
    """

    name = "rules"

    def classify(
        self, prepared: PreparedDocument, *, filename: str | None = None
    ) -> Classification:
        text = (prepared.embedded_text or "").strip()
        scores: dict[str, _Score] = {}

        def add(document_type: str, weight: float, reason: str) -> None:
            score = scores.setdefault(document_type, _Score())
            score.total += weight
            score.reasons.append(reason)

        if text:
            for document_type, signals in _SIGNALS.items():
                for signal in signals:
                    if signal.pattern.search(text):
                        add(document_type, signal.weight, signal.reason)

            # Generic invoice evidence lifts both invoice types together.
            for signal in _ADDITIONAL_INVOICE_EVIDENCE:
                if signal.pattern.search(text):
                    for document_type in (
                        DocumentType.SALES_INVOICE,
                        DocumentType.PURCHASE_INVOICE,
                    ):
                        add(document_type, signal.weight, signal.reason)

            for signal in _INVOICE_DIRECTION:
                if signal.pattern.search(text):
                    add(DocumentType.PURCHASE_INVOICE, signal.weight, signal.reason)

        if filename:
            lowered = filename.lower()
            for expression, document_type, weight in _FILENAME_HINTS:
                if re.search(expression, lowered):
                    label = DocumentType.label(document_type).lower()
                    add(document_type, weight, f"filename suggests {label}")

        if not scores:
            return Classification(
                document_type=DocumentType.OTHER,
                confidence=0.0,
                evidence=(
                    ("no readable text layer",)
                    if not text
                    else ("nothing in the text matched a known document type",)
                ),
                source=self.name,
            )

        ranked = sorted(scores.items(), key=lambda item: -item[1].total)
        best_type, best = ranked[0]

        # A score is capped at 0.97: this reads phrases, it does not understand
        # the document, and certainty it has not earned would be a lie.
        confidence = min(best.total, 0.97)

        # Two plausible readings should not look like one confident one.
        if len(ranked) > 1:
            runner_up = ranked[1][1].total
            if runner_up > 0 and best.total - runner_up < 0.20:
                confidence = min(confidence, 0.70)

        return Classification(
            document_type=best_type,
            confidence=round(confidence, 3),
            evidence=tuple(dict.fromkeys(best.reasons)),
            alternatives=tuple(
                (name, round(min(score.total, 0.97), 3)) for name, score in ranked[1:4]
            ),
            source=self.name,
        )


_classifier: DocumentClassifier = RulesClassifier()


def get_classifier() -> DocumentClassifier:
    return _classifier


def set_classifier(classifier: DocumentClassifier | None) -> None:
    """Swap the classifier. Used by tests and by a model-backed adapter."""
    global _classifier
    _classifier = classifier or RulesClassifier()
