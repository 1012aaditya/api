"use client";

import { useCallback, useEffect, useState } from "react";

import { CopyableCommand } from "@/components/CopyableCommand";
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
import { ApiRequestError, apiDelete, apiGet, apiSend } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, relativeTime } from "@/lib/format";
import type {
  CreatedWebhook,
  DeliverySummary,
  WebhookEventName,
  WebhookSummary,
} from "@/lib/types";

const EVENTS: { name: WebhookEventName; label: string; description: string }[] = [
  {
    name: "document.processing",
    label: "document.processing",
    description: "Sent as soon as a document is accepted and queued.",
  },
  {
    name: "document.completed",
    label: "document.completed",
    description: "Extraction finished. Fetch the result to read it.",
  },
  {
    name: "document.failed",
    label: "document.failed",
    description: "Extraction failed after every retry was used.",
  },
];

const DELIVERY_STATUS = {
  delivered: "passed",
  pending: "warning",
  failed: "failed",
} as const;

const DELIVERY_LABEL = {
  delivered: "Delivered",
  pending: "Retrying",
  failed: "Failed",
} as const;

export default function WebhooksPage() {
  const { user } = useAuth();
  const [webhooks, setWebhooks] = useState<WebhookSummary[]>([]);
  const [deliveries, setDeliveries] = useState<DeliverySummary[]>([]);
  const [created, setCreated] = useState<CreatedWebhook | null>(null);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<WebhookEventName[]>([
    "document.completed",
    "document.failed",
  ]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // Reading the endpoints is everyone's; changing them is refused by the
  // API for a staff login, so the controls are not offered either.
  const mayChange = user?.role === "owner" || user?.role === "admin";

  const load = useCallback(async () => {
    try {
      const [hooks, recent] = await Promise.all([
        apiGet<WebhookSummary[]>("/v1/webhooks"),
        apiGet<DeliverySummary[]>("/v1/webhooks/deliveries?limit=50"),
      ]);
      setWebhooks(hooks);
      setDeliveries(recent);
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

  async function act<T>(action: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    const hook = await act(() =>
      apiSend<CreatedWebhook>("/v1/webhooks", {
        url: url.trim(),
        events: selected,
        description: description.trim() || undefined,
      }),
    );
    if (hook) {
      setCreated(hook);
      setUrl("");
      setDescription("");
      await load();
    }
  }

  async function rotate(id: string) {
    if (
      !window.confirm(
        "Rotate this signing secret? Deliveries from now on carry the new signature, with no overlap — update your receiver first.",
      )
    ) {
      return;
    }
    const hook = await act(() => apiSend<CreatedWebhook>(`/v1/webhooks/${id}/rotate`, {}));
    if (hook) {
      setCreated(hook);
      await load();
    }
  }

  async function toggle(hook: WebhookSummary) {
    const action = hook.is_active ? "disable" : "enable";
    if (await act(() => apiSend(`/v1/webhooks/${hook.id}/${action}`, {}))) await load();
  }

  async function remove(id: string) {
    if (!window.confirm("Delete this endpoint? We stop sending to it immediately.")) return;
    if (await act(() => apiDelete(`/v1/webhooks/${id}`))) await load();
  }

  const notConfigured = error?.code === "webhooks_not_configured";

  return (
    <>
      <PageHeader
        title="Webhooks"
        description="Be told when a document finishes, instead of polling for it."
      />

      {error && !notConfigured && (
        <div className="mb-6">
          <ErrorNotice
            message={error.message}
            code={error.code}
            requestId={error.requestId}
          />
        </div>
      )}

      {notConfigured && (
        <Card className="mb-6">
          <div className="p-5 text-sm text-ink-2">
            <p className="font-medium text-ink">Webhooks are not configured.</p>
            <p className="mt-1.5">
              Set <code className="font-mono text-xs text-ink">WEBHOOK_SECRET</code> in
              the backend&apos;s <code className="font-mono text-xs text-ink">.env</code>{" "}
              and restart it. Without it there is nothing to sign deliveries with,
              and an unsigned webhook is worse than none — your receiver would
              have no way to tell our call from anyone else&apos;s.
            </p>
          </div>
        </Card>
      )}

      {created && (
        <Card className="mb-6 border-accent/40">
          <CardHeader
            title="Copy your signing secret now"
            description="It is derived from the deployment secret rather than stored, so this is the only time it is shown."
            action={
              <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>
                Done
              </Button>
            }
          />
          <div className="space-y-3 p-5">
            <CopyableCommand command={created.secret} />
            <p className="text-xs text-muted">
              Verify every delivery against the{" "}
              <code className="font-mono">X-DocuParse-Signature</code> header before
              trusting it. The header is{" "}
              <code className="font-mono">t=&lt;unix&gt;,v1=&lt;hmac&gt;</code>, where
              the HMAC-SHA256 is over{" "}
              <code className="font-mono">&quot;&lt;t&gt;.&quot; + raw body</code>.
              Reject anything older than five minutes.
            </p>
          </div>
        </Card>
      )}

      {mayChange && (
      <Card className="mb-6">
        <CardHeader title="Add an endpoint" />
        <div className="space-y-4 p-5">
          <Field
            label="URL"
            hint="Must be https and publicly reachable. Private, loopback and link-local addresses are refused."
          >
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://api.yourapp.com/hooks/docuparse"
              type="url"
            />
          </Field>

          <Field label="Description" hint="Optional, for your own reference.">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Production receiver"
              maxLength={200}
            />
          </Field>

          <fieldset>
            <legend className="mb-2 text-sm font-medium text-ink">Events</legend>
            <div className="space-y-2">
              {EVENTS.map((event) => (
                <label key={event.name} className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    className="mt-0.5 accent-accent"
                    checked={selected.includes(event.name)}
                    onChange={(e) =>
                      setSelected((current) =>
                        e.target.checked
                          ? [...current, event.name]
                          : current.filter((n) => n !== event.name),
                      )
                    }
                  />
                  <span>
                    <span className="font-mono text-xs text-ink">{event.label}</span>
                    <span className="block text-xs text-muted">{event.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <Button
            variant="primary"
            onClick={() => void create()}
            disabled={busy || !url.trim() || selected.length === 0}
          >
            Add endpoint
          </Button>
        </div>
      </Card>
      )}

      <Card className="mb-6">
        <CardHeader title="Endpoints" />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : webhooks.length === 0 ? (
          <EmptyState
            title="No endpoints yet"
            description="Add one above and we will POST a signed JSON payload whenever a subscribed event happens."
          />
        ) : (
          <ul className="divide-y divide-line">
            {webhooks.map((hook) => (
              <li key={hook.id} className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-ink">{hook.url}</p>
                    {hook.description && (
                      <p className="mt-0.5 text-sm text-ink-2">{hook.description}</p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {hook.events.map((event) => (
                        <Badge key={event}>{event}</Badge>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-muted">
                      Added {relativeTime(hook.created_at)} · last delivery{" "}
                      {relativeTime(hook.last_delivery_at).toLowerCase()}
                      {hook.consecutive_failures > 0 &&
                        ` · ${hook.consecutive_failures} consecutive failures`}
                    </p>
                    {!hook.is_active && hook.disabled_at && (
                      <p className="mt-1 text-xs text-critical">
                        Disabled {dateTime(hook.disabled_at)}. Re-enable once the
                        endpoint is healthy.
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <StatusBadge
                      status={hook.is_active ? "passed" : "not_checked"}
                      label={hook.is_active ? "Active" : "Disabled"}
                    />
                    {mayChange && (
                      <>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void rotate(hook.id)}>
                          Rotate secret
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void toggle(hook)}>
                          {hook.is_active ? "Disable" : "Enable"}
                        </Button>
                        <Button size="sm" variant="danger" disabled={busy} onClick={() => void remove(hook.id)}>
                          Delete
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Recent deliveries"
          description="Failed deliveries are retried with exponential backoff, up to the attempt limit."
          action={
            <Button size="sm" variant="ghost" onClick={() => void load()}>
              Refresh
            </Button>
          }
        />
        {deliveries.length === 0 ? (
          <EmptyState
            title="Nothing delivered yet"
            description="Deliveries appear here once a subscribed event fires."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Event</th>
                  <th className="px-3 py-2 font-medium">Job</th>
                  <th className="px-3 py-2 text-right font-medium">Attempts</th>
                  <th className="px-3 py-2 text-right font-medium">HTTP</th>
                  <th className="px-5 py-2 text-right font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {deliveries.map((delivery) => (
                  <tr key={delivery.id}>
                    <td className="px-5 py-2.5">
                      <StatusBadge
                        status={DELIVERY_STATUS[delivery.status]}
                        label={DELIVERY_LABEL[delivery.status]}
                      />
                      {delivery.error && (
                        <p className="mt-1 text-xs text-critical">{delivery.error}</p>
                      )}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-ink-2">
                      {delivery.event}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-muted">
                      {delivery.job_id ?? "—"}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                      {delivery.attempts} / {delivery.max_attempts}
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-xs text-ink-2">
                      {delivery.response_status ?? "—"}
                    </td>
                    <td className="px-5 py-2.5 text-right text-xs text-muted">
                      {relativeTime(delivery.created_at)}
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
