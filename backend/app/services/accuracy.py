"""Measuring how well extraction actually reads invoices.

The README says no accuracy figure has ever been measured, and that stays
true until somebody runs this against real documents. This is the rig that
lets them — and, as much as anything, it is built to stop the resulting
number from being flattering.

**Not every mistake is the same mistake**, and a single "94% accurate" hides
the distinction that matters most to a CA:

* ``wrong``  — a value was read, and it is not the one on the page. This is
  the dangerous failure. It looks like data, it flows into a Tally voucher,
  and nobody notices until a return is filed.
* ``invented`` — the page had nothing there and a value appeared anyway.
  Dangerous for the same reason, and a direct violation of the rule that a
  field DocuParse could not read is null (§13).
* ``missed`` — the page had a value and the answer was null. This costs
  somebody time. It does not cost them a wrong filing, and an honest
  ``null`` is the behaviour the product promises.

Reporting those three as one number would be the accuracy equivalent of
reporting a guess as a reading. So they are counted separately, and the
headline figure counts a miss as a failure while saying how many of the
failures were merely misses.

**Small samples are reported as small.** Every rate comes with a Wilson
interval, because "94% on 50 invoices" and "94% on 5,000 invoices" are not
the same claim, and a deck that prints the first without its interval is
making the second.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Verdict = Literal["correct", "wrong", "missed", "invented", "correctly_absent"]

#: Two readings of the same rupee amount may differ in the last paisa
#: through rounding, and calling that an extraction error would bury real
#: ones in noise. Anything larger is a different number.
AMOUNT_TOLERANCE = Decimal("0.01")

#: The fields worth measuring, and how to compare each. Line items are
#: deliberately excluded: a per-line comparison needs an alignment step of
#: its own, and reporting a number for it before that exists would be a
#: claim about something unmeasured.
COMPARABLE: dict[str, str] = {
    "invoice_number": "identifier",
    "invoice_date": "date",
    "due_date": "date",
    "supplier.name": "text",
    "supplier.gstin": "identifier",
    "buyer.name": "text",
    "buyer.gstin": "identifier",
    "place_of_supply": "text",
    "subtotal": "amount",
    "total": "amount",
    "tax.cgst": "amount",
    "tax.sgst": "amount",
    "tax.igst": "amount",
    "tax.total_tax": "amount",
    "irn": "identifier",
}

_SPACE = re.compile(r"\s+")


def _dig(obj: Any, path: str) -> Any:
    """Walk 'tax.cgst' through nested models or dicts alike."""
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = (
            current.get(part) if isinstance(current, dict) else getattr(current, part, None)
        )
    return current


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if hasattr(value, "amount"):  # a Money
        return _as_decimal(value.amount)
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _as_date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _SPACE.sub(" ", str(value)).strip()
    return text or None


# --- one field ----------------------------------------------------------


@dataclass
class FieldComparison:
    document: str
    path: str
    expected: str | None
    actual: str | None
    verdict: Verdict
    confidence: float | None = None
    #: Set when the values differ only in a way a person would call the same
    #: thing — case, spacing, punctuation. Still counted as wrong, because
    #: the promise is to preserve what is printed; surfaced so an operator
    #: can see at a glance that a run of "failures" is one formatting bug.
    near_miss: bool = False

    @property
    def failed(self) -> bool:
        return self.verdict in {"wrong", "missed", "invented"}


def compare_field(
    path: str, kind: str, expected: Any, actual: Any, *, document: str, confidence: float | None
) -> FieldComparison:
    def out(verdict: Verdict, *, near_miss: bool = False) -> FieldComparison:
        return FieldComparison(
            document=document,
            path=path,
            expected=_as_text(expected),
            actual=_as_text(actual),
            verdict=verdict,
            confidence=confidence,
            near_miss=near_miss,
        )

    expected_absent = expected is None or _as_text(expected) is None
    actual_absent = actual is None or _as_text(actual) is None

    if expected_absent and actual_absent:
        return out("correctly_absent")
    if expected_absent:
        # Nothing on the page, a value in the answer. The product's central
        # promise is that this never happens.
        return out("invented")
    if actual_absent:
        return out("missed")

    if kind == "amount":
        want, got = _as_decimal(expected), _as_decimal(actual)
        if want is None or got is None:
            return out("wrong")
        return out("correct" if abs(want - got) <= AMOUNT_TOLERANCE else "wrong")

    if kind == "date":
        want_date, got_date = _as_date(expected), _as_date(actual)
        if want_date is None or got_date is None:
            return out("wrong")
        return out("correct" if want_date == got_date else "wrong")

    want_text, got_text = _as_text(expected) or "", _as_text(actual) or ""
    if kind == "identifier":
        if want_text == got_text:
            return out("correct")
        # An invoice number differing only in case or punctuation is still
        # not what was printed — §13 says preserve it exactly — but knowing
        # that is the whole difference is worth an operator's attention.
        loose = re.sub(r"[^a-z0-9]", "", want_text.lower()) == re.sub(
            r"[^a-z0-9]", "", got_text.lower()
        )
        return out("wrong", near_miss=loose)

    # Free text — a supplier's name is printed differently on every invoice
    # they issue, so this is deliberately lenient about case and spacing.
    if want_text.casefold() == got_text.casefold():
        return out("correct")
    return out("wrong")


# --- one document -------------------------------------------------------


def compare_invoice(
    expected: dict[str, Any],
    actual: Any,
    *,
    document: str,
    confidences: dict[str, float] | None = None,
) -> list[FieldComparison]:
    """Compare one extraction against what the page actually says.

    Only the fields the truth file supplies are measured. A CA filling in
    50 invoices is not going to record every optional field, and scoring
    them against blanks they never claimed would invent failures.
    """
    confidences = confidences or {}
    results: list[FieldComparison] = []
    for path, kind in COMPARABLE.items():
        if path not in expected:
            continue
        results.append(
            compare_field(
                path,
                kind,
                expected.get(path),
                _dig(actual, path),
                document=document,
                confidence=confidences.get(path),
            )
        )
    return results


# --- adding it up -------------------------------------------------------


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval on a proportion.

    Reported with every rate because "94% on 50 invoices" and "94% on 5,000"
    are different claims, and printing the first without its interval is
    making the second.
    """
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    ) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class FieldScore:
    path: str
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    invented: int = 0
    correctly_absent: int = 0

    @property
    def measured(self) -> int:
        """Comparisons where the page had a value. A field the truth file
        left blank everywhere is not evidence of anything."""
        return self.correct + self.wrong + self.missed

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.measured if self.measured else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.correct, self.measured) if self.measured else None


@dataclass
class CalibrationBucket:
    """Does a reported confidence mean anything?

    The question a CA actually needs answered is not "how accurate is it"
    but "can I trust the ones it is sure about and review only the rest".
    That is only true if the number is calibrated, which has to be measured
    rather than assumed.
    """

    label: str
    low: float
    high: float
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None


@dataclass
class AccuracyReport:
    documents: int = 0
    fields: dict[str, FieldScore] = field(default_factory=dict)
    disagreements: list[FieldComparison] = field(default_factory=list)
    buckets: list[CalibrationBucket] = field(default_factory=list)
    #: Documents that could not be read at all, and why.
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def measured(self) -> int:
        return sum(score.measured for score in self.fields.values())

    @property
    def correct(self) -> int:
        return sum(score.correct for score in self.fields.values())

    @property
    def invented(self) -> int:
        return sum(score.invented for score in self.fields.values())

    @property
    def missed(self) -> int:
        return sum(score.missed for score in self.fields.values())

    @property
    def wrong(self) -> int:
        return sum(score.wrong for score in self.fields.values())

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.measured if self.measured else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.correct, self.measured) if self.measured else None

    @property
    def is_publishable(self) -> bool:
        """Whether this run supports saying a number out loud (§30, §33).

        Not a statistical threshold so much as a floor below which the
        interval is so wide that quoting the midpoint misleads. Below it,
        the report prints the range and refuses the headline.
        """
        low, high = self.interval or (0.0, 1.0)
        return self.documents >= 100 and self.measured >= 500 and (high - low) <= 0.10


def score(
    comparisons: list[FieldComparison],
    *,
    documents: int,
    failures: list[tuple[str, str]] | None = None,
) -> AccuracyReport:
    report = AccuracyReport(documents=documents, failures=failures or [])

    buckets = [
        CalibrationBucket("said it was sure (>= 0.85)", 0.85, 1.01),
        CalibrationBucket("said it was unsure (0.60-0.85)", 0.60, 0.85),
        CalibrationBucket("said it was guessing (< 0.60)", 0.0, 0.60),
    ]

    for comparison in comparisons:
        entry = report.fields.setdefault(comparison.path, FieldScore(comparison.path))
        setattr(entry, comparison.verdict, getattr(entry, comparison.verdict) + 1)
        if comparison.failed:
            report.disagreements.append(comparison)

        if comparison.confidence is not None and comparison.verdict in {"correct", "wrong"}:
            for bucket in buckets:
                if bucket.low <= comparison.confidence < bucket.high:
                    bucket.total += 1
                    if comparison.verdict == "correct":
                        bucket.correct += 1
                    break

    report.buckets = [bucket for bucket in buckets if bucket.total]
    return report
