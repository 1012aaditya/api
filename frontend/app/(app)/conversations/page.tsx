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
import { dateTime, relativeTime } from "@/lib/format";
import { conversationState } from "@/lib/practice";
import type { Conversation } from "@/lib/types";

export default function ConversationsPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const threads = await apiGet<Conversation[]>("/v1/conversations?limit=50");
      setConversations(threads);
      setOpenId((current) => current ?? threads[0]?.id ?? null);
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

  const open = conversations.find((thread) => thread.id === openId) ?? null;

  return (
    <>
      <PageHeader
        title="Conversations"
        description="Every WhatsApp thread, and what the agent understood from each reply."
        action={
          <Button onClick={() => void load()} disabled={loading}>
            Refresh
          </Button>
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

      {loading ? (
        <Card>
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        </Card>
      ) : conversations.length === 0 ? (
        <Card>
          <EmptyState
            title="No conversations yet"
            description="Once the agent messages a client, the thread appears here."
          />
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
          <Card className="h-fit">
            <CardHeader title="Threads" description={`${conversations.length} open`} />
            <ul className="divide-y divide-line">
              {conversations.map((thread) => {
                const look = conversationState(thread.status);
                return (
                  <li key={thread.id}>
                    <button
                      type="button"
                      onClick={() => setOpenId(thread.id)}
                      aria-current={thread.id === openId ? "true" : undefined}
                      className={`block w-full px-5 py-3 text-left transition-colors ${
                        thread.id === openId
                          ? "bg-surface-sunken"
                          : "hover:bg-surface-sunken"
                      }`}
                    >
                      <p className="truncate text-sm text-ink">
                        {thread.client_name ?? "Client"}
                      </p>
                      <p className="mt-1 text-xs text-muted">
                        {relativeTime(thread.last_message_at)}
                      </p>
                      <span className="mt-1.5 inline-block">
                        <StatusBadge status={look.tone} label={look.label} />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>

          <Card>
            {open === null ? (
              <EmptyState title="Pick a thread" description="Choose a client on the left." />
            ) : (
              <>
                <CardHeader
                  title={open.client_name ?? "Client"}
                  description={`${open.channel} · ${open.messages.length} messages`}
                  action={
                    <Link href={`/clients/${open.client_id}`}>
                      <Button size="sm">Open client</Button>
                    </Link>
                  }
                />
                <ul className="space-y-3 px-5 py-4">
                  {open.messages.map((message) => (
                    <li
                      key={message.id}
                      className={
                        message.direction === "outbound" ? "text-right" : "text-left"
                      }
                    >
                      <div
                        className={`inline-block max-w-[80%] rounded-lg px-3 py-2 text-left text-sm ${
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
                        {message.detected_intent &&
                          ` · read as ${message.detected_intent.replace(/_/g, " ")}`}
                      </p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
