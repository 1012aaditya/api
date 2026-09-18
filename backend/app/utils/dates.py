"""Date normalization for Indian invoices (§13).

Invoices print dates half a dozen ways. The prompt asks for ISO, but a model
does not always comply, so the pipeline re-parses defensively.

The one judgement call here is DD/MM vs MM/DD. Indian invoices are DD/MM, so
that is the assumption — and when a string is genuinely ambiguous (both parts
≤ 12), the parse is flagged so the confidence scorer can mark the field down
rather than presenting a coin flip as fact.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")
_TEXTUAL = re.compile(
    r"^(\d{1,2})[\s\-]*(?:st|nd|rd|th)?[\s\-]*"
    r"([A-Za-z]{3,9})[\s\-,]*(\d{2,4})$"
)
_TEXTUAL_LEADING = re.compile(r"^([A-Za-z]{3,9})[\s\-]+(\d{1,2})[\s\-,]+(\d{2,4})$")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass(frozen=True)
class ParsedDate:
    value: dt.date | None
    ambiguous: bool = False
    original: str | None = None


def _month_number(name: str) -> int | None:
    return _MONTHS.get(name.strip().lower()[:4].rstrip(".")) or _MONTHS.get(
        name.strip().lower()[:3]
    )


def _year(raw: int) -> int:
    if raw >= 100:
        return raw
    # A two-digit year on an invoice is this century in practice.
    return 2000 + raw


def _build(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_date(value: object) -> ParsedDate:
    if value is None:
        return ParsedDate(None)
    if isinstance(value, dt.datetime):
        return ParsedDate(value.date())
    if isinstance(value, dt.date):
        return ParsedDate(value)
    if not isinstance(value, str):
        return ParsedDate(None)

    text = value.strip()
    if not text:
        return ParsedDate(None)

    if match := _ISO.match(text):
        year, month, day = (int(g) for g in match.groups())
        return ParsedDate(_build(year, month, day), original=text)

    if match := _NUMERIC.match(text):
        a, b, c = (int(g) for g in match.groups())
        # YYYY/MM/DD
        if a > 31:
            return ParsedDate(_build(a, b, c), original=text)
        # DD/MM/YYYY is the Indian convention; MM/DD only when DD cannot be a day.
        if a > 12:
            return ParsedDate(_build(_year(c), b, a), original=text)
        if b > 12:
            return ParsedDate(_build(_year(c), a, b), original=text)
        return ParsedDate(_build(_year(c), b, a), ambiguous=True, original=text)

    if match := _TEXTUAL.match(text):
        day_str, month_name, year_str = match.groups()
        month = _month_number(month_name)
        if month:
            return ParsedDate(_build(_year(int(year_str)), month, int(day_str)), original=text)

    if match := _TEXTUAL_LEADING.match(text):
        month_name, day_str, year_str = match.groups()
        month = _month_number(month_name)
        if month:
            return ParsedDate(_build(_year(int(year_str)), month, int(day_str)), original=text)

    return ParsedDate(None, original=text)
