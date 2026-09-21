"""Shapes for the billing surface."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class PlanOut(BaseModel):
    key: str
    name: str
    description: str
    monthly_price: Decimal
    #: What the monthly price includes. Not a cap — going over is charged,
    #: never blocked.
    included_documents: int
    overage_per_document: Decimal
    #: This one is enforced: adding a client is never urgent.
    included_clients: int
    #: The runaway guard, far above the allowance.
    ceiling_documents: int
    voice_available: bool
    is_current: bool = False


class LineOut(BaseModel):
    label: str
    detail: str
    amount: Decimal


class StatementOut(BaseModel):
    organization_name: str
    period: str
    #: True while the month is still running, so a partial total is never
    #: mistaken for a final bill.
    provisional: bool
    plan: PlanOut

    documents_used: int
    documents_included: int
    overage_documents: int
    clients: int

    lines: list[LineOut]
    subtotal: Decimal
    tax_rate: Decimal
    tax: Decimal
    total: Decimal
    currency: str

    #: Why this is a statement and not a tax invoice.
    note: str
    #: How payment actually happens, since this software does not take it.
    payment_note: str
    warnings: list[str] = Field(default_factory=list)
