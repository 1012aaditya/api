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
import { API_URL, ApiRequestError, apiGet, apiUpload } from "@/lib/api";
import { bytes, duration, usd } from "@/lib/format";
import {
  fromExtractionResponse,
  fromStoredExtraction,
  type PlaygroundResult,
} from "@/lib/results";
import type {
  DocumentSummary,
  ExtractionResponse,
  JobAccepted,
  JobState,
  JobStatus,
  StoredExtraction,
} from "@/lib/types";

const ACCEPTED = ".pdf,.png,.jpg,.jpeg";
const ACCEPTED_MIME = ["application/pdf", "image/png", "image/jpeg"];
const SYNC_ENDPOINT = "/v1/invoices/extract";
const ASYNC_ENDPOINT = "/v1/documents";
const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 180_000;

type Tab = "json" | "validation" | "confidence" | "curl";
type Mode = "sync" | "async";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default function PlaygroundPage() {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>("sync");
  const [dragging, setDragging] = useState(false);
  const [running, setRunning] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [jobState, setJobState] = useState<JobState | null>(null);
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [tab, setTab] = useState<Tab>("json");
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback((next: File | null) => {
    setFile(next);
    setResult(null);
    setError(null);
    setJob(null);
    setJobState(null);
  }, []);

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) reset(dropped);
  }

  async function runSync(chosen: File) {
    const response = await apiUpload<ExtractionResponse>(SYNC_ENDPOINT, chosen);
    setResult(fromExtractionResponse(response));
  }

  async function runAsync(chosen: File) {
    const accepted = await apiUpload<JobAccepted>(ASYNC_ENDPOINT, chosen);
    setJobState(accepted.status);

    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let status: JobStatus | null = null;
    while (Date.now() < deadline) {
      await sleep(POLL_INTERVAL_MS);
      status = await apiGet<JobStatus>(`/v1/jobs/${accepted.job_id}`);
      setJob(status);
      setJobState(status.status);
      if (status.status === "completed" || status.status === "failed") break;
    }

    if (status === null || (status.status !== "completed" && status.status !== "failed")) {
      throw new ApiRequestError(
        0,
        {
          code: "job_still_running",
          message:
            "The job is still running. It will finish in the background — check the Documents page shortly.",
        },
        null,
      );
    }

    if (status.status === "failed") {
      throw new ApiRequestError(
        422,
        {
          code: status.error?.code ?? "extraction_failed",
          message: status.error?.message ?? "The job failed.",
        },
        status.request_id,
      );
    }

    const [stored, document] = await Promise.all([
      apiGet<StoredExtraction>(`/v1/documents/${status.document_id}/extraction`),
      apiGet<DocumentSummary>(`/v1/documents/${status.document_id}`),
    ]);
    setResult(fromStoredExtraction(stored, document.page_count));
  }

  async function run() {
    if (!file) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setJob(null);
    setJobState(null);
    try {
      if (mode === "sync") await runSync(file);
      else await runAsync(file);
      setTab("json");
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setRunning(false);
    }
  }

  const endpoint = mode === "sync" ? SYNC_ENDPOINT : ASYNC_ENDPOINT;
  const curl = [
    `curl -X POST ${API_URL}${endpoint} \\`,
    `  -H "Authorization: Bearer dp_live_xxxxxxxx" \\`,
    `  -F "file=@${file?.name ?? "invoice.pdf"}"`,
    ...(mode === "async"
      ? [
          ``,
          `# Then poll, or register a webhook and skip the polling:`,
          `curl ${API_URL}/v1/jobs/<job_id> \\`,
          `  -H "Authorization: Bearer dp_live_xxxxxxxx"`,
        ]
      : []),
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
            <CardHeader title="Request" description={`POST ${endpoint}`} />
            <div className="space-y-4 p-5">
              <fieldset>
                <legend className="mb-2 text-sm font-medium text-ink">Mode</legend>
                <div className="space-y-2">
                  <ModeChoice
                    checked={mode === "sync"}
                    onChange={() => setMode("sync")}
                    title="Synchronous"
                    description="One request, one response. Best for single-page invoices."
                  />
                  <ModeChoice
                    checked={mode === "async"}
                    onChange={() => setMode("async")}
                    title="Asynchronous"
                    description="Returns a job id immediately, then polls. Use this for large or multi-page documents."
                  />
                </div>
              </fieldset>

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
                  onChange={(e) => reset(e.target.files?.[0] ?? null)}
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
                  <Button size="sm" variant="ghost" onClick={() => reset(null)}>
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
                {running
                  ? mode === "async"
                    ? "Processing…"
                    : "Extracting…"
                  : "Run extraction"}
              </Button>

              <p className="text-xs text-muted">
                This runs a real extraction against your organization and counts
                towards your monthly quota, exactly like an API call.
              </p>
            </div>
          </Card>

          {jobState && (
            <Card>
              <CardHeader title="Job" />
              <div className="p-5">
                <JobProgress state={jobState} />
                {job && (
                  <dl className="mt-4 divide-y divide-line border-t border-line text-sm">
                    <Row label="Job id" value={job.id} mono />
                    <Row
                      label="Attempts"
                      value={`${job.attempts} of ${job.max_attempts}`}
                    />
                  </dl>
                )}
                {jobState === "queued" && (
                  <p className="mt-3 text-xs text-muted">
                    Waiting for a worker. If nothing happens, check that one is
                    running: <code className="font-mono">make worker</code>.
                  </p>
                )}
              </div>
            </Card>
          )}

          {result && (
            <Card>
              <CardHeader title="Processing" />
              <dl className="divide-y divide-line text-sm">
                <Row label="Total time" value={duration(result.processing.duration_ms)} />
                <Row
                  label="Provider time"
                  value={duration(result.processing.provider_latency_ms ?? null)}
                />
                <Row
                  label="Pages"
                  value={result.processing.pages === null ? "—" : String(result.processing.pages)}
                />
                <Row label="Model" value={result.processing.model ?? "—"} mono />
                <Row label="Prompt" value={result.processing.prompt_version ?? "—"} mono />
                <Row
                  label="Tokens"
                  value={
                    result.processing.input_tokens == null
                      ? "—"
                      : `${result.processing.input_tokens} in · ${result.processing.output_tokens ?? 0} out`
                  }
                />
                <Row
                  label="Estimated cost"
                  value={usd(result.processing.estimated_cost_usd ?? null)}
                />
                <Row label="Request id" value={result.requestId ?? "—"} mono />
                <Row label="Document id" value={result.documentId} mono />
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
                  {result.validation && <StatusBadge status={result.validation.overall} />}
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

              {tab === "validation" &&
                (result.validation ? (
                  <ValidationReport
                    overall={result.validation.overall}
                    checks={result.validation.checks}
                  />
                ) : (
                  <p className="px-5 py-8 text-center text-sm text-ink-2">
                    No validation report was stored for this extraction.
                  </p>
                ))}

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

const JOB_STEPS: { state: JobState; label: string }[] = [
  { state: "queued", label: "Queued" },
  { state: "processing", label: "Processing" },
  { state: "completed", label: "Completed" },
];

function JobProgress({ state }: { state: JobState }) {
  if (state === "failed") {
    return <StatusBadge status="failed" />;
  }
  const reached = JOB_STEPS.findIndex((step) => step.state === state);
  return (
    <ol className="flex items-center gap-2">
      {JOB_STEPS.map((step, index) => {
        const done = index <= reached;
        return (
          <li key={step.state} className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${done ? "bg-accent" : "bg-line"}`}
              aria-hidden="true"
            />
            <span className={`text-xs ${done ? "text-ink" : "text-muted"}`}>
              {step.label}
            </span>
            {index < JOB_STEPS.length - 1 && (
              <span className="h-px w-6 bg-line" aria-hidden="true" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function ModeChoice({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean;
  onChange: () => void;
  title: string;
  description: string;
}) {
  return (
    <label className="flex items-start gap-2.5">
      <input
        type="radio"
        name="mode"
        className="mt-0.5 accent-accent"
        checked={checked}
        onChange={onChange}
      />
      <span>
        <span className="text-sm text-ink">{title}</span>
        <span className="block text-xs text-muted">{description}</span>
      </span>
    </label>
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
