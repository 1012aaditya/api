"""Ledger matching and voucher construction.

These run without a database: the matcher and the builder are pure functions
over model instances, which is deliberate — the part that decides what enters
somebody's books should be testable without any I/O at all.
"""

from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.core.errors import InvalidFileError
from app.models import Ledger, LedgerAlias, TallySettings
from app.services.tally.ledgers import parse, parse_csv, parse_tally_xml
from app.services.tally.matching import (
    SUGGESTION_FLOOR,
    LedgerMatcher,
    normalize_name,
    similarity,
)
from app.services.tally.voucher import build_voucher, render_envelope, summarize


def ledger(ledger_id: str, name: str, gstin: str | None = None, active: bool = True) -> Ledger:
    return Ledger(
        id=ledger_id,
        organization_id="org_1",
        name=name,
        normalized_name=normalize_name(name),
        gstin=gstin,
        parent_group="Sundry Creditors",
        is_active=active,
    )


def _ledger_named(name: str) -> Ledger:
    return ledger("led_1", name)


def alias(key_type: str, match_key: str, ledger_id: str) -> LedgerAlias:
    return LedgerAlias(
        id=f"lal_{match_key}",
        organization_id="org_1",
        ledger_id=ledger_id,
        key_type=key_type,
        match_key=match_key,
    )


def settings(**overrides) -> TallySettings:
    values = {
        "organization_id": "org_1",
        "company_name": "Acme Books",
        "voucher_type": "Purchase",
        "purchase_ledger": "Purchase 18%",
        "cgst_ledger": "Input CGST",
        "sgst_ledger": "Input SGST",
        "igst_ledger": "Input IGST",
        "round_off_ledger": "Round Off",
    }
    values.update(overrides)
    return TallySettings(**values)


def invoice(**overrides) -> dict:
    base = {
        "invoice_number": "INV-2025-0042",
        "invoice_date": "2025-09-14",
        "place_of_supply": "27-Maharashtra",
        "supplier": {"name": "Acme Traders", "gstin": "27AAACA1111A1Z5"},
        "subtotal": 100000,
        "total": 118000,
        "tax": {"taxable_amount": 100000, "cgst": 9000, "sgst": 9000},
    }
    base.update(overrides)
    return base


def build(inv=None, ledgers=None, aliases=(), **setting_overrides):
    return build_voucher(
        document_id="doc_1",
        filename="invoice.pdf",
        invoice=inv if inv is not None else invoice(),
        settings=settings(**setting_overrides),
        matcher=LedgerMatcher(
            ledgers
            if ledgers is not None
            else [ledger("led_1", "Acme Traders", "27AAACA1111A1Z5")],
            aliases,
        ),
    )


# --- normalising -------------------------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        ("ACME TRADERS PVT. LTD.", "Acme Traders Private Limited"),
        ("Acme Traders & Co.", "Acme Traders and Company"),
        ("  acme   traders  ", "Acme Traders"),
        ("Acme Traders Pvt Ltd", "ACME TRADERS"),
    ],
)
def test_the_same_supplier_spelled_differently_normalises_the_same(left, right):
    assert normalize_name(left) == normalize_name(right)


def test_different_suppliers_do_not_collide():
    assert normalize_name("Acme Traders") != normalize_name("Acme Trading")


def test_similarity_is_bounded():
    assert similarity("acme traders", "acme traders") == 1.0
    assert similarity("", "acme") == 0.0
    assert similarity(normalize_name("Acme Traders"), normalize_name("Zenith Logistics")) < 0.3


@pytest.mark.parametrize(
    "supplier,ledger",
    [
        ("Udupi Software Systems Pvt Ltd", "Udupi Software Systems Pvt Ltd - Bengaluru"),
        ("Acme Traders", "Acme Traders (Mumbai)"),
        ("Acme Traders", "Acme Traders - Unit II"),
    ],
)
def test_a_branch_suffix_still_scores_above_the_suggestion_floor(supplier, ledger):
    """The real-world naming that character similarity alone scores too low.

    A live run turned up exactly this and offered the reviewer nothing at all.
    """
    score = similarity(normalize_name(supplier), normalize_name(ledger))
    assert score >= SUGGESTION_FLOOR, score

    matcher = LedgerMatcher([_ledger_named(ledger)])
    match = matcher.match(name=supplier, gstin=None)
    # Suggested, never silently matched: it is still a different ledger name.
    assert not match.matched
    assert match.suggestions and match.suggestions[0][1] == ledger


# --- matching ----------------------------------------------------------


def test_gstin_beats_a_different_printed_name():
    """A registration number is identity; the printed name is just a string."""
    matcher = LedgerMatcher([ledger("led_1", "Acme Traders", "27AAACA1111A1Z5")])
    match = matcher.match(name="SOMETHING ELSE ENTIRELY", gstin="27aaaca1111a1z5")
    assert match.method == "gstin"
    assert match.matched
    assert match.ledger_id == "led_1"


def test_a_confirmed_alias_resolves_a_name_that_matches_nothing():
    matcher = LedgerMatcher(
        [ledger("led_1", "Acme Traders")],
        [alias("name", "beta supplies", "led_1")],
    )
    match = matcher.match(name="Beta Supplies Pvt Ltd", gstin=None)
    assert match.method == "alias"
    assert match.ledger_id == "led_1"


def test_an_alias_pointing_at_a_deleted_ledger_is_ignored():
    matcher = LedgerMatcher(
        [ledger("led_1", "Acme Traders")], [alias("name", "beta", "led_gone")]
    )
    assert not matcher.match(name="Beta", gstin=None).matched


def test_exact_name_match_after_normalising():
    matcher = LedgerMatcher([ledger("led_1", "Acme Traders")])
    match = matcher.match(name="ACME TRADERS PVT. LTD.", gstin=None)
    assert match.method == "exact_name"
    assert match.matched


def test_two_ledgers_with_the_same_normalised_name_never_resolve_silently():
    """Picking whichever was imported first would post to the wrong books."""
    matcher = LedgerMatcher(
        [ledger("led_1", "Acme Traders Pvt Ltd"), ledger("led_2", "Acme Traders Limited")]
    )
    match = matcher.match(name="Acme Traders", gstin=None)
    assert not match.matched
    assert match.method == "unmatched"
    assert {s[0] for s in match.suggestions} == {"led_1", "led_2"}
    assert matcher.is_ambiguous("Acme Traders")


def test_a_near_miss_is_a_suggestion_and_never_a_match():
    matcher = LedgerMatcher([ledger("led_1", "Acme Traders")])
    match = matcher.match(name="Acme Traderz", gstin=None)
    assert not match.matched
    assert match.confirmed is False
    assert match.suggestions[0][0] == "led_1"


def test_an_unrelated_name_gets_no_suggestions():
    matcher = LedgerMatcher([ledger("led_1", "Acme Traders")])
    assert matcher.match(name="Zenith Logistics", gstin=None).suggestions == ()


def test_inactive_ledgers_are_not_offered():
    matcher = LedgerMatcher([ledger("led_1", "Acme Traders", active=False)])
    assert len(matcher) == 0
    assert not matcher.match(name="Acme Traders", gstin=None).matched


# --- the voucher -------------------------------------------------------


def test_a_clean_invoice_balances_to_zero():
    voucher = build()
    assert voucher.postable
    assert voucher.blockers == []
    assert voucher.imbalance == Decimal(0)


def test_debits_are_negative_and_the_credit_is_positive():
    """Tally's sign convention. Getting this backwards reverses the entry."""
    voucher = build()
    legs = {entry.ledger_name: entry for entry in voucher.entries}

    assert legs["Purchase 18%"].is_debit is True
    assert legs["Acme Traders"].is_debit is False

    xml = voucher.to_xml()
    assert "<LEDGERNAME>Purchase 18%</LEDGERNAME>" in xml
    assert "<AMOUNT>-100000.00</AMOUNT>" in xml  # debit
    assert "<AMOUNT>118000.00</AMOUNT>" in xml  # credit
    assert xml.count("<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>") == 3
    assert xml.count("<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>") == 1


def test_the_date_is_tally_format():
    assert "<DATE>20250914</DATE>" in build().to_xml()


def test_interstate_invoice_posts_igst():
    voucher = build(
        invoice(tax={"taxable_amount": 100000, "igst": 18000}, total=118000)
    )
    names = [entry.ledger_name for entry in voucher.entries]
    assert "Input IGST" in names
    assert "Input CGST" not in names
    assert voucher.imbalance == Decimal(0)


def test_an_unmatched_supplier_blocks_the_voucher():
    voucher = build(ledgers=[ledger("led_9", "Somebody Else")])
    assert not voucher.postable
    assert any("does not" in blocker for blocker in voucher.blockers)


def test_no_purchase_ledger_blocks_the_voucher():
    voucher = build(purchase_ledger=None)
    assert not voucher.postable
    assert any("purchase ledger" in blocker for blocker in voucher.blockers)


def test_tax_charged_without_a_configured_ledger_blocks():
    voucher = build(cgst_ledger=None)
    assert not voucher.postable
    assert any("CGST" in blocker for blocker in voucher.blockers)


def test_a_missing_date_blocks_because_tally_needs_one():
    voucher = build(invoice(invoice_date=None))
    assert not voucher.postable
    assert any("date" in blocker for blocker in voucher.blockers)


def test_a_missing_invoice_number_blocks():
    voucher = build(invoice(invoice_number=None))
    assert not voucher.postable


def test_parts_that_do_not_add_up_are_never_posted():
    """The rule this whole module exists for."""
    voucher = build(invoice(total=125000))  # 100000 + 18000 tax ≠ 125000
    assert not voucher.postable
    assert voucher.entries == []
    assert any("do not add up" in blocker for blocker in voucher.blockers)
    assert "7000" in " ".join(voucher.blockers)


def test_a_rounding_difference_goes_to_the_round_off_ledger_and_is_reported():
    voucher = build(invoice(total="118000.40"))
    assert voucher.postable
    assert voucher.imbalance == Decimal(0)
    round_off = [e for e in voucher.entries if e.ledger_name == "Round Off"]
    assert round_off and round_off[0].amount == Decimal("0.40")
    assert voucher.notes and "Round Off" in voucher.notes[0]


def test_a_rounding_difference_without_a_round_off_ledger_blocks():
    voucher = build(invoice(total="118000.40"), round_off_ledger=None)
    assert not voucher.postable
    assert any("round-off ledger" in blocker for blocker in voucher.blockers)


def test_a_difference_larger_than_tolerance_is_not_treated_as_rounding():
    voucher = build(invoice(total="118002.00"))
    assert not voucher.postable
    assert any("do not add up" in blocker for blocker in voucher.blockers)


def test_an_explicit_round_off_on_the_invoice_is_honoured():
    voucher = build(
        invoice(
            total=118000,
            round_off="-0.50",
            tax={"taxable_amount": "100000.50", "cgst": 9000, "sgst": 9000},
        )
    )
    assert voucher.postable, voucher.blockers
    assert voucher.imbalance == Decimal(0)


def test_no_total_means_nothing_to_post():
    voucher = build(invoice(total=None))
    assert not voucher.postable


def test_no_taxable_value_blocks_rather_than_guessing_it():
    voucher = build(invoice(subtotal=None, tax={"cgst": 9000, "sgst": 9000}))
    assert not voucher.postable
    assert any("taxable" in blocker for blocker in voucher.blockers)


# --- the file ----------------------------------------------------------


def test_the_envelope_is_well_formed_xml():
    xml = render_envelope([build()], company_name="Acme Books")
    root = ET.fromstring(xml)
    assert root.tag == "ENVELOPE"
    assert root.findtext(".//SVCURRENTCOMPANY") == "Acme Books"
    assert len(root.findall(".//VOUCHER")) == 1


def test_amounts_in_the_file_sum_to_zero():
    xml = render_envelope([build()], company_name=None)
    amounts = [
        Decimal(node.text or "0")
        for node in ET.fromstring(xml).findall(".//ALLLEDGERENTRIES.LIST/AMOUNT")
    ]
    assert sum(amounts) == Decimal(0)


def test_a_blocked_voucher_never_reaches_the_file():
    good = build()
    bad = build(invoice(total=125000))
    xml = render_envelope([good, bad], company_name=None)
    assert len(ET.fromstring(xml).findall(".//VOUCHER")) == 1
    assert "125000" not in xml


def test_special_characters_are_escaped_not_injected():
    voucher = build(
        invoice(supplier={"name": "Tata & Sons <Ltd>", "gstin": "27AAACA1111A1Z5"}),
        ledgers=[ledger("led_1", "Tata & Sons <Ltd>", "27AAACA1111A1Z5")],
    )
    xml = render_envelope([voucher], company_name="A & B")
    root = ET.fromstring(xml)  # would raise if the & broke the document
    assert root.findtext(".//PARTYLEDGERNAME") == "Tata & Sons <Ltd>"


def test_the_document_id_rides_along_so_a_re_import_does_not_duplicate():
    assert "<REMOTEID>doc_1</REMOTEID>" in build().to_xml()


def test_summarize_groups_unmatched_suppliers():
    unmatched = build(ledgers=[ledger("led_9", "Acme Traders Holdings")])
    counts = summarize([build(), unmatched])
    assert counts["postable"] == 1
    assert counts["blocked"] == 1
    assert counts["unmatched_suppliers"][0]["name"] == "Acme Traders"
    assert counts["unmatched_suppliers"][0]["suggestions"]


# --- parsing the ledger master ------------------------------------------

TALLY_XML = b"""<?xml version="1.0"?>
<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>
<TALLYMESSAGE><LEDGER NAME="Acme Traders">
  <PARENT>Sundry Creditors</PARENT><PARTYGSTIN>27AAACA1111A1Z5</PARTYGSTIN>
</LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Input CGST"><PARENT>Duties &amp; Taxes</PARENT></LEDGER></TALLYMESSAGE>
</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""


def test_tally_master_xml_is_parsed():
    parsed = parse_tally_xml(TALLY_XML)
    assert [entry.name for entry in parsed] == ["Acme Traders", "Input CGST"]
    assert parsed[0].gstin == "27AAACA1111A1Z5"
    assert parsed[1].parent_group == "Duties & Taxes"


def test_a_billion_laughs_payload_is_refused():
    """A small upload that expands to gigabytes. Every variant needs a DOCTYPE."""
    payload = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
        b"<ENVELOPE><LEDGER NAME='&lol2;'/></ENVELOPE>"
    )
    with pytest.raises(InvalidFileError, match="DOCTYPE"):
        parse(payload, "master.xml")


def test_an_empty_upload_is_refused():
    with pytest.raises(InvalidFileError, match="empty"):
        parse(b"   ", "master.xml")


def test_xml_without_ledgers_says_so_usefully():
    with pytest.raises(InvalidFileError, match="List of Accounts"):
        parse_tally_xml(b"<ENVELOPE><BODY/></ENVELOPE>")


def test_csv_headers_are_matched_loosely():
    payload = b"Ledger Name,GST No,Under\nBeta Supplies,29AAACB2222B1Z3,Sundry Creditors\n"
    parsed = parse_csv(payload)
    assert parsed[0].name == "Beta Supplies"
    assert parsed[0].gstin == "29AAACB2222B1Z3"
    assert parsed[0].parent_group == "Sundry Creditors"


def test_csv_without_a_name_column_is_refused():
    with pytest.raises(InvalidFileError, match="ledger-name column"):
        parse_csv(b"amount,date\n100,2025-01-01\n")


def test_the_format_is_chosen_by_content_not_extension():
    """A Tally export saved as .csv should still import."""
    assert parse(TALLY_XML, "ledgers.csv")[0].name == "Acme Traders"
