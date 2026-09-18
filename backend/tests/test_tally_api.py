"""The Tally workflow end to end: import a master, match, preview, download."""

from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

import httpx

from tests.conftest import StubProvider, Tenant
from tests.fixtures.invoices import build_invoice_pdf

LEDGERS = "/v1/tally/ledgers"
SETTINGS = "/v1/tally/settings"
MATCHES = "/v1/tally/matches"
PREVIEW = "/v1/tally/preview"
VOUCHERS = "/v1/tally/vouchers.xml"

MASTER_XML = """<?xml version="1.0"?>
<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>
<TALLYMESSAGE><LEDGER NAME="{supplier}">
  <PARENT>Sundry Creditors</PARENT><PARTYGSTIN>{gstin}</PARTYGSTIN>
</LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Purchase 18%"><PARENT>Purchase Accounts</PARENT></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Input CGST"><PARENT>Duties &amp; Taxes</PARENT></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Input SGST"><PARENT>Duties &amp; Taxes</PARENT></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Round Off"><PARENT>Indirect Expenses</PARENT></LEDGER></TALLYMESSAGE>
</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""

GOOD_SETTINGS = {
    "company_name": "Acme Books",
    "voucher_type": "Purchase",
    "purchase_ledger": "Purchase 18%",
    "cgst_ledger": "Input CGST",
    "sgst_ledger": "Input SGST",
    "igst_ledger": "Input IGST",
    "round_off_ledger": "Round Off",
}


async def import_master(
    client: httpx.AsyncClient, tenant: Tenant, *, supplier: str, gstin: str
) -> dict:
    payload = MASTER_XML.format(supplier=supplier, gstin=gstin).encode()
    response = await client.post(
        LEDGERS,
        files={"file": ("master.xml", payload, "application/xml")},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def extract_one(
    client: httpx.AsyncClient, tenant: Tenant
) -> dict:
    response = await client.post(
        "/v1/invoices/extract",
        files={"file": ("invoice.pdf", build_invoice_pdf(), "application/pdf")},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def configure(client: httpx.AsyncClient, tenant: Tenant, **overrides) -> dict:
    body = dict(GOOD_SETTINGS)
    body.update(overrides)
    response = await client.put(SETTINGS, json=body, headers=tenant.headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --- the ledger master --------------------------------------------------


async def test_importing_a_master_then_listing_it(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    result = await import_master(
        client, tenant, supplier="Udupi Software Systems", gstin="29AABCU9603R1ZJ"
    )
    assert result["imported"] == 5
    assert result["replaced"] == 0

    listed = await client.get(LEDGERS, headers=tenant.headers)
    names = [row["name"] for row in listed.json()["data"]]
    assert "Purchase 18%" in names
    assert "Udupi Software Systems" in names


async def test_a_csv_master_is_accepted_too(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    payload = b"Name,GSTIN,Under\nBeta Supplies,29AAACB2222B1Z3,Sundry Creditors\n"
    response = await client.post(
        LEDGERS,
        files={"file": ("ledgers.csv", payload, "text/csv")},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["imported"] == 1


async def test_a_malicious_xml_upload_is_refused(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    payload = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        b"<ENVELOPE><LEDGER NAME='&lol;'/></ENVELOPE>"
    )
    response = await client.post(
        LEDGERS,
        files={"file": ("evil.xml", payload, "application/xml")},
        headers=tenant.headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_file"


async def test_one_organization_cannot_see_anothers_ledgers(
    client: httpx.AsyncClient, tenant: Tenant, other_tenant: Tenant
) -> None:
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    response = await client.get(LEDGERS, headers=other_tenant.headers)
    assert response.status_code == 200
    assert response.json()["data"] == []


# --- settings -----------------------------------------------------------


async def test_settings_round_trip_and_report_configured(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    before = await client.get(SETTINGS, headers=tenant.headers)
    assert before.json()["data"]["configured"] is False

    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    data = await configure(client, tenant)
    assert data["configured"] is True
    assert data["purchase_ledger"] == "Purchase 18%"
    assert data["ledger_count"] == 5


async def test_a_ledger_name_that_is_not_in_the_master_is_flagged(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    """Tally rejects a voucher naming a ledger that does not exist."""
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    data = await configure(client, tenant, purchase_ledger="Purchases That Do Not Exist")
    assert "Purchases That Do Not Exist" in data["unknown_ledgers"]
    # The configured ones that do exist are not flagged.
    assert "Input CGST" not in data["unknown_ledgers"]


# --- preview ------------------------------------------------------------


async def test_preview_reports_an_unmatched_supplier_with_suggestions(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    result = await extract_one(client, tenant)
    supplier = result["data"]["supplier"]["name"]
    assert supplier

    # A master whose supplier ledger is close but not equal, and no GSTIN.
    await import_master(client, tenant, supplier=f"{supplier} Holdings", gstin="")
    await configure(client, tenant)

    response = await client.get(PREVIEW, headers=tenant.headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["postable"] == 0
    assert data["blocked"] == 1
    unmatched = data["unmatched_suppliers"]
    assert unmatched and unmatched[0]["name"] == supplier
    assert unmatched[0]["suggestions"][0]["ledger_name"] == f"{supplier} Holdings"


async def test_confirming_a_match_makes_the_voucher_postable(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """The moment the product earns its keep: a human answers once."""
    use_provider(stub_provider)
    result = await extract_one(client, tenant)
    supplier = result["data"]["supplier"]["name"]

    await import_master(client, tenant, supplier=f"{supplier} Holdings", gstin="")
    await configure(client, tenant)

    ledgers = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"]
    target = next(row for row in ledgers if row["name"].endswith("Holdings"))

    confirmed = await client.post(
        MATCHES,
        json={
            "ledger_id": target["id"],
            "supplier_name": supplier,
            "supplier_gstin": result["data"]["supplier"].get("gstin"),
        },
        headers=tenant.headers,
    )
    assert confirmed.status_code == 201, confirmed.text
    # Both keys remembered when both are known.
    assert {row["key_type"] for row in confirmed.json()["data"]} == {"name", "gstin"}

    after = (await client.get(PREVIEW, headers=tenant.headers)).json()["data"]
    assert after["postable"] == 1
    assert after["unmatched_suppliers"] == []
    assert after["vouchers"][0]["match_method"] == "alias"


async def test_confirming_against_another_organizations_ledger_is_refused(
    client: httpx.AsyncClient, tenant: Tenant, other_tenant: Tenant
) -> None:
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    ledger_id = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"][0]["id"]

    response = await client.post(
        MATCHES,
        json={"ledger_id": ledger_id, "supplier_name": "Whoever"},
        headers=other_tenant.headers,
    )
    assert response.status_code == 404


async def test_a_match_needs_something_to_match_on(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    ledger_id = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"][0]["id"]
    response = await client.post(
        MATCHES, json={"ledger_id": ledger_id}, headers=tenant.headers
    )
    assert response.status_code == 400


async def test_reimporting_the_master_keeps_confirmed_matches(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    """Their corrections are the asset. A re-export must not wipe them."""
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    ledgers = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"]
    target = next(row for row in ledgers if row["name"] == "Acme Traders")
    created = await client.post(
        MATCHES,
        json={"ledger_id": target["id"], "supplier_name": "ACME TRDRS"},
        headers=tenant.headers,
    )
    assert created.status_code == 201, created.text

    result = await import_master(
        client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ"
    )
    assert result["replaced"] == 5
    assert result["aliases_kept"] == 1

    remaining = (await client.get(MATCHES, headers=tenant.headers)).json()["data"]
    assert len(remaining) == 1
    assert remaining[0]["ledger_name"] == "Acme Traders"
    # And it points at the *new* row, not the deleted one.
    fresh = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"]
    fresh_id = next(row["id"] for row in fresh if row["name"] == "Acme Traders")
    assert remaining[0]["ledger_id"] == fresh_id


async def test_a_match_can_be_forgotten(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    await import_master(client, tenant, supplier="Acme Traders", gstin="29AABCU9603R1ZJ")
    ledger_id = (await client.get(LEDGERS, headers=tenant.headers)).json()["data"][0]["id"]
    created = await client.post(
        MATCHES,
        json={"ledger_id": ledger_id, "supplier_name": "Wrong Guess"},
        headers=tenant.headers,
    )
    alias_id = created.json()["data"][0]["id"]

    assert (
        await client.delete(f"{MATCHES}/{alias_id}", headers=tenant.headers)
    ).status_code == 204
    assert (await client.get(MATCHES, headers=tenant.headers)).json()["data"] == []


# --- the file -----------------------------------------------------------


async def test_the_voucher_file_is_well_formed_and_balances(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    use_provider(stub_provider)
    result = await extract_one(client, tenant)
    supplier = result["data"]["supplier"]

    await import_master(
        client, tenant, supplier=supplier["name"], gstin=supplier.get("gstin") or ""
    )
    await configure(client, tenant)

    response = await client.get(VOUCHERS, headers=tenant.headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-docuparse-voucher-count"] == "1"

    root = ET.fromstring(response.text)
    assert root.tag == "ENVELOPE"
    assert root.findtext(".//SVCURRENTCOMPANY") == "Acme Books"

    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == 1
    amounts = [
        Decimal(node.text or "0")
        for node in vouchers[0].findall("ALLLEDGERENTRIES.LIST/AMOUNT")
    ]
    assert sum(amounts) == Decimal(0)
    assert root.findtext(".//PARTYLEDGERNAME") == supplier["name"]


async def test_nothing_to_post_is_an_error_not_an_empty_file(
    client: httpx.AsyncClient, tenant: Tenant, use_provider, stub_provider: StubProvider
) -> None:
    """An empty envelope imports cleanly into Tally and does nothing, silently."""
    use_provider(stub_provider)
    await extract_one(client, tenant)
    await import_master(client, tenant, supplier="Somebody Else", gstin="")
    await configure(client, tenant)

    response = await client.get(VOUCHERS, headers=tenant.headers)
    assert response.status_code == 400
    assert "preview" in response.json()["error"]["message"]


async def test_the_export_window_is_capped(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    response = await client.get(
        VOUCHERS, params={"from": "2020-01-01", "to": "2026-01-01"}, headers=tenant.headers
    )
    assert response.status_code == 400
    assert "400 days" in response.json()["error"]["message"]


async def test_tally_endpoints_require_authentication(
    client: httpx.AsyncClient,
) -> None:
    for path in (LEDGERS, SETTINGS, PREVIEW, VOUCHERS, MATCHES):
        response = await client.get(path)
        assert response.status_code == 401, path
