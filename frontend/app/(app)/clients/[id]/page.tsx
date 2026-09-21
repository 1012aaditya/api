"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

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
import {
  caseState,
  contactState,
  conversationState,
  deadlineNote,
  requirementState,
} from "@/lib/practice";
import type {
  AgentEvent,
  ComplianceCase,
  Conversation,
  ErasureReceipt,
  PracticeClient,
} from "@/lib/types";

/** The current month, as the API writes a GST period. */
function thisPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export default function ClientPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [client, setClient] = useState<PracticeClient | null>(null);
  const [cases, setCases] = useState<ComplianceCase[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const { user } = useAuth();
  const [erasing, setErasing] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [erased, setErased] = useState<ErasureReceipt | null>(null);
  // Erasure is the most destructive thing the API does, and it refuses
  // a staff login outright. Offering the control anyway would be a
  // button whose only outcome is a 403.
  const mayErase = user?.role === "owner" || user?.role === "admin";
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [period, setPeriod] = useState(thisPeriod);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [one, theirCases, threads, activity] = await Promise.all([
        apiGet<PracticeClient>(`/v1/clients/${id}`),
        apiGet<ComplianceCase[]>(`/v1/cases?client_id=${id}`),
        apiGet<Conversation[]>(`/v1/conversations?client_id=${id}&limit=1`),
        apiGet<AgentEvent[]>(`/v1/agent/activity?client_id=${id}&limit=20`),
      ]);
      setClient(one);
      setCases(theirCases);
      setConversations(threads);
      setEvents(activity);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function patch(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      setClient(await apiSend<PracticeClient>(`/v1/clients/${id}`, body, "PATCH"));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  async function openCase(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiSend<ComplianceCase>("/v1/cases", {
        client_id: id,
        type: "gst",
        period,
      });
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="py-16 text-center">
        <Spinner />
      </div>
    );
  }

  if (!client) {
    return (
      <>
        <PageHeader title="Client" />
        {error && (
          <ErrorNotice
            message={error.message}
            code={error.code}
            requestId={error.requestId}
          />
        )}
      </>
    );
  }

  async function erase() {
    setErasing(true);
    setError(null);
    try {
      setErased(
        await apiDelete<ErasureReceipt>(
          `/v1/privacy/clients/${client!.id}?confirm=${encodeURIComponent(confirmName)}`,
        ),
      );
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setErasing(false);
    }
  }

  const stand = contactState(client.contact_state);
  const thread = conversations[0];

  return (
    <>
      <PageHeader
        title={client.business_name ?? client.name}
        description={[client.client_code, client.gstin, client.whatsapp_phone]
          .filter(Boolean)
          .join(" · ")}
        action={
          <Link href="/clients">
            <Button>All clients</Button>
          </Link>
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
        <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div>
            <StatusBadge status={stand.tone} label={stand.label} />
            <p className="mt-1.5 text-xs text-muted">
              Last contacted {relativeTime(client.last_contacted_at)} · last reply{" "}
              {relativeTime(client.last_response_at)}
            </p>
            {client.automation_paused_reason && (
              <p className="mt-1 text-xs text-ink-2">
                {client.automation_paused_reason}
              </p>
            )}
          </div>
          <Button
            disabled={busy}
            onClick={() =>
              void patch({ allow_automated_contact: !client.allow_automated_contact })
            }
          >
            {client.allow_automated_contact
              ? "Stop contacting them"
              : "Allow the agent to contact them"}
          </Button>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Cases"
              description="One period of work each. The requirements are what the filing needs."
            />
            {cases.length === 0 ? (
              <EmptyState
                title="No cases yet"
                description="Open one below and the agent knows what to ask for."
              />
            ) : (
              <ul className="divide-y divide-line">
                {cases.map((item) => {
                  const look = caseState(item.status);
                  return (
                    <li key={item.id} className="px-5 py-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-ink">{item.label}</p>
                          <p className="mt-0.5 text-xs text-muted">
                            {deadlineNote(item.deadline)}
                            {item.open_exceptions > 0 &&
                              ` · ${item.open_exceptions} exception${
                                item.open_exceptions === 1 ? "" : "s"
                              }`}
                          </p>
                        </div>
                        <StatusBadge status={look.tone} label={look.label} />
                      </div>
                      <ul className="mt-3 space-y-1.5">
                        {item.requirements.map((requirement) => {
                          const state = requirementState(requirement.status);
                          return (
                            <li
                              key={requirement.id}
                              className="flex items-center justify-between gap-3 text-xs"
                            >
                              <span className="text-ink-2">
                                {requirement.label}
                                {!requirement.required && (
                                  <span className="text-muted"> (optional)</span>
                                )}
                              </span>
                              <StatusBadge status={state.tone} label={state.label} />
                            </li>
                          );
                        })}
                      </ul>
                    </li>
                  );
                })}
              </ul>
            )}
            <form
              onSubmit={openCase}
              className="flex items-end gap-3 border-t border-line px-5 py-4"
            >
              <Field label="Open a GST case for">
                <span className="block w-40">
                  <Input
                    required
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                    placeholder="2026-09"
                  />
                </span>
              </Field>
              <Button type="submit" disabled={busy}>
                Open case
              </Button>
            </form>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="WhatsApp"
              description="Everything said to this client, and everything they said back."
              action={
                thread ? (
                  <StatusBadge
                    status={conversationState(thread.status).tone}
                    label={conversationState(thread.status).label}
                  />
                ) : undefined
              }
            />
            {!thread || thread.messages.length === 0 ? (
              <EmptyState
                title="No messages"
                description="Nothing has been sent to this client yet."
              />
            ) : (
              <ul className="space-y-3 px-5 py-4">
                {thread.messages.map((message) => (
                  <li
                    key={message.id}
                    className={
                      message.direction === "outbound" ? "text-right" : "text-left"
                    }
                  >
                    <div
                      className={`inline-block max-w-[85%] rounded-lg px-3 py-2 text-left text-sm ${
                        message.direction === "outbound"
                          ? "bg-surface-sunken text-ink"
                          : "border border-line text-ink"
                      }`}
                    >
                      {message.body ?? <span className="text-muted">A file</span>}
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {message.direction === "outbound"
                        ? message.sent_by_agent
                          ? "Agent"
                          : "You"
                        : "Client"}{" "}
                      · {dateTime(message.created_at)}
                      {message.detected_intent && ` · read as ${message.detected_intent}`}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader title="History" description="What happened, in order." />
            {events.length === 0 ? (
              <EmptyState
                title="Nothing yet"
                description="No activity recorded for this client."
              />
            ) : (
              <ul className="divide-y divide-line">
                {events.map((event) => (
                  <li key={event.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm text-ink">{event.summary}</p>
                      <Badge>{event.actor_type}</Badge>
                    </div>
                    <p className="mt-0.5 font-mono text-xs text-muted">
                      {event.action} · {dateTime(event.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      {erased ? (
        <Card className="mt-6 border-critical/30">
          <CardHeader
            title={`${erased.client_name} has been erased`}
            description="What was removed, so nobody has to take it on trust."
          />
          <div className="space-y-3 px-5 py-4 text-sm text-ink-2">
            <p>
              <span className="tnum font-semibold text-ink">
                {erased.rows_deleted}
              </span>{" "}
              records deleted across{" "}
              {Object.keys(erased.by_table).length} tables, and{" "}
              <span className="tnum font-semibold text-ink">
                {erased.objects_deleted}
              </span>{" "}
              stored file{erased.objects_deleted === 1 ? "" : "s"} removed.
            </p>
            {!erased.complete && (
              <p className="text-critical">
                {erased.objects_failed} stored file
                {erased.objects_failed === 1 ? "" : "s"} could not be deleted. The
                records are gone, but those files are still on disk and need
                removing by hand.
              </p>
            )}
            {Object.keys(erased.unlinked).length > 0 && (
              <p>
                Usage counts were kept with the link to this client removed —
                that is your billing record, and it now identifies nobody.
              </p>
            )}
            <Link href="/clients">
              <Button variant="primary">Back to clients</Button>
            </Link>
          </div>
        </Card>
      ) : (
        mayErase && (
          <Card className="mt-6 border-critical/30">
            <CardHeader
              title="Erase this client"
              description="Everything held about them, including the documents and what was read from them. Immediate, and there is no undo."
            />
            <div className="px-5 py-4">
              <p className="mb-3 text-sm text-ink-2">
                Type{" "}
                <span className="font-medium text-ink">
                  {client.business_name ?? client.name}
                </span>{" "}
                to confirm. Your usage counts are kept, with the link to this
                client removed.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-64">
                  <Input
                    value={confirmName}
                    onChange={(e) => setConfirmName(e.target.value)}
                    placeholder={client.business_name ?? client.name}
                  />
                </span>
                <Button
                  variant="danger"
                  disabled={
                    erasing ||
                    confirmName.trim().toLowerCase() !==
                      (client.business_name ?? client.name).trim().toLowerCase()
                  }
                  onClick={() => void erase()}
                >
                  {erasing ? "Erasing…" : "Erase permanently"}
                </Button>
              </div>
            </div>
          </Card>
        )
      )}
    </>
  );
}
