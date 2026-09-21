# Using this as a CA would: what happened

A working review, written after running a month of practice work through the
product on 21 September 2026. Not a design critique — a record of what the
software did when somebody tried to use it for the job it claims.

**How it was done.** Signed up cold at `/signup` as a new firm. Typed in
three clients by hand, imported five more from a Tally-shaped CSV, opened
this month's GST filings one card at a time, opened an income-tax case for a
professional client, asked the agent what it would do, pressed *Chase what
needs chasing*, wrote a client a message, marked two documents as arrived,
marked a return filed, then imported 292 more clients to see the board at the
size the product is sold for. The demo data was erased first, through the
product's own erasure, so nothing here is seeded.

Everything below was observed. Where a number appears it was measured.

---

## What genuinely helps

These are not consolation prizes; they are the reasons the thing is worth
finishing.

- **The board tells the truth about where everyone stands.** Seven clients in
  *Waiting on the client*, one overdue income-tax case showing **52 d overdue**
  in red, and a client with no filing sitting in *Nothing open*. No clicking
  between pages to assemble that picture.
- **Positions cannot lie.** Zones are computed from the rows on every read, so
  a card is never left in the wrong column after you fix something. Marking two
  documents arrived moved the card's line from *Not asked yet* to *Waiting on
  3 documents* without a refresh.
- **The import is honest before it writes.** 292 rows previewed in 0.1s with
  "*292 would be added, 0 look like duplicates. Nothing has been written.*"
  It recognised a Tally export's `Ledger Name`/`GSTIN/UIN`/`Mobile` columns and
  skipped a client already on the books.
- **The refusals are real refusals.** On the trial plan the same import was
  declined with "*The Trial plan covers 25 clients and you have 8*" rather than
  quietly importing 25 of them.
- **Quiet hours are respected.** The worker deferred ten queued reminders with
  "*It is quiet hours (21:00–9:00)*" and re-scheduled them for 08:59. A product
  that messages a trader's personal WhatsApp at 11pm loses the firm a client.
- **Erasure works and gives a receipt.** Ten demo clients erased, 8–16 rows
  each, with an audit line kept naming the client and the counts.
- **It is fast where speed matters.** `/v1/board` returns the whole practice in
  **20ms / 99KB at 300 clients**. The API is not the problem anywhere below.

---

## What is wrong, worst first

### 1. Pressing *Chase what needs chasing* does not chase anybody, and nothing says so

Clicking it on seven blocked filings produced: **"7 would be chased. Nothing
was sent."** — the same sentence the dry run gives.

What actually happened: seven `send_followup` jobs were queued with
`available_at` set to **the next day**, because the first reminder is 24 hours
out by default. They are sent later by a worker process. In this environment no
worker was running, so they would never have gone at all.

A CA reads that banner on the 18th, believes the chasing is in hand, and finds
out on the 20th that nobody was asked. Three separate gaps:

- the banner does not distinguish *queued* from *sent*, and uses the dry run's
  conditional tense for a real run;
- nothing anywhere shows the queue — you cannot see that seven reminders are
  waiting, when they go, or cancel one;
- nothing warns that the worker is not running. The board cheerfully reports
  *The agent · 0 of 200 sent today*, which is also what a quiet day looks like.

### 2. Messages are recorded as sent when nothing leaves the building

`WHATSAPP_PROVIDER` defaults to `mock`, which stores the message and sends
nothing. Typing a message to a client from their card showed it in the thread as
**"you · Just now"**, and the agent's log recorded *"WhatsApp sent to Ganesh
Traders"*. Identical in every respect to a message that reached the client.

The only place the truth appears is *What is held, and where* → "WhatsApp goes
through **mock**". Nobody outside this repository knows what that means.

This is the product's own rule broken (§42, §28): the interface claims an
action that did not happen. Until a real number is connected, every mock
message needs saying so where it is shown — in the thread, on the card, and on
the agent's card.

### 3. The firm's name cannot be changed, and it is in every client message

Signup put "S. Iyer" in as the organisation name. That string is what the
chasing message says the request comes from. There is no way to correct it: the
settings page displays the name read-only, and no endpoint updates it. Fixing it
took a SQL `UPDATE` against the database.

A typo at signup is therefore permanent and visible to every client.

### 4. Opening the month is typing, one client at a time

Opening a GST filing took **3.5 seconds and two clicks per client**, with the
period (`2026-09`) and the due date (`2026-10-20`) typed by hand each time. At
300 clients that is about **18 minutes of typing on the first of every month**,
and every date is an opportunity to type 2026-10-02.

Nothing derives the deadline from the filing: GSTR-3B for September is due on
the 20th of October by statute, and the software knows both the type and the
period. There is no "open this month for everybody", no copy-forward from last
month, and no recurring schedule per client.

### 5. The GST model is too thin for an Indian practice

One filing type called *GST*, with one period and one deadline. Real monthly
work is at least **GSTR-1 (11th)** and **GSTR-3B (20th)**, on the same client,
in the same month, with different documents and different consequences. Also
absent: **QRMP** quarterly filers, **CMP-08** for composition dealers (one of
my eight clients is a caterer — I could not represent her at all), **GSTR-9/9C**
annually, and **26Q/24Q** TDS quarters as anything other than a free-text
period.

The default document list — bank statement, GSTR-2B, purchase invoices, sales
invoices, credit notes — is a reasonable skeleton but misses what firms actually
chase: the **sales and purchase registers** (not individual invoices), **RCM
items**, **export invoices with LUT**, **e-way bill data**, and a second or third
bank account.

### 6. Three places disagree about what a client owes

For one client, at one moment:

- the confirmation said "*opened, asking for **4** documents*" (required only);
- the card said "*Waiting on **3** documents*" (outstanding, including an
  optional one);
- the chase message listed **4** ("bank statement, gstr-2b, purchase invoices
  and sales invoices"), omitting the optional credit notes.

None of them is lying, but a CA cannot hold three definitions of "owed" in their
head, and the number on the card is the one they will quote to the client.

### 7. At 300 clients the board is a very long strip

Measured with 300 clients: the drawn board is **1,452 × 30,514 px**, the lowest
card sits **30,423 px** down, and the page takes **3.2s** to redraw because
every card is in the DOM. 293 of those cards are *Nothing open* — the column
that matters least is by far the biggest thing in the interface.

It needs collapsing ("293 clients with nothing open ▸"), or paging, or both.
The API is fine; this is purely the drawing.

### 8. Search finds names only

Searching a client's own code `C-001` → **0 matching**. Their GSTIN
`29AAAPG7896R1ZT` → **0 matching**. Firms file by code and reconcile by GSTIN;
both must find the card.

### 9. Marking a return filed records nothing about the filing

*Mark it filed* sets a status. It does not ask for the **ARN**, the filing date,
the tax paid or the challan — so the one artefact a CA needs when a client or a
notice asks "when did you file this?" is not held. The firm still keeps that in
a spreadsheet, which means the spreadsheet is still the system of record.

### 10. Documents are deleted after seven days

*What is held, and where* reports **"Documents kept for 7 days"**. For a
practice this is the wrong default by three orders of magnitude: a bank
statement a client sends on the 5th is gone before the next quarter, and GST
records are expected to be available for years. The retention period is a
setting, but the default is what firms will run.

### 11. No client belongs to anybody in the firm

There is no field for who handles a client, so the board cannot answer "what is
on my plate" for a staff member, and a practice of five cannot divide 300
clients between them inside the product.

### 12. Smaller things, each cheap to fix

- The dry run lists what it *would* do under a heading that says **"Skipped:"**.
- A brand-new firm lands on an empty board with no suggestion to start by
  adding clients — five empty columns and ten cards of zeros.
- *Plan and usage* prints the heading **"What it comes to"** above nothing when
  the bill is zero.
- The plan refusal says "*move up a plan*", and there is nowhere in the product
  to do that.
- The agent's card counts a message a human typed under **"Sent today"**.
- The client's card shows no history — what was asked, when, and what came back
  is only in the firm-wide agent log.

---

## What could not be tested, and is therefore unproven

Stated plainly, because the sections above would otherwise read as if the rest
works:

- **No message has ever reached a real phone.** No WhatsApp number is connected
  to this deployment; everything sent went to the mock provider.
- **No document has ever been read.** No model is configured — extraction
  returns "*No AI provider is configured*", which is the correct refusal, but it
  means the whole document-reading half of the product is untested here.
- **No accuracy figure exists.** Nothing in this review says anything about how
  well invoices are extracted, because nothing was extracted.
- **Tally posting was not exercised** against a real Tally company.
- **No voice call has been made.**

---

## Does it help?

Half of it does, today.

The half that watches — who is blocked, on what, for how long, and what changed
— is better than the spreadsheet-and-memory method it replaces, and it is fast
enough at 300 clients. If the product stopped there and were honest about being
a tracker, a firm could use it on Monday.

The half that *acts* — the chasing that the whole promise rests on — cannot be
trusted yet, not because the queue is broken (it works, and it respects quiet
hours) but because the interface reports queued work as done and mock sends as
real. That is one bad month away from a CA telling a client "we asked you twice"
when the client was never asked.

**The order I would fix them in:**

1. Say what actually happened when you press chase — queued, when it goes, and
   whether a worker and a provider exist at all (#1, #2). Nothing else matters
   until the software stops overstating itself.
2. Let the firm change its own name (#3).
3. Open the month in one action, with statutory deadlines derived (#4).
4. GSTR-1 and GSTR-3B as separate filings, with QRMP and composition (#5).
5. One definition of "what they owe" (#6).
6. Collapse *Nothing open*, and search by code and GSTIN (#7, #8).
7. Capture the ARN when a return is marked filed (#9).
8. A sane retention default, and a client owner (#10, #11).
