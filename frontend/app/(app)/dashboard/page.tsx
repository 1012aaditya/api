"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { RequestsChart } from "@/components/RequestsChart";
import { QuotaMeter } from "@/components/QuotaMeter";
import { StatTile } from "@/components/StatTile";
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
import { compactNumber, duration, percent, relativeTime, usd } from "@/lib/format";
import type { UsageEvent, UsageSummary } from "@/lib/types";

export default function DashboardPage() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [summary, recent] = await Promise.all([
        apiGet<UsageSummary>("/v1/usage?days=30"),
        apiGet<UsageEvent[]>("/v1/usage/events?limit=8"),
      ]);
      setUsage(summary);
      setEvents(recent);
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

  if (error) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <ErrorNotice
          title="Could not load usage"
          message={error.message}
          code={error.code}
          requestId={error.requestId}
        />
        <Button onClick={() => void load()} className="mt-4">
          Try again
        </Button>
      </>
    );
  }

  if (!usage) return null;

  const { totals, quota, daily, success_rate: successRate } = usage;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="The last 30 days of API activity for this organization."
        action={
          <Link href="/playground">
            <Button variant="primary">Open playground</Button>
          </Link>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Requests"
          value={compactNumber(totals.requests)}
          caption="Last 30 days"
        />
        <StatTile
          label="Documents processed"
          value={compactNumber(quota.documents_used)}
          caption="This billing month"
        />
        <StatTile
          label="Success rate"
          value={percent(successRate, 1)}
          caption={
            totals.requests === 0
              ? "No requests yet"
              : `${totals.failed_requests} failed`
          }
        />
        <StatTile
          label="Average latency"
          value={duration(totals.average_duration_ms)}
          caption="End to end, per request"
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Requests per day"
            description="Succeeded and failed, over the last 30 days."
          />
          <RequestsChart daily={daily} />
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Quota" />
            <QuotaMeter
              used={quota.documents_used}
              quota={quota.monthly_quota}
              periodStart={quota.period_start}
            />
            <div className="border-t border-line px-5 py-3 text-xs text-muted">
              Rate limit: {quota.rate_limit_per_minute} requests / minute.
            </div>
          </Card>

          <Card>
            <CardHeader title="Estimated provider cost" />
            <div className="px-5 py-4">
              <p className="text-2xl font-semibold leading-none text-ink">
                {totals.estimated_cost_usd > 0
                  ? usd(totals.estimated_cost_usd)
                  : "—"}
              </p>
              <p className="mt-2 text-xs text-muted">
                {totals.estimated_cost_usd > 0
                  ? "Our estimate from configured token rates, over 30 days. Not a bill from your provider."
                  : "No token rates configured, so cost is unknown — which is not the same as free. Set AI_INPUT_COST_PER_MTOK and AI_OUTPUT_COST_PER_MTOK."}
              </p>
            </div>
          </Card>
        </div>
      </div>

      <Card className="mt-6">
        <CardHeader
          title="Recent requests"
          action={
            <Link
              href="/usage"
              className="text-xs text-ink-2 underline underline-offset-2 hover:text-ink"
            >
              View all
            </Link>
          }
        />
        {events.length === 0 ? (
          <EmptyState
            title="No requests yet"
            description="Once you extract an invoice — from the playground or your own code — every request shows up here with its request id."
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
                  <th className="px-5 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Endpoint</th>
                  <th className="px-3 py-2 font-medium">Request id</th>
                  <th className="px-3 py-2 text-right font-medium">Latency</th>
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
                    <td className="px-3 py-2.5 font-mono text-xs text-muted">
                      {event.request_id ?? "—"}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                      {duration(event.duration_ms)}
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
    </>
  );
}
