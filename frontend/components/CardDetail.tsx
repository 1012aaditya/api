"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Badge, Button, Spinner } from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type {
  AgentRunResult,
  BoardCard,
  ComplianceCase,
  Conversation,
  PracticeClient,
  ReviewException,
  PracticeTask,
} from "@/lib/types";

/**
 * A card that has been opened, still standing where it stood.
 *
 * This is the same component the board draws collapsed, grown: it keeps the
 * client's place in their column, so opening one never costs you the sense
 * of where they are. Nothing docks, nothing slides in from the side, and
 * the cards around it move out of its way rather than being covered by it.
 *
 * The rule every action follows: after it runs, reload this panel *and* the
 * board. An action whose effect you cannot see is an action you do twice —
 * and on a board where position means state, a card sitting in the wrong
 * column after you fixed it is the interface lying.
 */

const REQUIREMENT_TONE: Record<string, string> = {
  missing: "text-critical",
  requested: "text-ink-2",
  received: "text-ink",
  processing: "text-ink-2",
  valid: "text-good-ink",
  invalid: "text-critical",
  needs_review: "text-critical",
  waived: "text-muted",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 first:mt-0">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

export function CardDetail({
  card,
  width,
  height,
  onClose,
  onChanged,
}: {
  card: BoardCard;
  width: number;
  height: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [client, setClient] = useState<PracticeClient | null>(null);
  const [kase, setCase] = useState<ComplianceCase | null>(null);
  const [thread, setThread] = useState<Conversation | null>(null);
  const [tasks, setTasks] = useState<PracticeTask[]>([]);
  const [exceptions, setExceptions] = useState<ReviewException[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, threads, allTasks, allExceptions] = await Promise.all([
        apiGet<PracticeClient>(`/v1/clients/${card.client_id}`),
        apiGet<Conversation[]>(`/v1/conversations?client_id=${card.client_id}&limit=1`),
        apiGet<PracticeTask[]>("/v1/tasks?limit=100"),
        apiGet<ReviewException[]>("/v1/exceptions?limit=100"),
      ]);
      setClient(c);
      setThread(threads[0] ?? null);
      setTasks(allTasks.filter((t) => t.client_id === card.client_id));
      setExceptions(allExceptions.filter((e) => e.client_id === card.client_id));
      setCase(
        card.case_id ? await apiGet<ComplianceCase>(`/v1/cases/${card.case_id}`) : null,
      );
    } catch (caught) {
      if (caught instanceof ApiRequestError) setNote(caught.message);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, [card.client_id, card.case_id]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Every action goes through here, so every action refreshes the board. */
  async function act(key: string, run: () => Promise<string | null>) {
    setBusy(key);
    setNote(null);
    try {
      setNote(await run());
      await load();
      onChanged();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setNote(caught.message);
      else throw caught;
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    const body = draft.trim();
    if (!body || busy !== null) return;
    await act("send", async () => {
      await apiSend(`/v1/clients/${card.client_id}/messages`, {
        body,
        case_id: card.case_id,
      });
      setDraft("");
      return "Sent.";
    });
  }

  const requirements = kase?.requirements ?? [];

  return (
    <div
      data-card
      data-detail
      role="dialog"
      aria-label={`${card.name} details`}
      style={{ width, height }}
      className="flex flex-col overflow-hidden rounded-xl border border-accent bg-surface text-left shadow-2xl ring-4 ring-accent-100"
    >
      {/* --- the head of the card, grown ------------------------------- */}
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold text-ink">{card.name}</h2>
          <p className="mt-0.5 text-sm text-ink-2">{card.reason}</p>
        </div>
        {card.period && (
          <p className="shrink-0 pt-0.5 text-right font-mono text-xs text-muted">
            {card.period}
            {card.days_left !== null && (
              <span
                className={`mt-0.5 block ${card.days_left <= 3 ? "text-critical" : "text-muted"}`}
              >
                {card.days_left < 0
                  ? `${Math.abs(card.days_left)} d overdue`
                  : `${card.days_left} d left`}
              </span>
            )}
          </p>
        )}
        <button
          className="-mr-1 shrink-0 rounded px-1.5 text-sm text-muted hover:text-ink"
          onClick={onClose}
          aria-label="Close"
        >
          ✕
        </button>
      </header>

      {note && (
        <p className="border-b border-line bg-surface-sunken px-4 py-2 text-sm text-ink-2">
          {note}
        </p>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          {/* --- what you can do about them --------------------------- */}
          <div data-scroll className="min-w-0 flex-1 overflow-y-auto px-4 py-3">
            <Section title="Chase">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={busy !== null || !card.case_id}
                  onClick={() =>
                    act("dry", async () => {
                      const r = await apiSend<AgentRunResult>("/v1/agent/run", {
                        case_id: card.case_id,
                        dry_run: true,
                      });
                      return r.scheduled > 0
                        ? "It would message them. Nothing was sent."
                        : r.skipped[0] ?? "It would not message them right now.";
                    })
                  }
                >
                  {busy === "dry" ? "…" : "What would it do?"}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy !== null || !card.case_id}
                  onClick={() =>
                    act("chase", async () => {
                      const r = await apiSend<AgentRunResult>("/v1/agent/run", {
                        case_id: card.case_id,
                        dry_run: false,
                      });
                      return r.sent > 0 ? "Message sent." : r.skipped[0] ?? "Nothing was sent.";
                    })
                  }
                >
                  {busy === "chase" ? "…" : "Chase now"}
                </Button>
                <Button
                  size="sm"
                  variant={client?.allow_automated_contact ? "danger" : "secondary"}
                  disabled={busy !== null}
                  onClick={() =>
                    act("pause", async () => {
                      const on = !client?.allow_automated_contact;
                      await apiSend<PracticeClient>(
                        `/v1/clients/${card.client_id}`,
                        { allow_automated_contact: on },
                        "PATCH",
                      );
                      return on
                        ? "Automated messages are on again."
                        : "Automated messages are off for this client.";
                    })
                  }
                >
                  {client?.allow_automated_contact ? "Stop contacting" : "Resume"}
                </Button>
              </div>
            </Section>

            {requirements.length > 0 && (
              <Section title="What this filing needs">
                <ul className="space-y-1">
                  {requirements.map((requirement) => {
                    const settled = ["valid", "received", "processing", "waived"].includes(
                      requirement.status,
                    );
                    return (
                      <li
                        key={requirement.id}
                        className="flex items-center justify-between gap-2 rounded-md px-1 py-0.5 hover:bg-surface-sunken"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-ink">
                            {requirement.label}
                          </span>
                          <span
                            className={`text-xs ${REQUIREMENT_TONE[requirement.status] ?? "text-muted"}`}
                          >
                            {requirement.status.replace(/_/g, " ")}
                          </span>
                        </span>
                        {!settled && (
                          <span className="flex shrink-0 gap-1">
                            <Button
                              size="sm"
                              disabled={busy !== null}
                              onClick={() =>
                                act(`got-${requirement.id}`, async () => {
                                  await apiSend(
                                    `/v1/requirements/${requirement.id}`,
                                    { status: "received" },
                                    "PATCH",
                                  );
                                  return `${requirement.label} marked as arrived.`;
                                })
                              }
                            >
                              Got it
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy !== null}
                              onClick={() =>
                                act(`waive-${requirement.id}`, async () => {
                                  await apiSend(
                                    `/v1/requirements/${requirement.id}`,
                                    { status: "waived" },
                                    "PATCH",
                                  );
                                  return `${requirement.label} is no longer needed.`;
                                })
                              }
                            >
                              Not needed
                            </Button>
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </Section>
            )}

            {exceptions.length > 0 && (
              <Section title="Needs a person">
                <ul className="space-y-2">
                  {exceptions.map((item) => (
                    <li key={item.id} className="rounded-md border border-line p-2.5">
                      <p className="text-sm text-ink">{item.message}</p>
                      <Button
                        size="sm"
                        className="mt-2"
                        disabled={busy !== null}
                        onClick={() =>
                          act(`resolve-${item.id}`, async () => {
                            await apiSend(`/v1/exceptions/${item.id}/resolve`, {
                              resolution: "Handled from the board.",
                            });
                            return "Closed.";
                          })
                        }
                      >
                        I&apos;ve handled this
                      </Button>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {tasks.length > 0 && (
              <Section title="Your tasks">
                <ul className="space-y-1">
                  {tasks.map((task) => (
                    <li key={task.id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-sm text-ink">{task.title}</span>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() =>
                          act(`done-${task.id}`, async () => {
                            await apiSend(`/v1/tasks/${task.id}/complete`, {});
                            return "Done.";
                          })
                        }
                      >
                        Done
                      </Button>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <Section title="Where things stand">
              <dl className="space-y-1 text-sm">
                {[
                  ["Last asked", relativeTime(card.last_contacted_at)],
                  ["Last reply", relativeTime(card.last_response_at)],
                  ["GSTIN", client?.gstin ?? "—"],
                  ["WhatsApp", client?.whatsapp_phone ?? "—"],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-3">
                    <dt className="text-ink-2">{label}</dt>
                    <dd className="truncate text-ink">{value}</dd>
                  </div>
                ))}
              </dl>
            </Section>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Badge>{card.zone.replace(/_/g, " ")}</Badge>
              <Link href={`/clients/${card.client_id}`} className="ml-auto">
                <Button size="sm" variant="ghost">
                  Full page
                </Button>
              </Link>
            </div>
          </div>

          {/* --- what they have said ---------------------------------- */}
          <div className="flex w-[17rem] shrink-0 flex-col border-l border-line bg-surface-sunken">
            <p className="px-4 pt-3 text-xs font-medium uppercase tracking-wide text-muted">
              WhatsApp
            </p>
            <div data-scroll className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-4 py-2">
              {thread && thread.messages.length > 0 ? (
                thread.messages.slice(-12).map((message) => (
                  <div
                    key={message.id}
                    className={`rounded-md px-2.5 py-1.5 text-sm ${
                      message.direction === "outbound"
                        ? "ml-4 bg-accent-100 text-ink"
                        : "mr-4 border border-line bg-surface text-ink"
                    }`}
                  >
                    {message.body}
                    <span className="mt-0.5 block text-[11px] text-muted">
                      {message.direction === "outbound"
                        ? message.sent_by_agent
                          ? "the agent"
                          : "you"
                        : "them"}{" "}
                      · {relativeTime(message.created_at)}
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-ink-2">Nothing has been sent yet.</p>
              )}
            </div>
            <div className="flex gap-1.5 border-t border-line p-2.5">
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void send();
                }}
                placeholder="Write to them yourself…"
                className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
              />
              <Button size="sm" disabled={busy !== null || !draft.trim()} onClick={() => void send()}>
                Send
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
