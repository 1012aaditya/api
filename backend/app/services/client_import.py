"""Importing a firm's client list from a spreadsheet.

A CA firm arriving with 200 clients is not going to type them into a form
one at a time, so until this existed the product was demonstrable and not
usable. The list already exists — in Tally, or in the Excel file the office
actually runs on — and this reads it.

Three rules shape the whole file, and they are the same rules the extraction
side follows:

* **Never guess.** A malformed GSTIN is reported with the reason it is
  malformed. It is not silently dropped, not "corrected", and not written
  with the bad value hidden. The firm decides.
* **Never write before showing.** Every import is planned first and applied
  second. A CA pointing this at their real client list deserves to see
  exactly what will happen before anything does.
* **Never silently skip.** Every row in the file comes back in the plan with
  a verdict. A file of 200 rows that produced 180 clients has to say what
  happened to the other 20.

Duplicates are flagged, never merged. Two rows that look like the same
client might be a double entry or might be two genuine businesses sharing a
proprietor's phone number, and this cannot tell which — so it says so and
lets a person look.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Literal

from app.models import Channel, Language
from app.validators.gstin import check_gstin

#: A firm's whole client list, with room to spare. The file is parsed in
#: memory, so this is a real bound rather than a formality.
MAX_ROWS = 5000

Verdict = Literal["create", "skip", "duplicate"]


# --- reading whatever the office actually uses ---------------------------

#: Header spellings seen in the wild. Tally exports "Ledger Name"; Excel
#: files written by a human say whatever that human typed. Matching is done
#: on lowercase with punctuation stripped, so "GST No." and "gst_no" are the
#: same key.
COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("name", "clientname", "partyname", "ledgername", "customername", "client"),
    "business_name": ("businessname", "tradename", "legalname", "firmname", "companyname"),
    "client_code": ("clientcode", "code", "clientid", "ledgercode", "alias"),
    "gstin": ("gstin", "gstno", "gstnumber", "gstinuin", "gstidentificationnumber"),
    "pan": ("pan", "panno", "pannumber", "itpan"),
    "phone": ("phone", "phoneno", "contact", "contactno", "telephone", "landline"),
    "whatsapp_phone": ("whatsapp", "whatsappno", "whatsappphone", "mobile", "mobileno", "cell"),
    "email": ("email", "emailid", "emailaddress", "mail"),
    "preferred_language": ("language", "preferredlanguage", "lang"),
    "notes": ("notes", "remarks", "comment", "comments"),
}

_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def _key(header: str) -> str:
    return _PUNCTUATION.sub("", header.strip().lower())


def map_headers(headers: list[str]) -> dict[str, str]:
    """Which column in this file holds which field.

    Returns {field: actual header}. A field nobody supplied is simply absent
    — most of them are optional, and a file with only names and phone
    numbers is a perfectly good client list.
    """
    seen: dict[str, str] = {}
    for header in headers:
        key = _key(header)
        for field_name, spellings in COLUMNS.items():
            if field_name in seen:
                continue
            if key in spellings:
                seen[field_name] = header
                break
    return seen


# --- phone numbers -------------------------------------------------------

_NOT_DIGITS = re.compile(r"[^\d+]")


def normalise_indian_phone(raw: str | None) -> tuple[str | None, str | None]:
    """Return (E.164 number, complaint).

    WhatsApp needs a country code, and an office spreadsheet almost never
    has one. Ten digits starting 6-9 is an Indian mobile and gets +91; a
    leading 0 is the domestic trunk prefix and is dropped. Anything else is
    handed back untouched with a complaint, because inventing a country code
    for a number this cannot recognise would mean messaging a stranger.
    """
    if not raw or not raw.strip():
        return None, None

    cleaned = _NOT_DIGITS.sub("", raw.strip())
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if digits.startswith("91") and len(digits) == 12 and digits[2] in "6789":
            return f"+{digits}", None
        if 8 <= len(digits) <= 15:
            # A real international number. Kept as given — this is an Indian
            # product, not an Indian-only one.
            return f"+{digits}", None
        return None, f"{raw.strip()!r} is not a phone number this recognises."

    digits = cleaned
    if digits.startswith("0"):
        digits = digits.lstrip("0")
    if digits.startswith("91") and len(digits) == 12 and digits[2] in "6789":
        return f"+{digits}", None
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}", None

    if not digits:
        # The cell was not empty — it held something with no digits in it at
        # all, like "N/A" or "—". Returning None twice here would drop it
        # silently, which is the one thing this file is not allowed to do.
        return None, f"{raw.strip()!r} is not a phone number."

    return (
        None,
        f"{raw.strip()!r} is not an Indian mobile number, and no country "
        "code was given — it has been left out rather than guessed at.",
    )


# --- the plan ------------------------------------------------------------


@dataclass
class PlannedClient:
    """One row of the file, and what would become of it."""

    #: 1-based, counting the header as row 1, so it matches what the firm
    #: sees in Excel when they go to fix something.
    row_number: int
    verdict: Verdict
    name: str | None = None
    values: dict[str, object] = field(default_factory=dict)
    #: Why this row cannot be created. Present only when verdict is "skip".
    reason: str | None = None
    #: Things worth knowing that did not stop the row.
    warnings: list[str] = field(default_factory=list)
    #: For "duplicate": what it appears to match, and on what.
    matches: str | None = None


@dataclass
class ImportPlan:
    planned: list[PlannedClient] = field(default_factory=list)
    #: Headers that were understood, {field: header as written}.
    recognised_columns: dict[str, str] = field(default_factory=dict)
    #: Headers present in the file that mean nothing here. Reported rather
    #: than ignored, because a misspelt "GSTNI" column is exactly the sort
    #: of thing a person should be told about.
    ignored_columns: list[str] = field(default_factory=list)
    #: A problem with the file itself, rather than with a row.
    error: str | None = None

    @property
    def to_create(self) -> list[PlannedClient]:
        return [row for row in self.planned if row.verdict == "create"]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "rows": len(self.planned),
            "create": sum(1 for r in self.planned if r.verdict == "create"),
            "duplicate": sum(1 for r in self.planned if r.verdict == "duplicate"),
            "skip": sum(1 for r in self.planned if r.verdict == "skip"),
            "warnings": sum(len(r.warnings) for r in self.planned),
        }


# --- planning ------------------------------------------------------------


def _existing_key(value: str | None) -> str | None:
    return value.strip().upper() if value and value.strip() else None


def plan_import(
    content: bytes,
    *,
    existing_gstins: set[str],
    existing_phones: set[str],
    existing_codes: set[str],
) -> ImportPlan:
    """Read the file and decide what each row would do. Writes nothing.

    The existing-* sets are the firm's current clients, so a second import
    of the same spreadsheet reports 200 duplicates rather than creating 200
    more clients — which is what actually happens when somebody re-uploads
    after fixing three rows.
    """
    plan = ImportPlan()

    # Excel writes a BOM; a file saved from a Windows machine may not be
    # UTF-8 at all. Neither is a reason to refuse the firm's client list.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
        except UnicodeDecodeError:
            plan.error = (
                "This file is not text this can read. Export it from Excel "
                "as CSV UTF-8 and try again."
            )
            return plan

    if not text.strip():
        plan.error = "The file is empty."
        return plan

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        plan.error = "The first row has to name the columns; this file has no header."
        return plan

    columns = map_headers(headers)
    if "name" not in columns:
        plan.error = (
            "No column holds the client's name. One column must be called "
            "Name, Client Name, Party Name or Ledger Name."
        )
        plan.ignored_columns = headers
        return plan

    plan.recognised_columns = columns
    plan.ignored_columns = [h for h in headers if h not in columns.values()]

    # Duplicates inside the file matter as much as duplicates against the
    # database — a spreadsheet that lists a client twice is common.
    gstins_seen: dict[str, int] = {}
    phones_seen: dict[str, int] = {}
    codes_seen: dict[str, int] = {}

    for index, raw_row in enumerate(reader, start=2):
        if len(plan.planned) >= MAX_ROWS:
            plan.error = (
                f"This file has more than {MAX_ROWS} rows. Split it and "
                f"import in parts — the first {MAX_ROWS} are shown below."
            )
            break

        row = _plan_row(
            raw_row,
            row_number=index,
            columns=columns,
            existing_gstins=existing_gstins,
            existing_phones=existing_phones,
            existing_codes=existing_codes,
            gstins_seen=gstins_seen,
            phones_seen=phones_seen,
            codes_seen=codes_seen,
        )
        if row is not None:
            plan.planned.append(row)

    if not plan.planned and not plan.error:
        plan.error = "The file has a header but no rows."
    return plan


def _plan_row(
    raw_row: dict[str, str | None],
    *,
    row_number: int,
    columns: dict[str, str],
    existing_gstins: set[str],
    existing_phones: set[str],
    existing_codes: set[str],
    gstins_seen: dict[str, int],
    phones_seen: dict[str, int],
    codes_seen: dict[str, int],
) -> PlannedClient | None:
    def cell(field_name: str) -> str | None:
        header = columns.get(field_name)
        if header is None:
            return None
        value = raw_row.get(header)
        return value.strip() if isinstance(value, str) and value.strip() else None

    name = cell("name")
    # A wholly empty line is the trailing newline every spreadsheet writes,
    # not a row somebody meant. Reporting it as a failure would be noise.
    if not any(
        isinstance(v, str) and v.strip() for v in raw_row.values()
    ):
        return None

    if not name:
        return PlannedClient(
            row_number=row_number,
            verdict="skip",
            reason="No client name in this row.",
        )

    warnings: list[str] = []
    values: dict[str, object] = {"name": name[:200]}

    for optional in ("business_name", "client_code", "email", "notes"):
        value = cell(optional)
        if value:
            values[optional] = value

    # --- GSTIN: checked, never corrected --------------------------------
    gstin = cell("gstin")
    if gstin:
        checked = check_gstin(gstin)
        if checked and checked.is_valid:
            values["gstin"] = checked.gstin
        else:
            reason = (checked.reason if checked else None) or "It is not a valid GSTIN."
            # The client is still worth creating — a CA can fix a typo later,
            # and refusing the whole row over one field would lose the rest.
            warnings.append(f"GSTIN {gstin!r} was left out: {reason}")

    pan = cell("pan")
    if pan:
        candidate = pan.strip().upper()
        if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", candidate):
            values["pan"] = candidate
        else:
            warnings.append(
                f"PAN {pan!r} was left out: a PAN is five letters, four digits, a letter."
            )

    # --- phones ---------------------------------------------------------
    phone, complaint = normalise_indian_phone(cell("phone"))
    if complaint:
        warnings.append(complaint)
    if phone:
        values["phone"] = phone

    whatsapp, complaint = normalise_indian_phone(cell("whatsapp_phone"))
    if complaint:
        warnings.append(complaint)
    if whatsapp:
        values["whatsapp_phone"] = whatsapp
    elif phone:
        # The overwhelmingly common case: one mobile, used for everything.
        values["whatsapp_phone"] = phone

    if not values.get("whatsapp_phone"):
        warnings.append(
            "No WhatsApp number, so the agent cannot chase this client. "
            "They can still be tracked by hand."
        )

    language = (cell("preferred_language") or "").lower()
    if language:
        spellings = {
            "en": Language.EN, "english": Language.EN,
            "hi": Language.HI, "hindi": Language.HI,
            "hinglish": Language.HINGLISH, "hing": Language.HINGLISH,
        }
        if language in spellings:
            values["preferred_language"] = spellings[language]
        else:
            warnings.append(f"Language {language!r} was not recognised; English assumed.")

    values.setdefault("preferred_channel", Channel.WHATSAPP)

    # --- duplicates: flagged, never merged ------------------------------
    gstin_key = _existing_key(values.get("gstin"))  # type: ignore[arg-type]
    phone_key = _existing_key(values.get("whatsapp_phone"))  # type: ignore[arg-type]
    code_key = _existing_key(values.get("client_code"))  # type: ignore[arg-type]

    for key, seen, existing, label in (
        (gstin_key, gstins_seen, existing_gstins, "GSTIN"),
        (code_key, codes_seen, existing_codes, "client code"),
        (phone_key, phones_seen, existing_phones, "WhatsApp number"),
    ):
        if not key:
            continue
        if key in existing:
            return PlannedClient(
                row_number=row_number, verdict="duplicate", name=name,
                values=values, warnings=warnings,
                matches=f"a client you already have, with the same {label}",
            )
        if key in seen:
            return PlannedClient(
                row_number=row_number, verdict="duplicate", name=name,
                values=values, warnings=warnings,
                matches=f"row {seen[key]} of this file, with the same {label}",
            )

    for key, seen in ((gstin_key, gstins_seen), (code_key, codes_seen), (phone_key, phones_seen)):
        if key:
            seen[key] = row_number

    return PlannedClient(
        row_number=row_number, verdict="create", name=name,
        values=values, warnings=warnings,
    )
