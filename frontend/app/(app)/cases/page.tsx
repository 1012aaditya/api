"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet } from "@/lib/api";
import { caseState, deadlineNote, outstandingSummary } from "@/lib/practice";
import type { ComplianceCase } from "@/lib/types";

const FILTERS = [
  { value: "", label: "All" },
  { value: "blocked", label: "Waiting on the client" },
  { value: "in_progress", label: "In progress" },
  { value: "ready", label: "Ready to file" },
  { value: "escalated", label: "Needs a person" },
  { value: "completed", label: "Completed" },
];

export default function CasesPage() {
  const [cases, setCases] = useState<ComplianceCase[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (status: string) => {
    setLoading(true);
    setError(null);
    try {
      const query = status ? `&status=${status}` : "";
      setCases(await apiGet<ComplianceCase[]>(`/v1/cases?limit=200${query}`));
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

  return (
    <>
      <PageHeader
        title="Cases"
        description="One period of work for one client. A case is blocked until every required document is in."
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

      <Card>
        <CardHeader title="Open work" description={`${cases.length} shown`} />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : cases.length === 0 ? (
          <EmptyState
            title="Nothing here"
            description="No case matches that filter."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Client</th>
                  <th className="px-3 py-2 font-medium">Period</th>
                  <th className="px-3 py-2 font-medium">Still missing</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-5 py-2 font-medium">Deadline</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {cases.map((item) => {
                  const look = caseState(item.status);
                  return (
                    <tr key={item.id}>
                      <td className="px-5 py-3">
                        <Link
                          href={`/clients/${item.client_id}`}
                          className="text-ink underline-offset-2 hover:underline"
                        >
                          {item.client_name ?? "Client"}
                        </Link>
                      </td>
                      <td className="px-3 py-3 text-ink-2">{item.label}</td>
                      <td className="px-3 py-3 text-ink-2">
                        {outstandingSummary(item.outstanding)}
                        {item.open_exceptions > 0 && (
                          <p className="text-xs text-critical">
                            {item.open_exceptions} exception
                            {item.open_exceptions === 1 ? "" : "s"} open
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        <StatusBadge status={look.tone} label={look.label} />
                      </td>
                      <td className="px-5 py-3 text-xs text-muted">
                        {deadlineNote(item.deadline)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
