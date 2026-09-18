"""Upload validation (§5, §19)."""

from __future__ import annotations

import pytest

from app.core.errors import (
    FileTooLargeError,
    InvalidFileError,
    TooManyPagesError,
    UnsupportedFileTypeError,
)
from app.services.file_validation import sniff_mime_type, validate_upload
from tests.fixtures.invoices import InvoiceSpec, build_invoice_pdf, build_jpeg, build_png

LIMITS = {"max_size_bytes": 5 * 1024 * 1024, "max_page_count": 10}


def test_accepts_the_four_supported_types() -> None:
    for content, filename, expected in (
        (build_invoice_pdf(), "invoice.pdf", "application/pdf"),
        (build_png(), "invoice.png", "image/png"),
        (build_jpeg(), "invoice.jpg", "image/jpeg"),
    ):
        result = validate_upload(content, filename=filename, **LIMITS)
        assert result.content_type == expected
        assert result.checksum_sha256


def test_page_count_comes_from_the_pdf_not_the_caller() -> None:
    result = validate_upload(
        build_invoice_pdf(InvoiceSpec(extra_pages=3)), filename="i.pdf", **LIMITS
    )
    assert result.page_count == 4


def test_content_type_is_sniffed_not_trusted() -> None:
    # A Windows executable renamed to .pdf must not be accepted.
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(b"MZ\x90\x00" + b"\x00" * 200, filename="invoice.pdf", **LIMITS)


def test_rejects_unsupported_types() -> None:
    for content in (b"GIF89a" + b"\x00" * 50, b"PK\x03\x04" + b"\x00" * 50, b"<html>"):
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload(content, filename="x.pdf", **LIMITS)


def test_rejects_an_empty_file() -> None:
    with pytest.raises(InvalidFileError):
        validate_upload(b"", filename="x.pdf", **LIMITS)


def test_rejects_an_oversized_file() -> None:
    with pytest.raises(FileTooLargeError) as excinfo:
        validate_upload(
            build_invoice_pdf(), filename="i.pdf", max_size_bytes=100, max_page_count=10
        )
    assert excinfo.value.status_code == 413


def test_rejects_too_many_pages() -> None:
    with pytest.raises(TooManyPagesError):
        validate_upload(
            build_invoice_pdf(InvoiceSpec(extra_pages=5)),
            filename="i.pdf",
            max_size_bytes=5 * 1024 * 1024,
            max_page_count=3,
        )


def test_rejects_a_truncated_pdf() -> None:
    with pytest.raises(InvalidFileError):
        validate_upload(b"%PDF-1.4\nnot really a pdf", filename="i.pdf", **LIMITS)


def test_rejects_a_corrupt_image() -> None:
    with pytest.raises(InvalidFileError):
        validate_upload(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20, filename="i.png", **LIMITS)


def test_filename_path_components_are_stripped() -> None:
    result = validate_upload(
        build_invoice_pdf(), filename="../../../etc/passwd.pdf", **LIMITS
    )
    assert result.filename == "passwd.pdf"
    assert "/" not in result.filename


def test_sniffer_returns_none_for_unknown_content() -> None:
    assert sniff_mime_type(b"just some text") is None
