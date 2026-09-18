"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiDownload, apiGet, apiUploadMany } from "@/lib/api";
import { bytes, relativeTime } from "@/lib/format";
import type { BatchAccepted, BatchProgress, RejectedFile } from "@/lib/types";

const ACCEPTED = ".pdf,.png,.jpg,.jpeg";
const POLL_MS = 2000;

export default function BatchesPage() {
  const [batches, setBatches] = useState<BatchProgress[]>([]);
  const [selected, setSelected] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lastUpload, setLastUpload] = useState<BatchAccepted | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setBatches(await apiGet<BatchProgress[]>("/v1/batches?limit=25"));
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

  // Poll only while something is still running — a dashboard that hammers the
  // API when nothing is happening is just a load generator.
  useEffect(() => {
    if (batches.every((batch) => batch.done)) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [batches, load]);

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    setSelected((current) => [...current, ...Array.from(incoming)]);
    setError(null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  async function upload() {
    if (selected.length === 0) return;
    setUploading(true);
    setError(null);
    setLastUpload(null);
    try {
      const result = await apiUploadMany<BatchAccepted>(
        "/v1/batches",
        selected,
        name.trim() ? { name: name.trim() } : {},
      );
      setLastUpload(result);
      setSelected([]);
      setName("");
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setUploading(false);
    }
  }

  async function download(batchId: string | null, kind: "invoices" | "line-items") {
    const query = batchId ? `?batch_id=${batchId}` : "";
    try {
      await apiDownload(
        `/v1/exports/${kind}.csv${query}`,
        `${kind}${batchId ? `-${batchId}` : ""}.csv`,
      );
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    }
  }

  const totalBytes = selected.reduce((sum, file) => sum + file.size, 0);

  return (
    <>
      <PageHeader
        title="Bulk upload"
        description="Drop a folder of invoices in, then download the results as a spreadsheet."
        action={
          <div className="flex gap-2">
            <Button onClick={() => void download(null, "invoices")}>
              Export all invoices
            </Button>
            <Button onClick={() => void download(null, "line-items")}>
              Export line items
            </Button>
          </div>
        }
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice
            message={error.message}
            code={error.code}
            requestId={error.requestId}
          />
        </div>
      )}

      <Card className="mb-6">
        <CardHeader title="New batch" />
        <div className="space-y-4 p-5">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`rounded-lg border-2 border-dashed px-4 py-10 text-center transition-colors ${
              dragging ? "border-accent bg-accent-100/30" : "border-line bg-surface-sunken"
            }`}
          >
            <p className="text-sm text-ink">
              Drop invoices here, or{" "}
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="text-accent underline underline-offset-2"
              >
                choose files
              </button>
              .
            </p>
            <p className="mt-1.5 text-xs text-muted">
              PDF, PNG, JPG or JPEG. Up to 200 files at a time. A file we
              can&apos;t read is reported, not silently dropped.
            </p>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED}
              multiple
              className="sr-only"
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>

          {selected.length > 0 && (
            <div className="rounded-md border border-line">
              <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
                <p className="text-sm text-ink">
                  {selected.length} file{selected.length === 1 ? "" : "s"}
                  <span className="text-muted"> · {bytes(totalBytes)}</span>
                </p>
                <Button size="sm" variant="ghost" onClick={() => setSelected([])}>
                  Clear
                </Button>
              </div>
              <ul className="max-h-48 divide-y divide-line overflow-auto">
                {selected.map((file, index) => (
                  <li
                    key={`${file.name}-${index}`}
                    className="flex items-center justify-between gap-3 px-3 py-1.5"
                  >
                    <span className="truncate text-xs text-ink-2">{file.name}</span>
                    <span className="tnum shrink-0 text-xs text-muted">
                      {bytes(file.size)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Field label="Batch name" hint="Optional, e.g. “September purchases”.">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="September purchases"
              maxLength={200}
            />
          </Field>

          <Button
            variant="primary"
            onClick={() => void upload()}
            disabled={uploading || selected.length === 0}
          >
            {uploading
              ? `Uploading ${selected.length} file${selected.length === 1 ? "" : "s"}…`
              : "Upload and process"}
          </Button>

          <p className="text-xs text-muted">
            Each file is processed in the background and counts towards your
            monthly quota. You can leave this page — progress is kept below.
          </p>
        </div>
      </Card>

      {lastUpload && <UploadReceipt upload={lastUpload} />}

      <Card>
        <CardHeader title="Batches" />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : batches.length === 0 ? (
          <EmptyState
            title="No batches yet"
            description="Upload a folder of invoices above and they will appear here with live progress."
          />
        ) : (
          <ul className="divide-y divide-line">
            {batches.map((batch) => (
              <li key={batch.id} className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-ink">
                      {batch.name || "Untitled batch"}
                    </p>
                    <p className="font-mono text-xs text-muted">{batch.id}</p>
                    <p className="mt-1.5 text-xs text-muted">
                      {relativeTime(batch.created_at)} · {batch.document_count} queued
                      {batch.rejected_count > 0 &&
                        ` · ${batch.rejected_count} rejected on arrival`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <StatusBadge
                      status={
                        batch.failed > 0
                          ? "warning"
                          : batch.done
                            ? "passed"
                            : "not_checked"
                      }
                      label={
                        batch.done
                          ? batch.failed > 0
                            ? `${batch.failed} failed`
                            : "Done"
                          : "Processing"
                      }
                    />
                    <Button size="sm" onClick={() => void download(batch.id, "invoices")}>
                      Export CSV
                    </Button>
                  </div>
                </div>
                <BatchBar batch={batch} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <p className="mt-4 text-xs text-muted">
        Exports open in Excel or Google Sheets.{" "}
        <Link href="/documents" className="text-accent underline underline-offset-2">
          Documents
        </Link>{" "}
        shows each invoice individually, with its validation report.
      </p>
    </>
  );
}

function BatchBar({ batch }: { batch: BatchProgress }) {
  if (batch.total === 0) {
    return (
      <p className="mt-2 text-xs text-muted">
        Nothing was queued — every file was rejected on arrival.
      </p>
    );
  }
  const done = batch.completed + batch.failed;
  const percent = Math.round((done / batch.total) * 100);

  return (
    <div className="mt-3">
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-accent-100"
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={batch.total}
        aria-label="Batch progress"
      >
        <div
          className={`h-full rounded-full transition-[width] ${
            batch.failed > 0 ? "bg-warning" : "bg-accent"
          }`}
          style={{ width: `${Math.max(percent, done > 0 ? 2 : 0)}%` }}
        />
      </div>
      <p className="tnum mt-1.5 text-xs text-muted">
        {batch.completed} done
        {batch.failed > 0 && ` · ${batch.failed} failed`}
        {batch.processing > 0 && ` · ${batch.processing} running`}
        {batch.queued > 0 && ` · ${batch.queued} queued`}
        <span className="text-ink-2"> · {batch.total} total</span>
      </p>
    </div>
  );
}

function UploadReceipt({ upload }: { upload: BatchAccepted }) {
  const grouped = upload.rejected.reduce<Record<string, RejectedFile[]>>(
    (acc, file) => {
      (acc[file.code] ??= []).push(file);
      return acc;
    },
    {},
  );

  return (
    <Card className="mb-6">
      <CardHeader
        title={`Queued ${upload.accepted} file${upload.accepted === 1 ? "" : "s"}`}
        description={
          upload.rejected.length === 0
            ? "Every file was accepted."
            : `${upload.rejected.length} file(s) were not accepted — see below.`
        }
        action={<Badge>{upload.batch_id}</Badge>}
      />
      {upload.rejected.length > 0 && (
        <div className="space-y-3 p-5">
          {Object.entries(grouped).map(([code, entries]) => (
            <div key={code}>
              <p className="font-mono text-xs text-critical">{code}</p>
              <p className="mt-0.5 text-xs text-ink-2">{entries[0].message}</p>
              <ul className="mt-1.5 space-y-0.5">
                {entries.map((entry) => (
                  <li key={entry.filename} className="text-xs text-muted">
                    {entry.filename}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
