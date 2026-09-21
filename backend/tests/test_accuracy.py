"""Measuring extraction accuracy.

What is being defended here is the honesty of a number somebody will
eventually put in front of a customer. Two ways that number goes wrong:

* **It flatters.** A miss counted as a pass, a hard document quietly
  skipped, a headline figure quoted from five invoices.
* **It hides the distinction that matters.** A wrong value reaches a GST
  filing. An honest null costs somebody ten minutes. Averaging them into
  one percentage tells a CA nothing about their actual risk.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.services.accuracy import (
    AccuracyReport,
    FieldComparison,
    compare_field,
    compare_invoice,
    score,
    wilson_interval,
)


def compare(kind: str, expected, actual, *, path: str = "f") -> FieldComparison:
    return compare_field(path, kind, expected, actual, document="d.pdf", confidence=None)


# --- the distinction that matters ---------------------------------------


def test_a_value_that_is_not_on_the_page_is_invented_not_merely_wrong():
    """The product's central promise is that a field it could not read is
    null. A value appearing where the page had none is the violation."""
    result = compare("identifier", None, "INV-0042")

    assert result.verdict == "invented"
    assert result.failed


def test_an_honest_null_is_a_miss_not_an_invention():
    """It costs somebody ten minutes. It does not reach a filing."""
    result = compare("identifier", "INV-0042", None)

    assert result.verdict == "missed"
    assert result.failed


def test_nothing_expected_and_nothing_read_is_correct_not_a_gap():
    result = compare("identifier", None, None)

    assert result.verdict == "correctly_absent"
    assert not result.failed


def test_the_three_failures_are_counted_apart():
    """Reporting them as one number is the accuracy equivalent of
    reporting a guess as a reading."""
    report = score(
        [
            compare("identifier", "A", "A"),
            compare("identifier", "A", "B"),
            compare("identifier", "A", None),
            compare("identifier", None, "C"),
        ],
        documents=1,
    )

    assert (report.correct, report.wrong, report.missed, report.invented) == (1, 1, 1, 1)


# --- comparing values ---------------------------------------------------


@pytest.mark.parametrize(
    "expected,actual",
    [("118000.00", Decimal("118000.00")), ("118000", "118000.004"), ("1,18,000.00", "118000.0")],
)
def test_the_same_rupee_amount_written_differently_is_correct(expected, actual):
    assert compare("amount", expected, actual).verdict == "correct"


def test_amounts_a_rupee_apart_are_wrong():
    """A rounding paisa is noise. A rupee is a different number."""
    assert compare("amount", "118000.00", "118001.00").verdict == "wrong"


@pytest.mark.parametrize(
    "written", ["2026-09-14", "14-09-2026", "14/09/2026", "14.09.2026"]
)
def test_a_date_is_the_same_date_however_the_ca_typed_it(written):
    result = compare("date", written, dt.date(2026, 9, 14))

    assert result.verdict == "correct"


def test_the_wrong_date_is_wrong():
    assert compare("date", "2026-09-14", dt.date(2026, 9, 15)).verdict == "wrong"


def test_a_supplier_name_in_different_case_is_the_same_supplier():
    """A supplier prints their name differently on every invoice."""
    assert compare("text", "Ambika Wholesale", "AMBIKA WHOLESALE").verdict == "correct"


def test_an_invoice_number_differing_in_case_is_still_wrong_but_flagged():
    """§13 says preserve the invoice number exactly, so this is a failure.
    But knowing a run of failures is one formatting bug is worth saying."""
    result = compare("identifier", "INV-2026/0042", "inv-2026-0042")

    assert result.verdict == "wrong"
    assert result.near_miss


def test_a_genuinely_different_invoice_number_is_not_a_near_miss():
    result = compare("identifier", "INV-0042", "INV-0043")

    assert result.verdict == "wrong"
    assert not result.near_miss


# --- only what was claimed ----------------------------------------------


def test_a_field_the_truth_file_left_blank_is_not_measured():
    """Nobody keys in every optional field for 200 invoices, and scoring
    against blanks they never claimed would invent failures."""

    class Extraction:
        invoice_number = "INV-0042"
        place_of_supply = "29-Karnataka"

    results = compare_invoice(
        {"invoice_number": "INV-0042"}, Extraction(), document="d.pdf"
    )

    assert [r.path for r in results] == ["invoice_number"]


def test_a_field_asserted_absent_is_measured():
    """An explicit "-" in the file claims the page has nothing there, so a
    value appearing for it counts as invented."""

    class Extraction:
        irn = "SOMETHING"

    results = compare_invoice({"irn": None}, Extraction(), document="d.pdf")

    assert results[0].verdict == "invented"


def test_nested_paths_are_read():
    class Tax:
        cgst = Decimal("9000.00")

    class Extraction:
        tax = Tax()

    results = compare_invoice({"tax.cgst": "9000"}, Extraction(), document="d.pdf")

    assert results[0].verdict == "correct"


# --- refusing to flatter ------------------------------------------------


def test_a_small_run_refuses_to_support_a_headline_figure():
    """"94% on 50 invoices" and "94% on 5,000" are different claims (§30)."""
    report = score([compare("identifier", "A", "A")] * 40, documents=10)

    assert report.accuracy == 1.0
    assert not report.is_publishable


def test_a_large_clean_run_supports_one():
    report = score([compare("identifier", "A", "A")] * 600, documents=120)

    assert report.is_publishable


def test_a_large_but_uncertain_run_still_refuses():
    """Enough documents, but the interval is too wide to quote a midpoint."""
    comparisons = [compare("identifier", "A", "A")] * 300 + [
        compare("identifier", "A", "B")
    ] * 300
    report = score(comparisons, documents=120)

    low, high = report.interval
    assert high - low <= 0.10 or not report.is_publishable


def test_a_document_that_could_not_be_read_is_reported_not_dropped():
    """A run that quietly skipped its hard cases would report an accuracy
    belonging to the easy ones."""
    report = score([], documents=0, failures=[("scan-07.pdf", "PdfReadError: damaged")])

    assert report.failures == [("scan-07.pdf", "PdfReadError: damaged")]


def test_the_interval_widens_as_the_sample_shrinks():
    wide = wilson_interval(9, 10)
    narrow = wilson_interval(900, 1000)

    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_an_empty_run_claims_nothing():
    report = AccuracyReport()

    assert report.accuracy is None
    assert not report.is_publishable


# --- is the confidence worth anything? ----------------------------------


def test_calibration_separates_what_it_was_sure_of_from_what_it_guessed():
    """The question is not "how accurate" but "can I trust the confident
    ones and review only the rest" — which is only true if measured."""
    confident_right = [
        FieldComparison("d", "f", "A", "A", "correct", confidence=0.95) for _ in range(9)
    ]
    confident_wrong = [FieldComparison("d", "f", "A", "B", "wrong", confidence=0.95)]
    unsure_wrong = [
        FieldComparison("d", "f", "A", "B", "wrong", confidence=0.4) for _ in range(8)
    ]

    report = score(confident_right + confident_wrong + unsure_wrong, documents=10)
    sure = next(b for b in report.buckets if b.low == 0.85)
    guessing = next(b for b in report.buckets if b.low == 0.0)

    assert sure.accuracy == pytest.approx(0.9)
    assert guessing.accuracy == 0.0


def test_a_miss_does_not_enter_the_calibration_buckets():
    """A null carries no claim to have been confident about."""
    report = score(
        [FieldComparison("d", "f", "A", None, "missed", confidence=0.95)], documents=1
    )

    assert report.buckets == []
