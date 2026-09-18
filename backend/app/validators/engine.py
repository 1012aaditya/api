"""Runs every check and rolls the results into one verdict (§10)."""

from __future__ import annotations

from decimal import Decimal

from app.core.logging import get_logger
from app.schemas.invoice import InvoiceData
from app.validators.base import CheckResult, CheckStatus, ValidationOutcome
from app.validators.checks import ALL_CHECKS

logger = get_logger("docuparse.validation")


def _overall(checks: list[CheckResult]) -> CheckStatus:
    """One failure fails the document; one warning warns it.

    A document where nothing could be checked is reported as ``not_checked``,
    not ``passed`` — "we verified nothing" and "we verified everything" must
    not produce the same verdict.
    """
    statuses = {check.status for check in checks}
    if CheckStatus.FAILED in statuses:
        return CheckStatus.FAILED
    if CheckStatus.WARNING in statuses:
        return CheckStatus.WARNING
    if CheckStatus.PASSED in statuses:
        return CheckStatus.PASSED
    return CheckStatus.NOT_CHECKED


def validate_invoice(
    invoice: InvoiceData, *, rounding_tolerance: Decimal
) -> ValidationOutcome:
    checks: list[CheckResult] = []
    for check in ALL_CHECKS:
        try:
            checks.extend(check(invoice, rounding_tolerance))
        except Exception:  # noqa: BLE001
            # A bug in one rule must not take down the extraction. It is
            # reported as unrun, never as passed.
            logger.exception("validation.check_raised", check=check.__name__)
            checks.append(
                CheckResult(
                    name=check.__name__.removeprefix("check_"),
                    status=CheckStatus.NOT_CHECKED,
                    message="This check could not be completed.",
                )
            )

    outcome = ValidationOutcome(overall=_overall(checks), checks=checks)
    logger.info(
        "validation.completed",
        overall=str(outcome.overall),
        failed=outcome.failed_names,
        check_count=len(checks),
    )
    return outcome
