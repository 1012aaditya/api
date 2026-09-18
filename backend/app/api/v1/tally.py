"""Tally: the ledger master, supplier matching, and the voucher file.

The shape of this API follows the actual workflow: import your chart of
accounts once, say which ledgers the tax legs go to once, then every month
preview, fix the handful of suppliers we could not place, and download.

Nothing here posts to Tally directly. The customer downloads a file and
imports it themselves, which means they see exactly what is about to enter
their books before it does.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_rate_limit, get_request_id
from app.core.errors import InvalidFileError, InvalidRequestError, NotFoundError
from app.core.logging import get_logger
from app.db.session import get_db
from app.repositories.tally import (
    LedgerAliasRepository,
    LedgerRepository,
    TallySettingsRepository,
)
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.tally import (
    AliasOut,
    ConfirmMatchIn,
    LedgerImportResult,
    LedgerOut,
    LedgerSuggestion,
    TallyPreview,
    TallySettingsIn,
    TallySettingsOut,
    UnmatchedSupplier,
    VoucherPreview,
)
from app.services.tally import ledgers as ledger_parser
from app.services.tally import service as tally_service
from app.services.tally.voucher import render_envelope, summarize

router = APIRouter(prefix="/tally", tags=["tally"])
logger = get_logger("docuparse.tally")

#: Same cap as the CSV export: a window nobody meant to ask for is a mistake,
#: not a feature.
MAX_WINDOW_DAYS = 400


def _window(
    since: dt.date | None, until: dt.date | None
) -> tuple[dt.datetime | None, dt.datetime | None]:
    start = (
        dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC) if since else None
    )
    end = dt.datetime.combine(until, dt.time.max, tzinfo=dt.UTC) if until else None
    if start and end:
        if end < start:
            raise InvalidRequestError("'to' is before 'from'.")
        if (end - start).days > MAX_WINDOW_DAYS:
            raise InvalidRequestError(
                f"Export windows are limited to {MAX_WINDOW_DAYS} days."
            )
    return start, end


# --- ledger master -----------------------------------------------------


@router.post(
    "/ledgers",
    response_model=SuccessResponse[LedgerImportResult],
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Import the ledger master exported from Tally",
)
async def import_ledgers(
    file: UploadFile = File(..., description="Tally master XML, or a CSV."),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[LedgerImportResult]:
    payload = await file.read()
    if len(payload) > ledger_parser.MAX_UPLOAD_BYTES:
        raise InvalidFileError("The ledger file is too large.")

    parsed = ledger_parser.parse(payload, file.filename)

    repository = LedgerRepository(db)
    replaced = await repository.count(auth.organization_id)
    imported, aliases_kept = await repository.replace_all(auth.organization_id, parsed)
    await db.commit()

    # The ledger *names* are the customer's own account names — business data,
    # not secrets, but there is no reason to write them to the log either.
    logger.info(
        "tally.ledgers_imported",
        imported=imported,
        replaced=replaced,
        aliases_kept=aliases_kept,
    )
    return SuccessResponse(
        request_id=request_id,
        data=LedgerImportResult(
            imported=imported, replaced=replaced, aliases_kept=aliases_kept
        ),
    )


@router.get(
    "/ledgers",
    response_model=SuccessResponse[list[LedgerOut]],
    summary="The imported ledger master",
)
async def list_ledgers(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[LedgerOut]]:
    rows = await LedgerRepository(db).list_for_organization(
        auth.organization_id, limit=limit, offset=offset, search=search
    )
    return SuccessResponse(
        request_id=request_id,
        data=[
            LedgerOut(
                id=row.id, name=row.name, gstin=row.gstin, parent_group=row.parent_group
            )
            for row in rows
        ],
    )


# --- settings ----------------------------------------------------------


async def _settings_out(db: AsyncSession, organization_id: str) -> TallySettingsOut:
    settings, matcher = await tally_service.load_context(db, organization_id)
    return TallySettingsOut(
        company_name=settings.company_name,
        voucher_type=settings.voucher_type,
        purchase_ledger=settings.purchase_ledger,
        cgst_ledger=settings.cgst_ledger,
        sgst_ledger=settings.sgst_ledger,
        igst_ledger=settings.igst_ledger,
        utgst_ledger=settings.utgst_ledger,
        cess_ledger=settings.cess_ledger,
        round_off_ledger=settings.round_off_ledger,
        other_charges_ledger=settings.other_charges_ledger,
        configured=settings.is_configured(),
        ledger_count=len(matcher),
        unknown_ledgers=tally_service.unknown_configured_ledgers(settings, matcher),
    )


@router.get(
    "/settings",
    response_model=SuccessResponse[TallySettingsOut],
    summary="Which ledgers the non-supplier legs post to",
)
async def get_settings(
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TallySettingsOut]:
    data = await _settings_out(db, auth.organization_id)
    await db.commit()  # get_or_create may have inserted the row
    return SuccessResponse(request_id=request_id, data=data)


@router.put(
    "/settings",
    response_model=SuccessResponse[TallySettingsOut],
    summary="Update the posting settings",
)
async def update_settings(
    payload: TallySettingsIn,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TallySettingsOut]:
    await TallySettingsRepository(db).update(
        auth.organization_id, payload.model_dump(exclude_unset=False)
    )
    data = await _settings_out(db, auth.organization_id)
    await db.commit()
    return SuccessResponse(request_id=request_id, data=data)


# --- matching ----------------------------------------------------------


@router.post(
    "/matches",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[list[AliasOut]],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="Confirm which ledger a supplier is",
)
async def confirm_match(
    payload: ConfirmMatchIn,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[AliasOut]]:
    """Remember a human's answer, so this supplier resolves itself next month.

    Only a person's confirmation reaches this table — a similarity score never
    does. That is what makes it safe to trust it the next time around.
    """
    ledger = await LedgerRepository(db).get(auth.organization_id, payload.ledger_id)
    if ledger is None:
        raise NotFoundError("No ledger with that id exists in this organization.")
    if not payload.supplier_name and not payload.supplier_gstin:
        raise InvalidRequestError(
            "Give a supplier name or a GSTIN to match against the ledger."
        )

    aliases = await LedgerAliasRepository(db).confirm(
        organization_id=auth.organization_id,
        ledger_id=ledger.id,
        supplier_name=payload.supplier_name,
        supplier_gstin=payload.supplier_gstin,
        user_id=auth.user.id if auth.user else None,
    )
    await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=[
            AliasOut(
                id=alias.id,
                ledger_id=alias.ledger_id,
                ledger_name=ledger.name,
                key_type=alias.key_type,
                match_key=alias.match_key,
                created_at=alias.created_at,
            )
            for alias in aliases
        ],
    )


@router.get(
    "/matches",
    response_model=SuccessResponse[list[AliasOut]],
    summary="Supplier mappings confirmed so far",
)
async def list_matches(
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[AliasOut]]:
    aliases = await LedgerAliasRepository(db).list_for_organization(auth.organization_id)
    ledgers = {
        row.id: row.name
        for row in await LedgerRepository(db).all_for_matching(auth.organization_id)
    }
    return SuccessResponse(
        request_id=request_id,
        data=[
            AliasOut(
                id=alias.id,
                ledger_id=alias.ledger_id,
                ledger_name=ledgers.get(alias.ledger_id),
                key_type=alias.key_type,
                match_key=alias.match_key,
                created_at=alias.created_at,
            )
            for alias in aliases
        ],
    )


@router.delete(
    "/matches/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
    summary="Forget a confirmed supplier mapping",
)
async def delete_match(
    alias_id: str,
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not await LedgerAliasRepository(db).forget(auth.organization_id, alias_id):
        raise NotFoundError("No such confirmed mapping.")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- preview and export ------------------------------------------------


@router.get(
    "/preview",
    response_model=SuccessResponse[TallyPreview],
    summary="What would be posted, and what would not",
)
async def preview(
    since: dt.date | None = Query(default=None, alias="from"),
    until: dt.date | None = Query(default=None, alias="to"),
    batch_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TallyPreview]:
    start, end = _window(since, until)
    vouchers, settings, matcher = await tally_service.build_vouchers(
        db, auth.organization_id, since=start, until=end, batch_id=batch_id
    )
    await db.commit()

    counts = summarize(vouchers)
    return SuccessResponse(
        request_id=request_id,
        data=TallyPreview(
            postable=counts["postable"],
            blocked=counts["blocked"],
            ledger_count=len(matcher),
            settings_configured=settings.is_configured(),
            unmatched_suppliers=[
                UnmatchedSupplier(
                    name=row["name"],
                    gstin=row["gstin"],
                    documents=row["documents"],
                    suggestions=[LedgerSuggestion(**s) for s in row["suggestions"]],
                )
                for row in counts["unmatched_suppliers"]
            ],
            vouchers=[
                VoucherPreview(
                    document_id=v.document_id,
                    filename=v.filename,
                    invoice_number=v.invoice_number,
                    invoice_date=v.invoice_date,
                    supplier_name=v.supplier_name,
                    supplier_gstin=v.supplier_gstin,
                    total=str(v.total) if v.total else None,
                    ledger_name=v.match.ledger_name,
                    match_method=v.match.method,
                    postable=v.postable,
                    blockers=v.blockers,
                    notes=v.notes,
                )
                for v in vouchers[:limit]
            ],
        ),
    )


@router.get(
    "/vouchers.xml",
    response_class=Response,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="Purchase vouchers as a Tally import file",
)
async def export_vouchers(
    since: dt.date | None = Query(default=None, alias="from"),
    until: dt.date | None = Query(default=None, alias="to"),
    batch_id: str | None = Query(default=None),
    auth: AuthContext = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> Response:
    """Only postable vouchers are written.

    A blocked one is absent from the file rather than approximated in it. The
    preview says which, and why — an invoice missing from their books is a gap
    somebody notices, where a wrong one is a number that quietly reconciles to
    something untrue.
    """
    start, end = _window(since, until)
    vouchers, settings, _matcher = await tally_service.build_vouchers(
        db, auth.organization_id, since=start, until=end, batch_id=batch_id
    )
    await db.commit()

    postable = [voucher for voucher in vouchers if voucher.postable]
    if not postable:
        raise InvalidRequestError(
            "Nothing is ready to post. Check the preview: the usual causes are "
            "an unconfigured purchase ledger, or suppliers that have not been "
            "matched to a ledger yet."
        )

    xml = render_envelope(postable, company_name=settings.company_name)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    return Response(
        content=xml,
        media_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tally-vouchers-{stamp}.xml"',
            "X-Request-Id": request_id,
            "X-DocuParse-Voucher-Count": str(len(postable)),
            "X-DocuParse-Blocked-Count": str(len(vouchers) - len(postable)),
        },
    )
