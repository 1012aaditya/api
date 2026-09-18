"""Validation primitives (§9, §10).

A check reports one of four outcomes, and the difference between them is the
whole point:

* ``passed``      — checked, and correct.
* ``warning``     — checked, and something looks off, but not provably wrong.
* ``failed``      — checked, and provably inconsistent.
* ``not_checked`` — the inputs this check needs were not present.

``not_checked`` is never quietly folded into ``passed``. Claiming a document
was verified when it was not is the failure mode this product exists to
avoid (§10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class CheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "status": str(self.status)}
        if self.message:
            payload["message"] = self.message
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class ValidationOutcome:
    overall: CheckStatus
    checks: list[CheckResult]

    def to_payload(self) -> dict[str, Any]:
        return {
            "overall": str(self.overall),
            "checks": [check.to_payload() for check in self.checks],
        }

    def by_name(self, name: str) -> CheckResult | None:
        return next((check for check in self.checks if check.name == name), None)

    @property
    def failed_names(self) -> list[str]:
        return [c.name for c in self.checks if c.status is CheckStatus.FAILED]


class Validator(Protocol):
    """A single business rule. Pure: inputs in, results out, no I/O."""

    name: str

    def run(self, context: ValidationContext) -> list[CheckResult]: ...


@dataclass(frozen=True)
class ValidationContext:
    """Everything a check may look at."""

    invoice: Any  # InvoiceData — typed loosely to keep validators import-light
    rounding_tolerance: Any  # Decimal


def passed(name: str, message: str | None = None, **details: Any) -> CheckResult:
    return CheckResult(name, CheckStatus.PASSED, message, details)


def warning(name: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(name, CheckStatus.WARNING, message, details)


def failed(name: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(name, CheckStatus.FAILED, message, details)


def not_checked(name: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(name, CheckStatus.NOT_CHECKED, message, details)
