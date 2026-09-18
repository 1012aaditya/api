# docuparse — Python client

Indian GST invoices as structured, validated JSON.

```bash
pip install docuparse
```

```python
from docuparse import DocuParse

client = DocuParse()  # reads DOCUPARSE_API_KEY
result = client.extract("invoice.pdf")

print(result.data.invoice_number)  # 'INV-2025-0042'
print(result.data.total)  # Decimal('118000.00')
print(result.data.supplier.gstin)  # '27AAACA1111A1Z5'
print(result.validation.overall)  # 'passed'
```

Point it at your deployment with `DOCUPARSE_BASE_URL`, or
`DocuParse(base_url="https://docuparse.internal")`. There is no hosted
endpoint yet, so the default is `http://localhost:8000`.

## Three things this library will not do for you

**A missing field is `None`, and stays `None`.** If the invoice did not have a
due date, `result.data.due_date` is `None` — not today's date, not an empty
string. Code that treats `None` as zero is choosing to; the library never
chooses for it.

**Amounts are `Decimal`, never `float`.** JSON numbers are parsed straight into
`Decimal`, so `118000.60` does not arrive as `118000.59999999999` and a
reconciliation that should balance to zero does.

**Confidence is not a pass mark.** `validation.passed` being `True` means the
arithmetic and the GSTIN formats checked out — not that the document was read
correctly. Look at `needs_review()` before anything reaches a ledger:

```python
if result.needs_review():
    print(result.validation.failures())
    print(result.confidence.low_confidence_fields)
    # ('supplier.gstin', 0.41, 'low')
    print(result.confidence.field_confidence("supplier.gstin"))
```

## Bulk

```python
receipt = client.batches.create("~/invoices/september", name="September")

print(receipt.accepted)  # 197
for bad in receipt.rejected:  # read this list
    print(bad.filename, bad.code, bad.message)

batch = client.batches.wait(receipt.batch_id)
print(batch.completed, batch.failed)

client.exports.invoices("september.csv", batch_id=batch.id)
client.exports.line_items("september-lines.csv", batch_id=batch.id)
```

`batches.create` takes a directory, a list of paths, open file handles, or
`(name, bytes)` tuples. A directory is scanned non-recursively for PDF, PNG,
JPG and JPEG, sorted by name.

A file the server refuses comes back in `rejected`, named, with a reason. It is
never silently dropped — which matters when 3 files out of 200 fail and you
need to know *which three*.

The export streams to disk and is written atomically: a failure part-way
through leaves your previous export intact rather than replacing it with half a
file.

## Into Tally

```python
client.tally.import_ledgers("ledger-master.xml")  # once
client.tally.configure(  # once
    company_name="Acme Traders Pvt Ltd",
    purchase_ledger="Purchase 18%",
    cgst_ledger="Input CGST",
    sgst_ledger="Input SGST",
    round_off_ledger="Round Off",
)

preview = client.tally.preview(batch_id=batch.id)
for supplier in preview["unmatched_suppliers"]:
    best = supplier["suggestions"][0]  # a suggestion, not an answer
    client.tally.confirm_match(best["ledger_id"], supplier_name=supplier["name"])

client.tally.vouchers("september-vouchers.xml", batch_id=batch.id)
```

Suppliers resolve by GSTIN, then by a mapping you confirmed earlier, then by
name. Anything less certain is a *suggestion* — the library never applies one.
Each confirmation is remembered, so that supplier resolves itself next month.

An invoice whose parts do not add up to its total is never written to the file;
it stays in `preview["vouchers"]` with the arithmetic that failed. If nothing is
postable, `vouchers()` raises rather than writing an empty envelope — which
would import into Tally perfectly and do nothing.

**Not verified against a real Tally installation.** The file is well-formed and
every voucher balances, but import it into a test company and check one voucher
before trusting it with a month of purchases.

## One at a time, in the background

```python
job = client.documents.submit("invoice.pdf")
job = client.jobs.wait(job.id)  # returns whether it passed or failed

if job.succeeded:
    result = client.documents.extraction(job.document_id)
```

`wait` returns a failed job rather than raising — a failure is an answer. It
raises `TimeoutError` only when *your* wait ran out, and says so explicitly,
because the job itself carries on running on the server.

## Errors

Every exception carries `code`, `status_code`, `request_id` and `details`.
Quote the `request_id` in a support request; it is the only handle that finds
one specific call in the server's logs.

```python
from docuparse import QuotaExceeded, RateLimited, ExtractionFailed, DocuParseError

try:
    client.extract("invoice.pdf")
except RateLimited as exc:
    time.sleep(exc.retry_after or 60)
except QuotaExceeded:
    ...  # monthly allowance used up
except ExtractionFailed:
    ...  # this document cannot be parsed; do not retry
except DocuParseError as exc:
    log.error("docuparse failed", extra={"request_id": exc.request_id})
```

| Exception | HTTP |
|---|---|
| `InvalidRequest` | 400, 404, 409 |
| `AuthenticationError` | 401 |
| `PermissionDenied` / `QuotaExceeded` | 403 |
| `UnsupportedFile` | 413, 415 |
| `ExtractionFailed` | 422 |
| `RateLimited` | 429 |
| `ProviderUnavailable` | 503 |
| `ServerError` | other 5xx |
| `APIConnectionError` | never reached the server |

The class is chosen by HTTP status, so an error code added to a newer server
still lands in the right `except` clause.

## Retries

The policy is deliberately asymmetric, and worth knowing about:

| | network error / 5xx | 429 |
|---|---|---|
| `GET`, `DELETE` | retried with backoff | retried, honouring `Retry-After` |
| `POST` | **not retried** | retried, honouring `Retry-After` |

A `POST` that submits a document may have been processed before the connection
broke; repeating it would extract, bill and count the same invoice twice. `429`
is the one status that proves the request did not run, so it is safe to repeat.

Until the API offers idempotency keys, this library would rather hand you an
error to decide about than quietly double-charge you. Set `max_retries=0` to
turn retries off entirely.

## Reference

```python
client.extract(file)                       -> Extraction
client.health()                            -> dict

client.documents.submit(file)              -> Job
client.documents.list(limit=, offset=)     -> list[Document]
client.documents.get(id)                   -> Document
client.documents.delete(id)                -> None
client.documents.extraction(document_id)   -> Extraction

client.jobs.get(id)                        -> Job
client.jobs.list(limit=, offset=)          -> list[Job]
client.jobs.wait(id, timeout=, poll_interval=) -> Job

client.batches.create(files, name=)        -> BatchSubmission
client.batches.get(id)                     -> Batch
client.batches.list(limit=, offset=)       -> list[Batch]
client.batches.wait(id, timeout=, poll_interval=) -> Batch

client.exports.invoices(dest, start=, end=, batch_id=)   -> Path | bytes
client.exports.line_items(dest, start=, end=, batch_id=) -> Path | bytes

client.tally.import_ledgers(file)          -> dict
client.tally.ledgers(search=, limit=)      -> list[dict]
client.tally.settings()                    -> dict
client.tally.configure(**ledger_names)     -> dict
client.tally.preview(start=, end=, batch_id=) -> dict
client.tally.confirm_match(ledger_id, supplier_name=, supplier_gstin=) -> list[dict]
client.tally.matches()                     -> list[dict]
client.tally.forget_match(alias_id)        -> None
client.tally.vouchers(dest, start=, end=, batch_id=) -> Path | bytes

client.usage.summary(days=)                -> dict
client.usage.events(limit=, offset=)       -> list[dict]
```

Every model keeps the server's response on `.raw`, so a field added to the API
tomorrow is readable today without upgrading this package.

## Not built yet

- **An async client.** Everything here is synchronous.
- **Webhook helpers.** Signature verification is documented in the main README
  but is not in this package.
- **Idempotency keys**, which is why `POST` is not retried.

## Development

```bash
pip install -e ".[dev]"
pytest          # 58 tests, no network
ruff check .
```

Tests drive the real client through `httpx.MockTransport`, so what is under
test is the request the library actually builds.

The package declares `requires-python = ">=3.9"` and uses nothing newer than
3.9 syntax, but the suite has only been run locally on 3.10 and 3.11 — 3.9 is
proved by CI, not by hand.
