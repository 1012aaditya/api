"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { ApiRequestError, apiUploadEnvelope } from "@/lib/api";
import type { ImportPlan, PlannedClient } from "@/lib/types";

const VERDICT: Record<PlannedClient["verdict"], { label: string; tone: string }> = {
  create: { label: "Will be added", tone: "text-good-ink" },
  duplicate: { label: "Already there", tone: "text-ink-2" },
  skip: { label: "Cannot be read", tone: "text-critical" },
};

/** The values that would be written, in the order a person reads them. */
function Values({ values }: { values: Record<string, unknown> }) {
  const shown = ["gstin", "whatsapp_phone", "phone", "client_code", "email"]
    .map((key) => [key, values[key]] as const)
    .filter(([, value]) => value);
  if (shown.length === 0) return null;
  return (
    <span className="text-xs text-muted">
      {shown.map(([key, value]) => (
        <span key={key} className="mr-3 font-mono">
          {String(value)}
        </span>
      ))}
    </span>
  );
}

export default function ImportClientsPage() {
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function run(path: string, chosen: File) {
    setBusy(true);
    setError(null);
    try {
      setPlan(await apiUploadEnvelope<ImportPlan>(path, chosen));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  function choose(chosen: File | null) {
    setFile(chosen);
    setPlan(null);
    setError(null);
    // Previewing immediately: the plan is the point, and making someone
    // press a second button to see it just delays the information.
    if (chosen) void run("/v1/clients/import/preview", chosen);
  }

  const applied = plan?.applied === true;

  return (
    <>
      <PageHeader
        title="Import your client list"
        description="A CSV from Tally or Excel. Nothing is added until you say so."
        action={
          <Link href="/clients">
            <Button variant="secondary">Back to clients</Button>
          </Link>
        }
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      <Card className="mb-6">
        <CardHeader
          title="Choose the file"
          description="One row per client. A column called Name, Client Name, Party Name or Ledger Name is the only one required."
        />
        <div className="px-5 py-5">
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv,text/plain"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-ink-2 file:mr-4 file:rounded-md file:border file:border-line file:bg-surface file:px-3.5 file:py-2 file:text-sm file:font-medium file:text-ink hover:file:bg-surface-sunken"
          />
          <p className="mt-3 text-xs text-muted">
            Tally: Display &rarr; List of Accounts &rarr; Sundry Debtors, then export as
            CSV. Excel: save as <span className="font-mono">CSV UTF-8</span>.
          </p>
        </div>
      </Card>

      {busy && (
        <div className="py-10 text-center">
          <Spinner label="Reading the file" />
        </div>
      )}

      {plan && !busy && (
        <>
          {plan.error && (
            <div className="mb-6">
              <ErrorNotice title="This file could not be used" message={plan.error} />
            </div>
          )}

          {plan.rows.length > 0 && (
            <Card className="mb-6">
              <CardHeader
                title={applied ? "What was imported" : "What will happen"}
                description={
                  applied
                    ? "Every row of the file, and what became of it."
                    : "Every row of the file, and what would become of it. Nothing has been added yet."
                }
                action={
                  !applied && plan.counts.create > 0 ? (
                    <Button
                      variant="primary"
                      disabled={busy || !file}
                      onClick={() => file && void run("/v1/clients/import", file)}
                    >
                      Add {plan.counts.create}{" "}
                      {plan.counts.create === 1 ? "client" : "clients"}
                    </Button>
                  ) : undefined
                }
              />

              <div className="flex flex-wrap gap-x-6 gap-y-1 border-b border-line px-5 py-3 text-sm">
                <span className="text-ink-2">
                  <span className="tnum font-semibold text-ink">{plan.counts.rows}</span> rows
                  read
                </span>
                <span className="text-good-ink">
                  <span className="tnum font-semibold">{plan.counts.create}</span>{" "}
                  {applied ? "added" : "to add"}
                </span>
                <span className="text-ink-2">
                  <span className="tnum font-semibold">{plan.counts.duplicate}</span> already
                  there
                </span>
                <span className={plan.counts.skip > 0 ? "text-critical" : "text-muted"}>
                  <span className="tnum font-semibold">{plan.counts.skip}</span> cannot be read
                </span>
              </div>

              {plan.ignored_columns.length > 0 && (
                <p className="border-b border-line px-5 py-3 text-xs text-muted">
                  Columns not used:{" "}
                  <span className="font-mono">{plan.ignored_columns.join(", ")}</span>. If one of
                  those was meant to be a GSTIN or a phone number, rename it and try again.
                </p>
              )}

              <ul className="divide-y divide-line">
                {plan.rows.map((row) => (
                  <li key={row.row_number} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-sm text-ink">
                          <span className="tnum mr-2 text-xs text-muted">
                            row {row.row_number}
                          </span>
                          {row.name ?? <span className="text-muted">(no name)</span>}
                        </p>
                        <Values values={row.values} />
                        {row.reason && (
                          <p className="mt-1 text-xs text-critical">{row.reason}</p>
                        )}
                        {row.matches && (
                          <p className="mt-1 text-xs text-ink-2">Matches {row.matches}.</p>
                        )}
                        {row.warnings.map((warning) => (
                          <p key={warning} className="mt-1 text-xs text-ink-2">
                            {warning}
                          </p>
                        ))}
                      </div>
                      <span
                        className={`shrink-0 text-xs font-medium ${VERDICT[row.verdict].tone}`}
                      >
                        {applied && row.verdict === "create"
                          ? "Added"
                          : VERDICT[row.verdict].label}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {applied && (
            <div className="flex items-center gap-3">
              <Link href="/clients">
                <Button variant="primary">See your clients</Button>
              </Link>
              <Button
                variant="secondary"
                onClick={() => {
                  setFile(null);
                  setPlan(null);
                  if (input.current) input.current.value = "";
                }}
              >
                Import another file
              </Button>
            </div>
          )}
        </>
      )}

      {!plan && !busy && !file && (
        <Card>
          <EmptyState
            title="Nothing chosen yet"
            description="Pick a file above. You will see exactly what would be added, what is already there, and anything that could not be read — before a single client is created."
          />
        </Card>
      )}
    </>
  );
}
