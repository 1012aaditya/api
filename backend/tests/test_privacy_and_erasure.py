"""Erasing a client, and telling a firm the truth about what is held.

A CA firm is a processor: the invoices describe their client's business,
not theirs, and under the DPDP Act 2023 the person those records describe
can ask for them to be removed. The two failures that matter here are an
erasure that leaves something behind, and a trust page that reassures a
firm about a configuration that does not warrant it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models import Base
from app.services.erasure import unreachable_tables
from tests.conftest import Tenant

GSTIN = "29AABCU9603R1ZJ"


def data(response: httpx.Response):
    assert response.status_code < 300, response.text
    return response.json()["data"]


async def a_client_with_history(client: httpx.AsyncClient, headers, *, name="Verma Hardware"):
    """A client with a case, a requirement and an agent event behind them."""
    created = data(
        await client.post(
            "/v1/clients",
            headers=headers,
            json={"name": name, "gstin": GSTIN, "whatsapp_phone": "+919800000001"},
        )
    )
    await client.post(
        "/v1/cases",
        headers=headers,
        json={
            "client_id": created["id"],
            "case_type": "gst",
            "period": "2026-09",
            "requirements": ["bank_statement", "gstr_2b"],
        },
    )
    return created


async def rows_for(organization_id: str, table: str, client_id: str) -> int:
    t = Base.metadata.tables[table]
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(t)
                .where(t.c.organization_id == organization_id, t.c.client_id == client_id)
            )
        ).scalar_one()


# --- the table nobody remembered ----------------------------------------


def test_every_table_holding_a_client_id_is_erased():
    """A table added later would otherwise survive an erasure silently, and
    the first anybody would know is a subject access request. This fails
    with the migration instead."""
    missed = unreachable_tables()

    assert missed == set(), (
        f"these tables hold a client_id and erase_client does not clear them: "
        f"{sorted(missed)}"
    )


# --- erasure ------------------------------------------------------------


async def test_erasing_removes_the_client_and_their_history(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    created = await a_client_with_history(client, auth_headers)

    assert await rows_for(tenant.organization_id, "compliance_cases", created["id"]) > 0

    receipt = data(
        await client.delete(
            f"/v1/privacy/clients/{created['id']}",
            headers=auth_headers,
            params={"confirm": created["name"]},
        )
    )

    assert receipt["complete"] is True
    assert receipt["rows_deleted"] > 0
    assert await rows_for(tenant.organization_id, "compliance_cases", created["id"]) == 0
    assert await rows_for(tenant.organization_id, "agent_events", created["id"]) == 0

    remaining = data(await client.get("/v1/clients", headers=auth_headers))
    assert created["id"] not in [c["id"] for c in remaining]


async def test_erasing_takes_the_requirements_hanging_off_the_case(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """Requirements name the case, not the client, so a naive sweep would
    leave them — along with what the firm was waiting for and from whom."""
    created = await a_client_with_history(client, auth_headers)

    receipt = data(
        await client.delete(
            f"/v1/privacy/clients/{created['id']}",
            headers=auth_headers,
            params={"confirm": created["name"]},
        )
    )

    assert receipt["by_table"].get("document_requirements", 0) > 0
    requirements = Base.metadata.tables["document_requirements"]
    async with get_session_factory()() as session:
        left = (
            await session.execute(select(func.count()).select_from(requirements))
        ).scalar_one()
    assert left == 0


async def test_the_name_has_to_be_typed(client: httpx.AsyncClient, auth_headers, tenant):
    """A confirmation dialog is dismissed by reflex; a name has to be read."""
    created = await a_client_with_history(client, auth_headers)

    response = await client.delete(
        f"/v1/privacy/clients/{created['id']}",
        headers=auth_headers,
        params={"confirm": "something else"},
    )

    assert response.status_code == 400
    assert "Nothing has been deleted" in response.json()["error"]["message"]
    assert await rows_for(tenant.organization_id, "compliance_cases", created["id"]) > 0


async def test_a_staff_login_cannot_erase_a_client(
    client: httpx.AsyncClient, auth_headers
):
    """The most destructive thing the API does is not a junior's to do, nor
    a phished junior account's."""
    from tests.test_team import invite_and_join

    created = await a_client_with_history(client, auth_headers)
    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    response = await client.delete(
        f"/v1/privacy/clients/{created['id']}",
        headers=staff,
        params={"confirm": created["name"]},
    )

    assert response.status_code == 403


async def test_one_firm_cannot_erase_anothers_client(
    client: httpx.AsyncClient, auth_headers, other_tenant: Tenant, tenant: Tenant
):
    """The worst possible bug in this file (§18)."""
    created = await a_client_with_history(client, auth_headers)

    signed_in = await client.post(
        "/v1/auth/login",
        json={"email": other_tenant.email, "password": other_tenant.password},
    )
    stranger = {"Authorization": f"Bearer {data(signed_in)['access_token']}"}

    response = await client.delete(
        f"/v1/privacy/clients/{created['id']}",
        headers=stranger,
        params={"confirm": created["name"]},
    )

    assert response.status_code == 404
    assert await rows_for(tenant.organization_id, "compliance_cases", created["id"]) > 0


async def test_the_erasure_is_on_the_record_without_preserving_what_was_erased(
    client: httpx.AsyncClient, auth_headers
):
    """An audit entry describing the erased data in any detail would keep
    the very thing somebody asked to have removed."""
    created = await a_client_with_history(client, auth_headers, name="Kanchan Textiles")

    await client.delete(
        f"/v1/privacy/clients/{created['id']}",
        headers=auth_headers,
        params={"confirm": created["name"]},
    )

    events = data(await client.get("/v1/agent/activity?limit=20", headers=auth_headers))
    erasure = [e for e in events if e["action"] == "client.erased"]

    assert len(erasure) == 1
    record = str(erasure[0])
    # The name stays, so the firm can show the request was honoured. Both
    # privacy documents disclose that, and they are wrong the moment this
    # line stops being true in either direction.
    assert "Kanchan Textiles" in record
    assert GSTIN not in record, "the erased GSTIN survived in the audit log"
    assert "+919800000001" not in record, "the erased number survived in the audit log"
    assert erasure[0]["client_id"] is None, "the record still points at the erased client"


def test_both_privacy_documents_disclose_what_an_erasure_leaves_behind():
    """An audit line naming an erased client is a retention, and a policy
    that said "everything is gone" while it remained would be inaccurate.
    These drafts go to a lawyer and then to customers; a claim drifting out
    of step with the code is the failure that matters."""
    docs = Path(__file__).resolve().parents[2] / "docs"

    def plain(text: str) -> str:
        """Markdown out and lines rejoined, so a phrase split across a
        wrapped blockquote still reads as the sentence it is."""
        import re

        stripped = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
        stripped = stripped.replace("*", "").replace("_", "")
        return re.sub(r"\s+", " ", stripped).lower()

    for name in ("PRIVACY.md", "DPA.md"):
        raw = (docs / name).read_text()
        text = plain(raw)

        assert "audit" in text, f"{name} does not mention the audit record"
        assert "draft" in text, f"{name} must be marked a draft, not a policy"
        assert "not legal advice" in text, f"{name} must disclaim advice"
        # Compliance is counsel's determination, never the code's (§14).
        assert "compliant with" not in text, f"{name} claims compliance"
        assert "fully complies" not in text, f"{name} claims compliance"


# --- the footprint ------------------------------------------------------


async def test_the_footprint_reports_the_retention_actually_configured(
    client: httpx.AsyncClient, auth_headers, settings
):
    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert report["retention_days"] == settings.document_retention_days


async def test_a_staff_login_may_read_the_footprint(
    client: httpx.AsyncClient, auth_headers
):
    """Knowing what the firm holds is not an administrator's privilege."""
    from tests.test_team import invite_and_join

    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    response = await client.get("/v1/privacy/footprint", headers=staff)

    assert response.status_code == 200


async def test_no_model_configured_says_so_rather_than_reassuring(
    client: httpx.AsyncClient, auth_headers
):
    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert report["extraction_configured"] is False
    assert report["document_locality"] == "no_model"


def configured_as(app, **overrides):
    """Point the running app at a different configuration.

    FastAPI binds `Depends(get_settings)` when the route is defined, so
    patching the module afterwards changes nothing — the override has to go
    through the app itself.
    """
    from app.core.config import get_settings

    patched = get_settings().model_copy(update=overrides)
    app.dependency_overrides[get_settings] = lambda: patched
    return patched


@pytest.mark.parametrize(
    "base_url,locality",
    [
        ("http://127.0.0.1:11434/v1", "stays_here"),
        ("http://192.168.1.40:11434/v1", "stays_here"),
        ("http://ollama.local:11434/v1", "stays_here"),
        # A compose service name is a model on this very machine, and still
        # cannot be shown to be one from here.
        ("http://ollama:11434/v1", "cannot_be_proven"),
        ("https://api.openai.com/v1", "cannot_be_proven"),
    ],
)
async def test_where_documents_go_is_derived_not_asserted(
    client: httpx.AsyncClient, auth_headers, app, base_url, locality
):
    """Three states, because the classifier answers "provably private", not
    "public". Collapsing them into a bool would make one of them a lie: a
    firm self-hosting Ollama would be told its invoices leave."""
    configured_as(app, ai_base_url=base_url, ai_api_key="k", ai_model="m")

    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert report["document_locality"] == locality


async def test_an_unprovable_endpoint_says_what_to_change(
    client: httpx.AsyncClient, auth_headers, app
):
    """The compose file ships a service name, so this is the state most
    self-hosting firms land in. Telling them only that it is unproven would
    leave them stuck."""
    configured_as(app, ai_base_url="http://ollama:11434/v1", ai_api_key="k", ai_model="m")

    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert "AI_BASE_URL" in report["locality_note"]
    assert "IP address" in report["locality_note"]


async def test_a_hosted_model_is_named_as_a_processor(
    client: httpx.AsyncClient, auth_headers, app
):
    configured_as(
        app, ai_base_url="https://api.openai.com/v1", ai_api_key="k", ai_model="m"
    )

    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert any("openai.com" in p["name"] for p in report["processors"])


async def test_meta_is_named_when_a_real_whatsapp_number_is_connected(
    client: httpx.AsyncClient, auth_headers, app
):
    """A firm on the Cloud API is sending their clients' numbers and
    documents to Meta, and has to be able to say so in their own policy."""
    configured_as(app, whatsapp_provider="whatsapp_cloud")

    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert any("Meta" in p["name"] for p in report["processors"])


async def test_nothing_is_named_when_nothing_leaves(
    client: httpx.AsyncClient, auth_headers
):
    """The demo configuration sends nothing anywhere, and the page says so
    rather than naming a processor to look thorough."""
    report = data(await client.get("/v1/privacy/footprint", headers=auth_headers))

    assert report["processors"] == []
