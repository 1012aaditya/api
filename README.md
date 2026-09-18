# DocuParse

Turn Indian GST invoices into structured, validated JSON through one API call.

```
POST /v1/invoices/extract
  Authorization: Bearer dp_live_...
  file=@invoice.pdf
     ↓
{ "data": { … }, "confidence": { … }, "validation": { … } }
```

DocuParse is not an OCR wrapper. The pipeline runs document understanding,
vision extraction, normalization, schema validation, **business validation**
and confidence scoring, and it reports every one of those separately. The
value is in knowing which fields you can trust.

> **Status: MVP.** The API (phases 1–10) and the developer dashboard with its
> API playground (phases 13–14) are built and working. Async jobs, webhooks and
> the Python SDK are not — see
> [What is not built yet](#what-is-not-built-yet).

---

## Two things worth knowing before you start

**A field DocuParse could not read is `null`.** It is never a guess, never a
plausible default, and never carried over from elsewhere in the document. If
you get a value, it was on the page.

**If extraction cannot run, you get an error, not data.** With no AI provider
configured the API returns `503 extraction_provider_unavailable`. There is no
demo mode, no fixture fallback, and no code path in `backend/app/` that can
produce invoice data without a real provider call.

---

## Quickstart

### Prerequisites

- Python 3.11+
- Docker (for PostgreSQL and Redis), or your own Postgres 14+ and Redis 7+
- An API key for an OpenAI-compatible vision model endpoint

### 1. Install

```bash
git clone https://github.com/1012aaditya/api.git docuparse
cd docuparse
make setup          # creates backend/.venv, installs deps, copies .env.example → .env
```

### 2. Configure

Edit `.env`. The values that matter to start:

```bash
DATABASE_URL=postgresql+asyncpg://docuparse:docuparse@localhost:5432/docuparse
AI_PROVIDER=openai_compatible
AI_BASE_URL=https://api.openai.com/v1   # or your vLLM / OpenRouter / LiteLLM endpoint
AI_API_KEY=...                          # required — no key means 503, by design
AI_MODEL=...                            # a model that accepts images
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
```

`.env` is gitignored. Nothing in this repository contains a real secret.

### 3. Start dependencies and migrate

```bash
make up             # Postgres + Redis + MinIO
make migrate        # alembic upgrade head
```

### 4. Run

```bash
make run            # http://localhost:8000
```

Interactive API docs: <http://localhost:8000/docs>.

### 5. Make your first call

```bash
# Create an account
curl -X POST http://localhost:8000/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-long-enough-password","organization_name":"Acme"}'

# Use the access_token from that response to mint an API key
curl -X POST http://localhost:8000/v1/api-keys \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"local"}'

# Extract. The key is shown once, at creation — store it now.
curl -X POST http://localhost:8000/v1/invoices/extract \
  -H "Authorization: Bearer dp_live_..." \
  -F "file=@invoice.pdf"
```

### 6. Run the dashboard

```bash
make setup-web      # npm install + frontend/.env.local
make web            # http://localhost:3000
```

Sign up at <http://localhost:3000/signup>, then use the **Playground** to run an
invoice through the API and see the JSON, the validation report, the per-field
confidence and a copy-pasteable cURL command.

### 7. Run the tests

```bash
make test           # 226 backend tests, no services required
make lint
make test-web       # typecheck + browser smoke check (needs both servers up)
```

The backend suite runs entirely on SQLite and in-memory doubles, so it needs
neither Postgres, Redis, nor an AI provider.

---

## The response

```jsonc
{
  "success": true,
  "request_id": "req_01M2TRXWKDBD531H4TG82GF36C",
  "document_id": "doc_01M2TRXWPB8P2C5PYXRF85WMF7",
  "extraction_id": "ext_01M2TRXWS5WW0Y281EE47N0G7N",

  "data": {
    "document_type": "gst_invoice",
    "invoice_number": "INV-29381",
    "invoice_date": "2026-09-18",
    "place_of_supply": "29-Karnataka",
    "supplier": { "name": "…", "gstin": "29AABCU9603R1ZJ", "address": "…" },
    "buyer":    { "name": "…", "gstin": "…", "address": "…" },
    "items": [ { "description": "…", "hsn_sac": "998314", "quantity": 1,
                 "unit_price": 80000, "taxable_value": 80000,
                 "cgst": 7200, "sgst": 7200, "total": 94400 } ],
    "subtotal": 100000,
    "tax": { "taxable_amount": 100000, "cgst": 9000, "sgst": 9000,
             "igst": null, "utgst": null, "cess": null },
    "total": 118000,
    "currency": "INR",
    "e_invoice_details": { "irn": "…", "ack_number": "…", "ack_date": "…" }
  },

  "confidence": {
    "overall": 0.904,
    "band": "high",
    "fields": {
      "total": { "confidence": 0.93, "band": "high",
                 "page": 1, "source_text": "1,18,000.00" }
    },
    "low_confidence_fields": []
  },

  "validation": {
    "overall": "passed",
    "gstin_format_valid": true,
    "calculation_matches": true,
    "required_fields_present": true,
    "checks": [
      { "name": "supply_type_consistency", "status": "passed",
        "message": "Intrastate supply (CGST + SGST)." }
    ]
  },

  "processing": {
    "duration_ms": 79, "pages": 2,
    "provider": "openai_compatible", "model": "…",
    "prompt_version": "gst_invoice.v1",
    "input_tokens": 2480, "output_tokens": 612,
    "estimated_cost_usd": 0.01662
  }
}
```

Every response — success or error — carries a `request_id`, also returned as
the `X-Request-Id` header, written to the logs, and stored on the document,
extraction and usage rows. Quote it and any request can be reconstructed.

### Amounts

Money is `Decimal` throughout the pipeline and is emitted as a plain JSON
number: `118000`, not `"118000.00"` and not `118000.0`. Invoice arithmetic is
checked against printed totals, and binary floats do not reconcile reliably at
two decimal places.

---

## Validation

Validation is the product, not a footnote. Each check reports one of four
statuses, and the fourth one is the important one:

| Status | Meaning |
|---|---|
| `passed` | Checked, and correct. |
| `warning` | Checked, and something looks off, but not provably wrong. |
| `failed` | Checked, and provably inconsistent. |
| `not_checked` | The inputs this check needs were not on the document. |

`not_checked` is never folded into `passed`. A B2C invoice with no buyer GSTIN
reports `buyer_gstin_format: not_checked` — not a green tick.

Checks that run today:

- `required_fields_present` — invoice number, date, supplier, total
- `supplier_gstin_format`, `buyer_gstin_format` — layout, state code, and the
  mod-36 check digit
- `supply_type_consistency` — CGST+SGST for intrastate, IGST for interstate,
  decided from the supplier's state code and the place of supply
- `cgst_sgst_split` — CGST and SGST are levied at equal rates
- `invoice_total` — taxable base + taxes + charges + round-off ≈ printed total
- `taxable_amount_consistency` — subtotal − discount ≈ taxable amount
- `line_item_calculation` — quantity × unit price − discount ≈ taxable value
- `line_item_sum` — line items sum to the invoice-level taxable amount

Tolerance is configurable via `ROUNDING_TOLERANCE`.

**A failing invoice is still a 200.** An invoice whose totals do not add up is
a successful extraction of an inconsistent invoice, and you need to see it.
Errors are reserved for requests we could not process.

### On GST compliance

GSTIN validation here is **format validation**: character layout, an assigned
state code, and the published check-digit algorithm. A GSTIN that passes may
still be inactive, cancelled or never issued — confirming that needs a GSTN
lookup, which DocuParse does not perform. Nothing this API returns is a
compliance or tax statement.

---

## Confidence

Per-field confidence is computed from evidence, not asserted by the model.
Asking a language model for a 0–1 certainty produces a number that looks
calibrated and is not. Instead each score is a deterministic function of
signals that can be checked:

- did the model cite the characters it read, and flag the read as uncertain?
- do those characters actually appear in the PDF's own text layer?
- does the value satisfy the format its field is defined to have?
- do the arithmetic and jurisdiction checks covering that field agree?

Bands are `high ≥ 0.85`, `medium ≥ 0.60`, `low` below (configurable). A field
with no corroboration sits at the bottom of `medium` — "extracted, not
verified". `low` is reserved for fields there is an actual reason to doubt, so
`low_confidence_fields` is a review queue worth reading. Nothing ever scores
1.0.

---

## Errors

Consistent envelope, stable codes, no stack traces and no provider details:

```json
{
  "success": false,
  "request_id": "req_01M2…",
  "error": { "code": "rate_limit_exceeded", "message": "Rate limit exceeded." }
}
```

| Status | Code |
|---|---|
| 400 | `invalid_request`, `invalid_file` |
| 401 | `authentication_required`, `invalid_api_key` |
| 403 | `forbidden`, `quota_exceeded` |
| 404 | `not_found` |
| 409 | `conflict` |
| 413 | `file_too_large`, `too_many_pages` |
| 415 | `unsupported_file_type` |
| 422 | `extraction_failed` |
| 429 | `rate_limit_exceeded` (with `Retry-After`) |
| 500 | `internal_error` |
| 503 | `extraction_provider_unavailable` |

---

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | — | Liveness. Touches nothing. |
| `GET` | `/ready` | — | Readiness: database, and whether a provider is configured. |
| `POST` | `/v1/auth/signup` | — | Create an account and its organization. |
| `POST` | `/v1/auth/login` | — | Exchange email + password for a session token. |
| `GET` | `/v1/auth/me` | session | The signed-in user. |
| `POST` | `/v1/api-keys` | session | Mint a key. The secret is shown once. |
| `GET` | `/v1/api-keys` | session | List keys (masked). |
| `DELETE` | `/v1/api-keys/{id}` | session | Revoke. |
| `POST` | `/v1/api-keys/{id}/rotate` | session | Issue a replacement, retire the old one. |
| `POST` | `/v1/invoices/extract` | API key | **Extract a GST invoice.** |
| `GET` | `/v1/documents` | API key | List your documents. |
| `GET` | `/v1/documents/{id}` | API key | One document's metadata. |
| `DELETE` | `/v1/documents/{id}` | API key | Delete the stored bytes now. |
| `GET` | `/v1/documents/{id}/extraction` | either | The stored result for a document. |
| `GET` | `/v1/usage` | either | Totals, quota status, and a daily series. |
| `GET` | `/v1/usage/events` | either | The request log, newest first. |

API keys authenticate machine traffic; session tokens authenticate the
dashboard. **Key management is session-only** — a leaked API key cannot mint
more keys. Read endpoints marked *either* accept both, because the dashboard
holds a session rather than a key (keys are stored hashed and cannot be
replayed from the browser) and needs to read the same data.

`/v1/invoices/extract` also accepts a session, so the playground can run a real
extraction. Those runs are tagged `dashboard_request` in the usage log and cost
and count exactly like an API call — the playground does not get a free path.

---

## Dashboard and playground

`frontend/` is a Next.js 15 app in TypeScript and Tailwind 4 — a light theme
only, deliberately: this is developer infrastructure, so it is quiet, dense and
free of gradients and animation.

| Page | What it does |
|---|---|
| `/dashboard` | Requests, documents, success rate, average latency, a 30-day chart, quota, and the recent request log. |
| `/playground` | Upload an invoice, run it, and see the JSON, validation, per-field confidence, timing and a cURL command. |
| `/documents` | Everything uploaded, with per-document extraction results and one-click deletion. |
| `/usage` | 7/30/90-day totals and the full request log, with the estimated provider cost per request. |
| `/api-keys` | Create, rotate and revoke. The secret is shown once, at creation. |
| `/docs` | Quickstart, cURL/Python/JavaScript examples, the error table, and what is not built yet. |
| `/settings` | The organization, the account, and which API the dashboard is pointed at. |

There is no `/webhooks` or `/billing` page, because neither works yet. Listing
them as greyed-out menu items would make the dashboard look more finished than
it is.

### The chart is not decorative

The two series colours (`#2a78d6`, `#d03b3b`) were run through a
colour-vision-deficiency validator against the page surface and clear every
gate — CVD ΔE 23.8, normal-vision ΔE 31.6, both above 3:1 contrast. Changing
them means re-validating, not just picking something nicer. The daily series is
filled end to end, so a month with no traffic renders as a month of zeroes
rather than drawing two distant days as neighbours.

## Architecture

A modular monolith. One FastAPI application, strict internal seams.

```
backend/app/
├── api/v1/       routes — thin; they never call a model directly
├── core/         config, errors, security, logging, rate limiting, middleware
├── db/           engine, session, portable column types
├── models/       SQLAlchemy ORM
├── schemas/      Pydantic: invoice, extraction response, envelopes
├── providers/    base.py + openai_compatible.py + registry.py
├── pipelines/    stages/ (preprocess, parse, normalize, confidence) + strategies
├── prompts/      versioned extraction prompts
├── repositories/ data access — every method is organization-scoped
├── services/     file validation, storage, retention, extraction orchestration
└── validators/   GSTIN, totals, line items, supply type, required fields
```

The pipeline, in order:

```
FileValidation → Preprocess → Provider → Parse → Normalize
    → SchemaValidation → BusinessValidation → Confidence → Persistence → Response
```

### Swapping the AI provider

Everything above `providers/` is provider-agnostic. `AI_PROVIDER` selects an
adapter from a registry; `openai_compatible` ships today and speaks the wire
format used by the OpenAI API, vLLM, Ollama, LiteLLM, OpenRouter and Together.
Adding a vendor-specific adapter means one new file implementing
`DocumentAIProvider` and one `register_provider()` call.

Adapters must report their own token usage so cost accounting works regardless
of vendor, and must raise rather than return a substitute payload.

### Adding a document type

`pipelines/strategies.py` binds a document type to its schema, prompt, parser,
normalizer and validator set. Receipts, purchase orders and bank statements
each become one new strategy module — the pipeline, routing and persistence do
not change. Only `gst_invoice` is registered, because an endpoint that exists
but cannot work is worse than one that does not exist.

### Multi-tenancy

Every repository method that touches customer data takes `organization_id` as
a required argument. There is no unscoped `get_by_id` to reach for by
accident. The single deliberate exception is the API-key hash lookup, which is
what establishes the tenant in the first place.

A resource belonging to another organization returns `404`, not `403` — a 403
on an id you do not own confirms that the id exists.

---

## Security

- API keys are 32 random bytes, stored only as a SHA-256 digest. Passwords use
  bcrypt with a SHA-256 pre-hash so bcrypt's 72-byte truncation cannot make
  two long passwords interchangeable.
- Uploads are identified by magic bytes; the declared `Content-Type` is
  attacker-controlled and is not trusted. Bodies are read in bounded chunks
  and rejected the moment they exceed the limit.
- Storage keys are validated against traversal, and S3 objects are written
  with server-side encryption. Nothing is made public; reads go through a
  short-lived signed URL or the API.
- Logs are structured and redact API keys, authorization headers, passwords,
  provider credentials, key hashes and raw extracted invoice data.
- Errors never carry stack traces, driver messages or provider URLs.
- Secrets come only from the environment. `.env` is gitignored.

### Data retention

`DOCUMENT_RETENTION_DAYS` (default 7) sets when stored bytes become eligible
for deletion; an organization can override it. Setting it to `0` is
**process-and-delete**: the document is extracted and the bytes are never
written down at all.

`DELETE /v1/documents/{id}` deletes immediately. The sweeper handles expiry:

```bash
make purge          # or: python scripts/purge_expired_documents.py
```

Either way the metadata row survives — usage history and support lookups keep
working without retaining the document.

---

## Usage and cost

A `usage_events` row is written for every authenticated request, successful or
not, carrying endpoint, status, pages, provider, model, token counts,
estimated cost, duration and error code. A request that reached the provider
is billable even if it failed — it cost money. A request rejected before the
provider (bad file, wrong type, too large) is not.

`estimated_cost_usd` is computed from `AI_INPUT_COST_PER_MTOK` and
`AI_OUTPUT_COST_PER_MTOK`. With no rates configured it is `null` — unknown
cost reads as unknown, not as free. It is our estimate for your tracking, not
a bill from your provider.

Rate limits are per organization, per minute, Redis-backed with an in-memory
fallback for single-process development. The limiter fails open if Redis is
unreachable, so a limiter outage cannot take the API down. The monthly
document quota is database-backed and does not fail open.

---

## Configuration

Every setting, with its default, is documented in
[`.env.example`](.env.example). The ones most worth knowing:

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `openai_compatible` | Which adapter to use. |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | — | The endpoint. No key ⇒ `503`. |
| `MAX_FILE_SIZE_BYTES` | 20 MiB | Upload ceiling. |
| `MAX_PAGE_COUNT` | 25 | Page ceiling for the synchronous endpoint. |
| `DOCUMENT_RETENTION_DAYS` | 7 | `0` = process and delete. |
| `ROUNDING_TOLERANCE` | 1.0 | Currency tolerance when reconciling totals. |
| `CONFIDENCE_HIGH_THRESHOLD` | 0.85 | Band boundary. |
| `DEFAULT_RATE_LIMIT_PER_MINUTE` | 60 | Per organization; overridable per org. |
| `DEFAULT_MONTHLY_DOCUMENT_QUOTA` | 1000 | Per organization; overridable per org. |

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are present but unused — the
storage and auth layers are self-hosted. They exist so a Supabase-backed
deployment can be wired up without changing the env contract.

---

## Development

```bash
make help       # list every target
make setup      # venv + dependencies + .env
make up/down    # Postgres, Redis, MinIO
make migrate    # alembic upgrade head
make revision m="add webhooks"
make run        # uvicorn with reload
make test       # pytest — needs no services
make lint       # ruff
make setup-web  # npm install for the dashboard
make web        # the dashboard on :3000
make test-web   # typecheck + browser smoke check
make purge      # run the retention sweeper
```

### Migrations

The schema is owned by Alembic. `backend/tests/test_schema_and_logging.py`
compares the models against a freshly migrated database on every test run, so
a model change without a migration fails the suite rather than production.

### Tests

226 backend tests covering authentication, API key lifecycle, file validation,
the invoice schema, GSTIN validation, invoice and line-item arithmetic, the
extraction response, missing fields, malformed files, rate limiting, quota,
tenant isolation, webhook signatures, provider retry and failure handling,
retention, usage reporting, and log redaction.

The dashboard is checked by `frontend/scripts/smoke.mjs`, which drives a real
browser against a running stack (`npx playwright install chromium` once, then
`make test-web` with both servers up): it signs in, asserts every page renders live
data, checks for horizontal overflow at three widths, and fails if a full API
key ever appears in the rendered HTML. A component test would not have caught
the two layout bugs it found.

Fixtures are synthetic. `tests/fixtures/pdf_builder.py` writes real
text-bearing PDFs, and `tests/fixtures/invoices.py` renders GST invoices from
them. Every GSTIN in the repository is constructed to be checksum-valid and
identifies no registered taxpayer. No real customer document is in this
repository.

---

## What is not built yet

Honest scope. These are designed for but not implemented:

- **Async processing** — `POST /v1/documents`, `GET /v1/jobs/{id}`, worker.
  The pipeline object is already shared-ready; the queue is not wired.
- **Webhooks** — delivery, retries and backoff. The HMAC signing primitive
  exists and is tested; nothing sends yet.
- **Python SDK.**
- **Billing** — usage tracking is billing-ready; no payment provider is
  integrated.
- **Evaluation harness** — the synthetic corpus generator exists in
  `tests/fixtures/`; field-level accuracy scoring does not.

No accuracy figure is published anywhere in this repository, because none has
been measured.

## License

Proprietary. All rights reserved.
