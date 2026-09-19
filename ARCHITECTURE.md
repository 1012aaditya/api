# CA Operations AI — architecture and plan

An audit of what this repository already is, what the new product needs, and
how the two meet. Written before any feature code, per §46 of the brief.

## 1. What this repository currently is

A production-grade **document intelligence API** for Indian GST invoices:
FastAPI + SQLAlchemy 2.0 async + Alembic, PostgreSQL (verified) or SQLite
(tests), a Next.js 15 dashboard, and a Postgres-backed job queue with its own
worker process.

385 backend tests and 58 client tests pass on **both** SQLite and PostgreSQL 16.

## 2. Reusable infrastructure — this is most of the plumbing

| Need (brief §) | Already exists | State |
|---|---|---|
| Auth, sessions (§A) | `api/v1/auth.py`, JWT, bcrypt | **Reuse.** Password reset missing. |
| Multi-tenancy (§18) | `Organization`; every repository method is org-scoped; tenant-isolation tests | **Reuse as-is.** |
| Roles (§B) | `User.role` column, defaults `owner` | **Extend** — no enforcement yet. |
| Document upload (§F) | `services/file_validation.py` — MIME sniffing, page and size caps | **Reuse.** Extensions are already not trusted. |
| Storage (§32) | `ObjectStore` protocol + Local and S3 adapters + signed URLs | **Reuse.** This *is* `StorageProvider`. |
| Document pipeline (§F) | `pipelines/` — tiered qr → text_layer → model | **Reuse.** |
| Extraction (§H) | Invoice fields incl. GSTIN, tax split, totals | **Reuse.** Bank statements are new. |
| Validation (§I) | `validators/` — GSTIN checksum, arithmetic, supply type, required fields | **Reuse directly.** Deterministic already. |
| Background jobs (§22) | `extraction_jobs` + `FOR UPDATE SKIP LOCKED`, retries, backoff, stale release | **Reuse pattern.** See §5 below. |
| Outbound webhooks (§23) | Signed, retried, SSRF-guarded | **Reuse.** Inbound is new. |
| Provider abstraction (§32) | `providers/base.py` + `registry.py` | **Copy the pattern** for WhatsApp and Voice. |
| AI provider (§19) | `openai_compatible` — works against Ollama/vLLM/LM Studio | **Reuse.** Local-first is already possible. |
| Usage counters (§25) | `usage_events`, billable flags, cost tracking | **Extend** with new event types. |
| Observability (§24) | structlog, request ids, secret redaction with an AST test | **Reuse.** |
| Dashboard (§26) | Next.js 15, light theme, 10 pages | **Extend.** |
| Deployment | Dockerfiles + compose, Postgres | **Reuse.** |

## 3. Existing database schema

`organizations`, `users`, `api_keys`, `documents`, `extractions`,
`validation_results`, `extraction_jobs`, `webhooks`, `webhook_deliveries`,
`usage_events`, `document_batches`, `ledgers`, `ledger_aliases`,
`tally_settings`. Five migrations, verified up and down on PostgreSQL.

## 4. What is missing for this product

Nothing in the list below exists yet:

- `Client` — the CA firm's customer. **The central new entity.**
- `ComplianceCase` — a work period (GST / Sept 2026).
- `DocumentRequirement` — what that case needs, with its own state machine.
- `Exception` — the human review queue.
- `Task` — work assigned to staff.
- `Conversation` / `Message` — WhatsApp threads.
- `AgentEvent` — the audit timeline (§15).
- `AgentPolicy` — per-firm limits (§17).
- `ClientFact` — structured agent memory (§10).
- WhatsApp and Voice provider abstractions.
- Document **classifier** (existing code assumes "this is an invoice").
- Follow-up scheduler and inbound webhooks.
- Agent tool layer with per-tool authorization (§20).

## 5. Design decisions, and why

**Jobs: reuse the pattern, not the table.** `extraction_jobs.document_id` is
`NOT NULL` — correct for extraction, wrong for "call this client on Thursday".
Widening it would weaken a well-constrained table that 385 tests depend on. A
separate `agent_jobs` table takes the same claim mechanics (`SKIP LOCKED`,
attempts, backoff, `available_at`) and the same worker loop gains a second
drain. Two tables, one pattern, one worker.

**Documents get a nullable `client_id` and `case_id`.** The existing API
uploads documents with no client; the new flow attaches one. Nullable columns
keep every current test and endpoint working.

**Classification is a separate stage from extraction.** Today the pipeline
assumes an invoice. The classifier proposes a type with a confidence; business
rules decide. Below threshold → `NEEDS_REVIEW`, never a silent guess (§G).

**The agent never writes state directly.** It calls tools; tools call
repositories; repositories are org-scoped. `organization_id` is derived from
the authenticated context and is **not a tool argument the model can set**
(§20).

**Truth comes from the database, never the model** (§28). The existing codebase
already refuses to invent invoice fields; the same rule now covers "was this
document received", "did the call happen", "did validation pass".

## 6. Conflicts and risks

| Risk | Handling |
|---|---|
| Widening `extraction_jobs` breaks 385 tests | Separate `agent_jobs` table instead. |
| Reusing `Document` for non-invoices | Nullable `client_id`/`case_id`; classifier decides type. |
| Agent acting on stale state | Tools read live; state machines reject illegal transitions. |
| Runaway agent messaging clients | `AgentPolicy` caps per day; opt-out is checked before every send. |
| WhatsApp webhook replay | Idempotency on `provider_message_id`, as webhook delivery already does. |
| PII to an AI provider | Org policy can force local-only; prompts receive named fields, never whole documents. |
| Demo needs no credentials | Mock WhatsApp, Voice and AI providers selected by config (§31). |

## 6b. Known defect in the PostgreSQL test harness — three theories disproven

Two worker tests fail roughly one run in five **on PostgreSQL only**, and only
when other test files run first. They pass in isolation and always pass on
SQLite. `process_available_jobs` returns fewer jobs than were queued.

This is in the test harness, not the product. It cannot predate the PostgreSQL
test path itself (commit `70d1798`), because before that there was no way to
run the suite against PostgreSQL at all.

**The strongest evidence, and the best lead:**

In a captured failure, only **two** `extraction_jobs` rows existed where three
had been submitted, *and* one of those two failed with
`storage_error: The stored document is no longer available` — meaning its
bytes were absent from the in-memory store.

A vanished row and vanished bytes together are explained by one thing: the
*next* test's fixtures running while the current test is still working.
`_clean_database` deletes every row; `_reset_singletons` installs a fresh
empty `InMemoryObjectStore`. Both would produce exactly this.

That points at an un-awaited coroutine or a stray background task somewhere in
the test path, letting pytest advance while work is still in flight. **That is
where the next person should look**, rather than at the pieces below.

**Disproven, each by experiment:**

1. *A real `LocalObjectStore` is built in the gap when the singleton is None.*
   Instrumented `get_object_store()` to log every real construction and
   reproduced the failure: **no real store was ever built.**
2. *Resetting the store to None at teardown opens the window.* Left the store
   installed instead; the flake survived eight runs unchanged.
3. *Per-test `dispose_engine()` in teardown races with in-flight work.* Moved
   disposal to setup, where nothing can be in flight by construction; still
   failed 2 of 10. (Removing disposal entirely is not an option — the
   "attached to a different loop" error returns immediately.)

All three changes were reverted rather than kept on a disproven rationale.

**Status:** unfixed. The PostgreSQL CI job must not gate merges until it is.
SQLite runs are unaffected and green. No production code path is implicated by
any evidence gathered.

## 7. Plan against the brief's day numbering

| Day | Work | Status |
|---|---|---|
| 1 | Repository audit, this document | **done** |
| 2 | `Client`, `ComplianceCase`, roles | **done** |
| 3 | `DocumentRequirement` + state machines | **done** |
| 4 | Ingestion: classify → extract → validate, attached to a case | **done** |
| 5 | Exception engine | **done** |
| 6 | WhatsApp abstraction + mock provider + inbound webhook | **done** |
| 7 | Client communication agent + tools | **done** |
| 8 | Follow-up engine (`agent_jobs`) | **done** |
| 9 | Voice abstraction + mock workflow | **done** |
| 10 | CA command centre | **done** |
| 11 | End-to-end tests + demo seed | **done** |
| 12 | Hardening | |

P2 items in the brief (Tally, GST integration) are **already built** and stay
where they are.

## 8. Definition of done

§45 of the brief: the 24-step flow, with every existing test still passing and
demo mode working without any external credentials.

`backend/tests/test_end_to_end.py` walks it in one test: a CA adds a client
and opens a case over HTTP, the system derives what the filing needs, the
sweep queues a chase, the worker sends it, the client answers in Hinglish,
sends a bank statement over WhatsApp, and sends one invoice that belongs to
somebody else — which stops and waits for a person rather than being filed
under a guess. The firm resolves it, the last document lands, and the case
becomes ready. No external credentials anywhere: the WhatsApp provider is
the mock, the store is in memory, and no AI provider is configured.

`scripts/seed_demo.py` (with `--reset`) is the same product with data in it,
for showing rather than testing.

### After the twelve days

* **The WhatsApp Cloud API adapter** (`app/providers/messaging/whatsapp_cloud.py`)
  — the only thing standing between this and a real firm's clients. Written
  against Meta's documented shapes and tested against a scripted transport.
  Never run against Meta from here, which is stated in the README rather than
  glossed over.
* **A rung that did not happen goes back on the queue.** A send refused for
  quiet hours or the daily cap, or a provider that could not be reached, used
  to consume its job: the ladder stopped silently and nobody chased that
  client again. It is now requeued for when the obstacle passes — after the
  quiet window, tomorrow, or in fifteen minutes — and a rung that runs out of
  attempts raises an exception naming the client nobody is chasing.
* **Both list pages page**, because the brief's own 50–500 clients would
  otherwise have silently shown the first two hundred.

### The planner (after the twelve days)

`app/services/planner.py` is the only component allowed to decide anything
with a model. Its shape is the point:

* one case in, no identifiers, one action out of five;
* the answer is validated before it is acted on, and a rejected plan is
  recorded with its reason rather than hidden;
* execution goes through the same services as the deterministic path, so the
  policy gate, the caps and the state machines apply unchanged;
* anything going wrong — no model, a timeout, prose instead of JSON — falls
  back to the ladder that ran before it existed.

`app/services/ai_policy.py` is what makes "keep documents on your own
hardware" true. A model endpoint is local or it is not; a hostname that
cannot be shown to be local is treated as remote. Enforced in two places that
matter: before a document is sent for extraction, and before a case is sent
to the planner.

### Not done, and worth saying plainly

* No AI provider key exists in this environment, so extraction has only ever
  run against a stub. No accuracy figure has been measured and none is
  claimed (§30, §33).
* `docker compose up` has never been executed here — the registry is blocked
  from this container. The files are written and reviewed, not run.
* No WhatsApp message from this code has been watched arriving on a phone,
  and no Exotel call has been listened to. Both adapters are written and
  unit-tested; a scripted transport is not a telco.
* No model has run against the planner outside the tests. The prompts are
  written and the guard rails are tested against scripted answers; how a
  real 7B model behaves on a real case is unmeasured.
* The PostgreSQL test-harness flake in §6b is still unexplained. It did not
  reproduce in three consecutive full runs against PostgreSQL at the end of
  this work, which is evidence of nothing except that it is intermittent.
