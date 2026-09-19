"use client";

import Link from "next/link";

import { Card } from "@/components/ui";

/**
 * The public front page.
 *
 * Everything claimed here is something the repository can be pointed at. No
 * accuracy number, no customer logos, no "99.9%" — a CA reading this can
 * check every sentence against the product (§30, §33).
 */

const LADDER = [
  {
    when: "Day 1",
    what: "The case is opened",
    detail:
      "GST for September needs sales invoices, purchase invoices, the bank statement and GSTR-2B. The system knows, so nobody has to remember.",
  },
  {
    when: "Day 2",
    what: "The client is asked",
    detail:
      "One WhatsApp message naming exactly what is outstanding — not a form letter, and not a list of everything.",
  },
  {
    when: "Day 3",
    what: "They reply",
    detail:
      "“kal bhej dunga” is read as a promise for tomorrow, and the next reminder moves. Nothing is marked as received, because nothing arrived.",
  },
  {
    when: "Day 4",
    what: "A file arrives",
    detail:
      "It is identified from what it says, checked against the client's GSTIN and the period, and the requirement closes itself.",
  },
  {
    when: "Day 6",
    what: "Or it stops",
    detail:
      "After the reminders you allow, the agent stops and tells you who to call. It does not send a fourth message, and it never decides what it is unsure of.",
  },
];

const REFUSALS = [
  [
    "It never says a document arrived when it did not.",
    "A client saying they sent it is recorded as a claim, and the requirement stays open.",
  ],
  [
    "It never files a document it is unsure about.",
    "Below your confidence threshold it goes to the review queue with the evidence for what it thought it was.",
  ],
  [
    "It never calls unless you switch calls on.",
    "Voice is off by default, and a call that is placed says it is an AI assistant calling on your behalf.",
  ],
  [
    "It never messages a client who asked it to stop.",
    "Nor outside your quiet hours, nor past your daily limit. Every refusal is written down, so “why did nobody chase them?” has an answer.",
  ],
];

export default function LandingPage() {
  return (
    <div className="space-y-14 py-6">
      <section className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          Stop chasing clients for documents.
        </h1>
        <p className="mt-4 text-lg text-ink-2">
          A CA firm does not lose its month to reading invoices. It loses the
          month to asking fifteen clients, again, for the same bank statement.
          DocuParse knows what each filing needs, asks for it, reads what comes
          back, and tells you the moment a person is actually required.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/signup"
            className="rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
          >
            Create an account
          </Link>
          <Link
            href="/docs"
            className="rounded-md border border-line bg-surface px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:bg-surface-sunken"
          >
            Read the API reference
          </Link>
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          One client, one month
        </h2>
        <ol className="mt-4 space-y-3">
          {LADDER.map((step) => (
            <li key={step.when}>
              <Card className="flex flex-col gap-1 px-5 py-4 sm:flex-row sm:gap-6">
                <p className="w-20 shrink-0 text-sm font-medium text-muted">
                  {step.when}
                </p>
                <div>
                  <p className="text-sm font-medium text-ink">{step.what}</p>
                  <p className="mt-1 text-sm text-ink-2">{step.detail}</p>
                </div>
              </Card>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          What it will not do
        </h2>
        <p className="mt-2 max-w-3xl text-sm text-ink-2">
          This matters more than the feature list. An assistant that guesses
          costs more time than it saves, because everything it touches has to
          be checked.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {REFUSALS.map(([claim, detail]) => (
            <Card key={claim} className="px-5 py-4">
              <p className="text-sm font-medium text-ink">{claim}</p>
              <p className="mt-1 text-sm text-ink-2">{detail}</p>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          And when the documents are in
        </h2>
        <p className="mt-2 max-w-3xl text-sm text-ink-2">
          The same system reads the invoices: supplier, GSTIN, line items, HSN
          codes, tax split and totals, with arithmetic and GST checks run over
          the result and a confidence figure per field. Export to CSV, or
          generate a Tally voucher file for the entries that pass. A field it
          could not read is <code className="font-mono text-xs">null</code> —
          never a guess.
        </p>
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Where this honestly is
        </h2>
        <div className="mt-3 space-y-2 text-sm text-ink-2">
          <p>
            Early. The document collection, the review queue, the dashboard and
            the extraction API are built and tested; billing is not, and the
            WhatsApp connection is an interface with a test double behind it
            rather than a live WhatsApp Business account.
          </p>
          <p>
            No accuracy figure is published, because none has been measured on
            a corpus large enough to mean anything. Nothing here is a statement
            about whether a filing is compliant — that stays your professional
            judgement.
          </p>
        </div>
      </section>
    </div>
  );
}
