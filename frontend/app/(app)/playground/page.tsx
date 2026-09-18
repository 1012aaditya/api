"use client";

import Link from "next/link";
import { useCallback, useRef, useState, type DragEvent } from "react";

import { ConfidenceTable } from "@/components/ConfidenceTable";
import { CopyableCommand } from "@/components/CopyableCommand";
import { JsonBlock } from "@/components/JsonBlock";
import { ValidationReport } from "@/components/ValidationReport";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ErrorNotice,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { API_URL, ApiRequestError, apiUpload } from "@/lib/api";
import { bytes, duration, usd } from "@/lib/format";
import type { ExtractionResponse } from "@/lib/types";

const ACCEPTED = ".pdf,.png,.jpg,.jpeg";
const ACCEPTED_MIME = ["application/pdf", "image/png", "image/jpeg"];
const ENDPOINT = "/v1/invoices/extract";

type Tab = "json" | "validation" | "confidence" | "curl";

export default function PlaygroundPage() {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ExtractionResponse | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [tab, setTab] = useState<Tab>("json");
  const inputRef = useRef<HTMLInputElement>(null);

  const choose = useCallback((next: File | null) => {
    setFile(next);
    setResult(null);
    setError(null);
  }, []);

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) choose(dropped);
  }

  async function run() {
    if (!file) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const response = await apiUpload<ExtractionResponse>(ENDPOINT, file);
      setResult(response);
      setTab("json");
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setRunning(false);
    }
  }

  const curl = [
    `curl -X POST ${API_URL}${ENDPOINT} \\`,
    `  -H "Authorization: Bearer dp_live_xxxxxxxx" \\`,
    `  -F "file=@${file?.name ?? "invoice.pdf"}"`,
  ].join("\n");

  const unsupported =
    file !== null && !ACCEPTED_MIME.includes(file.type) && file.type !== "";

  return (
    <>
      <PageHeader
        title="Playground"
        description="Upload an invoice and see exactly what the API returns — the same pipeline your own code calls."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-start">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Request" description={`POST ${ENDPOINT}`} />
            <div className="space-y-4 p-5">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={`rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
                  dragging ? "border-accent bg-accent-100/30" : "border-line bg-surface-sunken"
                }`}
              >
                <p className="text-sm text-ink">
                  Drop a GST invoice here, or{" "}
                  <button
                    type="button"
                    onClick={() => inputRef.current?.click()}
                    className="text-accent underline underline-offset-2"
                  >
                    choose a file
                  </button>
                  .
                </p>
                <p className="mt-1.5 text-xs text-muted">
                  PDF, PNG, JPG or JPEG. Up to 20 MB and 25 pages.
                </p>
                <input
                  ref={inputRef}
                  type="file"
                  accept={ACCEPTED}
                  className="sr-only"
                  onChange={(e) => choose(e.target.files?.[0] ?? null)}
                />
              </div>

              {file && (
                <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink">{file.name}</p>
                    <p className="text-xs text-muted">
                      {bytes(file.size)}
                      {file.type ? ` · ${file.type}` : ""}
                    </p>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => choose(null)}>
                    Remove
                  </Button>
                </div>
              )}

              {unsupported && (
                <p className="text-xs text-critical">
                  That looks like an unsupported type. The API checks the file&apos;s
                  own bytes, so it will return 415 rather than trusting the
                  extension.
                </p>
              )}

              <Button
                variant="primary"
                className="w-full"
                disabled={!file || running}
                onClick={() => void run()}
              >
                {running ? "Extracting…" : "Run extraction"}
              </Button>

              <p className="text-xs text-muted">
                This runs a real extraction against your organization and counts
                towards your monthly quota, exactly like an API call.
              </p>
            </div>
          </Card>

          {result && (
            <Card>
              <CardHeader title="Processing" />
              <dl className="divide-y divide-line text-sm">
                <Row label="Total time" value={duration(result.processing.duration_ms)} />
                <Row
                  label="Provider time"
                  value={duration(result.processing.provider_latency_ms)}
                />
                <Row label="Pages" value={String(result.processing.pages)} />
                <Row label="Model" value={result.processing.model} mono />
                <Row label="Prompt" value={result.processing.prompt_version} mono />
                <Row
                  label="Tokens"
                  value={
                    result.processing.input_tokens === null
                      ? "—"
                      : `${result.processing.input_tokens} in · ${result.processing.output_tokens ?? 0} out`
                  }
                />
                <Row
                  label="Estimated cost"
                  value={usd(result.processing.estimated_cost_usd)}
                />
                <Row label="Request id" value={result.request_id} mono />
                <Row label="Document id" value={result.document_id} mono />
              </dl>
            </Card>
          )}
        </div>

        <div className="min-w-0">
          {error && (
            <ErrorNotice
              title="The API returned an error"
              message={error.message}
              code={error.code}
              requestId={error.requestId}
            />
          )}

          {error?.code === "extraction_provider_unavailable" && (
            <Card className="mt-4">
              <div className="p-5 text-sm text-ink-2">
                <p className="font-medium text-ink">No AI provider is configured.</p>
                <p className="mt-1.5">
                  This is the intended behaviour, not a bug: with no provider,
                  the API refuses rather than inventing invoice data. Set{" "}
                  <code className="font-mono text-xs text-ink">AI_API_KEY</code> and{" "}
                  <code className="font-mono text-xs text-ink">AI_MODEL</code> in the
                  backend&apos;s <code className="font-mono text-xs text-ink">.env</code>,
                  restart it, and run this again.
                </p>
              </div>
            </Card>
          )}

          {!result && !error && (
            <Card>
              <div className="px-5 py-16 text-center">
                <p className="text-sm font-medium text-ink">No response yet</p>
                <p className="mx-auto mt-1 max-w-sm text-sm text-ink-2">
                  Upload an invoice and run it. You&apos;ll get the extracted JSON,
                  the validation report, per-field confidence, and a cURL command
                  you can paste into a terminal.
                </p>
              </div>
            </Card>
          )}

          {result && (
            <Card>
              <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
                <div className="flex flex-wrap gap-1.5">
                  {(
                    [
                      ["json", "JSON"],
                      ["validation", "Validation"],
                      ["confidence", "Confidence"],
                      ["curl", "cURL"],
                    ] as [Tab, string][]
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setTab(key)}
                      className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                        tab === key
                          ? "bg-surface-sunken text-ink"
                          : "text-ink-2 hover:text-ink"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge>{duration(result.processing.duration_ms)}</Badge>
                  <StatusBadge status={result.validation.overall} />
                </div>
              </div>

              {tab === "json" && (
                <div className="p-5">
                  <JsonBlock value={result.data} maxHeight="34rem" />
                  <p className="mt-3 text-xs text-muted">
                    <code className="font-mono">null</code> means the field was
                    not on the document. It is never a guess.
                  </p>
                </div>
              )}

              {tab === "validation" && (
                <ValidationReport
                  overall={result.validation.overall}
                  checks={result.validation.checks}
                />
              )}

              {tab === "confidence" && (
                <ConfidenceTable
                  fields={result.confidence.fields}
                  lowConfidenceFields={result.confidence.low_confidence_fields}
                  overall={result.confidence.overall}
                  band={result.confidence.band}
                />
              )}

              {tab === "curl" && (
                <div className="space-y-3 p-5">
                  <CopyableCommand command={curl} />
                  <p className="text-xs text-muted">
                    Replace the placeholder with a real key from{" "}
                    <Link
                      href="/api-keys"
                      className="text-accent underline underline-offset-2"
                    >
                      API keys
                    </Link>
                    . Keys are stored hashed, so an existing one cannot be shown
                    again — create a new one if you no longer have it.
                  </p>
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-5 py-2.5">
      <dt className="shrink-0 text-ink-2">{label}</dt>
      <dd
        className={`min-w-0 truncate text-right text-ink ${
          mono ? "font-mono text-xs" : "tnum"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
