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

> **Status: MVP.** The synchronous API, asynchronous jobs, webhooks, the Python
> SDK, and the dashboard with its API playground are built and working. So is
> the layer above them — the one that
> [chases clients for the documents](#chasing-the-documents-in-the-first-place)
> in the first place, which is what a CA firm actually spends its month doing.
> Billing is not built, and nobody has yet watched a WhatsApp message from it
> arrive on a phone — see [What is not built yet](#what-is-not-built-yet).

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

## Run it in one command

If you have Docker, you need nothing else — not Python, not Node, not Postgres:

```bash
git clone https://github.com/1012aaditya/api.git docuparse
cd docuparse
docker compose up --build        # or: make stack
```

Then open <http://localhost:3000> and sign up. The API is on
<http://localhost:8000>, its interactive schema on
<http://localhost:8000/docs>.

This runs the API, the worker and the dashboard against **PostgreSQL**, which
is what a real deployment uses. Migrations are applied on startup.

No extraction provider is configured out of the box, so
`POST /v1/invoices/extract` returns `503 extraction_provider_unavailable`
rather than inventing invoice data. The `qr` and `text_layer` tiers still read
digital invoices with no model at all — which is most B2B PDFs. To use a model,
point it at any OpenAI-compatible endpoint before starting:

```bash
AI_BASE_URL=http://host.docker.internal:11434/v1 \
AI_API_KEY=... AI_MODEL=... docker compose up --build
```

The compose file's `JWT_SECRET` and `WEBHOOK_SECRET` are development values and
say so. Generate real ones (`openssl rand -hex 32`) before it is reachable by
anyone but you.

> **Not yet verified:** the images are written against the documented base
> images and every part that can be checked without Docker has been — the
> dependency extraction, the package install, the entrypoint, and the exact
> production server the dashboard image runs. But the container registry is
> blocked from the environment they were written in, so `docker compose up`
> itself has never been executed. If it fails, the error is worth reporting
> rather than working around.

---

## Quickstart (without Docker)

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

Two processes: the API, and a worker for async jobs and webhook delivery.

```bash
make run            # http://localhost:8000
make worker         # in a second terminal
```

The API works without a worker — `/v1/invoices/extract` is synchronous. But
`POST /v1/documents` will queue jobs that nobody picks up, and webhooks will
never be delivered.

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

Or from Python, with the client in [`clients/python`](clients/python):

```python
from docuparse import DocuParse

client = DocuParse()                   # DOCUPARSE_API_KEY, DOCUPARSE_BASE_URL
result = client.extract("invoice.pdf")

print(result.data.invoice_number)      # 'INV-2025-0042'
print(result.data.total)               # Decimal('118000.00')
print(result.validation.overall)       # 'passed'
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
make test           # 388 backend tests, no services required
make test-pg        # the same 388 against PostgreSQL (needs `make up`)
make test-sdk       # 58 Python client tests, no services required
make lint
make test-web       # typecheck + browser smoke check (needs both servers up)
```

The backend suite runs entirely on SQLite and in-memory doubles, so it needs
neither Postgres, Redis, nor an AI provider.

Point `DOCUPARSE_TEST_DATABASE_URL` at a real PostgreSQL and the same 388 tests
run against the engine production uses. That is where dialect-only bugs live,
and it has found them: a boolean column whose server default was written as
`1` passes on SQLite and makes Postgres refuse the comparison outright, and the
model-drift check was building a *synchronous* engine from an async URL — so it
could only ever have run on SQLite, the one dialect it is least useful on. CI
runs both.

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
| `POST` | `/v1/invoices/extract` | API key | **Extract a GST invoice, synchronously.** |
| `POST` | `/v1/documents` | API key | **Submit for background extraction.** Returns a job id. |
| `GET` | `/v1/jobs` · `/v1/jobs/{id}` | either | Job state: queued / processing / completed / failed. |
| `POST` | `/v1/batches` | either | **Submit up to 200 files at once.** Unreadable files are listed, not dropped. |
| `GET` | `/v1/batches` · `/v1/batches/{id}` | either | Batch progress, counted from the jobs themselves. |
| `GET` | `/v1/exports/invoices.csv` | either | Streaming CSV, one row per invoice. |
| `GET` | `/v1/exports/line-items.csv` | either | Streaming CSV, one row per line item. |
| `POST` | `/v1/tally/ledgers` | either | **Import the ledger master exported from Tally.** |
| `GET` | `/v1/tally/ledgers` | either | The imported chart of accounts. |
| `GET` · `PUT` | `/v1/tally/settings` | either | Which ledgers the tax and purchase legs post to. |
| `GET` | `/v1/tally/preview` | either | What would be posted, and what would not. |
| `POST` · `GET` | `/v1/tally/matches` | either | Confirm which ledger a supplier is; list confirmations. |
| `DELETE` | `/v1/tally/matches/{id}` | either | Forget a confirmed mapping. |
| `GET` | `/v1/tally/vouchers.xml` | either | **Purchase vouchers as a Tally import file.** |
| `GET` | `/v1/documents` | API key | List your documents. |
| `GET` | `/v1/documents/{id}` | API key | One document's metadata. |
| `DELETE` | `/v1/documents/{id}` | API key | Delete the stored bytes now. |
| `GET` | `/v1/documents/{id}/extraction` | either | The stored result for a document. |
| `GET` | `/v1/usage` | either | Totals, quota status, and a daily series. |
| `GET` | `/v1/usage/events` | either | The request log, newest first. |
| `POST` | `/v1/webhooks` | session | Register an endpoint. Secret shown once. |
| `GET` | `/v1/webhooks` | session | List endpoints. |
| `DELETE` | `/v1/webhooks/{id}` | session | Delete an endpoint. |
| `POST` | `/v1/webhooks/{id}/rotate` | session | New signing secret, same endpoint. |
| `POST` | `/v1/webhooks/{id}/enable` · `/disable` | session | Stop or resume delivery. |
| `GET` | `/v1/webhooks/deliveries` | session | Delivery attempts, with status and retries. |
| `GET` | `/v1/command-centre` | session | **What needs attention today**, counted from the rows themselves. |
| `POST` · `GET` | `/v1/clients` | session | Add a client; list them with where each one stands. |
| `GET` · `PATCH` | `/v1/clients/{id}` | session | One client; change their details or switch automation off. |
| `GET` | `/v1/clients/{id}/missing-documents` | session | What this client still owes, across their open cases. |
| `POST` · `GET` | `/v1/cases` | session | **Open a compliance case**; list cases and what each is missing. |
| `GET` | `/v1/cases/{id}` | session | One case, with every requirement and its state. |
| `GET` | `/v1/exceptions` | session | **The review queue**: what the system would not decide alone. |
| `POST` | `/v1/exceptions/{id}/resolve` | session | Resolve or dismiss one, with a note kept on the record. |
| `POST` · `GET` | `/v1/tasks` | session | Work for a person, from the agent or from a colleague. |
| `POST` | `/v1/tasks/{id}/complete` | session | Mark one done. |
| `GET` | `/v1/conversations` | session | Every WhatsApp thread, with what was read from each reply. |
| `GET` | `/v1/agent/activity` | session | Everything the agent did, in order. |
| `GET` · `PUT` | `/v1/agent/policy` | session | **What the agent may do, and how hard it may push.** |
| `POST` | `/v1/agent/run` | session | Queue a chase for every case that needs one. It queues; it does not send. |
| `GET` · `POST` | `/v1/inbound/whatsapp/{org}` | signature | The provider's handshake, and inbound messages from clients. |

API keys authenticate machine traffic; session tokens authenticate the
dashboard. **Key management is session-only** — a leaked API key cannot mint
more keys. Read endpoints marked *either* accept both, because the dashboard
holds a session rather than a key (keys are stored hashed and cannot be
replayed from the browser) and needs to read the same data.

`/v1/invoices/extract` also accepts a session, so the playground can run a real
extraction. Those runs are tagged `dashboard_request` in the usage log and cost
and count exactly like an API call — the playground does not get a free path.

---

## Chasing the documents in the first place

Reading an invoice is the easy half. The hard half, for a CA firm, is getting
the invoice at all: the same fifteen WhatsApp messages every month, to the same
clients, asking for the same bank statement.

That is what the operations layer does. A firm adds its clients, opens a case
per client per period, and the system works out what that filing needs:

```bash
# Add a client.
curl -X POST http://localhost:8000/v1/clients \
  -H "Authorization: Bearer $SESSION" -H 'Content-Type: application/json' \
  -d '{"name":"Marigold Retail","whatsapp_phone":"+9198...","gstin":"29AABCU9603R1ZJ"}'

# Open this month's GST case. The requirements come with it.
curl -X POST http://localhost:8000/v1/cases \
  -H "Authorization: Bearer $SESSION" -H 'Content-Type: application/json' \
  -d '{"client_id":"cli_...","type":"gst","period":"2026-09"}'
# → status "blocked", with sales invoices, purchase invoices, the bank
#   statement and GSTR-2B all "missing"

# Queue a chase for everything that needs one.
curl -X POST http://localhost:8000/v1/agent/run \
  -H "Authorization: Bearer $SESSION" -H 'Content-Type: application/json' -d '{}'
# → {"scheduled": 14, "sent": 0}
```

`sent: 0` is not a bug. The sweep queues; the worker sends each reminder when
it comes due. Pressing the button cannot message two hundred clients at once.

When a client answers, the reply is read by rules rather than a model —
including Hinglish, because that is what people actually write:

| They send | It is read as | What happens |
|---|---|---|
| "kal bhej dunga" | a commitment, for tomorrow | the next reminder moves to the day after; **nothing is marked received** |
| "bhej diya hai" | a claim that it was sent | the requirement does **not** move; the firm is told to look |
| "mujhe samajh nahi aa raha" | a question | a task for a person; the agent does not explain tax |
| "mat bhejo message" | do not contact | automation off for that client, an exception for the firm, queued work cancelled |

### What the agent decides for itself

With a model configured, the agent does not simply walk a fixed schedule. It
reads the case — what is missing, what the client last said, how close the
deadline is, how many times they have been asked — and picks one of five
things: send a message it writes itself, wait, hand the case to a person,
raise a task, or do nothing. A client who says "thoda time do, audit chal
raha hai" gets a week, not another reminder tomorrow, and gets it in the
language they wrote in.

That is the only place in this product where a model decides anything, and it
is fenced in:

* **It sees one case, and no identifiers.** It cannot name another firm's
  client however it is prompted, because no id goes in or comes out.
* **Its message is checked before it is sent.** A draft that implies a
  document was received, asks for something the firm already has, carries a
  link, or is too long is thrown away — and the deterministic reminder goes
  instead. The suggestion and the reason it was refused both land on the
  timeline.
* **It cannot get past the policy.** Opt-outs, the daily cap and quiet hours
  are checked after the decision, not before it. A plan is a suggestion; the
  cap is not.
* **It cannot fail quietly.** No model configured, a model that is down, a
  model that answers with prose instead of JSON — all of them fall back to
  the fixed ladder. There is no path where a model having a bad day means a
  client is not chased.

Switch it off per firm on the Agent page and the fixed schedule runs, exactly
as it did before any of this existed.

Before letting it near a real client list, ask it what it would do:

```bash
curl -X POST http://localhost:8000/v1/agent/run \
  -H "Authorization: Bearer $SESSION" -H 'Content-Type: application/json' \
  -d '{"dry_run": true}'
# → {"scheduled": 8, "sent": 0,
#    "skipped": ["would chase Marigold Retail Pvt Ltd for bank statement and gstr-2b", ...]}
```

Nothing is queued and nothing is sent; the database is left exactly as it was
found. The dashboard has the same thing as a button.

When a file arrives it is classified from what it says — "Statement of
Account", an IFSC code, opening and closing balances — and the evidence is
kept, so the firm can see *why* it was read that way. A document the
classifier is not confident about is not filed under a guess: it waits for a
person.

An invoice then goes one step further, because **arriving is not the same as
being checked**. It is marked *received, waiting to be read*, queued for the
extraction pipeline, and only once the fields are off the page do the checks
run: is this GSTIN the client's, is it the right month, do the totals add up.
An invoice naming neither the client nor their supplier stops there and waits
for a person — which is how one client's papers stop landing in another's
books. If nothing can read it, that is an exception on the case too, not a
document quietly sitting in limbo.

Nothing says a requirement is met until its document has been read.

The ladder ends with a person, never with a fourth message. After the firm's
configured number of reminders the agent stops, raises an exception, and
creates a task saying who to call.

### What it will not do

* **It will not say a document arrived when it did not.** A client saying they
  sent it is a claim, recorded as a claim.
* **It will not decide what it is unsure of.** Below the firm's confidence
  threshold the document goes to the review queue, not into the filing.
* **It will not call anyone unless the firm switches calls on.** Voice is off
  by default, and a call that is placed opens by saying it is an AI assistant
  calling on behalf of the firm.
* **It will not message a client who asked it to stop**, or outside the firm's
  quiet hours, or past the firm's daily cap. Every refusal is written to the
  timeline, so "why did nobody chase them?" has an answer.
* **It will not answer questions about anyone's tax position.** Those become
  tasks.

### Seeing it without a client

```bash
python scripts/seed_demo.py           # a firm, ten synthetic clients, mixed states
python scripts/seed_demo.py --reset   # tear it down and build it again
```

The clients are invented, the GSTINs are checksum-valid and identify no
registered taxpayer, and no real document is in this repository. The demo uses
no external credentials and sends no message anywhere.

### Connecting a real WhatsApp number

```bash
WHATSAPP_PROVIDER=whatsapp_cloud
WHATSAPP_PHONE_NUMBER_ID=...      # the number's id, not the number
WHATSAPP_ACCESS_TOKEN=...         # a permanent system-user token
WHATSAPP_WEBHOOK_SECRET=...       # so the inbound URL cannot be posted to by anyone
WHATSAPP_VERIFY_TOKEN=...         # echoed during Meta's one-time handshake
```

Point the app's webhook at `POST /v1/inbound/whatsapp/{organization_id}`.
Missing credentials are a configuration error: the app refuses to start rather
than falling back to something that sends nothing.

**Nothing in this repository has been run against Meta.** The adapter follows
the Cloud API's documented shapes and is covered by tests against a scripted
transport, which is not the same as a message arriving on a phone. Send one to
yourself before pointing it at a client.

Two WhatsApp rules are worth knowing before you do:

- **The 24-hour window.** Free-form text is allowed only within 24 hours of the
  client's last message — which is exactly the window a reminder falls outside
  of. Past it, the only thing that may be sent is a template the business had
  approved beforehand.
- **So configure a template.** With `WHATSAPP_TEMPLATE_NAME` set, a reminder
  Meta refuses is re-sent as that template and the chase still happens. Its
  body must take four variables, in this order, because WhatsApp fills them
  positionally:

  | | |
  |---|---|
  | `{{1}}` | the firm's name |
  | `{{2}}` | the client's name |
  | `{{3}}` | the period, e.g. "GST 2026-09" |
  | `{{4}}` | what is outstanding, e.g. "bank statement and GSTR-2B" |

  The timeline then shows the template's name and those values rather than the
  free-form wording that was refused — nobody sent that text, so nothing says
  they did. Without a template configured, the refusal simply stands and the
  firm sees why.

### Connecting a phone line

```bash
VOICE_PROVIDER=exotel
EXOTEL_SID=...            EXOTEL_API_KEY=...       EXOTEL_API_TOKEN=...
EXOTEL_CALLER_ID=...      # the ExoPhone, not anyone's mobile
EXOTEL_FLOW_ID=...        # the App that speaks when the client answers
EXOTEL_REGION=in          # or sg
```

**One thing to understand before you switch this on.** Exotel does not speak
text this software sends it. A call connects your client to a *flow* you
build in the Exotel dashboard, and that flow is what talks. The script in
`app/services/voice.py` is what the firm intended to say; it is sent along as
a custom field and kept on the call record, but it is not a transcript.

That matters for one sentence in particular. A call from a machine must say
so, immediately — **and your Exotel flow has to be the thing that says it.**
This code cannot check that it does. Build the flow to open with something
like *"Hello, this is an automated assistant calling on behalf of Sharma &
Associates about your GST documents"*, and confirm it yourself by calling
your own number before pointing it at a client.

Calls are off by default for every firm, and nothing here retries: a phone
call placed twice because a response was slow is a real cost to a real
person. `VOICE_PROVIDER=mock` is refused in production, same as WhatsApp.


---

## Posting to Tally

A CSV is not what anyone wanted. They wanted the entry *in their books*. This
is the last mile, and it is where most of the actual work of this feature is.

```bash
# 1. Once: import the chart of accounts.
#    In Tally: Gateway → Display → List of Accounts → Export as XML.
curl -X POST .../v1/tally/ledgers -H "Authorization: Bearer $KEY" \
  -F "file=@ledger-master.xml"
# → {"imported": 412, "replaced": 0, "aliases_kept": 0}

# 2. Once: say where the non-supplier legs post.
curl -X PUT .../v1/tally/settings -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"company_name":"Acme Traders Pvt Ltd","purchase_ledger":"Purchase 18%",
       "cgst_ledger":"Input CGST","sgst_ledger":"Input SGST","igst_ledger":"Input IGST",
       "round_off_ledger":"Round Off"}'

# 3. Every month: look before you post.
curl -G .../v1/tally/preview -H "Authorization: Bearer $KEY" -d batch_id=bat_01M2...
# → {"postable": 194, "blocked": 6, "unmatched_suppliers": [...]}

# 4. Download.
curl -OJ ".../v1/tally/vouchers.xml?batch_id=bat_01M2..." -H "Authorization: Bearer $KEY"
```

### Matching a supplier to a ledger

The invoice prints `ACME TRADERS PVT. LTD.`. Their books hold `Acme Traders -
Bengaluru`. Nothing on the page says those are the same account, and guessing
wrong posts real money to the wrong supplier.

So four strategies run, strongest first, and only the first three ever resolve
anything:

| | Basis | Resolves? |
|---|---|---|
| **GSTIN** | The registration number on both sides | Yes — identity, not resemblance |
| **Confirmed alias** | A human answered this before | Yes |
| **Normalised name** | Same string once case, punctuation and `Pvt Ltd` are gone | Yes |
| **Similarity** | It looks close | **No** — offered as a suggestion only |

A near-miss is shown to a person, never applied. And when two of their ledgers
normalise to the same key, *neither* is used: picking whichever was imported
first would silently post to the wrong one.

**Confirmations are the thing that compounds.** A human answers "which ledger
is this?" once, and it is stored against both the GSTIN and the name. Next
month that supplier resolves itself. Re-importing the master carries those
confirmations across by ledger name rather than dropping them — six months in,
the deployment is better at *their* books than a fresh install could be, and
that is the only moat here that grows instead of eroding.

### What is never posted

Tally's format has three conventions that corrupt data silently when you get
them wrong: dates are `YYYYMMDD`, `ISDEEMEDPOSITIVE=Yes` means *debit* and a
debit's amount is *negative*, and every voucher must sum to zero.

The last one is a rule, not a formatting detail. An invoice whose parts do not
add up to its printed total is **not written to the file** — it appears in the
preview with the arithmetic that failed. A difference within ₹1 goes to the
round-off ledger and says so; anything larger means a field was misread, and
working out which one is not our job.

The same applies to everything else that would require a guess: an unmatched
supplier, a missing invoice date or number, GST charged with no ledger
configured to receive it. All of it is reported, none of it is approximated.

> A missing entry is something a bookkeeper notices at month end. A wrong one
> quietly reconciles to something untrue.

If nothing is postable the export returns an error rather than an empty
envelope — an empty envelope imports into Tally perfectly and does nothing,
which is the worst possible outcome.

### From Python

```python
client.tally.import_ledgers("ledger-master.xml")
client.tally.configure(purchase_ledger="Purchase 18%", cgst_ledger="Input CGST", ...)

preview = client.tally.preview(batch_id=batch.id)
for supplier in preview["unmatched_suppliers"]:
    print(supplier["name"], "→", [s["ledger_name"] for s in supplier["suggestions"]])
    client.tally.confirm_match(chosen_ledger_id, supplier_name=supplier["name"])

client.tally.vouchers("september-vouchers.xml", batch_id=batch.id)
```

### Not verified yet

The file is well-formed XML, every voucher balances to zero, and the conventions
above are implemented deliberately — but **it has not been imported into a real
Tally installation**. Import into a test company and check one voucher before
trusting it with a month of purchases. The dashboard says the same thing, in
the same words, above the download button.

---

## Python client

`clients/python` is a small, dependency-light client (`httpx` only). It is
published from this repository and versioned with it.

```bash
pip install docuparse
```

```python
from docuparse import DocuParse, QuotaExceeded

client = DocuParse()                            # DOCUPARSE_API_KEY

receipt = client.batches.create("~/invoices/september", name="September")
for bad in receipt.rejected:                    # read this list
    print(bad.filename, bad.code, bad.message)

batch = client.batches.wait(receipt.batch_id)
client.exports.invoices("september.csv", batch_id=batch.id)
```

Three decisions in it are worth repeating here, because they are the same
decisions the API makes and the client would undo them if it were careless:

- **Amounts are `Decimal`, never `float`.** JSON numbers are parsed straight
  into `Decimal`, so `118000.60` does not arrive as `118000.59999999999`. This
  is why the transport parses the body itself instead of calling
  `response.json()`.
- **A missing field is `None` and stays `None`** — not zero, not today's date.
  `result.needs_review()` folds the validation verdict and the confidence floor
  into the one question a bookkeeper actually asks.
- **A `POST` is retried only on `429`.** A network failure or a `5xx` on a
  submission might mean the server already processed it, and repeating that
  would extract, bill and count the same invoice twice. `429` is the one status
  that proves the request did not run. `GET`s retry freely. Until the API
  offers idempotency keys, an error the caller can decide about beats a silent
  double charge.

Every model keeps the server's response on `.raw`, so a field added to the API
tomorrow is readable without upgrading the package.

51 tests cover it, driving the real client through `httpx.MockTransport` so
what is under test is the request the library actually builds. Full reference:
[`clients/python/README.md`](clients/python/README.md).

Not built: an async client, webhook helpers in the package, and a JavaScript
SDK. `fetch` against the documented endpoints is the JavaScript path for now.

---

## Running it without sending documents anywhere

Invoices are client financial data. If your customers require that it never
leaves your infrastructure — or you would simply rather promise that — this
runs fully local.

### The provider is already an interface

```bash
AI_BASE_URL=http://localhost:8001/v1
AI_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

That is the whole change. The `openai_compatible` adapter speaks the wire
format that vLLM, Ollama, llama.cpp, LM Studio and LiteLLM all implement, so
no code moves. Two things about vLLM specifically are worth having: **prefix
caching**, because the ~3,300-character system prompt is identical on every
call and becomes nearly free after the first, and **guided decoding**, which
constrains generation to the schema and guarantees valid JSON — the one thing
local models are reliably worse at than hosted ones.

### Better: most invoices never need a model at all

The real saving is not a faster model, it is not calling one. Extraction runs
in tiers, cheapest first, and escalates only when what it has is not enough:

| Tier | Reads | Cost | Covers |
|---|---|---|---|
| `qr` | The e-invoice QR — the IRP's own record | ~free, ~5 ms | Registered e-invoices |
| `text_layer` | The characters already in the PDF | free, ~50 ms | Digital B2B invoices |
| `model` | A vision model on the page images | expensive | Scans, photos, odd layouts |

Measured on the repository's own fixtures, both cheap tiers return a complete,
validated header in **107 ms and 157 ms respectively, with no provider call**:

```
e-invoice PNG   tiers=['qr']          model_called=False   confidence 0.85
digital PDF     tiers=['text_layer']  model_called=False   confidence 0.95
```

`EXTRACTION_TIERS` selects which run. Drop `model` from the list and the
deployment is model-free: a document the cheap tiers cannot read comes back
partially filled with the gaps **reported**, not guessed.

Line items are the honest catch. Neither cheap tier extracts them — rebuilding
table columns without layout analysis guesses, and a guessed line item is worse
than none. So with `EXTRACTION_REQUIRE_LINE_ITEMS=true` (the default) a
table-bearing invoice still escalates. Set it to `false` when header data —
numbers, dates, GSTINs, totals — is what your workflow actually needs.

### Two readings are better than one

Even when the model does run, the cheap tiers have already read the document
independently. That is a correctness win, not only a cost one: the response
carries a `source_agreement` check, and a disagreement is reported rather than
resolved behind your back.

```jsonc
{ "name": "source_agreement", "status": "warning",
  "message": "Sources disagreed on total. The more direct source was used…",
  "details": { "conflicts": [ { "field": "total", "kept": "118000.00",
    "kept_source": "qr", "conflicting": "125000", "conflicting_source": "model" } ] } }
```

Precedence is by directness: the QR is the IRP's own record, the text layer is
the literal characters in the file, and the model is a reading of a picture of
those characters.

### What the QR tier does not do

It does **not verify the signature.** That needs the IRP's public key, which
this deployment does not ship. A decoded QR is therefore treated as a very
strong reading, not as proof: the values still go through validation, the
response says so in `processing.notes`, and a forged QR would be caught by the
arithmetic checks rather than trusted.

### The honest trade-off

A local 7B model will be meaningfully worse than a frontier model on crumpled
phone photos and handwriting. The validation layer catches most bad
extractions before your customer sees them, but you are now the one
responsible for quality — which makes the evaluation harness (not yet built)
more important, not less.

## Asynchronous processing

A 25-page scan can take a while. `POST /v1/documents` stores the upload,
queues a job and returns immediately:

```bash
curl -X POST http://localhost:8000/v1/documents \
  -H "Authorization: Bearer dp_live_..." \
  -F "file=@invoice.pdf"
# → {"success":true,"job_id":"job_01M2…","document_id":"doc_01M2…","status":"queued"}

curl http://localhost:8000/v1/jobs/job_01M2… -H "Authorization: Bearer dp_live_..."
# status: queued → processing → completed | failed

curl http://localhost:8000/v1/documents/doc_01M2…/extraction \
  -H "Authorization: Bearer dp_live_..."
```

### The queue is in Postgres, not Redis

At this scale that is a feature rather than a shortcut: the job row and the
document row commit in one transaction, a stuck job is a row you can look at
and edit, and there is no second source of truth to drift. Workers claim rows
with `FOR UPDATE SKIP LOCKED`, so several can run at once and two racing
workers take two different jobs. A worker killed mid-job leaves the row in
`processing`; another worker returns it to the queue after
`JOB_STALE_AFTER_SECONDS`.

Move to Redis when a single Postgres cannot keep up with the claim rate — the
`JobRepository` is the only thing that would change.

### What is retried, and what is not

A job that failed because the provider was briefly unreachable is retried with
backoff (30s, 2m, 10m), bounded by `JOB_MAX_ATTEMPTS`. A document the model
could not parse is **not**: the same bytes and the same prompt produce the same
answer, so a retry would only cost money (§34). Nothing here can loop forever.

A submission is not billable; the worker's attempt is. Refusing a document for
its type or size costs nothing and is recorded but not billed.

---

## Bulk upload and CSV export

A month of purchase invoices is a folder, not a document. `POST /v1/batches`
takes up to `MAX_BATCH_FILES` (default 200) in one multipart request, queues
them on the same job queue as `/v1/documents`, and returns immediately:

```bash
curl -X POST https://api.docuparse.example/v1/batches \
  -H "Authorization: Bearer $DOCUPARSE_API_KEY" \
  -F "name=September purchases" \
  -F "files=@inv-001.pdf" -F "files=@inv-002.pdf" -F "files=@notes.gif"
```

```json
{
  "success": true,
  "request_id": "req_01M2…",
  "batch_id": "bat_01M2V5TG5DX6B9EE970GX7AQGW",
  "accepted": 2,
  "job_ids": ["job_01M2…", "job_01M2…"],
  "rejected": [
    {
      "filename": "notes.gif",
      "code": "unsupported_file_type",
      "message": "Only PDF, PNG, JPG and JPEG files are supported. The uploaded file's contents did not match any of them."
    }
  ]
}
```

**A file we cannot read is reported, not dropped.** One bad scan does not fail
the other 199, and it does not disappear either — it comes back named, with the
reason, so you know exactly what to re-send. The same is true of the quota: if
the batch would cross your monthly allowance, the files that fit are queued and
the rest are returned as `rejected` with `quota_exceeded`, naming each one.

`GET /v1/batches/{id}` reports progress. The counts are derived by grouping over
the batch's jobs, not stored on the batch row — a counter that has to be updated
in step with the jobs is a counter that will eventually disagree with them.

```json
{
  "success": true,
  "request_id": "req_01M2…",
  "data": {
    "id": "bat_01M2V5TG5DX6B9EE970GX7AQGW",
    "name": "September purchases",
    "document_count": 199,
    "rejected_count": 1,
    "total": 199,
    "queued": 4,
    "processing": 1,
    "completed": 194,
    "failed": 0,
    "done": false,
    "created_at": "2025-09-18T09:14:22Z"
  }
}
```

### Export

Results come back as CSV, streamed row by row so a 10,000-invoice export does
not have to fit in memory:

```bash
curl -G https://api.docuparse.example/v1/exports/invoices.csv \
  -H "Authorization: Bearer $DOCUPARSE_API_KEY" \
  -d from=2025-09-01 -d to=2025-09-30 -o september.csv
```

`invoices.csv` is one row per invoice (35 columns: the header fields, both
parties, the tax split, the validation verdict and the confidence).
`line-items.csv` is one row per line, carrying its invoice number so the two
join. Both accept `from`, `to` and `batch_id`; the window is capped at 400 days.

Two details that matter more than they look:

- **A null is an empty cell, never the string `None`.** A spreadsheet formula
  over `None` silently produces nonsense; over an empty cell it produces an
  empty cell. Where a field was not on the invoice, the column is blank.
- **The file starts with a UTF-8 BOM**, because Excel otherwise reads
  `₹` and Devanagari supplier names as mojibake. Google Sheets and pandas both
  skip the BOM, so nothing else is affected.

The dashboard's **Bulk upload** page is the same two endpoints with a drop zone
in front of them.

## Webhooks

Register an endpoint and stop polling:

```bash
curl -X POST http://localhost:8000/v1/webhooks \
  -H "Authorization: Bearer <session token>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://api.yourapp.com/hooks/docuparse",
       "events":["document.completed","document.failed"]}'
```

Events: `document.processing`, `document.completed`, `document.failed`.

```jsonc
{
  "id": "whd_01M2…",            // stable across retries — use it to dedupe
  "event": "document.completed",
  "job_id": "job_01M2…",
  "document_id": "doc_01M2…",
  "extraction_id": "ext_01M2…",
  "status": "completed",
  "validation": { "overall": "passed" },
  "occurred_at": "2026-09-18T19:54:36.919878+00:00"
}
```

### Signing

Every delivery carries `X-DocuParse-Signature: t=<unix>,v1=<hmac>`, where the
HMAC-SHA256 is over `"<t>." + raw body`. The timestamp is *inside* the signed
material, so a captured delivery cannot be replayed later.

```python
import hashlib, hmac, time

def verify(body: bytes, header: str, secret: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    issued_at, received = int(parts["t"]), parts["v1"]
    if abs(time.time() - issued_at) > 300:
        return False
    expected = hmac.new(
        secret.encode(), f"{issued_at}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)
```

**There is no webhook secret at rest.** Each endpoint's secret is derived from
the deployment's `WEBHOOK_SECRET` plus the endpoint's id and a version counter,
so there is nothing in the database to leak, and rotating one endpoint is a
version bump. That also means the secret genuinely cannot be shown twice.

### Retries

`5xx`, timeouts and refused connections retry with exponential backoff — 30s,
1m, 2m, 4m, up to a six-hour cap — bounded by `WEBHOOK_MAX_ATTEMPTS`. A `4xx`
does not retry: the receiver understood and refused. An endpoint that fails
`WEBHOOK_FAILURE_THRESHOLD` times in a row is disabled until you re-enable it.

The receiver's response body is never stored — only the status code. That is
somebody else's server talking.

### Webhook URLs are an SSRF vector

A customer-supplied URL is not just a string: left unchecked, "deliver my
webhook to `http://169.254.169.254/`" turns this service into a proxy for
reading cloud instance metadata. Every destination is resolved and checked
against private, loopback, link-local, multicast and reserved address space —
at registration *and* again immediately before each delivery, because DNS can
change in between. Redirects are not followed, since a redirect is another hop.
`WEBHOOK_ALLOW_PRIVATE_URLS=true` exists for local development and re-opens
exactly the hole this closes; never set it in production.

Known residual risk: re-checking narrows the DNS-rebinding window to one
connection rather than closing it. Closing it entirely needs connection-level
IP pinning.

## Dashboard and playground

`frontend/` is a Next.js 15 app in TypeScript and Tailwind 4 — a light theme
only, deliberately: this is developer infrastructure, so it is quiet, dense and
free of gradients and animation.

| Page | What it does |
|---|---|
| `/dashboard` | Requests, documents, success rate, average latency, a 30-day chart, quota, and the recent request log. |
| `/playground` | Upload an invoice, run it synchronously or as a background job, and see the JSON, validation, per-field confidence, timing and a cURL command. |
| `/batches` | Drop a folder of invoices in, watch the batch drain, and download the results as CSV. |
| `/documents` | Everything uploaded, with per-document extraction results and one-click deletion. |
| `/tally` | Import your ledger master, match suppliers once, and download a month of purchase vouchers. |
| `/usage` | 7/30/90-day totals and the full request log, with the estimated provider cost per request. |
| `/api-keys` | Create, rotate and revoke. The secret is shown once, at creation. |
| `/webhooks` | Register endpoints, rotate secrets, enable or disable, and read the delivery log. |
| `/docs` | **Public** — the API reference, readable without an account: the SDK quickstart, cURL/Python/JavaScript examples, the error table, and what is not built yet. |
| `/settings` | The organization, the account, and which API the dashboard is pointed at. |

There is no `/billing` page, because billing does not work yet. Listing it as a
greyed-out menu item would make the dashboard look more finished than it is.

`/docs` sits outside the authentication gate, in the `(public)` route group. It
is what a developer reads *before* deciding to sign up, so putting it behind a
login would hide it from the people it is written for. Same page, same URL,
signed in or out — the header offers the dashboard or an account accordingly.

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
backend/          the API, the worker and the migrations
frontend/         the dashboard, and the public API reference at /docs
clients/python/   the Python client, published as `docuparse`
```

```
backend/app/
├── api/v1/       routes — thin; they never call a model directly
├── core/         config, errors, security, logging, rate limiting, middleware
├── db/           engine, session, portable column types
├── models/       SQLAlchemy ORM
├── schemas/      Pydantic: invoice, extraction response, envelopes
├── providers/    base.py + openai_compatible.py + registry.py
├── pipelines/    tiers (qr, text_layer, model), merge, stages, strategies
├── prompts/      versioned extraction prompts
├── repositories/ data access — every method is organization-scoped
├── services/     file validation, storage, retention, extraction, webhooks
├── workers/      the background worker loop
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
| `EXTRACTION_TIERS` | `qr,text_layer,model` | Ordered, cheapest first. Drop `model` to go fully local. |
| `EXTRACTION_REQUIRE_LINE_ITEMS` | `true` | Off means the cheap tiers can answer alone. |
| `AI_PROVIDER` | `openai_compatible` | Which adapter to use. Works with vLLM, Ollama, LM Studio. |
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
make stack      # everything in Docker: API, worker, dashboard, Postgres
make up/down    # just the backing services, for host-side development
make migrate    # alembic upgrade head
make revision m="add webhooks"
make run        # uvicorn with reload
make test       # pytest — needs no services
make test-pg    # the same suite against PostgreSQL
make lint       # ruff
make setup-sdk  # editable install of the Python client
make test-sdk   # the client's tests
make lint-sdk   # ruff check + format check
make setup-web  # npm install for the dashboard
make worker     # async jobs + webhook delivery
make web        # the dashboard on :3000
make test-web   # typecheck + browser smoke check
make purge      # run the retention sweeper
```

### Migrations

The schema is owned by Alembic. `backend/tests/test_schema_and_logging.py`
compares the models against a freshly migrated database on every test run, so
a model change without a migration fails the suite rather than production.

### Tests

388 backend tests covering authentication, API key lifecycle, file validation,
the invoice schema, GSTIN validation, invoice and line-item arithmetic, the
extraction response, missing fields, malformed files, rate limiting, quota,
tenant isolation, webhook signatures, provider retry and failure handling,
retention, usage reporting, log redaction, async job lifecycle and retry
policy, worker claim semantics, webhook signing and replay resistance, delivery
retries and backoff, the SSRF guard on webhook destinations, QR and text-layer
extraction, tier routing and escalation, cross-source conflict reporting,
bulk-upload partial acceptance, CSV export escaping and null handling, and
the Tally integration: ledger matching, voucher balance, and the ledger-master
parser's refusal of a hostile XML upload.

58 client tests cover the Python SDK — response parsing, decimal exactness,
the error hierarchy, the asymmetric retry policy, batch and directory upload,
atomic CSV download, and the Tally namespace — driving the real client through
`httpx.MockTransport`, so what is under test is the request the library
actually builds.

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

- **A WhatsApp send that anyone has watched arrive.** The Cloud API adapter is
  written and tested against a scripted transport; no WhatsApp Business account
  has been connected to it from here. A production deployment left on the mock
  is refused at startup rather than allowed to report messages as sent that
  nobody received.
- **A call anyone has listened to.** The Exotel adapter is written and tested
  against a scripted transport; no Exotel account has been connected from
  here, and the flow that does the talking is something you build on their
  side.
- **Billing** — usage tracking is billing-ready; no payment provider is
  integrated.
- **A hosted endpoint.** `docker compose up` runs the whole stack on one
  machine; nothing is deployed anywhere for customers to call.
- **An async Python client**, webhook helpers in the SDK, and a JavaScript SDK.
- **Tally verified against a real installation** — the voucher file is
  well-formed and every voucher balances, but nobody has watched one import
  into Tally itself. Import into a test company before trusting it.
- **Other accounting software** — Busy, Marg, Zoho Books and Vyapar are not
  supported. The CSV export is the fallback.
- **Evaluation harness** — the synthetic corpus generator exists in
  `tests/fixtures/`; field-level accuracy scoring does not.

No accuracy figure is published anywhere in this repository, because none has
been measured. Nor is anything here a statement about whether a filing made
with it is compliant: that is the firm's professional judgement, and no part
of this system has been reviewed by anyone qualified to say otherwise.

## License

Proprietary. All rights reserved.
