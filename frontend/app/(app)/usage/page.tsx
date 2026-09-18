"use client";

import { useCallback, useEffect, useState } from "react";

import { QuotaMeter } from "@/components/QuotaMeter";
import { RequestsChart } from "@/components/RequestsChart";
import { StatTile } from "@/components/StatTile";
import {
  Badge,
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
import { compactNumber, duration, percent, relativeTime, usd } from "@/lib/format";
import type { UsageEvent, UsageSummary } from "@/lib/types";

const WINDOWS = [7, 30, 90] as const;

export default function UsagePage() {
  const [days, setDays] = useState<number>(30);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [summary, recent] = await Promise.all([
        apiGet<UsageSummary>(`/v1/usage?days=${days}`),
        apiGet<UsageEvent[]>("/v1/usage/events?limit=100"),
      ]);
      setUsage(summary);
      setEvents(recent);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Usage"
        description="Every authenticated request is recorded — successes and failures alike."
        action={
          /* Filters sit in one row above the charts. */
          <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
            {WINDOWS.map((window) => (
              <button
                key={window}
                type="button"
                onClick={() => setDays(window)}
                aria-pressed={days === window}
                className={`rounded px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  days === window
                    ? "bg-surface-sunken text-ink"
                    : "text-ink-2 hover:text-ink"
                }`}
              >
                {window} days
              </button>
            ))}
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

      {loading && !usage ? (
        <div className="py-16 text-center">
          <Spinner />
        </div>
      ) : usage ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Requests"
              value={compactNumber(usage.totals.requests)}
              caption={`Last ${usage.window_days} days`}
            />
            <StatTile
              label="Successful"
              value={compactNumber(usage.totals.successful_requests)}
              caption={percent(usage.success_rate, 1) + " of requests"}
            />
            <StatTile
              label="Pages processed"
              value={compactNumber(usage.totals.pages)}
              caption="Across all documents"
            />
            <StatTile
              label="Average latency"
              value={duration(usage.totals.average_duration_ms)}
              caption="End to end"
            />
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-3 lg:items-start">
            <Card className="lg:col-span-2">
              <CardHeader
                title="Requests per day"
                description={`Succeeded and failed, over the last ${usage.window_days} days.`}
              />
              <RequestsChart daily={usage.daily} />
            </Card>

            <Card>
              <CardHeader title="Monthly quota" />
              <QuotaMeter
                used={usage.quota.documents_used}
                quota={usage.quota.monthly_quota}
                periodStart={usage.quota.period_start}
              />
              <div className="border-t border-line px-5 py-3 text-xs text-muted">
                Only requests that reached the provider count. A file rejected
                for its type or size does not consume quota.
              </div>
            </Card>
          </div>

          <Card className="mt-6">
            <CardHeader
              title="Request log"
              description="Newest first. Quote a request id in any support conversation."
            />
            {events.length === 0 ? (
              <EmptyState
                title="No requests recorded"
                description="Usage appears here as soon as you call the API."
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-muted">
                    <tr className="border-b border-line">
                      <th className="px-5 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Endpoint</th>
                      <th className="px-3 py-2 font-medium">Source</th>
                      <th className="px-3 py-2 font-medium">Request id</th>
                      <th className="px-3 py-2 text-right font-medium">Pages</th>
                      <th className="px-3 py-2 text-right font-medium">Latency</th>
                      <th className="px-3 py-2 text-right font-medium">Cost</th>
                      <th className="px-5 py-2 text-right font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {events.map((event) => (
                      <tr key={event.id}>
                        <td className="px-5 py-2.5">
                          <StatusBadge
                        status={event.success ? "passed" : "failed"}
                        label={event.success ? "Succeeded" : "Failed"}
                      />
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-ink-2">
                          {event.endpoint}
                          {event.error_code && (
                            <span className="text-critical"> · {event.error_code}</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge>
                            {event.event_type === "dashboard_request"
                              ? "Dashboard"
                              : "API key"}
                          </Badge>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-muted">
                          {event.request_id ?? "—"}
                        </td>
                        <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                          {event.pages || "—"}
                        </td>
                        <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                          {duration(event.duration_ms)}
                        </td>
                        <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                          {usd(event.estimated_cost_usd)}
                        </td>
                        <td className="px-5 py-2.5 text-right text-xs text-muted">
                          {relativeTime(event.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <p className="mt-4 text-xs text-muted">
            Cost is our estimate from the token rates configured on the backend,
            for your own tracking. It is not a bill from your provider, and it
            shows as “—” when no rates are set.
          </p>
        </>
      ) : null}

      {loading && usage && (
        <div className="mt-4">
          <Spinner label="Refreshing" />
        </div>
      )}

      {!loading && !usage && !error && (
        <Button onClick={() => void load()}>Reload</Button>
      )}
    </>
  );
}
