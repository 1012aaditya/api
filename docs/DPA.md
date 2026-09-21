# Data Processing Addendum — DRAFT

> **A draft for a lawyer to review, not an agreement to sign or send.**
> It describes what the software actually does, verified against the code.
> It is not legal advice, it has not been reviewed by anyone qualified to
> give any, and it makes no claim of compliance with the Digital Personal
> Data Protection Act 2023 or any other law. Your counsel decides what this
> needs to say.
>
> Placeholders in `[BRACKETS]` are yours.

Between **[YOUR COMPANY NAME]** ("Processor") and **[CUSTOMER FIRM]**
("Fiduciary"). Effective **[DATE]**.

---

## 1. Roles

The Fiduciary is a chartered accountancy firm acting for its own clients.
The personal data processed under this Addendum belongs to those clients —
the Data Principals — and the Fiduciary determines why it is processed. The
Processor processes it **only on the Fiduciary's documented instructions**,
which for ordinary operation means: as directed through the product.

## 2. What is processed

| | |
|---|---|
| **Subject matter** | Chasing, receiving and reading the documents a CA firm needs to complete its clients' statutory filings. |
| **Duration** | For as long as the Fiduciary's account is open, subject to §5. |
| **Nature** | Storing, reading, classifying, validating and messaging about business documents. |
| **Purpose** | Solely to provide the service. |
| **Principals** | The Fiduciary's clients, and the individuals named in their documents. |
| **Categories** | Names, business names, GSTIN, PAN, phone numbers, email addresses, and the contents of invoices, bank statements and GST returns. |

**No special-category data is sought.** Documents are supplied by the
Fiduciary's clients, so the Processor cannot guarantee none is present; it
is not requested, not extracted, and not processed for any purpose.

## 3. The Processor's obligations

1. **Instruction only.** Process only on the Fiduciary's instructions. No
   other use — in particular, **no training of any model on the Fiduciary's
   data, at any time, for any purpose.**
2. **Confidentiality.** Access limited to personnel who need it, under
   confidentiality obligations.
3. **Security.** The measures in Schedule A.
4. **Sub-processors.** Only those in Schedule B, and the Fiduciary is given
   **[30]** days' notice before another is added.
5. **Assistance.** Help the Fiduciary respond to a Principal exercising
   their rights, within **[N]** business days.
6. **Breach.** Notify the Fiduciary **without undue delay and in any event
   within [24/72] hours** of becoming aware, with what is known at the time
   rather than waiting for a complete picture.
7. **Deletion.** As set out in §5.
8. **Audit.** Make available the information needed to demonstrate
   compliance with this Addendum.

## 4. The Fiduciary's obligations

The Fiduciary warrants that it has the lawful basis to provide the data it
uploads or instructs the Processor to collect, and that its own notices to
its clients cover processing by a processor.

## 5. Deletion and return

**During the term.** The Fiduciary can erase any client, at any time,
without asking the Processor. The erasure removes the client record, their
cases and requirements, their documents and the stored files, every value
extracted from those documents, every message and call, and the tasks,
exceptions and agent decisions concerning them. It is immediate and
irreversible.

**Retained by design.** Two things survive an erasure, and only two:

1. The count of documents processed, with the link to any client removed,
   retained as an accounting record of work the Processor was paid for. It
   identifies nobody.
2. A single audit line recording that the erasure happened — the client's
   name, the time, who asked, and how many records were removed. No GSTIN,
   no contact details, no document and nothing extracted from one. It is the
   Fiduciary's evidence that the request was honoured, and it points at no
   client record.

**[Confirm with counsel that retaining the name in (2) is appropriate for
your purposes, and for how long.]**

**Document files** are deleted automatically **[N]** days after receipt,
independently of any erasure request.

**On termination.** Within **[30]** days of the account closing, the
Processor deletes all personal data, save the accounting record above and
anything it is required by law to keep. A written confirmation is provided
on request.

## 6. Location

Processing takes place in **[LOCATION]**. **[If any sub-processor in
Schedule B is outside India, say so here and take your counsel's view on
what that requires.]**

## 7. Liability

**[Your counsel's wording. Do not draft this from a template.]**

---

## Schedule A — Security measures

What the software does, as built:

**Access**
- Every request is scoped to one firm, enforced in the API rather than by
  hiding controls in the interface.
- Three roles. A staff login can do the daily work but cannot invite
  colleagues, change the agent's limits, mint API keys, manage webhook
  endpoints, or erase a client.
- Disabling a login takes effect on that person's next request, not when
  their session happens to expire.
- Passwords are hashed and never stored in plaintext. API keys and
  invitation links are stored as a SHA-256 of a value shown once; a lost one
  is re-issued, never recovered.
- Invitations are single-use and expire after **[7]** days.

**Data**
- TLS in transit.
- Document files deleted automatically after the retention window.
- Document contents are never written to application logs; credentials,
  tokens and raw provider responses are redacted before a log line is
  written.
- Outbound webhooks are signed; the receiver can verify the sender.
- Customer-supplied webhook URLs are checked against private and loopback
  address ranges before any request is made.

**Operational**
- Migrations are version-controlled and applied automatically on deploy.
- The deployment refuses to start in production without a rate-limit
  backend, with a wildcard CORS origin, or with a messaging provider that
  reports success without sending anything.
- Automated nightly database backups, retained **[14]** days. **[State
  where they are copied to — a backup on the same disk is not a backup.]**

**[Whatever else is true of your hosting: disk encryption at rest, firewall
policy, SSH key-only access, who has production access. Do not list a
control you have not implemented.]**

---

## Schedule B — Sub-processors

**This list depends on the deployment's configuration. The Fiduciary's
Privacy page in the product shows the live list for theirs; this Schedule
must match it.**

| Sub-processor | Purpose | What it receives | Location |
|---|---|---|---|
| **[Hosting provider]** | Servers and storage | All of it | **[LOCATION]** |
| **Meta Platforms** | WhatsApp Business Cloud API | Clients' phone numbers; every message sent and received; every document they send | **[Per Meta's terms]** |
| **Exotel** | Outbound voice *(only if enabled — off by default)* | Clients' phone numbers; that a call was placed and its duration | India |

**Reading documents.** If the model runs on the Processor's own hardware —
the default configuration — **it is not a sub-processor and no document
leaves the network.** If a hosted model API is used instead, it must be
listed here, and it receives the page image of every document that reaches
the model tier.

**Tally.** The product produces a file the Fiduciary imports themselves.
Tally is not a sub-processor and receives nothing from the Processor.
