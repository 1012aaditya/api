"""Importing a firm's client list.

The thing being protected here is trust. A CA is pointing this at their
real client list — the one their practice runs on — and the failure that
matters is not a crash, it is a quiet one: a row silently dropped, a GSTIN
quietly "corrected", a client created twice because somebody re-uploaded
the file after fixing three rows.

So every row in the file comes back with a verdict, nothing is written
before it has been shown, and a duplicate is flagged rather than merged.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.client_import import normalise_indian_phone, plan_import

# Checksum-valid and fictional, as everywhere else in this repository.
GSTIN_A = "29AABCU9603R1ZJ"
GSTIN_B = "27AAACA1111A1ZS"


def plan(csv: str, *, gstins=(), phones=(), codes=()):
    return plan_import(
        csv.encode("utf-8"),
        existing_gstins=set(gstins),
        existing_phones=set(phones),
        existing_codes=set(codes),
    )


def data(response: httpx.Response):
    assert response.status_code < 300, response.text
    return response.json()["data"]


# --- reading what the office actually exports ---------------------------


def test_a_tally_export_is_understood():
    """Tally calls it "Ledger Name", and that is what most CA firms have."""
    result = plan(
        "Ledger Name,GSTIN/UIN,Mobile No\n"
        f"ABC Traders,{GSTIN_A},9800012345\n"
    )

    assert result.error is None
    assert result.counts["create"] == 1
    row = result.planned[0]
    assert row.values["name"] == "ABC Traders"
    assert row.values["gstin"] == GSTIN_A
    assert row.values["whatsapp_phone"] == "+919800012345"


@pytest.mark.parametrize(
    "header",
    ["Name", "Client Name", "Party Name", "Ledger Name", "CUSTOMER NAME", "client_name"],
)
def test_the_name_column_is_recognised_however_it_is_spelt(header):
    result = plan(f"{header},Mobile\nABC Traders,9800012345\n")

    assert result.error is None
    assert result.counts["create"] == 1


def test_a_file_with_no_name_column_is_refused_with_the_reason():
    """Better than importing 200 nameless clients."""
    result = plan(f"GSTIN,Mobile\n{GSTIN_A},9800012345\n")

    assert result.counts["create"] == 0
    assert "Name" in (result.error or "")


def test_semicolons_and_tabs_are_read_too():
    """Excel on a machine with a European locale writes semicolons."""
    result = plan("Name;Mobile\nABC Traders;9800012345\n")

    assert result.counts["create"] == 1


def test_an_excel_byte_order_mark_does_not_break_the_first_column():
    """Excel's "CSV UTF-8" prepends a BOM, which would otherwise make the
    first header unrecognisable and fail the whole file."""
    content = "﻿Name,Mobile\nABC Traders,9800012345\n".encode()

    result = plan_import(
        content, existing_gstins=set(), existing_phones=set(), existing_codes=set()
    )

    assert result.error is None
    assert result.counts["create"] == 1
    assert result.planned[0].values["name"] == "ABC Traders"


def test_a_column_nobody_understands_is_reported_not_ignored():
    """A misspelt "GSTNI" column is exactly what a person should be told
    about, rather than have silently dropped."""
    result = plan(f"Name,GSTNI,Mobile\nABC Traders,{GSTIN_A},9800012345\n")

    assert "GSTNI" in result.ignored_columns


def test_the_trailing_blank_line_every_spreadsheet_writes_is_not_a_failure():
    result = plan("Name,Mobile\nABC Traders,9800012345\n,\n")

    assert result.counts["rows"] == 1
    assert result.counts["skip"] == 0


# --- phone numbers ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9800012345", "+919800012345"),
        ("09800012345", "+919800012345"),
        ("+91 98000 12345", "+919800012345"),
        ("98000-12345", "+919800012345"),
        ("919800012345", "+919800012345"),
        ("  9800012345  ", "+919800012345"),
    ],
)
def test_an_office_spreadsheet_number_becomes_something_whatsapp_can_use(raw, expected):
    number, complaint = normalise_indian_phone(raw)

    assert number == expected
    assert complaint is None


@pytest.mark.parametrize("raw", ["12345", "not a number", "5551234", "N/A", "—"])
def test_a_number_this_cannot_recognise_is_left_out_rather_than_guessed(raw):
    """Inventing a country code would mean messaging a stranger. And a cell
    holding "N/A" must still produce a complaint — dropping it silently is
    the one thing this is not allowed to do."""
    number, complaint = normalise_indian_phone(raw)

    assert number is None
    assert complaint is not None, f"{raw!r} was dropped without saying so"
    assert raw.strip() in complaint


def test_an_empty_cell_is_not_a_complaint():
    """Most clients have no landline, and that is not a problem to report."""
    assert normalise_indian_phone("") == (None, None)
    assert normalise_indian_phone("   ") == (None, None)
    assert normalise_indian_phone(None) == (None, None)


def test_a_real_international_number_is_kept():
    number, complaint = normalise_indian_phone("+14155550123")

    assert number == "+14155550123"
    assert complaint is None


def test_one_mobile_column_fills_whatsapp_too():
    """The overwhelmingly common case: a client has one number."""
    result = plan("Name,Phone\nABC Traders,9800012345\n")

    assert result.planned[0].values["whatsapp_phone"] == "+919800012345"


def test_a_client_with_no_number_is_created_but_flagged():
    """They can still be tracked by hand — but the agent cannot chase them,
    and the firm needs to know that before wondering why."""
    result = plan("Name,GSTIN\nABC Traders," + GSTIN_A + "\n")

    row = result.planned[0]
    assert row.verdict == "create"
    assert any("cannot chase" in w for w in row.warnings)


# --- GSTIN: checked, never corrected ------------------------------------


def test_a_bad_gstin_is_left_out_with_the_reason_and_the_client_still_created():
    """Refusing the whole row over one typo would lose the rest of it."""
    result = plan("Name,GSTIN,Mobile\nABC Traders,29AABCU9603R1ZZ,9800012345\n")

    row = result.planned[0]
    assert row.verdict == "create"
    assert "gstin" not in row.values
    assert any("GSTIN" in w and "check digit" in w for w in row.warnings)


def test_a_bad_gstin_is_never_quietly_written():
    result = plan("Name,GSTIN,Mobile\nABC Traders,NONSENSE,9800012345\n")

    assert result.planned[0].values.get("gstin") is None


def test_a_bad_pan_is_left_out_with_the_reason():
    result = plan("Name,PAN,Mobile\nABC Traders,NOTAPAN,9800012345\n")

    row = result.planned[0]
    assert "pan" not in row.values
    assert any("PAN" in w for w in row.warnings)


# --- duplicates: flagged, never merged ----------------------------------


def test_a_client_the_firm_already_has_is_flagged_not_created_again():
    """What happens when somebody re-uploads after fixing three rows."""
    result = plan(
        f"Name,GSTIN,Mobile\nABC Traders,{GSTIN_A},9800012345\n",
        gstins={GSTIN_A},
    )

    assert result.counts["create"] == 0
    assert result.counts["duplicate"] == 1
    assert "already have" in (result.planned[0].matches or "")


def test_the_same_client_twice_in_one_file_is_caught():
    result = plan(
        f"Name,GSTIN,Mobile\n"
        f"ABC Traders,{GSTIN_A},9800012345\n"
        f"A B C Traders,{GSTIN_A},9800012345\n"
    )

    assert result.counts["create"] == 1
    assert result.counts["duplicate"] == 1
    assert "row 2" in (result.planned[1].matches or "")


def test_a_shared_number_is_flagged_rather_than_merged():
    """Two businesses under one proprietor share a mobile. This cannot tell
    that from a double entry, so it says so and lets a person look."""
    result = plan(
        "Name,Mobile\nSharma Traders,9800012345\nSharma Exports,9800012345\n"
    )

    assert result.planned[1].verdict == "duplicate"
    assert "WhatsApp number" in (result.planned[1].matches or "")


# --- every row is accounted for -----------------------------------------


def test_every_row_comes_back_with_a_verdict():
    """A file of N rows that produced fewer clients has to say what
    happened to the rest."""
    result = plan(
        f"Name,GSTIN,Mobile\n"
        f"Good Traders,{GSTIN_A},9800012345\n"
        f",,9800099999\n"
        f"Dup Traders,{GSTIN_A},9800012346\n"
        f"Fine Traders,{GSTIN_B},9800012347\n"
    )

    assert result.counts["rows"] == 4
    assert result.counts["create"] + result.counts["skip"] + result.counts["duplicate"] == 4
    assert [row.row_number for row in result.planned] == [2, 3, 4, 5]


def test_a_nameless_row_says_why_it_was_skipped():
    result = plan("Name,Mobile\n,9800012345\n")

    assert result.planned[0].verdict == "skip"
    assert "name" in (result.planned[0].reason or "").lower()


# --- through the API ----------------------------------------------------


async def test_the_preview_writes_nothing(client: httpx.AsyncClient, auth_headers):
    csv = f"Name,GSTIN,Mobile\nABC Traders,{GSTIN_A},9800012345\n"

    before = data(await client.get("/v1/clients", headers=auth_headers))
    preview = data(
        await client.post(
            "/v1/clients/import/preview",
            headers=auth_headers,
            files={"file": ("clients.csv", csv, "text/csv")},
        )
    )
    after = data(await client.get("/v1/clients", headers=auth_headers))

    assert preview["counts"]["create"] == 1
    assert preview["applied"] is False
    assert len(after) == len(before), "the preview created a client"


async def test_importing_creates_the_clients_and_reports_what_it_did(
    client: httpx.AsyncClient, auth_headers
):
    csv = (
        f"Name,GSTIN,Mobile\n"
        f"ABC Traders,{GSTIN_A},9800012345\n"
        f"Marigold Retail,{GSTIN_B},9800012346\n"
    )

    result = data(
        await client.post(
            "/v1/clients/import",
            headers=auth_headers,
            files={"file": ("clients.csv", csv, "text/csv")},
        )
    )

    assert result["applied"] is True
    assert result["counts"]["create"] == 2

    clients = data(await client.get("/v1/clients", headers=auth_headers))
    assert {c["name"] for c in clients} >= {"ABC Traders", "Marigold Retail"}


async def test_importing_the_same_file_twice_does_not_double_the_client_list(
    client: httpx.AsyncClient, auth_headers
):
    """The single most likely thing a firm will do."""
    csv = f"Name,GSTIN,Mobile\nABC Traders,{GSTIN_A},9800012345\n"
    files = {"file": ("clients.csv", csv, "text/csv")}

    await client.post("/v1/clients/import", headers=auth_headers, files=files)
    second = data(
        await client.post(
            "/v1/clients/import",
            headers=auth_headers,
            files={"file": ("clients.csv", csv, "text/csv")},
        )
    )

    assert second["counts"]["create"] == 0
    assert second["counts"]["duplicate"] == 1

    clients = data(await client.get("/v1/clients", headers=auth_headers))
    assert sum(1 for c in clients if c["name"] == "ABC Traders") == 1


async def test_a_file_that_cannot_be_read_is_refused_rather_than_half_applied(
    client: httpx.AsyncClient, auth_headers
):
    response = await client.post(
        "/v1/clients/import",
        headers=auth_headers,
        files={"file": ("clients.csv", "GSTIN,Mobile\nx,y\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_one_firms_import_cannot_see_anothers_clients(
    client: httpx.AsyncClient, auth_headers, other_tenant
):
    """Duplicate detection is scoped to the firm doing the importing (§18)."""
    csv = f"Name,GSTIN,Mobile\nABC Traders,{GSTIN_A},9800012345\n"
    files = {"file": ("clients.csv", csv, "text/csv")}

    await client.post("/v1/clients/import", headers=auth_headers, files=files)

    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": other_tenant.email, "password": other_tenant.password},
    )
    other = {"Authorization": f"Bearer {data(signed_in)['access_token']}"}
    theirs = data(
        await client.post(
            "/v1/clients/import/preview",
            headers=other,
            files={"file": ("clients.csv", csv, "text/csv")},
        )
    )

    assert theirs["counts"]["create"] == 1, "another firm's client blocked this import"
