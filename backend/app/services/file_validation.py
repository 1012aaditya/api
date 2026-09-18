"""Upload validation (§5, §23).

The declared Content-Type of a multipart part is attacker-controlled, so it
is not trusted for anything. The file's own leading bytes decide what it is,
and the file must then actually open before we spend money sending it to a
provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO

from app.core.errors import (
    FileTooLargeError,
    InvalidFileError,
    TooManyPagesError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger

logger = get_logger("docuparse.files")

PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"

SUPPORTED_MIME_TYPES = frozenset({PDF_MIME, PNG_MIME, JPEG_MIME})
SUPPORTED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

_MAGIC = (
    (b"%PDF-", PDF_MIME),
    (b"\x89PNG\r\n\x1a\n", PNG_MIME),
    (b"\xff\xd8\xff", JPEG_MIME),
)


@dataclass(frozen=True)
class ValidatedFile:
    content: bytes
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
    checksum_sha256: str

    @property
    def is_pdf(self) -> bool:
        return self.content_type == PDF_MIME


def sniff_mime_type(content: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if content.startswith(magic):
            return mime
    return None


def _pdf_page_count(content: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(BytesIO(content))
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise InvalidFileError(
                "The PDF is password-protected. Upload an unprotected copy."
            ) from exc
        raise InvalidFileError("The PDF could not be opened.") from exc
    except Exception as exc:  # noqa: BLE001 — any parse failure is a bad file
        raise InvalidFileError("The PDF could not be opened.") from exc

    try:
        count = len(document)
    finally:
        document.close()

    if count < 1:
        raise InvalidFileError("The PDF contains no pages.")
    return count


def _assert_image_opens(content: bytes) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(content)) as image:
            # verify() walks the file without decoding it into memory.
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidFileError("The image could not be decoded.") from exc


def validate_upload(
    content: bytes,
    *,
    filename: str,
    max_size_bytes: int,
    max_page_count: int,
) -> ValidatedFile:
    """Run every cheap check before anything expensive happens."""
    if not content:
        raise InvalidFileError("The uploaded file is empty.")

    size = len(content)
    if size > max_size_bytes:
        raise FileTooLargeError(
            f"File is {size} bytes; the limit is {max_size_bytes} bytes.",
            details={"size_bytes": size, "max_size_bytes": max_size_bytes},
        )

    mime = sniff_mime_type(content)
    if mime is None or mime not in SUPPORTED_MIME_TYPES:
        raise UnsupportedFileTypeError(
            "Only PDF, PNG, JPG and JPEG files are supported. "
            "The uploaded file's contents did not match any of them.",
            details={"supported": sorted(SUPPORTED_MIME_TYPES)},
        )

    if mime == PDF_MIME:
        page_count = _pdf_page_count(content)
        if page_count > max_page_count:
            raise TooManyPagesError(
                f"Document has {page_count} pages; the limit is {max_page_count}.",
                details={"page_count": page_count, "max_page_count": max_page_count},
            )
    else:
        _assert_image_opens(content)
        page_count = 1

    return ValidatedFile(
        content=content,
        filename=_safe_filename(filename),
        content_type=mime,
        size_bytes=size,
        page_count=page_count,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
    )


def _safe_filename(filename: str) -> str:
    """Strip path components so a crafted name cannot escape a storage prefix."""
    cleaned = (filename or "upload").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(c for c in cleaned if c.isprintable() and c not in '\0"')
    return cleaned[:400] or "upload"
