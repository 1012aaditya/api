"""CSV export (§25).

Accountants live in spreadsheets, so the fastest way to make extracted data
useful to a non-developer is a file they can open in Excel or import into
their ledger.

Written as a streaming generator: an export of a year's invoices should not
have to fit in memory before the first byte reaches the client.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.repositories.extractions import ExtractionRepository

logger = get_logger("docuparse.export")

INVOICE_COLUMNS: tuple[str, ...] = (
    "document_id",
    "filename",
    "extracted_at",
    "invoice_number",
    "invoice_date",
    "due_date",
    "document_subtype",
    "place_of_supply",
    "reverse_charge",
    "supplier_name",
    "supplier_gstin",
    "supplier_address",
    "buyer_name",
    "buyer_gstin",
    "buyer_address",
    "subtotal",
    "discount",
    "taxable_amount",
    "cgst",
    "sgst",
    "igst",
    "utgst",
    "cess",
    "other_charges",
    "round_off",
    "total",
    "currency",
    "irn",
    "line_item_count",
    "validation",
    "gstin_format_valid",
    "calculation_matches",
    "confidence",
    "model",
    "request_id",
)

LINE_ITEM_COLUMNS: tuple[str, ...] = (
    "document_id",
    "invoice_number",
    "invoice_date",
    "supplier_gstin",
    "line_no",
    "description",
    "hsn_sac",
    "quantity",
    "unit",
    "unit_price",
    "discount",
    "taxable_value",
    "tax_rate",
    "cgst",
    "sgst",
    "igst",
    "cess",
    "total",
)


def _cell(value: Any) -> str:
    """Render one value.

    A null becomes an empty cell, never the string "None" — a spreadsheet
    formula over "None" silently produces nonsense, and the whole point of
    this product is that a missing field looks missing.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _row(columns: tuple[str, ...], values: dict[str, Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(
        [_cell(values.get(column)) for column in columns]
    )
    return buffer.getvalue()


def _header(columns: tuple[str, ...]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(columns)
    # A BOM so Excel opens UTF-8 correctly on Windows — without it, a supplier
    # name with an accent or a rupee sign arrives mangled.
    return "﻿" + buffer.getvalue()


def _flags(validation: Any) -> dict[str, Any]:
    if validation is None:
        return {"validation": None, "gstin_format_valid": None, "calculation_matches": None}

    checks = {c.get("name"): c.get("status") for c in (validation.checks or [])}

    def roll_up(names: tuple[str, ...]) -> bool | None:
        statuses = [checks[n] for n in names if checks.get(n) not in (None, "not_checked")]
        if not statuses:
            return None
        return all(status == "passed" for status in statuses)

    return {
        "validation": validation.overall,
        "gstin_format_valid": roll_up(("supplier_gstin_format", "buyer_gstin_format")),
        "calculation_matches": roll_up(
            ("invoice_total", "taxable_amount_consistency", "line_item_calculation")
        ),
    }


async def stream_invoices_csv(
    session: AsyncSession,
    organization_id: str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    batch_id: str | None = None,
) -> AsyncIterator[str]:
    yield _header(INVOICE_COLUMNS)
    exported = 0

    async for extraction, document, validation in ExtractionRepository(
        session
    ).iter_for_export(
        organization_id, since=since, until=until, batch_id=batch_id
    ):
        data: dict[str, Any] = extraction.data or {}
        supplier = data.get("supplier") or {}
        buyer = data.get("buyer") or {}
        tax = data.get("tax") or {}

        yield _row(
            INVOICE_COLUMNS,
            {
                "document_id": document.id,
                "filename": document.filename,
                "extracted_at": extraction.created_at.isoformat(),
                "invoice_number": data.get("invoice_number"),
                "invoice_date": data.get("invoice_date"),
                "due_date": data.get("due_date"),
                "document_subtype": data.get("document_subtype"),
                "place_of_supply": data.get("place_of_supply"),
                "reverse_charge": data.get("reverse_charge"),
                "supplier_name": supplier.get("name"),
                "supplier_gstin": supplier.get("gstin"),
                "supplier_address": supplier.get("address"),
                "buyer_name": buyer.get("name"),
                "buyer_gstin": buyer.get("gstin"),
                "buyer_address": buyer.get("address"),
                "subtotal": data.get("subtotal"),
                "discount": data.get("discount"),
                "taxable_amount": tax.get("taxable_amount"),
                "cgst": tax.get("cgst"),
                "sgst": tax.get("sgst"),
                "igst": tax.get("igst"),
                "utgst": tax.get("utgst"),
                "cess": tax.get("cess"),
                "other_charges": data.get("other_charges"),
                "round_off": data.get("round_off"),
                "total": data.get("total"),
                "currency": data.get("currency"),
                "irn": data.get("irn"),
                "line_item_count": len(data.get("items") or []),
                "confidence": extraction.overall_confidence,
                "model": extraction.model,
                "request_id": extraction.request_id,
                **_flags(validation),
            },
        )
        exported += 1

    logger.info("export.invoices_csv", organization_id=organization_id, rows=exported)


async def stream_line_items_csv(
    session: AsyncSession,
    organization_id: str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    batch_id: str | None = None,
) -> AsyncIterator[str]:
    yield _header(LINE_ITEM_COLUMNS)
    exported = 0

    async for extraction, document, _validation in ExtractionRepository(
        session
    ).iter_for_export(
        organization_id, since=since, until=until, batch_id=batch_id
    ):
        data: dict[str, Any] = extraction.data or {}
        supplier = data.get("supplier") or {}
        for index, item in enumerate(data.get("items") or [], start=1):
            yield _row(
                LINE_ITEM_COLUMNS,
                {
                    "document_id": document.id,
                    "invoice_number": data.get("invoice_number"),
                    "invoice_date": data.get("invoice_date"),
                    "supplier_gstin": supplier.get("gstin"),
                    "line_no": index,
                    **{
                        column: item.get(column)
                        for column in (
                            "description", "hsn_sac", "quantity", "unit",
                            "unit_price", "discount", "taxable_value",
                            "tax_rate", "cgst", "sgst", "igst", "cess", "total",
                        )
                    },
                },
            )
            exported += 1

    logger.info("export.line_items_csv", organization_id=organization_id, rows=exported)
