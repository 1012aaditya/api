"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConfidenceTable } from "@/components/ConfidenceTable";
import { JsonBlock } from "@/components/JsonBlock";
import { ValidationReport } from "@/components/ValidationReport";
import {
  Badge,
  Card,
  CardHeader,
  ErrorNotice,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet } from "@/lib/api";
import { bytes, dateTime, duration, usd } from "@/lib/format";
import type { ConfidenceBand, DocumentSummary, StoredExtraction } from "@/lib/types";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [document, setDocument] = useState<DocumentSummary | null>(null);
  const [extraction, setExtraction] = useState<StoredExtraction | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [extractionError, setExtractionError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setDocument(await apiGet<DocumentSummary>(`/v1/documents/${id}`));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
      setLoading(false);
      return;
    }
    try {
      setExtraction(
        await apiGet<StoredExtraction>(`/v1/documents/${id}/extraction`),
      );
    } catch (caught) {
      // A document with no extraction yet is a normal state, not a failure.
      if (caught instanceof ApiRequestError) setExtractionError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, [id]);

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

  if (error) {
    return (
      <>
        <PageHeader title="Document" />
        <ErrorNotice
          message={error.message}
          code={error.code}
          requestId={error.requestId}
        />
        <p className="mt-4 text-sm">
          <Link href="/documents" className="text-accent underline underline-offset-2">
            Back to documents
          </Link>
        </p>
      </>
    );
  }

  if (!document) return null;

  const overallBand: ConfidenceBand | null =
    extraction?.overall_confidence == null
      ? null
      : extraction.overall_confidence >= 0.85
        ? "high"
        : extraction.overall_confidence >= 0.6
          ? "medium"
          : "low";

  return (
    <>
      <PageHeader
        title={document.filename}
        description={`Uploaded ${dateTime(document.created_at)}`}
        action={
          <Link
            href="/documents"
            className="text-sm text-ink-2 underline underline-offset-2 hover:text-ink"
          >
            Back to documents
          </Link>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)] lg:items-start">
        <Card>
          <CardHeader title="Document" />
          <dl className="divide-y divide-line text-sm">
            <Row label="Id" value={document.id} mono />
            <Row label="Status" value={document.status} />
            <Row label="Type" value={document.content_type} mono />
            <Row label="Pages" value={String(document.page_count)} />
            <Row label="Size" value={bytes(document.size_bytes)} />
            <Row label="Request id" value={document.request_id ?? "—"} mono />
            <Row
              label="File retained"
              value={
                document.purged_at
                  ? `Deleted ${dateTime(document.purged_at)}`
                  : document.retention_expires_at
                    ? `Until ${dateTime(document.retention_expires_at)}`
                    : "Not stored (process and delete)"
              }
            />
            {extraction && (
              <>
                <Row label="Model" value={extraction.model ?? "—"} mono />
                <Row
                  label="Processing time"
                  value={duration(extraction.total_latency_ms)}
                />
                <Row
                  label="Estimated cost"
                  value={usd(extraction.estimated_cost_usd)}
                />
              </>
            )}
          </dl>
        </Card>

        <div className="min-w-0 space-y-6">
          {extractionError && (
            <Card>
              <div className="px-5 py-10 text-center">
                <p className="text-sm font-medium text-ink">No extraction stored</p>
                <p className="mx-auto mt-1 max-w-sm text-sm text-ink-2">
                  {extractionError.message}
                </p>
              </div>
            </Card>
          )}

          {extraction?.status === "failed" && (
            <ErrorNotice
              title="This extraction failed"
              message={
                extraction.error_message ??
                "The document could not be turned into structured data."
              }
              code={extraction.error_code ?? undefined}
              requestId={extraction.request_id}
            />
          )}

          {extraction?.data && (
            <Card>
              <CardHeader
                title="Extracted data"
                action={<Badge>{extraction.document_type}</Badge>}
              />
              <div className="p-5">
                <JsonBlock value={extraction.data} maxHeight="30rem" />
              </div>
            </Card>
          )}

          {extraction?.validation && (
            <Card>
              <CardHeader
                title="Validation"
                action={<StatusBadge status={extraction.validation.overall} />}
              />
              <ValidationReport
                overall={extraction.validation.overall}
                checks={extraction.validation.checks}
              />
            </Card>
          )}

          {extraction?.confidence && (
            <Card>
              <CardHeader title="Confidence" />
              <ConfidenceTable
                fields={extraction.confidence}
                lowConfidenceFields={Object.entries(extraction.confidence)
                  .filter(([, value]) => value.band === "low")
                  .map(([path]) => path)}
                overall={extraction.overall_confidence}
                band={overallBand}
              />
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
        className={`min-w-0 text-right text-ink ${
          mono ? "break-all font-mono text-xs" : "break-words"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
