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

## 6b. Known pre-existing defect (not introduced by this work)

`test_the_worker_claims_each_job_once` and
`test_batch_progress_moves_as_the_worker_runs` fail intermittently — roughly
one run in five — **on PostgreSQL only**, and only in a multi-test run. Both
pass in isolation.

What is established:

* The worker fails one job with `StorageError: The stored document is no
  longer available.`, which sends it to a 30-second retry, so the loop returns
  fewer jobs than the test expects.
* At the moment of failure the document's `storage_key` **is** present in the
  test's `InMemoryObjectStore`, both before and after the run, and
  `get_object_store() is object_store` is True when checked from the test body.
* `InMemoryObjectStore.get` raises `KeyError`, never `StorageError`. That
  message exists only in `LocalObjectStore` and `S3ObjectStore`. So the worker
  resolved a **different** object store than the test installed — the
  `set_object_store` singleton was reset underneath it.
* **It reproduces on a clean checkout of `HEAD`** (verified by stashing this
  branch's changes and rerunning: 1 failure in 5 runs), so the CA-operations
  work did not cause it.

Most likely cause: the autouse `_reset_singletons` fixture's teardown
(`set_object_store(None)`) interleaving with the next test under asyncio, so
the worker falls back to building a real store from settings. The fix is
probably to make the object store request-scoped rather than a module global,
or to have the worker capture the store once per run. Left as a known issue
rather than fixed blind, because it is unrelated to this feature and the
product work is time-boxed.

**It will make CI red on the PostgreSQL job intermittently.** It should be
fixed before that job is treated as a gate.

## 7. Plan against the brief's day numbering

| Day | Work | Status |
|---|---|---|
| 1 | Repository audit, this document | **done** |
| 2 | `Client`, `ComplianceCase`, roles | |
| 3 | `DocumentRequirement` + state machines | |
| 4 | Ingestion: classify → extract → validate, attached to a case | |
| 5 | Exception engine | |
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
