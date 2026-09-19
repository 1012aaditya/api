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

## 6b. Known defect in the PostgreSQL test path — cause not yet found

`test_the_worker_claims_each_job_once` fails roughly one run in five **on
PostgreSQL only**, and only when other test files run first. It passes in
isolation and always passes on SQLite. `process_available_jobs` returns fewer
jobs than were queued.

**What is established:**

* It reproduces on a clean checkout of the commit before the CA-operations
  work, so no model or service added for this product causes it.
* It cannot predate the PostgreSQL test harness itself (commit `70d1798`),
  because before that there was no way to run the suite against PostgreSQL.
  So it is most likely something in that harness — the per-test
  `dispose_engine()`, or fixture teardown interleaving under pytest-asyncio's
  per-test event loops.
* In one captured failure the worker raised
  `StorageError: The stored document is no longer available` while the
  document's key was demonstrably present in the test's `InMemoryObjectStore`,
  and `get_object_store() is object_store` was True from the test body.

**What was tried and did not work:** leaving the object store installed at
teardown instead of resetting it to `None`, on the theory that a real
`LocalObjectStore` was being built in the gap. The flake survived unchanged
over eight runs, so that explanation is wrong and the change was reverted
rather than kept on a disproven rationale.

**Status:** root cause unknown. It will make the PostgreSQL CI job
intermittently red, and that job should not be treated as a gate until this is
fixed. It does not affect SQLite runs, and no production code path is
implicated by any evidence gathered so far.

## 7. Plan against the brief's day numbering

| Day | Work | Status |
|---|---|---|
| 1 | Repository audit, this document | **done** |
| 2 | `Client`, `ComplianceCase`, roles | **done** |
| 3 | `DocumentRequirement` + state machines | **done** |
| 4 | Ingestion: classify → extract → validate, attached to a case | **done** |
| 5 | Exception engine | **done** |
| 6 | WhatsApp abstraction + mock provider + inbound webhook | |
| 7 | Client communication agent + tools | |
| 8 | Follow-up engine (`agent_jobs`) | |
| 9 | Voice abstraction + mock workflow | |
| 10 | CA command centre | |
| 11 | End-to-end tests + demo seed | |
| 12 | Hardening | |

P2 items in the brief (Tally, GST integration) are **already built** and stay
where they are.

## 8. Definition of done

§45 of the brief: the 24-step flow, with every existing test still passing and
demo mode working without any external credentials.
