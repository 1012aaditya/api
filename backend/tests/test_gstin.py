"""GSTIN structural validation (§9, §14).

Format only: a GSTIN passing these checks may still be inactive or never
issued. Nothing here contacts GSTN.
"""

from __future__ import annotations

import pytest

from app.validators.gstin import (
    check_gstin,
    compute_check_digit,
    is_valid_gstin,
    pan_from_gstin,
    state_code_from_place_of_supply,
    state_name,
)

VALID = "27AAPFU0939F1ZV"


def test_a_valid_gstin_passes_every_component() -> None:
    result = check_gstin(VALID)
    assert result is not None
    assert result.structure_valid and result.checksum_valid and result.state_recognised
    assert result.is_valid
    assert state_name(VALID) == "Maharashtra"
    assert pan_from_gstin(VALID) == "AAPFU0939F"


def test_absent_gstin_is_not_checked_rather_than_invalid() -> None:
    # The distinction matters: a B2C invoice has no buyer GSTIN, and that is
    # not the same as having a wrong one.
    assert check_gstin(None) is None
    assert check_gstin("") is None


@pytest.mark.parametrize(
    "gstin,reason_fragment",
    [
        ("27AAPFU0939F1Z", "15 characters"),
        ("27AAPFU0939F1ZX", "check digit"),
        ("45AAPFU0939F1ZV", "not an assigned"),
        ("2AAPFU0939F1ZVV", "character layout"),
        ("27AAPFU0939F1AV", "character layout"),
        ("hello", "15 characters"),
    ],
)
def test_invalid_gstins_are_rejected_with_a_reason(gstin: str, reason_fragment: str) -> None:
    result = check_gstin(gstin)
    assert result is not None
    assert not result.is_valid
    assert reason_fragment in (result.reason or "")


def test_check_digit_detects_single_character_corruption() -> None:
    body = VALID[:14]
    assert compute_check_digit(body) == VALID[14]
    corrupted = VALID[:5] + ("A" if VALID[5] != "A" else "B") + VALID[6:]
    assert not is_valid_gstin(corrupted)


def test_lowercase_and_spaced_input_is_tolerated() -> None:
    assert is_valid_gstin(" 27aapfu0939f1zv ".upper())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("29-Karnataka", "29"),
        ("Karnataka", "29"),
        ("33", "33"),
        ("Tamil Nadu (33)", "33"),
        ("Orissa", "21"),
        ("07 - Delhi", "07"),
        ("Karnataka to Tamil Nadu", None),
        ("Atlantis", None),
        (None, None),
    ],
)
def test_place_of_supply_resolution(text: str | None, expected: str | None) -> None:
    assert state_code_from_place_of_supply(text) == expected
