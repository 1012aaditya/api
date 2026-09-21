"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Spinner } from "@/components/ui";
import {
  ApiRequestError,
  apiDelete,
  apiGet,
  apiSend,
  apiUpload,
  apiUploadEnvelope,
} from "@/lib/api";
import { dateTime, relativeTime } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type {
  AgentEvent,
  AgentPolicy,
  AgentRunResult,
  CommandCentre,
  Conversation,
  DocumentSummary,
  ImportPlan,
  Invitation,
  InvitationCreated,
  Plan,
  PracticeClient,
  PracticeTask,
  PrivacyFootprint,
  ReviewException,
  Statement,
  TeamMember,
} from "@/lib/types";

/**
 * The firm's own work, on the board with the clients.
 *
 * Everything here had a page of its own, which meant the answer to "what
 * needs me today" was spread across six links in a sidebar. These are the
 * same features, as objects on the board: a column of small cards to the
 * left of the clients, each of which opens in place exactly as a client
 * card does.
 *
 * They open one at a time and close the same three ways, so there is one
 * thing to learn for the whole board rather than one per panel.
 */

export type PanelKey =
  | "clients"
  | "attention"
  | "tasks"
  | "replies"
  | "agent"
  | "documents"
  | "firm"
  | "plan"
  | "data";

export const PANEL_ORDER: PanelKey[] = [
  "clients",
  "attention",
  "tasks",
  "replies",
  "agent",
  "documents",
  "firm",
  "plan",
  "data",
];

export const PANEL_LABELS: Record<PanelKey, string> = {
  clients: "Everyone you act for",
  attention: "Needs a person",
  tasks: "Your tasks",
  replies: "What they said",
  agent: "The agent",
  documents: "Documents arriving",
  firm: "Who works here",
  plan: "Plan and usage",
  data: "What is held, and where",
};

/* --- the small card, closed ------------------------------------------- */

export function FirmChip({
  panel,
  line,
  urgent,
  width,
  height,
  dimmed,
  onOpen,
}: {
  panel: PanelKey;
  line: string;
  urgent: boolean;
  width: number;
  height: number;
  dimmed: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      data-card
      data-firm-chip={panel}
      onClick={onOpen}
      style={{ width, height }}
      className={`flex flex-col justify-center rounded-lg border border-line bg-surface px-3 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-accent hover:shadow-md ${
        dimmed ? "opacity-25 hover:opacity-100" : "opacity-100"
      }`}
    >
      <span className="text-sm font-medium text-ink">{PANEL_LABELS[panel]}</span>
      <span className={`mt-0.5 text-xs ${urgent ? "text-critical" : "text-muted"}`}>
        {line}
      </span>
    </button>
  );
}

/* --- the frame every opened panel shares ------------------------------ */

function Frame({
  title,
  note,
  width,
  height,
  onClose,
  children,
  footer,
}: {
  title: string;
  note: string | null;
  width: number;
  height: number;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div
      data-card
      data-panel
      role="dialog"
      aria-label={title}
      style={{ width, height }}
      className="flex flex-col overflow-hidden rounded-xl border border-accent bg-surface text-left shadow-2xl ring-4 ring-accent-100"
    >
      <header className="flex items-center gap-3 border-b border-line px-4 py-3">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold text-ink">{title}</h2>
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
      <div data-scroll className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {children}
      </div>
      {footer && <div className="border-t border-line px-4 py-2.5">{footer}</div>}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>;
}

/** One place for the load / act / refresh cycle every panel repeats. */
function usePanel<T>(fetcher: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetcher());
    } catch (caught) {
      if (caught instanceof ApiRequestError) setNote(caught.message);
      else throw caught;
    } finally {
      setLoading(false);
    }
    // The fetcher is rebuilt on every render by design; the panel's identity
    // is what decides when to reload, and that is the key React gives it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, run: () => Promise<string | null>, after?: () => void) {
    setBusy(key);
    setNote(null);
    try {
      setNote(await run());
      await load();
      after?.();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setNote(caught.message);
      else throw caught;
    } finally {
      setBusy(null);
    }
  }

  return { data, loading, busy, note, load, act, setNote };
}

/* --- needs a person --------------------------------------------------- */

function AttentionPanel(props: PanelProps) {
  const { data, loading, busy, note, act } = usePanel<ReviewException[]>(
    () => apiGet<ReviewException[]>("/v1/exceptions?status=open&limit=100"),
    [],
  );

  return (
    <Frame
      title="Needs a person"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : data.length === 0 ? (
        <Empty>Nothing is waiting on a human right now.</Empty>
      ) : (
        <ul className="space-y-2">
          {data.map((item) => (
            <li key={item.id} className="rounded-md border border-line p-2.5">
              <p className="text-sm text-ink">{item.message}</p>
              <p className="mt-0.5 text-xs text-muted">
                {item.client_name ?? "No client"} · {item.type.replace(/_/g, " ")} ·{" "}
                {relativeTime(item.created_at)}
              </p>
              <div className="mt-2 flex gap-2">
                <Button
                  size="sm"
                  disabled={busy !== null}
                  onClick={() =>
                    act(item.id, async () => {
                      await apiSend(`/v1/exceptions/${item.id}/resolve`, {
                        resolution: "Handled from the board.",
                      });
                      props.onChanged();
                      return "Closed.";
                    })
                  }
                >
                  I&apos;ve handled this
                </Button>
                {item.client_id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => props.onOpenClient(item.client_id as string)}
                  >
                    Open their card
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

/* --- your tasks ------------------------------------------------------- */

function TasksPanel(props: PanelProps) {
  const { data, loading, busy, note, act } = usePanel<PracticeTask[]>(
    () => apiGet<PracticeTask[]>("/v1/tasks?limit=100"),
    [],
  );
  const [draft, setDraft] = useState("");

  async function add() {
    const title = draft.trim();
    if (!title || busy !== null) return;
    await act("new", async () => {
      await apiSend<PracticeTask>("/v1/tasks", { title });
      setDraft("");
      props.onChanged();
      return "Added.";
    });
  }

  return (
    <Frame
      title="Your tasks"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        <div className="flex gap-1.5">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void add();
            }}
            placeholder="Something you have to do…"
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
          <Button size="sm" disabled={busy !== null || !draft.trim()} onClick={() => void add()}>
            Add
          </Button>
        </div>
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : data.length === 0 ? (
        <Empty>Nothing on your list.</Empty>
      ) : (
        <ul className="space-y-1">
          {data.map((task) => (
            <li
              key={task.id}
              className="flex items-center justify-between gap-2 rounded-md px-1 py-1 hover:bg-surface-sunken"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm text-ink">{task.title}</span>
                <span className="text-xs text-muted">
                  {task.client_name ?? "Your firm"}
                  {task.due_at ? ` · due ${relativeTime(task.due_at)}` : ""}
                </span>
              </span>
              <span className="flex shrink-0 gap-1">
                {task.client_id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => props.onOpenClient(task.client_id as string)}
                  >
                    Open
                  </Button>
                )}
                <Button
                  size="sm"
                  disabled={busy !== null}
                  onClick={() =>
                    act(task.id, async () => {
                      await apiSend(`/v1/tasks/${task.id}/complete`, {});
                      props.onChanged();
                      return "Done.";
                    })
                  }
                >
                  Done
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

/* --- what clients have said ------------------------------------------- */

function RepliesPanel(props: PanelProps) {
  const { data, loading, note } = usePanel<Conversation[]>(
    () => apiGet<Conversation[]>("/v1/conversations?limit=25"),
    [],
  );

  const threads = data
    .filter((thread) => thread.messages.length > 0)
    .sort((a, b) => (b.last_message_at ?? "").localeCompare(a.last_message_at ?? ""));

  return (
    <Frame
      title="What they said"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : threads.length === 0 ? (
        <Empty>No conversations yet.</Empty>
      ) : (
        <ul className="space-y-1.5">
          {threads.map((thread) => {
            const last = thread.messages[thread.messages.length - 1];
            return (
              <li key={thread.id}>
                <button
                  onClick={() => props.onOpenClient(thread.client_id)}
                  className="w-full rounded-md border border-line px-2.5 py-2 text-left transition-colors hover:border-accent hover:bg-surface-sunken"
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-ink">
                      {thread.client_name ?? "Unknown client"}
                    </span>
                    <span className="shrink-0 text-[11px] text-muted">
                      {relativeTime(thread.last_message_at)}
                    </span>
                  </span>
                  <span className="mt-0.5 line-clamp-2 text-xs text-ink-2">
                    {last.direction === "inbound" ? "" : "You: "}
                    {last.body ?? `(${last.type})`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Frame>
  );
}

/* --- the agent -------------------------------------------------------- */

function AgentPanel(props: PanelProps) {
  const { data, loading, busy, note, act } = usePanel<{
    policy: AgentPolicy | null;
    events: AgentEvent[];
  }>(
    async () => ({
      policy: await apiGet<AgentPolicy>("/v1/agent/policy"),
      events: await apiGet<AgentEvent[]>("/v1/agent/activity?limit=20"),
    }),
    { policy: null, events: [] },
  );

  const policy = data.policy;
  const summary = props.summary;

  return (
    <Frame
      title="The agent"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() =>
              act("dry", async () => {
                const r = await apiSend<AgentRunResult>("/v1/agent/run", { dry_run: true });
                return `${r.scheduled} would be chased. Nothing was sent.`;
              })
            }
          >
            {busy === "dry" ? "…" : "What would it do?"}
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null}
            onClick={() =>
              act("run", async () => {
                const r = await apiSend<AgentRunResult>("/v1/agent/run", { dry_run: false });
                props.onChanged();
                return r.sent > 0
                  ? `${r.sent} message${r.sent === 1 ? "" : "s"} sent.`
                  : r.skipped[0] ?? "Nothing was sent.";
              })
            }
          >
            Chase what needs chasing
          </Button>
          {policy && (
            <Button
              size="sm"
              variant={policy.enabled ? "danger" : "secondary"}
              disabled={busy !== null}
              onClick={() =>
                act("toggle", async () => {
                  const on = !policy.enabled;
                  await apiSend<AgentPolicy>("/v1/agent/policy", { enabled: on }, "PUT");
                  props.onChanged();
                  return on
                    ? "The agent is on again."
                    : "The agent will not message anybody until you turn it back on.";
                })
              }
            >
              {policy.enabled ? "Stop everything" : "Turn it back on"}
            </Button>
          )}
        </div>
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-ink-2">Right now</dt>
            <dd className={policy?.enabled ? "text-ink" : "text-critical"}>
              {policy?.enabled ? "Working" : "Stopped"}
            </dd>
            <dt className="text-ink-2">Sent today</dt>
            <dd className="tnum text-ink">
              {summary ? `${summary.messages_sent_today} of ${summary.message_limit_per_day}` : "—"}
            </dd>
            <dt className="text-ink-2">Quiet hours</dt>
            <dd className="tnum text-ink">
              {policy ? `${policy.quiet_hours_start}:00 – ${policy.quiet_hours_end}:00` : "—"}
            </dd>
            <dt className="text-ink-2">Reminders per case</dt>
            <dd className="tnum text-ink">{policy?.max_followups_per_case ?? "—"}</dd>
          </dl>

          <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
            What it has done
          </p>
          {data.events.length === 0 ? (
            <Empty>Nothing yet.</Empty>
          ) : (
            <ul className="mt-1.5 space-y-1">
              {data.events.map((event) => (
                <li key={event.id} className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-sm text-ink-2">
                    {event.summary}
                    {event.client_name ? ` · ${event.client_name}` : ""}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted">
                    {relativeTime(event.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Frame>
  );
}

/* --- documents -------------------------------------------------------- */

function DocumentsPanel(props: PanelProps) {
  const { data, loading, busy, note, act } = usePanel<DocumentSummary[]>(
    () => apiGet<DocumentSummary[]>("/v1/documents?limit=25"),
    [],
  );
  const [over, setOver] = useState(false);

  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    await act("upload", async () => {
      for (const file of list) {
        await apiUpload<{ job_id: string }>("/v1/documents", file);
      }
      return list.length === 1
        ? `${list[0].name} is being read.`
        : `${list.length} files are being read.`;
    });
  }

  return (
    <Frame
      title="Documents arriving"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            void send(e.dataTransfer.files);
          }}
          className={`flex cursor-pointer items-center justify-center rounded-md border border-dashed px-3 py-3 text-center text-sm ${
            over ? "border-accent bg-accent-100/40 text-ink" : "border-line text-ink-2"
          }`}
        >
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg"
            className="hidden"
            onChange={(e) => {
              if (e.target.files) void send(e.target.files);
              e.target.value = "";
            }}
          />
          {busy === "upload"
            ? "Sending…"
            : "Drop a document here, or click to choose. It is read, then matched to a client by the GSTIN on it."}
        </label>
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : data.length === 0 ? (
        <Empty>No documents have come in yet.</Empty>
      ) : (
        <ul className="space-y-1">
          {data.map((document) => (
            <li key={document.id} className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-sm text-ink">{document.filename}</span>
              <span
                className={`shrink-0 text-xs ${
                  document.status === "needs_review"
                    ? "text-critical"
                    : document.status === "failed"
                      ? "text-critical"
                      : "text-muted"
                }`}
              >
                {document.status.replace(/_/g, " ")} · {relativeTime(document.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

/* --- everyone you act for --------------------------------------------- */

const BLANK_CLIENT = {
  name: "",
  business_name: "",
  client_code: "",
  whatsapp_phone: "",
  gstin: "",
};

function ClientsPanel(props: PanelProps) {
  const { data, loading, busy, note, act } = usePanel<PracticeClient[]>(
    () => apiGet<PracticeClient[]>("/v1/clients?limit=500"),
    [],
  );
  const [find, setFind] = useState("");
  const [draft, setDraft] = useState(BLANK_CLIENT);
  const [adding, setAdding] = useState(false);
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [file, setFile] = useState<File | null>(null);

  const needle = find.trim().toLowerCase();
  const shown = data.filter(
    (client) =>
      !needle ||
      `${client.business_name ?? ""} ${client.name} ${client.client_code ?? ""} ${client.gstin ?? ""}`
        .toLowerCase()
        .includes(needle),
  );

  async function add() {
    if (!draft.name.trim() || busy !== null) return;
    await act("add", async () => {
      const body = Object.fromEntries(
        Object.entries(draft).filter(([, value]) => value.trim() !== ""),
      );
      const created = await apiSend<PracticeClient>("/v1/clients", body);
      setDraft(BLANK_CLIENT);
      setAdding(false);
      props.onChanged();
      return `${created.business_name ?? created.name} is on the board.`;
    });
  }

  return (
    <Frame
      title="Everyone you act for"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setAdding((open) => !open)}>
            {adding ? "Cancel" : "Add a client"}
          </Button>
          <label className="cursor-pointer text-sm text-ink-2 underline underline-offset-2 hover:text-ink">
            <input
              type="file"
              accept=".csv,.tsv,.txt"
              className="hidden"
              onChange={(e) => {
                const chosen = e.target.files?.[0] ?? null;
                e.target.value = "";
                if (!chosen) return;
                setFile(chosen);
                void act("preview", async () => {
                  const previewed = await apiUploadEnvelope<ImportPlan>(
                    "/v1/clients/import/preview",
                    chosen,
                  );
                  setPlan(previewed);
                  return previewed.error
                    ? previewed.error
                    : `${previewed.counts.create} would be added, ${previewed.counts.duplicate} look like duplicates. Nothing has been written.`;
                });
              }}
            />
            {busy === "preview" ? "Reading…" : "Import a list"}
          </label>
          {plan && !plan.error && plan.counts.create > 0 && file && (
            <Button
              size="sm"
              variant="primary"
              disabled={busy !== null}
              onClick={() =>
                act("import", async () => {
                  const applied = await apiUploadEnvelope<ImportPlan>(
                    "/v1/clients/import",
                    file,
                  );
                  setPlan(null);
                  setFile(null);
                  props.onChanged();
                  return `${applied.counts.create} clients imported.`;
                })
              }
            >
              Import {plan.counts.create}
            </Button>
          )}
          <span className="ml-auto text-xs text-muted">{shown.length} shown</span>
        </div>
      }
    >
      {adding && (
        <div className="mb-3 grid grid-cols-2 gap-2 rounded-md border border-line p-2.5">
          {(
            [
              ["name", "Name"],
              ["business_name", "Registered name"],
              ["client_code", "Your code"],
              ["whatsapp_phone", "WhatsApp number"],
              ["gstin", "GSTIN"],
            ] as [keyof typeof BLANK_CLIENT, string][]
          ).map(([key, label]) => (
            <label key={key} className="text-xs text-muted">
              {label}
              <input
                value={draft[key]}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]: key === "gstin" ? e.target.value.toUpperCase() : e.target.value,
                  })
                }
                className="mt-0.5 w-full rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </label>
          ))}
          <div className="flex items-end">
            <Button
              size="sm"
              variant="primary"
              disabled={busy !== null || !draft.name.trim()}
              onClick={() => void add()}
            >
              Add
            </Button>
          </div>
          <p className="col-span-2 text-xs text-muted">
            Without a WhatsApp number nothing can be sent to them.
          </p>
        </div>
      )}

      <input
        value={find}
        onChange={(e) => setFind(e.target.value)}
        placeholder="Search by name, code or GSTIN"
        aria-label="Search clients"
        className="mb-2 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
      />

      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : shown.length === 0 ? (
        <Empty>{needle ? "Nobody matches that." : "No clients yet."}</Empty>
      ) : (
        <ul className="space-y-0.5">
          {shown.map((client) => (
            <li key={client.id}>
              <button
                onClick={() => props.onOpenClient(client.id)}
                className="flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-left hover:bg-surface-sunken"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm text-ink">
                    {client.business_name ?? client.name}
                  </span>
                  <span className="text-xs text-muted">
                    {client.client_code ?? "No code"}
                    {client.gstin ? ` · ${client.gstin}` : ""}
                    {client.status !== "active" ? ` · ${client.status}` : ""}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-muted">
                  {client.blocked_cases > 0
                    ? `${client.blocked_cases} blocked`
                    : client.open_cases > 0
                      ? `${client.open_cases} open`
                      : "nothing open"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  );
}

/* --- who works here --------------------------------------------------- */

function TeamPanel(props: PanelProps) {
  const { user } = useAuth();
  const privileged = user?.role === "owner" || user?.role === "admin";
  const { data, loading, busy, note, act } = usePanel<{
    members: TeamMember[];
    invites: Invitation[];
  }>(
    async () => ({
      members: await apiGet<TeamMember[]>("/v1/team"),
      // Staff cannot read invitations, and asking anyway would only produce
      // a 403 to apologise for.
      invites: privileged ? await apiGet<Invitation[]>("/v1/team/invites") : [],
    }),
    { members: [], invites: [] },
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("staff");

  return (
    <Frame
      title="Who works here"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        privileged ? (
          <div className="flex flex-wrap gap-1.5">
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="colleague@firm.in"
              type="email"
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
            >
              <option value="staff">Staff</option>
              <option value="admin">Administrator</option>
            </select>
            <Button
              size="sm"
              disabled={busy !== null || !email.includes("@")}
              onClick={() =>
                act("invite", async () => {
                  const invitation = await apiSend<InvitationCreated>("/v1/team/invites", {
                    email: email.trim(),
                    role,
                  });
                  setEmail("");
                  return `Invitation for ${invitation.email}: ${invitation.accept_url}`;
                })
              }
            >
              Invite
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted">
            Only an owner or an administrator can invite people or change roles.
          </p>
        )
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : (
        <>
          <ul className="space-y-1">
            {data.members.map((member) => (
              <li key={member.id} className="flex items-center justify-between gap-2">
                <span className="min-w-0">
                  <span className="block truncate text-sm text-ink">
                    {member.full_name ?? member.email}
                    {member.is_you && <span className="text-muted"> · you</span>}
                  </span>
                  <span className="block truncate text-xs text-muted">
                    {/* The email is the login, so it is shown even when the
                        person has a name — it is what you type to sign in
                        and what an invitation went to. */}
                    {member.email} · {member.role_label}
                    {member.is_active ? "" : " · switched off"} ·{" "}
                    {member.last_login_at
                      ? `last in ${relativeTime(member.last_login_at)}`
                      : "never signed in"}
                  </span>
                </span>
                {privileged && !member.is_you && member.role !== "owner" && (
                  <span className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() =>
                        act(`role-${member.id}`, async () => {
                          const next = member.role === "admin" ? "staff" : "admin";
                          await apiSend(`/v1/team/${member.id}`, { role: next }, "PATCH");
                          return `${member.email} is now ${next}.`;
                        })
                      }
                    >
                      Make {member.role === "admin" ? "staff" : "an admin"}
                    </Button>
                    <Button
                      size="sm"
                      variant={member.is_active ? "danger" : "secondary"}
                      disabled={busy !== null}
                      onClick={() =>
                        act(`active-${member.id}`, async () => {
                          await apiSend(
                            `/v1/team/${member.id}`,
                            { is_active: !member.is_active },
                            "PATCH",
                          );
                          return member.is_active
                            ? `${member.email} can no longer sign in.`
                            : `${member.email} can sign in again.`;
                        })
                      }
                    >
                      {member.is_active ? "Switch off" : "Switch on"}
                    </Button>
                  </span>
                )}
              </li>
            ))}
          </ul>

          {privileged && data.invites.length > 0 && (
            <>
              <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
                Invited, not yet joined
              </p>
              <ul className="mt-1.5 space-y-1">
                {data.invites.map((invitation) => (
                  <li
                    key={invitation.id}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-ink">
                        {invitation.email}
                      </span>
                      <span className="text-xs text-muted">
                        {invitation.role_label} · expires {dateTime(invitation.expires_at)}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() =>
                        act(`revoke-${invitation.id}`, async () => {
                          await apiDelete(`/v1/team/invites/${invitation.id}`);
                          return "Withdrawn.";
                        })
                      }
                    >
                      Withdraw
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </Frame>
  );
}

/* --- plan and usage --------------------------------------------------- */

function PlanPanel(props: PanelProps) {
  const { data, loading, note } = usePanel<{
    statement: Statement | null;
    plans: Plan[];
  }>(
    async () => ({
      statement: await apiGet<Statement>("/v1/billing/statement"),
      plans: await apiGet<Plan[]>("/v1/billing/plans"),
    }),
    { statement: null, plans: [] },
  );

  const statement = data.statement;

  return (
    <Frame
      title="Plan and usage"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        statement ? (
          <p className="text-xs text-muted">{statement.payment_note}</p>
        ) : undefined
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : !statement ? (
        <Empty>No statement yet.</Empty>
      ) : (
        <>
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-sm text-ink">
              {statement.plan.name}
              <span className="text-muted"> · {statement.period}</span>
            </p>
            <p className="tnum text-base font-semibold text-ink">
              {statement.currency} {statement.total}
              {statement.provisional && (
                <span className="ml-1 text-xs font-normal text-muted">so far</span>
              )}
            </p>
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-ink-2">Documents read</dt>
            <dd className="tnum text-ink">
              {statement.documents_used} of {statement.documents_included} included
            </dd>
            <dt className="text-ink-2">Over the allowance</dt>
            <dd className="tnum text-ink">{statement.overage_documents}</dd>
            <dt className="text-ink-2">Clients</dt>
            <dd className="tnum text-ink">
              {statement.clients} of {statement.plan.included_clients}
            </dd>
          </dl>

          {statement.warnings.length > 0 && (
            <ul className="mt-3 space-y-1">
              {statement.warnings.map((warning) => (
                <li key={warning} className="text-sm text-critical">
                  {warning}
                </li>
              ))}
            </ul>
          )}

          <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
            What it comes to
          </p>
          <ul className="mt-1.5 space-y-1">
            {statement.lines.map((line) => (
              <li key={line.label} className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-sm text-ink-2">
                  {line.label}
                  <span className="text-muted"> · {line.detail}</span>
                </span>
                <span className="tnum shrink-0 text-sm text-ink">{line.amount}</span>
              </li>
            ))}
          </ul>

          <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
            The plans
          </p>
          <ul className="mt-1.5 space-y-1">
            {data.plans.map((plan) => (
              <li key={plan.key} className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-sm text-ink">
                  {plan.name} {plan.is_current && <Badge>current</Badge>}
                  <span className="block text-xs text-muted">
                    {plan.included_documents} documents, {plan.included_clients} clients
                  </span>
                </span>
                <span className="tnum shrink-0 text-sm text-ink-2">
                  ₹{plan.monthly_price}
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-3 text-xs text-muted">{statement.note}</p>
        </>
      )}
    </Frame>
  );
}

/* --- what is held, and where ------------------------------------------ */

const LOCALITY: Record<string, { label: string; tone: string }> = {
  no_model: { label: "No model is configured, so nothing is sent anywhere", tone: "text-ink" },
  stays_here: { label: "Documents stay on this machine", tone: "text-good-ink" },
  cannot_be_proven: {
    label: "Documents leave this machine",
    tone: "text-critical",
  },
};

function DataPanel(props: PanelProps) {
  const { data, loading, note } = usePanel<PrivacyFootprint | null>(
    () => apiGet<PrivacyFootprint>("/v1/privacy/footprint"),
    null,
  );

  const locality = data ? LOCALITY[data.document_locality] ?? null : null;

  return (
    <Frame
      title="What is held, and where"
      note={note}
      width={props.width}
      height={props.height}
      onClose={props.onClose}
      footer={
        <p className="text-xs text-muted">
          Erasing one client&apos;s data is on their own card, under Their details.
        </p>
      }
    >
      {loading ? (
        <div className="py-8 text-center">
          <Spinner />
        </div>
      ) : !data ? (
        <Empty>Nothing to report.</Empty>
      ) : (
        <>
          <p className={`text-sm ${locality?.tone ?? "text-ink"}`}>
            {locality?.label ?? data.document_locality}
          </p>
          <p className="mt-0.5 text-xs text-muted">{data.locality_note}</p>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-ink-2">Documents kept for</dt>
            <dd className="tnum text-ink">{data.retention_days} days</dd>
            <dt className="text-ink-2">Next clear-out</dt>
            <dd className="text-ink">
              {data.next_purge_at ? dateTime(data.next_purge_at) : "—"}
            </dd>
            <dt className="text-ink-2">Files are stored</dt>
            <dd className="text-ink">{data.storage_backend}</dd>
            <dt className="text-ink-2">WhatsApp goes through</dt>
            <dd className="text-ink">{data.whatsapp_provider}</dd>
            <dt className="text-ink-2">Calls go through</dt>
            <dd className="text-ink">{data.voice_provider}</dd>
          </dl>

          <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
            How much is held
          </p>
          <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            {Object.entries(data.counts).map(([what, count]) => (
              <div key={what} className="col-span-2 flex justify-between gap-3">
                <dt className="text-ink-2">{what.replace(/_/g, " ")}</dt>
                <dd className="tnum text-ink">{count}</dd>
              </div>
            ))}
          </dl>

          {data.processors.length > 0 && (
            <>
              <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
                Who else sees any of it
              </p>
              <ul className="mt-1.5 space-y-1">
                {data.processors.map((processor) => (
                  <li key={processor.name} className="text-sm">
                    <span className="text-ink">{processor.name}</span>
                    <span className="block text-xs text-muted">{processor.receives}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </Frame>
  );
}

/* --- the one a board asks for ----------------------------------------- */

interface PanelProps {
  width: number;
  height: number;
  summary: CommandCentre | null;
  onClose: () => void;
  /** Reload the board: these panels change what the cards say. */
  onChanged: () => void;
  onOpenClient: (clientId: string) => void;
}

export function FirmPanel({ panel, ...props }: PanelProps & { panel: PanelKey }) {
  switch (panel) {
    case "clients":
      return <ClientsPanel {...props} />;
    case "attention":
      return <AttentionPanel {...props} />;
    case "tasks":
      return <TasksPanel {...props} />;
    case "replies":
      return <RepliesPanel {...props} />;
    case "agent":
      return <AgentPanel {...props} />;
    case "documents":
      return <DocumentsPanel {...props} />;
    case "firm":
      return <TeamPanel {...props} />;
    case "plan":
      return <PlanPanel {...props} />;
    case "data":
      return <DataPanel {...props} />;
  }
}
