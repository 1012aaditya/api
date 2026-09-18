"""POST /v1/invoices/extract — the product (§5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, enforce_document_quota, get_request_id
from app.core.errors import DocuParseError, FileTooLargeError, InvalidRequestError
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.extraction import ExtractionResponse
from app.services.extraction_service import ExtractionService, UploadedFile

router = APIRouter(prefix="/invoices", tags=["invoices"])

_CHUNK = 1024 * 1024

_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "The file could not be read."},
    401: {"model": ErrorResponse, "description": "Missing or invalid API key."},
    403: {"model": ErrorResponse, "description": "Monthly document quota exhausted."},
    413: {"model": ErrorResponse, "description": "File or page count too large."},
    415: {"model": ErrorResponse, "description": "Unsupported file type."},
    422: {"model": ErrorResponse, "description": "The document could not be extracted."},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
    503: {"model": ErrorResponse, "description": "No extraction provider is available."},
}


async def _read_upload(upload: UploadFile, *, max_size_bytes: int) -> bytes:
    """Read the body in chunks and stop the moment it exceeds the limit.

    Reading first and measuring afterwards would let anyone with a valid key
    push an arbitrarily large body into this process's memory.
    """
    buffer = bytearray()
    while chunk := await upload.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_size_bytes:
            raise FileTooLargeError(
                f"File exceeds the maximum size of {max_size_bytes} bytes.",
                details={"max_size_bytes": max_size_bytes},
            )
    return bytes(buffer)


@router.post(
    "/extract",
    response_model=ExtractionResponse,
    responses=_ERROR_RESPONSES,
    summary="Extract structured data from a GST invoice",
    description=(
        "Upload a PDF, PNG, JPG or JPEG as multipart/form-data under the field "
        "name `file`. Returns the extracted invoice, per-field confidence, and "
        "the validation report. Fields that could not be read are `null` — "
        "they are never guessed."
    ),
)
async def extract_invoice(
    file: UploadFile = File(..., description="The invoice: PDF, PNG, JPG or JPEG."),
    auth: AuthContext = Depends(enforce_document_quota),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ExtractionResponse:
    if file is None or not file.filename:
        raise InvalidRequestError("No file was provided in the 'file' form field.")

    service = ExtractionService(db)
    try:
        content = await _read_upload(
            file, max_size_bytes=auth.settings.max_file_size_bytes
        )
    except DocuParseError as exc:
        # Rejected while still reading the body, so the service never ran.
        await service.record_rejected_request(
            auth=auth, request_id=request_id, error=exc
        )
        raise

    return await service.extract_invoice(
        auth=auth,
        upload=UploadedFile(content=content, filename=file.filename),
        request_id=request_id,
    )
