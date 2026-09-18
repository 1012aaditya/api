"""A minimal PDF writer for test fixtures.

Written by hand rather than pulled in as a dependency: the test suite needs
PDFs with a real text layer, in a handful of layouts, and nothing heavier
earns its place in the dependency list for that.

Only the subset of PDF needed here is implemented — Helvetica text at
absolute positions on Letter/A4-sized pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


@dataclass
class TextLine:
    x: float
    y: float
    text: str
    size: float = 10.0
    bold: bool = False


@dataclass
class Page:
    lines: list[TextLine] = field(default_factory=list)

    def write(self, x: float, y: float, text: str, size: float = 10.0, bold: bool = False) -> None:
        self.lines.append(TextLine(x, y, text, size, bold))

    def content_stream(self) -> bytes:
        parts = ["BT"]
        for line in self.lines:
            font = "/F2" if line.bold else "/F1"
            parts.append(f"{font} {line.size:g} Tf")
            parts.append(f"1 0 0 1 {line.x:g} {line.y:g} Tm")
            parts.append(f"({_escape(line.text)}) Tj")
        parts.append("ET")
        return "\n".join(parts).encode("latin-1", "replace")


class PDFBuilder:
    def __init__(self) -> None:
        self.pages: list[Page] = []

    def new_page(self) -> Page:
        page = Page()
        self.pages.append(page)
        return page

    def build(self) -> bytes:
        if not self.pages:
            self.new_page()

        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)  # 1-based object number

        # Object numbers are allocated up front so /Kids and /Parent can
        # reference each other.
        catalog_num = 1
        pages_num = 2
        font_regular_num = 3
        font_bold_num = 4
        first_page_num = 5

        page_nums = [first_page_num + 2 * i for i in range(len(self.pages))]
        kids = " ".join(f"{n} 0 R" for n in page_nums)

        add(f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode())
        add(f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode())
        add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        for page in self.pages:
            stream = page.content_stream()
            contents_num = len(objects) + 2
            add(
                (
                    f"<< /Type /Page /Parent {pages_num} 0 R "
                    f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                    f"/Resources << /Font << /F1 {font_regular_num} 0 R "
                    f"/F2 {font_bold_num} 0 R >> >> "
                    f"/Contents {contents_num} 0 R >>"
                ).encode()
            )
            add(
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_offset = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
        return bytes(out)
