# Privacy policy — DRAFT

> **This is a draft for a lawyer to review, not a policy you can publish.**
> It is written to describe what the software actually does, verified
> against the code, which is the part a template gets wrong. It is *not*
> legal advice and it has not been reviewed by anyone qualified to give
> any. Nothing here claims compliance with the Digital Personal Data
> Protection Act 2023 or any other law — that is a determination only your
> counsel can make.
>
> Placeholders in `[BRACKETS]` are yours to fill in.

**[YOUR COMPANY NAME]** · Last updated **[DATE]**

---

## 1. Who is who

You are a chartered accountancy firm. Your clients' invoices, bank
statements and GST returns describe *their* businesses, not yours. Under the
DPDP Act's vocabulary, for the personal data inside those documents:

- **Your client** is the Data Principal — the person the records describe.
- **Your firm** is the Data Fiduciary — you decide why the records are held.
- **We** are a Data Processor — we hold and process them only on your
  instruction, and for nothing else.

We do not decide what to do with your clients' data. We do not use it to
train models, we do not sell it, and we do not look at it except when you
ask us to help with a specific problem and grant access for that purpose.

## 2. What we hold

**About your firm:** the email address and name of each person you give a
login to, a hash of their password (never the password), when they last
signed in, and your firm's name and settings.

**About your clients, because you told us:** name, business name, GSTIN,
PAN, phone number, email, and any note you wrote.

**Documents your clients send:** the file itself, and the values read out of
it — invoice number, date, supplier and buyer names and GSTINs, amounts and
tax figures.

**The conversation:** every WhatsApp message sent and received, every call
placed, and every decision the agent made, including the ones where it
decided not to act.

**Usage:** counts of documents processed, for billing.

## 3. What we do not hold

We never store the password itself — only a hash from which it cannot be
recovered. API keys and invitation links are stored the same way: as a
SHA-256 of a value we show you once and then cannot retrieve.

**Document contents are never written to our logs.** Credentials, tokens and
raw provider responses are redacted before a log line is written.

## 4. How long

Your clients' **document files** are deleted **[N] days** after they arrive.
That window is configured per deployment and shown on your Privacy page.
After it passes, the file is removed from storage and cannot be recovered.

**The values read out of the document are kept after the file is gone.** We
say this plainly because it is the part most policies leave out: deleting
the PDF does not delete the invoice number, amounts and GSTINs taken from
it. Those remain as the record of work done for you, until you erase the
client.

**Everything else** — clients, cases, messages, tasks — is kept for as long
as your account is open, because it is the record of your practice.

## 5. Getting data removed

Any owner or administrator of your firm can erase a client and everything
held about them, from the client's page. This removes:

- the client record
- every case, and what each one was waiting for
- every document, including the stored file
- every value extracted from those documents
- every WhatsApp message and call
- the tasks, exceptions and agent decisions about them

It is immediate and irreversible. There is no soft delete and no undo.

**One thing survives, deliberately:** the count of documents processed, with
the link to the client removed. Those counts are the basis of what you were
billed, and we keep them as an accounting record. They no longer point at
any person.

The page shows a receipt afterwards saying exactly how many rows were
deleted from which tables, and whether every stored file was successfully
removed. If one was not, it says so rather than reporting success.

**We keep one record of the erasure itself:** a line in your firm's audit
log saying that a client of that name was erased, when, by whom, and how
many records went. It holds the name and the counts — no GSTIN, no phone
number, no document and nothing read out of one — and it no longer points at
any client record. It exists so you can show that you honoured the request.
We mention it because a policy that said "everything is gone" while this
line remained would be inaccurate.

## 6. Who else sees it

**This depends on how your deployment is configured, and your Privacy page
shows the live answer for yours.** In general:

- **Meta (WhatsApp Business Cloud API)** — if you have connected a WhatsApp
  number, Meta carries every message to and from your clients, and every
  document they send back. That is what WhatsApp is.
- **Exotel** — if you have enabled voice calls, Exotel places them and
  therefore knows your clients' phone numbers and that a call happened.
- **The model that reads documents** — if you run it on your own hardware,
  no document leaves your network. If you use a hosted API, every document
  that reaches the model tier is sent to it.
- **Object storage** — if you store documents in S3 or similar rather than
  on the server's own disk.

Your Privacy page names each one that applies to you, and what it receives.
If it names none, nothing leaves your deployment.

## 7. Where it lives

**[Your server's location — e.g. "Mumbai, India (ap-south-1)".]**

## 8. Security

Passwords are hashed. API keys and invitation links are stored hashed and
shown once. Every request is scoped to one firm, enforced in the API rather
than in the interface. Staff logins cannot reach the firm's controls —
inviting colleagues, the agent's limits, API keys or webhook endpoints.
Switching off a colleague's login takes effect on their next request.

Traffic is served over TLS. Webhooks we send are signed so the receiver can
verify they came from us.

## 9. What you should ask us

If you are evaluating this, ask for: the Data Processing Addendum, the
current retention window, where the server is, and a screenshot of the
Privacy page from your own deployment — that page is generated from the
running configuration and is harder to be wrong than this document is.

## 10. Contact

**[NAME]**, **[EMAIL]**, **[POSTAL ADDRESS]**

Under the DPDP Act you may be required to name a Data Protection Officer or
a grievance contact. **[Ask your counsel which applies to you.]**
