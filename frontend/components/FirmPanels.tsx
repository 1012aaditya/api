"use client";

import { useCallback, useEffect, useState } from "react";

import { Button, Spinner } from "@/components/ui";
import { ApiRequestError, apiGet, apiSend, apiUpload } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type {
  AgentEvent,
  AgentPolicy,
  AgentRunResult,
  CommandCentre,
  Conversation,
  DocumentSummary,
  PracticeTask,
  ReviewException,
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

export type PanelKey = "attention" | "tasks" | "replies" | "agent" | "documents";

export const PANEL_ORDER: PanelKey[] = [
  "attention",
  "tasks",
  "replies",
  "agent",
  "documents",
];

export const PANEL_LABELS: Record<PanelKey, string> = {
  attention: "Needs a person",
  tasks: "Your tasks",
  replies: "What they said",
  agent: "The agent",
  documents: "Documents arriving",
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
  }
}
