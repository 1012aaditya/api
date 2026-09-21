"""Plans, allowances and what a month comes to.

One behaviour matters more than the arithmetic. A CA firm that hit a wall
on the 18th, with a GST filing due on the 20th, cannot retry tomorrow — and
it would happen every month to every firm that grew. So the document
allowance bills rather than blocks, and the thing that does block sits far
above it and catches runaway loops.

The rest is about not surprising anybody: a warning before the bill, a
partial month labelled partial, and a statement that does not claim to be a
tax invoice or imply a payment flow that does not exist.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import Organization
from app.repositories.usage import UsageRepository
from app.services.plans import PLANS, get_plan
from tests.conftest import Tenant


def data(response: httpx.Response):
    assert response.status_code < 300, response.text
    return response.json()["data"]


async def set_plan(organization_id: str, plan: str) -> None:
    async with get_session_factory()() as session:
        organization = (
            await session.execute(
                select(Organization).where(Organization.id == organization_id)
            )
        ).scalar_one()
        organization.plan = plan
        await session.commit()


async def record_usage(organization_id: str, *, count: int) -> None:
    async with get_session_factory()() as session:
        repository = UsageRepository(session)
        for _ in range(count):
            await repository.record(
                organization_id=organization_id,
                endpoint="/v1/invoices/extract",
                status_code=200,
                success=True,
                billable=True,
            )
        await session.commit()


# --- the allowance bills, it does not block -----------------------------


def test_an_allowance_is_not_a_ceiling():
    """The whole design, in one assertion: every plan's runaway guard sits
    far above what it includes, so ordinary growth never hits a wall."""
    for plan in PLANS.values():
        assert plan.ceiling_documents > plan.included_documents * 2, (
            f"{plan.key}'s ceiling is close enough to its allowance that a "
            "busy month would hit it"
        )


def test_overage_is_counted_and_charged():
    plan = get_plan("practice")

    assert plan.overage(plan.included_documents) == 0
    assert plan.overage(plan.included_documents + 10) == 10
    assert plan.usage_charge(plan.included_documents + 10) == Decimal("30.00")


def test_an_unknown_plan_falls_to_the_most_restrictive_one():
    """A typo in a database column must not silently grant the largest plan."""
    assert get_plan("enterprise-platinum").key == "trial"
    assert get_plan(None).key == "trial"
    assert get_plan("").key == "trial"


# --- warning before the bill --------------------------------------------


async def test_a_firm_is_warned_before_it_goes_over(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """A bill nobody saw coming is its own kind of harm."""
    plan = get_plan("trial")
    await record_usage(tenant.organization_id, count=int(plan.included_documents * 0.85))

    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert statement["warnings"], "no warning at 85% of the allowance"
    assert "left this month" in " ".join(statement["warnings"])


async def test_going_over_says_so_plainly_and_says_work_continues(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    await set_plan(tenant.organization_id, "practice")
    plan = get_plan("practice")
    await record_usage(tenant.organization_id, count=plan.included_documents + 7)

    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert statement["overage_documents"] == 7
    warnings = " ".join(statement["warnings"])
    assert "Work continues" in warnings
    assert any(line["label"] == "Additional documents" for line in statement["lines"])


async def test_no_warning_when_there_is_nothing_to_warn_about(
    client: httpx.AsyncClient, auth_headers
):
    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert statement["warnings"] == []


# --- the statement ------------------------------------------------------


async def test_this_month_is_labelled_provisional(
    client: httpx.AsyncClient, auth_headers
):
    """A partial month must never read as a final bill."""
    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert statement["provisional"] is True


async def test_a_finished_month_is_not_provisional(
    client: httpx.AsyncClient, auth_headers
):
    statement = data(
        await client.get("/v1/billing/statement?period=2020-01", headers=auth_headers)
    )

    assert statement["provisional"] is False
    assert statement["documents_used"] == 0


async def test_the_arithmetic_adds_up(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    await set_plan(tenant.organization_id, "firm")
    plan = get_plan("firm")
    await record_usage(tenant.organization_id, count=plan.included_documents + 100)

    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    lines = sum(Decimal(str(line["amount"])) for line in statement["lines"])
    subtotal = Decimal(str(statement["subtotal"]))
    tax = Decimal(str(statement["tax"]))
    total = Decimal(str(statement["total"]))

    assert subtotal == lines
    assert tax == (subtotal * Decimal(str(statement["tax_rate"]))).quantize(Decimal("0.01"))
    assert total == subtotal + tax
    assert subtotal == plan.monthly_price + Decimal("300")


async def test_the_statement_does_not_claim_to_be_a_tax_invoice(
    client: httpx.AsyncClient, auth_headers
):
    """Getting a tax invoice's particulars wrong is the customer's problem
    as much as the issuer's, and the customer here is a CA firm (§14)."""
    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert "not a tax invoice" in statement["note"]
    assert "your own accountant" in statement["note"]


async def test_the_statement_does_not_imply_a_payment_flow(
    client: httpx.AsyncClient, auth_headers
):
    """There is no payment provider in this deployment, and a page that
    looked like it charged a card would be the worst version of §42."""
    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert "No payment is collected" in statement["payment_note"]


@pytest.mark.parametrize("period", ["2026-13", "2026", "september", "2026-1"])
async def test_a_period_that_is_not_a_month_is_refused(
    client: httpx.AsyncClient, auth_headers, period
):
    response = await client.get(
        f"/v1/billing/statement?period={period}", headers=auth_headers
    )

    assert response.status_code == 400


async def test_usage_is_counted_within_the_period_asked_for(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    await record_usage(tenant.organization_id, count=5)

    this_month = data(await client.get("/v1/billing/statement", headers=auth_headers))
    long_ago = data(
        await client.get("/v1/billing/statement?period=2020-01", headers=auth_headers)
    )

    assert this_month["documents_used"] == 5
    assert long_ago["documents_used"] == 0


async def test_a_staff_login_may_read_the_statement(
    client: httpx.AsyncClient, auth_headers
):
    """A junior who can see the allowance running down can say something
    before the bill does."""
    from tests.test_team import invite_and_join

    staff = await invite_and_join(client, auth_headers, email="junior@sharma.example")

    assert (await client.get("/v1/billing/statement", headers=staff)).status_code == 200


async def test_one_firms_statement_does_not_count_anothers_usage(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant, other_tenant: Tenant
):
    await record_usage(other_tenant.organization_id, count=40)

    statement = data(await client.get("/v1/billing/statement", headers=auth_headers))

    assert statement["documents_used"] == 0


# --- clients, which is a real limit -------------------------------------


async def test_a_plan_that_covers_no_more_clients_refuses_another(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    """Unlike documents, this blocks — adding a client is a considered act
    nobody's filing deadline turns on."""
    plan = get_plan("trial")
    for index in range(plan.included_clients):
        response = await client.post(
            "/v1/clients", headers=auth_headers, json={"name": f"Client {index}"}
        )
        assert response.status_code == 201, response.text

    refused = await client.post(
        "/v1/clients", headers=auth_headers, json={"name": "One too many"}
    )

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "quota_exceeded"
    assert "Move up a plan" in refused.json()["error"]["message"]


async def test_importing_a_list_cannot_go_round_the_client_limit(
    client: httpx.AsyncClient, auth_headers
):
    """Importing 300 clients on a plan covering 25 must not be the way
    around the thing the one-at-a-time endpoint checks."""
    plan = get_plan("trial")
    rows = "\n".join(
        f"Client {i},98000{i:05d}" for i in range(plan.included_clients + 5)
    )
    csv = f"Name,Mobile\n{rows}\n"

    response = await client.post(
        "/v1/clients/import",
        headers=auth_headers,
        files={"file": ("clients.csv", csv, "text/csv")},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "quota_exceeded"


# --- the catalogue ------------------------------------------------------


async def test_the_plan_list_marks_the_one_the_firm_is_on(
    client: httpx.AsyncClient, auth_headers, tenant: Tenant
):
    await set_plan(tenant.organization_id, "practice")

    plans = data(await client.get("/v1/billing/plans", headers=auth_headers))
    current = [p for p in plans if p["is_current"]]

    assert [p["key"] for p in current] == ["practice"]
