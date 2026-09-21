#!/usr/bin/env python
"""Measure extraction accuracy against invoices whose answers you know.

    python scripts/measure_accuracy.py ~/truth
    python scripts/measure_accuracy.py ~/truth --json report.json

``~/truth`` holds your documents and one ``expected.csv`` naming them:

    file,invoice_number,invoice_date,supplier.gstin,total
    inv-001.pdf,INV-2026-0042,2026-09-14,29AABCU9603R1ZJ,118000.00
    inv-002.pdf,KB/908,2026-09-15,,45200.50

Only the columns you fill in are measured. Nobody is going to key in every
optional field for 200 invoices, and scoring against blanks you never
claimed would invent failures. A blank cell means "not claimed"; to assert
that a field is genuinely absent from the page, write ``-``.

**Point this at real invoices, and keep them out of the repository.** Real
client documents belong on a machine you control (§32). The directory is
read and never copied, and nothing about a document's contents is written
to the report beyond the field values you are already comparing.

Exit codes: 0 — the run completed. 1 — it could not run, and says why.
Accuracy being low is not an error; it is a result.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.pipelines.invoice_pipeline import ExtractionPipeline  # noqa: E402
from app.providers.registry import get_provider  # noqa: E402
from app.services.accuracy import (  # noqa: E402
    COMPARABLE,
    AccuracyReport,
    compare_invoice,
    score,
)
from app.services.file_validation import validate_upload  # noqa: E402

#: A cell holding this asserts the field is genuinely not on the page, so an
#: extraction that produces a value for it counts as invented. An empty cell
#: says nothing at all.
ABSENT = "-"

DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


def read_manifest(directory: Path) -> tuple[list[tuple[Path, dict[str, str]]], list[str]]:
    """Return [(document, expected values)], and complaints about the file."""
    manifest = directory / "expected.csv"
    if not manifest.exists():
        raise SystemExit(
            f"No expected.csv in {directory}.\n"
            "It needs a 'file' column naming each document, plus one column "
            "per field you want measured. Known fields:\n  "
            + "\n  ".join(sorted(COMPARABLE))
        )

    rows: list[tuple[Path, dict[str, str]]] = []
    complaints: list[str] = []
    with manifest.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        if "file" not in headers:
            raise SystemExit("expected.csv needs a 'file' column.")

        unknown = [h for h in headers if h != "file" and h not in COMPARABLE]
        if unknown:
            complaints.append(
                f"Columns not measured (unknown field names): {', '.join(unknown)}"
            )

        for line, raw in enumerate(reader, start=2):
            name = (raw.get("file") or "").strip()
            if not name:
                continue
            document = directory / name
            if not document.exists():
                complaints.append(f"expected.csv line {line}: {name} is not in {directory}")
                continue

            expected: dict[str, str] = {}
            for column, value in raw.items():
                if column == "file" or column not in COMPARABLE:
                    continue
                text = (value or "").strip()
                if not text:
                    continue  # not claimed
                expected[column] = None if text == ABSENT else text  # type: ignore[assignment]
            rows.append((document, expected))

    return rows, complaints


async def measure(directory: Path) -> AccuracyReport:
    settings = get_settings()
    # The report is this tool's output, and the pipeline logs a paragraph
    # per document. Quiet by default; LOG_LEVEL=INFO when a run needs
    # explaining rather than summarising.
    configure_logging(
        settings.log_level if settings.log_level != "INFO" else "WARNING",
        json_output=False,
    )
    if not settings.provider_configured:
        # Measuring with no model would report how well the qr and
        # text_layer tiers do alone and call it the product's accuracy.
        raise SystemExit(
            "No extraction provider is configured, so there is nothing to "
            "measure. Set AI_BASE_URL, AI_API_KEY and AI_MODEL first — for a "
            "local Ollama, see DEPLOYMENT.md."
        )

    rows, complaints = read_manifest(directory)
    for complaint in complaints:
        print(f"  ! {complaint}", file=sys.stderr)
    if not rows:
        raise SystemExit("expected.csv named no documents that exist.")

    pipeline = ExtractionPipeline(provider=get_provider(), settings=settings)
    comparisons = []
    failures: list[tuple[str, str]] = []

    for index, (document, expected) in enumerate(rows, start=1):
        print(f"  [{index}/{len(rows)}] {document.name}", file=sys.stderr)
        try:
            validated = validate_upload(
                document.read_bytes(),
                filename=document.name,
                max_size_bytes=settings.max_file_size_bytes,
                max_page_count=settings.max_page_count,
            )
            result = await pipeline.run(validated)
        except Exception as exc:  # noqa: BLE001
            # A document that could not be read is reported as such, never
            # dropped — a run that quietly skipped its hard cases would
            # report an accuracy belonging to the easy ones.
            failures.append((document.name, f"{type(exc).__name__}: {exc}"))
            continue

        confidences = {
            path: fc.confidence for path, fc in result.confidence.fields.items()
        }
        comparisons.extend(
            compare_invoice(
                expected, result.invoice, document=document.name, confidences=confidences
            )
        )

    return score(comparisons, documents=len(rows) - len(failures), failures=failures)


def render(report: AccuracyReport) -> str:
    lines = ["", "Extraction accuracy", "=" * 66]

    if report.failures:
        lines.append(f"{len(report.failures)} document(s) could not be read at all:")
        for name, reason in report.failures[:10]:
            lines.append(f"    {name}: {reason}")
        lines.append("")

    if not report.measured:
        lines += ["Nothing was measured.", "=" * 66, ""]
        return "\n".join(lines)

    low, high = report.interval or (0.0, 1.0)
    lines.append(
        f"{report.documents} documents · {report.measured} field readings compared"
    )
    lines.append("")

    if report.is_publishable:
        lines.append(
            f"  Overall: {report.accuracy:.1%}  (95% CI {low:.1%}–{high:.1%})"
        )
    else:
        # Refusing the headline rather than printing a number somebody will
        # paste into a deck (§30, §33).
        lines.append(f"  Overall: somewhere between {low:.0%} and {high:.0%}.")
        lines.append(
            "  Too few readings to quote a single figure. Do not publish a "
            "number from this run."
        )
    lines.append("")
    lines.append(
        f"  of {report.measured} readings: {report.correct} correct · "
        f"{report.wrong} wrong · {report.missed} missed · {report.invented} invented"
    )
    lines.append(
        "  A miss is an honest null and costs review time. A wrong or "
        "invented value is the one that reaches a filing."
    )
    lines.append("")

    lines.append("  Per field")
    width = max(len(p) for p in report.fields) + 2
    for path in sorted(report.fields, key=lambda p: (report.fields[p].accuracy or 1.0)):
        entry = report.fields[path]
        if not entry.measured:
            continue
        interval = entry.interval or (0.0, 1.0)
        flags = []
        if entry.invented:
            flags.append(f"{entry.invented} invented")
        if entry.missed:
            flags.append(f"{entry.missed} missed")
        lines.append(
            f"    {path:<{width}} {entry.accuracy:>6.1%}  "
            f"({interval[0]:.0%}–{interval[1]:.0%}, n={entry.measured})"
            + (f"   {', '.join(flags)}" if flags else "")
        )

    if report.buckets:
        lines += ["", "  Does its confidence mean anything?"]
        for bucket in report.buckets:
            lines.append(
                f"    {bucket.label:<32} {bucket.accuracy:>6.1%} correct  "
                f"(n={bucket.total})"
            )
        lines.append(
            "    If the top row is not clearly better than the bottom, the "
            "confidence score is not telling you anything."
        )

    near = [d for d in report.disagreements if d.near_miss]
    if near:
        lines += [
            "",
            f"  {len(near)} of the failures differ only in case or punctuation — "
            "likely one formatting bug rather than a reading problem.",
        ]

    if report.disagreements:
        lines += ["", f"  Every disagreement ({len(report.disagreements)})"]
        lines.append(
            "  Check these against the page before believing the score: some "
            "will be mistakes in expected.csv, and finding those is the point."
        )
        for item in report.disagreements[:40]:
            lines.append(
                f"    {item.document}  {item.path}\n"
                f"        expected {item.expected!r}\n"
                f"        read     {item.actual!r}   ({item.verdict})"
            )
        if len(report.disagreements) > 40:
            lines.append(f"    … and {len(report.disagreements) - 40} more; use --json for all.")

    lines += ["=" * 66, ""]
    return "\n".join(lines)


def as_json(report: AccuracyReport) -> dict:
    return {
        "documents": report.documents,
        "readings": report.measured,
        "accuracy": report.accuracy,
        "interval": report.interval,
        "publishable": report.is_publishable,
        "totals": {
            "correct": report.correct,
            "wrong": report.wrong,
            "missed": report.missed,
            "invented": report.invented,
        },
        "fields": {
            path: {
                "accuracy": entry.accuracy,
                "interval": entry.interval,
                "n": entry.measured,
                "correct": entry.correct,
                "wrong": entry.wrong,
                "missed": entry.missed,
                "invented": entry.invented,
            }
            for path, entry in report.fields.items()
            if entry.measured
        },
        "calibration": [
            {"band": b.label, "accuracy": b.accuracy, "n": b.total} for b in report.buckets
        ],
        "unreadable": [{"document": n, "reason": r} for n, r in report.failures],
        "disagreements": [
            {
                "document": d.document,
                "field": d.path,
                "expected": d.expected,
                "read": d.actual,
                "verdict": d.verdict,
                "near_miss": d.near_miss,
            }
            for d in report.disagreements
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Documents plus expected.csv")
    parser.add_argument("--json", type=Path, default=None, help="Write the full report here")
    args = parser.parse_args()

    if not args.directory.is_dir():
        raise SystemExit(f"{args.directory} is not a directory.")

    report = await measure(args.directory)
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(as_json(report), indent=2, default=str))
        print(f"Full report written to {args.json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
