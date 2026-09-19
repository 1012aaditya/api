"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { JsonBlock } from "@/components/JsonBlock";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Input,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { exceptionState, exceptionTitle, severity as severityLook } from "@/lib/practice";
import type { ReviewException } from "@/lib/types";

const FILTERS = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

export default function ExceptionsPage() {
  const [exceptions, setExceptions] = useState<ReviewException[]>([]);
  const [filter, setFilter] = useState("open");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (status: string) => {
    setLoading(true);
    setError(null);
    try {
      setExceptions(
        await apiGet<ReviewException[]>(`/v1/exceptions?status=${status}&limit=200`),
      );
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filter);
  }, [filter, load]);

  async function resolve(id: string, status: "resolved" | "dismissed") {
    setBusy(id);
    setError(null);
    try {
      await apiSend<ReviewException>(`/v1/exceptions/${id}/resolve`, {
        status,
        note: notes[id]?.trim() || null,
      });
      await load(filter);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Needs a person"
        description="Everything the system would not decide by itself. Nothing here was guessed at and filed anyway."
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

      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <Button
            key={option.value}
            size="sm"
            variant={filter === option.value ? "primary" : "secondary"}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      {loading ? (
        <Card>
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        </Card>
      ) : exceptions.length === 0 ? (
        <Card>
          <EmptyState
            title={filter === "open" ? "Nothing waiting on you" : "Nothing here"}
            description={
              filter === "open"
                ? "Every document the agent could not decide has been dealt with."
                : "No exception has that status."
            }
          />
        </Card>
      ) : (
        <div className="space-y-4">
          {exceptions.map((item) => {
            const look = severityLook(item.severity);
            const state = exceptionState(item.status);
            return (
              <Card key={item.id}>
                <CardHeader
                  title={exceptionTitle(item.type)}
                  description={item.message}
                  action={
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <StatusBadge status={look.tone} label={look.label} />
                      <StatusBadge status={state.tone} label={state.label} />
                    </div>
                  }
                />
                <div className="space-y-3 px-5 py-4">
                  <p className="text-xs text-muted">
                    {item.client_id ? (
                      <Link
                        href={`/clients/${item.client_id}`}
                        className="underline-offset-2 hover:underline"
                      >
                        {item.client_name ?? "Client"}
                      </Link>
                    ) : (
                      "No client"
                    )}{" "}
                    · raised {dateTime(item.created_at)}
                    {item.document_id && ` · document ${item.document_id}`}
                  </p>

                  {Object.keys(item.details).length > 0 && (
                    <JsonBlock value={item.details} />
                  )}

                  {item.status === "open" && (
                    <div className="flex flex-wrap items-center gap-2">
                      <Input
                        value={notes[item.id] ?? ""}
                        onChange={(e) =>
                          setNotes({ ...notes, [item.id]: e.target.value })
                        }
                        placeholder="What did you decide? (kept on the record)"
                        className="max-w-md flex-1"
                        aria-label="Resolution note"
                      />
                      <Button
                        variant="primary"
                        disabled={busy === item.id}
                        onClick={() => void resolve(item.id, "resolved")}
                      >
                        Resolved
                      </Button>
                      <Button
                        disabled={busy === item.id}
                        onClick={() => void resolve(item.id, "dismissed")}
                      >
                        Dismiss
                      </Button>
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
