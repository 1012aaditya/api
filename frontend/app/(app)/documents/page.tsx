"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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
import { ApiRequestError, apiDelete, apiDownload, apiGet } from "@/lib/api";
import { bytes, relativeTime } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setDocuments(await apiGet<DocumentSummary[]>("/v1/documents?limit=100"));
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

  async function exportCsv(kind: "invoices" | "line-items") {
    try {
      await apiDownload(`/v1/exports/${kind}.csv`, `${kind}.csv`);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    }
  }

  async function remove(id: string) {
    if (
      !window.confirm(
        "Delete the stored file now? The record stays for your usage history, but the document itself is gone.",
      )
    ) {
      return;
    }
    try {
      await apiDelete(`/v1/documents/${id}`);
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    }
  }

  return (
    <>
      <PageHeader
        title="Documents"
        description="Everything you have uploaded. Files are deleted automatically when their retention window expires."
        action={
          <div className="flex gap-2">
            <Button onClick={() => void exportCsv("invoices")}>Export invoices</Button>
            <Button onClick={() => void exportCsv("line-items")}>Export line items</Button>
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

      <Card>
        <CardHeader title="Uploads" description={`${documents.length} shown`} />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : documents.length === 0 ? (
          <EmptyState
            title="Nothing uploaded yet"
            description="Invoices you send to the API — or run through the playground — appear here."
            action={
              <Link href="/playground">
                <Button variant="primary">Open playground</Button>
              </Link>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">File</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 text-right font-medium">Pages</th>
                  <th className="px-3 py-2 text-right font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Uploaded</th>
                  <th className="px-5 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {documents.map((document) => (
                  <tr key={document.id}>
                    <td className="px-5 py-3">
                      <Link
                        href={`/documents/${document.id}`}
                        className="text-ink underline-offset-2 hover:underline"
                      >
                        {document.filename}
                      </Link>
                      <p className="font-mono text-xs text-muted">{document.id}</p>
                    </td>
                    <td className="px-3 py-3">
                      <Badge>{document.status}</Badge>
                      {!document.stored && document.purged_at && (
                        <p className="mt-1 text-xs text-muted">File deleted</p>
                      )}
                    </td>
                    <td className="tnum px-3 py-3 text-right text-ink-2">
                      {document.page_count}
                    </td>
                    <td className="tnum px-3 py-3 text-right text-ink-2">
                      {bytes(document.size_bytes)}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">
                      {relativeTime(document.created_at)}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {document.stored && (
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => void remove(document.id)}
                        >
                          Delete file
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
