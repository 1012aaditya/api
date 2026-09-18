"""Turning a window of extractions into vouchers.

One place builds the vouchers, so the preview a human approves and the file
they download are produced by the same code. A preview that is generated
differently from the export is a preview that will eventually lie.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TallySettings
from app.repositories.extractions import ExtractionRepository
from app.repositories.tally import (
    LedgerAliasRepository,
    LedgerRepository,
    TallySettingsRepository,
)
from app.services.tally.matching import LedgerMatcher
from app.services.tally.voucher import Voucher, build_voucher

#: A cap, because a preview is rendered in a browser and an export is built in
#: memory before the first byte is sent.
MAX_VOUCHERS = 5000


async def load_context(
    db: AsyncSession, organization_id: str
) -> tuple[TallySettings, LedgerMatcher]:
    settings = await TallySettingsRepository(db).get_or_create(organization_id)
    ledgers = await LedgerRepository(db).all_for_matching(organization_id)
    aliases = await LedgerAliasRepository(db).list_for_organization(organization_id)
    return settings, LedgerMatcher(ledgers, aliases)


async def build_vouchers(
    db: AsyncSession,
    organization_id: str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    batch_id: str | None = None,
    limit: int = MAX_VOUCHERS,
) -> tuple[list[Voucher], TallySettings, LedgerMatcher]:
    settings, matcher = await load_context(db, organization_id)

    vouchers: list[Voucher] = []
    async for extraction, document, _validation in ExtractionRepository(
        db
    ).iter_for_export(
        organization_id, since=since, until=until, batch_id=batch_id
    ):
        invoice: dict[str, Any] = extraction.data or {}
        vouchers.append(
            build_voucher(
                document_id=document.id,
                filename=document.filename,
                invoice=invoice,
                settings=settings,
                matcher=matcher,
            )
        )
        if len(vouchers) >= limit:
            break
    return vouchers, settings, matcher


def unknown_configured_ledgers(
    settings: TallySettings, matcher: LedgerMatcher
) -> list[str]:
    """Configured ledger names that are not in the imported master.

    Tally rejects a voucher naming a ledger that does not exist, and it does so
    after the customer has already downloaded the file and opened Tally. Better
    to say so here.
    """
    if len(matcher) == 0:
        return []  # no master imported yet; nothing to check against
    names = [
        settings.purchase_ledger,
        settings.cgst_ledger,
        settings.sgst_ledger,
        settings.igst_ledger,
        settings.utgst_ledger,
        settings.cess_ledger,
        settings.round_off_ledger,
        settings.other_charges_ledger,
    ]
    return sorted(
        {name for name in names if name and matcher.by_name(name) is None}
    )
