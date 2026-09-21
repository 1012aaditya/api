"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Spinner } from "@/components/ui";
import { ApiRequestError, apiDelete, apiGet, apiSend } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { relativeTime } from "@/lib/format";
import type {
  AgentRunResult,
  BoardCard,
  ComplianceCase,
  Conversation,
  ErasureReceipt,
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
 * Everything a firm does to one client is in here — chase them, open or
 * close a filing, change what it asks for, correct their details, stop
 * contacting them, erase them — because a board you have to leave to do
 * half the work is a menu with extra steps.
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

/** What a case may ask for. The labels are the CA's words, not the API's. */
const DOCUMENT_TYPES: [string, string][] = [
  ["purchase_invoice", "Purchase invoices"],
  ["sales_invoice", "Sales invoices"],
  ["bank_statement", "Bank statement"],
  ["credit_note", "Credit notes"],
  ["debit_note", "Debit notes"],
  ["gstr_2b", "GSTR-2B"],
  ["gst_certificate", "GST certificate"],
  ["pan", "PAN"],
  ["aadhaar", "Aadhaar"],
  ["itr", "ITR"],
  ["tds_certificate", "TDS certificate"],
  ["other", "Other"],
];

const CASE_TYPES: [string, string][] = [
  ["gst", "GST"],
  ["itr", "Income tax"],
  ["tds", "TDS"],
];

/** This month, as the period a filing is usually for. */
function thisPeriod() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 first:mt-0">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function Editable({
  label,
  value,
  onChange,
  placeholder,
  upper,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  upper?: boolean;
}) {
  return (
    <label className="text-xs text-muted">
      {label}
      <input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(upper ? e.target.value.toUpperCase() : e.target.value)}
        className="mt-0.5 w-full rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
      />
    </label>
  );
}

type Tab = "do" | "details";

export function CardDetail({
  card,
  width,
  height,
  onClose,
  onChanged,
  onNotice,
}: {
  card: BoardCard;
  width: number;
  height: number;
  onClose: () => void;
  onChanged: () => void;
  /** Something to say after this card is gone from the board. */
  onNotice: (text: string) => void;
}) {
  const { user } = useAuth();
  const privileged = user?.role === "owner" || user?.role === "admin";

  const [client, setClient] = useState<PracticeClient | null>(null);
  const [kase, setCase] = useState<ComplianceCase | null>(null);
  const [thread, setThread] = useState<Conversation | null>(null);
  const [tasks, setTasks] = useState<PracticeTask[]>([]);
  const [exceptions, setExceptions] = useState<ReviewException[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<Tab>("do");

  // The editable copy of their details, and the erasure confirmation.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState("");

  // A new filing, and a new requirement on the one they have.
  const [newCase, setNewCase] = useState({
    type: "gst",
    period: thisPeriod(),
    deadline: "",
  });
  const [wanted, setWanted] = useState("bank_statement");
  const [deadline, setDeadline] = useState("");

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
      setEdits({
        name: c.name ?? "",
        business_name: c.business_name ?? "",
        whatsapp_phone: c.whatsapp_phone ?? "",
        phone: c.phone ?? "",
        email: c.email ?? "",
        gstin: c.gstin ?? "",
        pan: c.pan ?? "",
      });
      setThread(threads[0] ?? null);
      setTasks(allTasks.filter((t) => t.client_id === card.client_id));
      setExceptions(allExceptions.filter((e) => e.client_id === card.client_id));
      // The board points at the open filing, and stops pointing at one the
      // moment it is filed — so the card asks for the client's cases too.
      // Otherwise "mark it filed" would be a door that locks behind you:
      // the case would vanish from the card that had just changed it.
      const cases = await apiGet<ComplianceCase[]>(
        `/v1/cases?client_id=${card.client_id}&limit=20`,
      );
      const chosen =
        (card.case_id && cases.find((one) => one.id === card.case_id)) ||
        cases.find((one) => one.status !== "completed") ||
        cases[0] ||
        null;
      const loaded = chosen
        ? await apiGet<ComplianceCase>(`/v1/cases/${chosen.id}`)
        : null;
      setCase(loaded);
      setDeadline(loaded?.deadline ?? "");
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

  /**
   * Erasure cannot go through ``act``.
   *
   * Every other action reloads this card afterwards; this one deletes the
   * client the card is about, so reloading would fetch a 404 and replace
   * the receipt with "no client with that id". The receipt goes to the
   * board instead, which is still there to show it.
   */
  async function erase() {
    setBusy("erase");
    setNote(null);
    try {
      const receipt = await apiDelete<ErasureReceipt>(
        `/v1/privacy/clients/${card.client_id}?confirm=${encodeURIComponent(confirm.trim())}`,
      );
      onNotice(
        `${receipt.client_name}: ${receipt.rows_deleted} rows and ` +
          `${receipt.objects_deleted} files erased.` +
          (receipt.complete ? "" : " Some files could not be deleted — check the logs."),
      );
      onClose();
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
  const asked = new Set(requirements.map((r) => r.document_type));
  const available = DOCUMENT_TYPES.filter(([type]) => !asked.has(type));
  const filed = kase?.status === "completed";

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

        <div className="flex shrink-0 gap-0.5 rounded-md bg-surface-sunken p-0.5">
          {(
            [
              ["do", "What to do"],
              ["details", "Their details"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              aria-pressed={tab === key}
              onClick={() => setTab(key)}
              className={`rounded px-2 py-1 text-xs transition-colors ${
                tab === key ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2"
              }`}
            >
              {label}
            </button>
          ))}
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
          <div data-scroll className="min-w-0 flex-1 overflow-y-auto px-4 py-3">
            {tab === "do" ? (
              <>
                {/* --- chase them --------------------------------------- */}
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
                          return r.sent > 0
                            ? "Message sent."
                            : r.skipped[0] ?? "Nothing was sent.";
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

                {/* --- the filing --------------------------------------- */}
                {kase && (
                  <Section title="This filing">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="text-ink">{kase.label}</span>
                      <Badge>{kase.status.replace(/_/g, " ")}</Badge>
                      <input
                        type="date"
                        value={deadline}
                        onChange={(e) => setDeadline(e.target.value)}
                        aria-label="Deadline"
                        className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink focus:border-accent focus:outline-none"
                      />
                      {deadline !== (kase.deadline ?? "") && deadline !== "" && (
                        <Button
                          size="sm"
                          disabled={busy !== null}
                          onClick={() =>
                            act("deadline", async () => {
                              await apiSend(
                                `/v1/cases/${kase.id}`,
                                { deadline },
                                "PATCH",
                              );
                              return `The date is now ${deadline}.`;
                            })
                          }
                        >
                          Move the date
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant={filed ? "secondary" : "primary"}
                        disabled={busy !== null}
                        onClick={() =>
                          act("filed", async () => {
                            await apiSend(
                              `/v1/cases/${kase.id}`,
                              { filed: !filed },
                              "PATCH",
                            );
                            return filed
                              ? "Put back as unfiled. Its state is worked out from the documents again."
                              : "Marked as filed.";
                          })
                        }
                      >
                        {filed ? "It isn't filed after all" : "Mark it filed"}
                      </Button>
                    </div>
                  </Section>
                )}

                {(!kase || filed) && (
                  <Section title={kase ? "Start the next one" : "No filing is open"}>
                    <div className="flex flex-wrap items-end gap-2">
                      <label className="text-xs text-muted">
                        What for
                        <select
                          value={newCase.type}
                          onChange={(e) => setNewCase({ ...newCase, type: e.target.value })}
                          className="mt-0.5 block rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                        >
                          {CASE_TYPES.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="text-xs text-muted">
                        Period
                        <input
                          value={newCase.period}
                          onChange={(e) =>
                            setNewCase({ ...newCase, period: e.target.value })
                          }
                          className="mt-0.5 block w-24 rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                        />
                      </label>
                      <label className="text-xs text-muted">
                        Due
                        <input
                          type="date"
                          value={newCase.deadline}
                          onChange={(e) =>
                            setNewCase({ ...newCase, deadline: e.target.value })
                          }
                          className="mt-0.5 block rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                        />
                      </label>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={busy !== null || !newCase.period.trim()}
                        onClick={() =>
                          act("open-case", async () => {
                            const opened = await apiSend<ComplianceCase>("/v1/cases", {
                              client_id: card.client_id,
                              type: newCase.type,
                              period: newCase.period.trim(),
                              ...(newCase.deadline ? { deadline: newCase.deadline } : {}),
                            });
                            return `${opened.label} opened, asking for ${opened.outstanding.length} documents.`;
                          })
                        }
                      >
                        Start it
                      </Button>
                    </div>
                  </Section>
                )}

                {/* --- what it asks for --------------------------------- */}
                {kase && (
                  <Section title="What this filing needs">
                    <ul className="space-y-1">
                      {requirements.map((requirement) => {
                        const settled = ["valid", "received", "processing", "waived"].includes(
                          requirement.status,
                        );
                        const removable =
                          !requirement.received_document_id &&
                          ["missing", "requested"].includes(requirement.status);
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
                            <span className="flex shrink-0 items-center gap-1">
                              {!settled && (
                                <>
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
                                </>
                              )}
                              {removable && (
                                <button
                                  aria-label={`Stop asking for ${requirement.label}`}
                                  disabled={busy !== null}
                                  onClick={() =>
                                    act(`drop-${requirement.id}`, async () => {
                                      await apiDelete(
                                        `/v1/requirements/${requirement.id}`,
                                      );
                                      return `${requirement.label} is off the list.`;
                                    })
                                  }
                                  className="rounded px-1 text-xs text-muted hover:text-critical"
                                >
                                  ✕
                                </button>
                              )}
                            </span>
                          </li>
                        );
                      })}
                    </ul>

                    {available.length > 0 && (
                      <div className="mt-2 flex items-center gap-1.5">
                        <select
                          value={wanted}
                          onChange={(e) => setWanted(e.target.value)}
                          aria-label="Another document to ask for"
                          className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                        >
                          {available.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <Button
                          size="sm"
                          disabled={busy !== null}
                          onClick={() =>
                            act("ask", async () => {
                              await apiSend(`/v1/cases/${kase.id}/requirements`, {
                                document_type: wanted,
                              });
                              return "Added to what they owe.";
                            })
                          }
                        >
                          Also ask for this
                        </Button>
                      </div>
                    )}
                  </Section>
                )}

                {/* --- what needs a person ------------------------------ */}
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

                {/* --- tasks -------------------------------------------- */}
                {tasks.length > 0 && (
                  <Section title="Your tasks">
                    <ul className="space-y-1">
                      {tasks.map((task) => (
                        <li
                          key={task.id}
                          className="flex items-center justify-between gap-2"
                        >
                          <span className="min-w-0 truncate text-sm text-ink">
                            {task.title}
                          </span>
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
                      ["On the board", card.zone.replace(/_/g, " ")],
                    ].map(([label, value]) => (
                      <div key={label} className="flex justify-between gap-3">
                        <dt className="text-ink-2">{label}</dt>
                        <dd className="truncate text-ink">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </Section>
              </>
            ) : (
              /* --- their details ------------------------------------- */
              <>
                <Section title="Their details">
                  <div className="grid grid-cols-2 gap-2">
                    <Editable
                      label="Name"
                      value={edits.name ?? ""}
                      onChange={(v) => setEdits({ ...edits, name: v })}
                    />
                    <Editable
                      label="Registered name"
                      value={edits.business_name ?? ""}
                      onChange={(v) => setEdits({ ...edits, business_name: v })}
                    />
                    <Editable
                      label="WhatsApp number"
                      value={edits.whatsapp_phone ?? ""}
                      onChange={(v) => setEdits({ ...edits, whatsapp_phone: v })}
                      placeholder="+9198…"
                    />
                    <Editable
                      label="Other phone"
                      value={edits.phone ?? ""}
                      onChange={(v) => setEdits({ ...edits, phone: v })}
                    />
                    <Editable
                      label="GSTIN"
                      value={edits.gstin ?? ""}
                      onChange={(v) => setEdits({ ...edits, gstin: v })}
                      upper
                    />
                    <Editable
                      label="PAN"
                      value={edits.pan ?? ""}
                      onChange={(v) => setEdits({ ...edits, pan: v })}
                      upper
                    />
                    <Editable
                      label="Email"
                      value={edits.email ?? ""}
                      onChange={(v) => setEdits({ ...edits, email: v })}
                    />
                    <div className="flex items-end">
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={busy !== null}
                        onClick={() =>
                          act("save", async () => {
                            // Only what actually changed, so a blank field
                            // never quietly wipes something it did not touch.
                            const body = Object.fromEntries(
                              Object.entries(edits).filter(
                                ([key, value]) =>
                                  value.trim() !==
                                  ((client?.[key as keyof PracticeClient] as string | null) ??
                                    ""),
                              ),
                            );
                            if (Object.keys(body).length === 0) return "Nothing changed.";
                            await apiSend<PracticeClient>(
                              `/v1/clients/${card.client_id}`,
                              body,
                              "PATCH",
                            );
                            return "Saved.";
                          })
                        }
                      >
                        Save
                      </Button>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-muted">
                    Their code {client?.client_code ?? "—"} · with you since{" "}
                    {relativeTime(client?.created_at ?? null)}
                  </p>
                </Section>

                <Section title="If they have left">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      disabled={busy !== null}
                      onClick={() =>
                        act("status", async () => {
                          const active = client?.status === "active";
                          await apiSend<PracticeClient>(
                            `/v1/clients/${card.client_id}`,
                            { status: active ? "inactive" : "active" },
                            "PATCH",
                          );
                          return active
                            ? "Marked as no longer a client. Nothing is deleted."
                            : "Back to being an active client.";
                        })
                      }
                    >
                      {client?.status === "active"
                        ? "No longer a client"
                        : "Make them active again"}
                    </Button>
                    <span className="text-xs text-muted">
                      Currently {client?.status ?? "—"}.
                    </span>
                  </div>
                </Section>

                <Section title="Erase everything about them">
                  <p className="text-sm text-ink-2">
                    Their documents, messages, cases and facts, deleted for good. There
                    is no undo. An audit line keeps their name and the counts, so the
                    firm can show what was erased and when.
                  </p>
                  {privileged ? (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <input
                        value={confirm}
                        onChange={(e) => setConfirm(e.target.value)}
                        placeholder={`Type ${card.name} to confirm`}
                        aria-label="Type their name to confirm erasure"
                        className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink placeholder:text-muted focus:border-critical focus:outline-none"
                      />
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy !== null || confirm.trim() === ""}
                        onClick={() => void erase()}
                      >
                        Erase them
                      </Button>
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-muted">
                      Only an owner or an administrator can do this.
                    </p>
                  )}
                </Section>
              </>
            )}
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
