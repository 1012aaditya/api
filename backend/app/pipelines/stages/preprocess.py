"""Turn an uploaded file into pages a vision model can read (§11).

Two outputs, both useful downstream:

* **Page images** — what actually goes to the provider.
* **Embedded text** — the PDF's own text layer, when it has one. It is not
  used as the extraction source (layout and tables are lost), but it is
  ground truth for checking that a value the model returned really appears
  on the page, which is what keeps confidence scores honest (§8).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO

from app.core.errors import InvalidFileError
from app.core.logging import get_logger
from app.services.file_validation import ValidatedFile

logger = get_logger("docuparse.preprocess")

# 150 DPI is the usual floor for reliable OCR of 8-10pt invoice type.
RENDER_DPI = 150
MAX_EDGE_PIXELS = 2200
JPEG_QUALITY = 85


@dataclass
class PreparedPage:
    page_number: int  # 1-based, as a human would cite it
    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    text: str | None = None

    def as_data_url(self) -> str:
        encoded = base64.b64encode(self.image_bytes).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass
class PreparedDocument:
    pages: list[PreparedPage] = field(default_factory=list)
    embedded_text: str | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text_layer(self) -> bool:
        return bool(self.embedded_text and self.embedded_text.strip())

    @property
    def total_image_bytes(self) -> int:
        return sum(len(page.image_bytes) for page in self.pages)


def _encode(image: object) -> tuple[bytes, int, int]:
    from PIL import Image

    assert isinstance(image, Image.Image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    longest = max(image.size)
    if longest > MAX_EDGE_PIXELS:
        ratio = MAX_EDGE_PIXELS / longest
        image = image.resize(
            (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
            Image.LANCZOS,
        )

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue(), image.width, image.height


def _prepare_pdf(content: bytes) -> PreparedDocument:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(BytesIO(content))
    prepared = PreparedDocument()
    text_chunks: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=RENDER_DPI / 72)
                image_bytes, width, height = _encode(bitmap.to_pil())

                page_text: str | None = None
                textpage = page.get_textpage()
                try:
                    page_text = textpage.get_text_bounded() or None
                finally:
                    textpage.close()

                if page_text:
                    text_chunks.append(page_text)
                prepared.pages.append(
                    PreparedPage(
                        page_number=index + 1,
                        image_bytes=image_bytes,
                        mime_type="image/jpeg",
                        width=width,
                        height=height,
                        text=page_text,
                    )
                )
            finally:
                page.close()
    except Exception as exc:  # noqa: BLE001
        raise InvalidFileError("The PDF could not be rendered.") from exc
    finally:
        document.close()

    prepared.embedded_text = "\n".join(text_chunks) if text_chunks else None
    return prepared


def _prepare_image(content: bytes) -> PreparedDocument:
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(content)) as image:
            # Phone photos of invoices routinely carry an EXIF rotation.
            image = ImageOps.exif_transpose(image)
            image_bytes, width, height = _encode(image)
    except Exception as exc:  # noqa: BLE001
        raise InvalidFileError("The image could not be processed.") from exc

    return PreparedDocument(
        pages=[
            PreparedPage(
                page_number=1,
                image_bytes=image_bytes,
                mime_type="image/jpeg",
                width=width,
                height=height,
            )
        ]
    )


def prepare_document(file: ValidatedFile) -> PreparedDocument:
    prepared = (
        _prepare_pdf(file.content) if file.is_pdf else _prepare_image(file.content)
    )
    if not prepared.pages:
        raise InvalidFileError("The document produced no readable pages.")
    logger.info(
        "preprocess.completed",
        pages=prepared.page_count,
        has_text_layer=prepared.has_text_layer,
        image_bytes=prepared.total_image_bytes,
    )
    return prepared
