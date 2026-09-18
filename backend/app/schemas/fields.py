"""Shared field types for extracted data.

Money is ``Decimal`` everywhere internally — invoice arithmetic is checked
against customer totals, and binary floats do not reconcile reliably at two
decimal places. It is rendered as a plain JSON number on the way out so the
payload matches what a developer expects to parse.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer


def _to_decimal(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # A bool is an int in Python; silently becoming 1.00 would be wrong.
        raise ValueError("boolean is not a valid amount")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = (
            value.strip()
            .replace("₹", "")       # ₹
            .replace("Rs.", "")
            .replace("Rs", "")
            .replace(",", "")
            .replace(" ", "")
        )
        if not cleaned or cleaned in {"-", "--"}:
            return None
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        if negative:
            cleaned = cleaned[1:-1]
        try:
            parsed = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"not a valid amount: {value!r}") from exc
        return -parsed if negative else parsed
    raise ValueError(f"not a valid amount: {value!r}")


def _render_number(value: Decimal | None) -> int | float | None:
    """Emit 118000 rather than 118000.00, and 118.5 rather than 118.50."""
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


Money = Annotated[
    Decimal,
    BeforeValidator(_to_decimal),
    # No return_type: declaring one would coerce the int branch back to float
    # and put 118000.0 in the payload where the document said 118000.
    PlainSerializer(_render_number, when_used="json"),
]

Quantity = Money  # same parsing and rendering rules
