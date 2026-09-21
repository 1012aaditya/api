"use client";

import { useCallback, useEffect, useState } from "react";

import {
  Card,
  CardHeader,
  ErrorNotice,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { PrivacyFootprint } from "@/lib/types";

const LABELS: Record<string, string> = {
  clients: "Clients",
  documents_stored: "Documents held right now",
  documents_purged: "Documents already deleted",
  messages: "Messages sent and received",
  people_with_a_login: "People with a login",
};

/** The three answers, and what each one is honestly worth. */
const LOCALITY: Record<string, { label: string; status: "passed" | "warning" | "not_checked" }> = {
  stays_here: { label: "Stays on your hardware", status: "passed" },
  cannot_be_proven: { label: "Cannot be proven from here", status: "warning" },
  no_model: { label: "No model configured", status: "not_checked" },
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 border-b border-line px-5 py-3 last:border-b-0">
      <span className="text-sm text-ink-2">{label}</span>
      <span className="text-right text-sm text-ink">{children}</span>
    </div>
  );
}

export default function PrivacyPage() {
  const [report, setReport] = useState<PrivacyFootprint | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setReport(await apiGet<PrivacyFootprint>("/v1/privacy/footprint"));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="py-16 text-center">
        <Spinner />
      </div>
    );
  }

  const locality = report ? LOCALITY[report.document_locality] : undefined;

  return (
    <>
      <PageHeader
        title="Your clients' data"
        description="Read from this deployment as it is configured right now, not from a page somebody wrote once."
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      {report && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Where documents are read"
              description="The one question a client's partner will ask you."
            />
            <div className="px-5 py-4">
              {locality && <StatusBadge status={locality.status} label={locality.label} />}
              <p className="mt-3 text-sm text-ink-2">{report.locality_note}</p>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="How long documents are kept"
              description="After this, the file is deleted and cannot be recovered."
            />
            <div>
              <Row label="Retention window">
                {report.retention_days} {report.retention_days === 1 ? "day" : "days"}
              </Row>
              <Row label="Next deletion due">
                {report.next_purge_at ? (
                  dateTime(report.next_purge_at)
                ) : (
                  <span className="text-muted">nothing waiting</span>
                )}
              </Row>
              <Row label="Where files are stored">
                <span className="font-mono text-xs">{report.storage_backend}</span>
              </Row>
            </div>
            <p className="border-t border-line px-5 py-3 text-xs text-muted">
              The values read out of a document — invoice number, amounts, GSTINs —
              are kept after the file itself is deleted. They are the record of the
              work done. Erasing a client removes those too.
            </p>
          </Card>

          <Card>
            <CardHeader title="What is held about your clients" />
            <div>
              {Object.entries(LABELS).map(([key, label]) => (
                <Row key={key} label={label}>
                  <span className="tnum">{report.counts[key] ?? 0}</span>
                </Row>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Who else sees it"
              description="Every third party this configuration sends client data to."
            />
            {report.processors.length === 0 ? (
              <p className="px-5 py-5 text-sm text-ink-2">
                Nobody. In this configuration, no client data leaves this
                deployment.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {report.processors.map((processor) => (
                  <li key={processor.name} className="px-5 py-3">
                    <p className="text-sm font-medium text-ink">{processor.name}</p>
                    <p className="mt-0.5 text-sm text-ink-2">{processor.receives}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      <Card className="mt-6">
        <CardHeader
          title="Removing a client's data"
          description="An owner or an administrator can erase a client from that client's page."
        />
        <div className="space-y-3 px-5 py-4 text-sm text-ink-2">
          <p>
            Erasing removes the client, their cases, every document and stored
            file, every value read out of those documents, every message and
            call, and the tasks and decisions about them. It is immediate and
            cannot be undone.
          </p>
          <p>
            The count of documents processed survives, with the link to the
            client removed. That count is what you were billed for, and it
            identifies nobody.
          </p>
        </div>
      </Card>
    </>
  );
}
