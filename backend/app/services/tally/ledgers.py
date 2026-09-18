"""Importing the customer's ledger master.

They export their chart of accounts from Tally (Gateway → Display → List of
Accounts → Export, as XML) and upload it here. We never invent a ledger: this
file is the complete set of accounts a voucher is allowed to name.

Two formats are accepted. Tally's own XML is the real path. CSV is the escape
hatch for someone whose export will not cooperate, or who keeps their supplier
list in a spreadsheet.

**This parses a file a customer uploaded, so it is attacker-controlled input.**
XML has a well-known amplification attack — a small document that declares
nested entities and expands to gigabytes in memory. Every such attack needs a
DOCTYPE, so the parser refuses any document that has one, and caps the input
size besides.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from app.core.errors import InvalidFileError
from app.services.tally.matching import normalize_gstin, normalize_name

#: A ledger master is a list of names. Anything much larger is not one.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_LEDGERS = 50_000

_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

#: GSTIN can be recorded in several places depending on the Tally version and
#: whether the ledger was created before or after GST registration.
_GSTIN_PATHS = (
    "PARTYGSTIN",
    "GSTIN",
    "LEDGERPARTYGSTIN",
    "LEDGSTREGDETAILS.LIST/GSTIN",
    "GSTREGISTRATIONDETAILS.LIST/GSTIN",
)

_CSV_NAME_HEADERS = ("name", "ledger", "ledger name", "ledger_name", "party", "particulars")
_CSV_GSTIN_HEADERS = ("gstin", "gst no", "gst number", "gstin/uin", "gst_no", "party gstin")
_CSV_GROUP_HEADERS = ("parent", "group", "under", "parent group", "parent_group")


@dataclass(frozen=True)
class ParsedLedger:
    name: str
    gstin: str | None
    parent_group: str | None

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


def _guard(payload: bytes) -> None:
    if not payload.strip():
        raise InvalidFileError("The uploaded ledger file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise InvalidFileError(
            f"The ledger file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    if _DOCTYPE.search(payload):
        # Closes billion-laughs and external-entity attacks in one check: both
        # need a document type declaration, and a Tally export never has one.
        raise InvalidFileError(
            "The XML declares a DOCTYPE or ENTITY, which this importer refuses. "
            "Export the ledger master again from Tally without modifying it."
        )


def _decode(payload: bytes) -> str:
    # Tally writes UTF-8 or ISO-8859-1 depending on version and settings.
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return payload.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise InvalidFileError("The ledger file's character encoding could not be read.")


def _text(element: ET.Element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _ledger_name(element: ET.Element) -> str | None:
    # Tally puts the name on the element, but older exports nest it.
    for candidate in (
        element.get("NAME"),
        _text(element, "NAME"),
        _text(element, "LANGUAGENAME.LIST/NAME.LIST/NAME"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def parse_tally_xml(payload: bytes) -> list[ParsedLedger]:
    """Pull every ``<LEDGER>`` out of a Tally master export."""
    _guard(payload)
    try:
        root = ET.fromstring(_decode(payload))
    except ET.ParseError as exc:
        raise InvalidFileError(f"The XML could not be parsed: {exc}") from exc

    ledgers: list[ParsedLedger] = []
    seen: set[str] = set()
    # Depth varies between Tally versions, so search rather than walk a path.
    for element in root.iter("LEDGER"):
        name = _ledger_name(element)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)

        gstin = None
        for path in _GSTIN_PATHS:
            found = _text(element, path)
            if found:
                gstin = normalize_gstin(found)
                break

        ledgers.append(
            ParsedLedger(
                name=name,
                gstin=gstin or None,
                parent_group=_text(element, "PARENT"),
            )
        )
        if len(ledgers) > MAX_LEDGERS:
            raise InvalidFileError(
                f"The file holds more than {MAX_LEDGERS:,} ledgers, which is more "
                "than this importer accepts."
            )

    if not ledgers:
        raise InvalidFileError(
            "No <LEDGER> entries were found. Export 'List of Accounts' from Tally "
            "as XML, rather than a report or a voucher export."
        )
    return ledgers


def _column(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    lowered = [h.strip().casefold() for h in headers]
    for index, header in enumerate(lowered):
        if header in candidates:
            return index
    for index, header in enumerate(lowered):
        if any(candidate in header for candidate in candidates):
            return index
    return None


def parse_csv(payload: bytes) -> list[ParsedLedger]:
    """Read a spreadsheet with at least a name column."""
    _guard(payload)
    text = _decode(payload)
    rows = list(csv.reader(io.StringIO(text)))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise InvalidFileError("The ledger file has no rows.")

    headers = rows[0]
    name_at = _column(headers, _CSV_NAME_HEADERS)
    if name_at is None:
        raise InvalidFileError(
            "No ledger-name column was found. The first row must be a header "
            "containing a column called 'name' or 'ledger'."
        )
    gstin_at = _column(headers, _CSV_GSTIN_HEADERS)
    group_at = _column(headers, _CSV_GROUP_HEADERS)

    def cell(row: list[str], index: int | None) -> str | None:
        if index is None or index >= len(row):
            return None
        value = row[index].strip()
        return value or None

    ledgers: list[ParsedLedger] = []
    seen: set[str] = set()
    for row in rows[1:]:
        name = cell(row, name_at)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        gstin = cell(row, gstin_at)
        ledgers.append(
            ParsedLedger(
                name=name,
                gstin=normalize_gstin(gstin) or None,
                parent_group=cell(row, group_at),
            )
        )
        if len(ledgers) > MAX_LEDGERS:
            raise InvalidFileError(
                f"The file holds more than {MAX_LEDGERS:,} ledgers."
            )

    if not ledgers:
        raise InvalidFileError("No ledger rows were found under the header.")
    return ledgers


def parse(payload: bytes, filename: str | None = None) -> list[ParsedLedger]:
    """Pick a parser from the content, falling back to the extension.

    Content first: a file named ``.csv`` that is really a Tally export should
    still import, and a customer who renames a file should not be punished.
    """
    _guard(payload)
    head = payload.lstrip()[:400].lower()
    if head.startswith(b"<?xml") or b"<envelope" in head or b"<tallymessage" in head:
        return parse_tally_xml(payload)
    if filename and filename.lower().endswith(".xml"):
        return parse_tally_xml(payload)
    return parse_csv(payload)
