"""GSTIN structural validation (§9, §14).

A GSTIN is 15 characters:

    ``[2-digit state code][10-character PAN][entity code][Z][check digit]``

and the last character is a mod-36 checksum over the first fourteen. Both the
structure and the checksum are verified here.

**This is format validation, not verification.** A GSTIN that passes every
check below may still be inactive, cancelled, or never issued. Confirming
that requires a GSTN lookup, which this service does not perform, and no
output of this module should be read as a compliance statement.
"""

from __future__ import annotations

import re

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VALUE = {char: index for index, char in enumerate(_ALPHABET)}

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}[Z][0-9A-Z]{1}$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# State / UT codes as published by GSTN. 97 is "Other Territory", 99 "Centre
# Jurisdiction". Codes outside this set do not correspond to a jurisdiction.
STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}


def compute_check_digit(first_fourteen: str) -> str | None:
    """The GSTIN check character: a Luhn-style mod-36 over base-36 digits."""
    if len(first_fourteen) != 14:
        return None
    total = 0
    for index, char in enumerate(first_fourteen):
        value = _VALUE.get(char)
        if value is None:
            return None
        # Factor alternates 1, 2, 1, 2 ... starting at the leftmost character.
        product = value * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def state_code(gstin: str) -> str | None:
    return gstin[:2] if len(gstin) >= 2 else None


def state_name(gstin: str) -> str | None:
    code = state_code(gstin)
    return STATE_CODES.get(code) if code else None


def pan_from_gstin(gstin: str) -> str | None:
    """Characters 3-12 of a GSTIN are the holder's PAN by construction."""
    if len(gstin) < 12:
        return None
    candidate = gstin[2:12]
    return candidate if PAN_PATTERN.match(candidate) else None


class GSTINCheck:
    """The result of examining one GSTIN string."""

    __slots__ = ("gstin", "structure_valid", "checksum_valid", "state_recognised", "reason")

    def __init__(
        self,
        gstin: str,
        *,
        structure_valid: bool,
        checksum_valid: bool,
        state_recognised: bool,
        reason: str | None = None,
    ) -> None:
        self.gstin = gstin
        self.structure_valid = structure_valid
        self.checksum_valid = checksum_valid
        self.state_recognised = state_recognised
        self.reason = reason

    @property
    def is_valid(self) -> bool:
        return self.structure_valid and self.checksum_valid and self.state_recognised


def check_gstin(value: str | None) -> GSTINCheck | None:
    """None means "nothing to check" — not "valid"."""
    if not value:
        return None
    gstin = value.strip().upper()

    if len(gstin) != 15:
        return GSTINCheck(
            gstin, structure_valid=False, checksum_valid=False, state_recognised=False,
            reason=f"A GSTIN is 15 characters; this one is {len(gstin)}.",
        )
    if not GSTIN_PATTERN.match(gstin):
        return GSTINCheck(
            gstin, structure_valid=False, checksum_valid=False, state_recognised=False,
            reason="Does not match the GSTIN character layout.",
        )

    state_recognised = gstin[:2] in STATE_CODES
    expected = compute_check_digit(gstin[:14])
    checksum_valid = expected is not None and expected == gstin[14]

    reason = None
    if not state_recognised:
        reason = f"State code {gstin[:2]!r} is not an assigned GST jurisdiction."
    elif not checksum_valid:
        reason = "The check digit does not match the rest of the GSTIN."

    return GSTINCheck(
        gstin,
        structure_valid=True,
        checksum_valid=checksum_valid,
        state_recognised=state_recognised,
        reason=reason,
    )


def is_valid_gstin(value: str | None) -> bool:
    result = check_gstin(value)
    return bool(result and result.is_valid)


_LEADING_CODE = re.compile(r"^\s*(\d{1,2})\b")
_NAME_TO_CODE = {name.lower(): code for code, name in STATE_CODES.items()}
# A few spellings that appear on invoices but differ from the GSTN list.
_NAME_ALIASES = {
    "orissa": "21", "pondicherry": "34", "uttaranchal": "05",
    "new delhi": "07", "delhi ncr": "07", "nct of delhi": "07",
    "j&k": "01", "jammu & kashmir": "01",
    "andaman and nicobar": "35", "andaman & nicobar islands": "35",
    "dadra and nagar haveli": "26", "daman & diu": "25",
    "tamilnadu": "33", "chattisgarh": "22", "chhatisgarh": "22",
    "telengana": "36", "puduchery": "34",
}


def state_code_from_place_of_supply(value: str | None) -> str | None:
    """Resolve a printed place of supply to a GST state code, or None.

    Invoices write this as "29-Karnataka", "Karnataka (29)", "29", or just
    "Karnataka". Anything that does not resolve unambiguously returns None,
    so the caller reports ``not_checked`` rather than guessing a jurisdiction.
    """
    if not value:
        return None
    text = value.strip()

    if match := _LEADING_CODE.match(text):
        code = match.group(1).zfill(2)
        if code in STATE_CODES:
            return code

    lowered = re.sub(r"[^a-z& ]", " ", text.lower())
    lowered = " ".join(lowered.split())
    if lowered in _NAME_TO_CODE:
        return _NAME_TO_CODE[lowered]
    if lowered in _NAME_ALIASES:
        return _NAME_ALIASES[lowered]

    # Fall back to a containment match, but only when exactly one state name
    # is present — "Karnataka to Tamil Nadu" must stay unresolved.
    matches = {
        code
        for name, code in (*_NAME_TO_CODE.items(), *_NAME_ALIASES.items())
        if len(name) > 4 and name in lowered
    }
    return matches.pop() if len(matches) == 1 else None
