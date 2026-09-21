"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CardDetail } from "@/components/CardDetail";
import { Canvas, type Viewport } from "@/components/Canvas";
import {
  FirmChip,
  FirmPanel,
  PANEL_LABELS,
  PANEL_ORDER,
  type PanelKey,
} from "@/components/FirmPanels";
import { Wordmark } from "@/components/Wordmark";
import { Button, ErrorNotice, Spinner } from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type {
  AgentRunResult,
  BoardCard,
  BoardData,
  CommandCentre,
  TeamMember,
} from "@/lib/types";

/* --- layout ------------------------------------------------------------
 * Cards are laid out, never placed by hand. Position means state, so a
 * card somebody dragged would be a lie the board tells about a client.
 *
 * Two kinds of object share the board: a client, and a piece of the firm's
 * own work — what needs a person, your tasks, what clients have said, the
 * agent, documents coming in. They sit in a column of their own on the
 * left and open exactly as a client card does, which is the point: one
 * board and one gesture, rather than a sidebar of pages.
 *
 * Opening anything keeps its coordinates and moves what it would cover.  */

const COLUMN_WIDTH = 300;
const COLUMN_GAP = 40;
const CARD_HEIGHT = 128;
const CARD_GAP = 14;
const HEADER = 92;

const FIRM_WIDTH = 248;
const FIRM_HEIGHT = 62;
const FIRM_GAP = 12;

const DETAIL_WIDTH = 700;
const DETAIL_HEIGHT = 500;
const PANEL_WIDTH = 580;
const PANEL_HEIGHT = 460;

const ZONE_TONE: Record<string, { bar: string; text: string }> = {
  needs_you: { bar: "bg-critical", text: "text-critical" },
  waiting: { bar: "bg-warning", text: "text-ink-2" },
  reading: { bar: "bg-accent", text: "text-ink-2" },
  ready: { bar: "bg-good", text: "text-good-ink" },
  clear: { bar: "bg-axis", text: "text-muted" },
};

/** Column 0 is the firm's own work; the zones follow it. */
function columnX(column: number) {
  if (column <= 0) return 0;
  return FIRM_WIDTH + COLUMN_GAP + (column - 1) * (COLUMN_WIDTH + COLUMN_GAP);
}

type Opened = { kind: "client"; key: string } | { kind: "panel"; key: PanelKey };

/* --- a client card, closed -------------------------------------------- */

function Card({
  card,
  dimmed,
  onOpen,
}: {
  card: BoardCard;
  dimmed: boolean;
  onOpen: () => void;
}) {
  const tone = ZONE_TONE[card.zone] ?? ZONE_TONE.clear;
  const urgent = card.days_left !== null && card.days_left <= 3;

  return (
    <button
      data-card
      onClick={onOpen}
      style={{ width: COLUMN_WIDTH, height: CARD_HEIGHT }}
      className={`flex flex-col items-start rounded-lg border border-line bg-surface p-3 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-accent hover:shadow-md ${
        dimmed ? "opacity-25 hover:opacity-100" : "opacity-100"
      }`}
    >
      <div className="flex w-full items-start justify-between gap-2">
        <span className="truncate text-sm font-medium text-ink">{card.name}</span>
        {card.period && (
          <span className="shrink-0 font-mono text-[10px] text-muted">{card.period}</span>
        )}
      </div>

      <span className={`mt-1 line-clamp-2 text-xs ${tone.text}`}>{card.reason}</span>

      {card.outstanding.length > 0 && (
        <span className="mt-auto line-clamp-1 text-[11px] text-muted">
          {card.outstanding.join(" · ")}
        </span>
      )}

      <div className="mt-auto flex w-full items-center gap-2 pt-1.5 text-[11px]">
        {!card.automated && <span className="text-critical">automation off</span>}
        {card.days_left !== null && (
          <span className={urgent ? "text-critical" : "text-muted"}>
            {card.days_left < 0
              ? `${Math.abs(card.days_left)} d overdue`
              : `${card.days_left} d left`}
          </span>
        )}
        <span className="ml-auto text-muted">
          {card.last_response_at
            ? `replied ${relativeTime(card.last_response_at)}`
            : card.last_contacted_at
              ? `asked ${relativeTime(card.last_contacted_at)}`
              : "not asked"}
        </span>
      </div>
    </button>
  );
}

/* --- the page --------------------------------------------------------- */

export default function BoardPage() {
  const [board, setBoard] = useState<BoardData | null>(null);
  const [summary, setSummary] = useState<CommandCentre | null>(null);
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState<Opened | null>(null);
  const [run, setRun] = useState<AgentRunResult | null>(null);
  /** Something an opened card said on its way out, e.g. an erasure receipt. */
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewport, setViewport] = useState<Viewport>({ x: 48, y: 24, scale: 0.85 });
  const [glide, setGlide] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  /** Where the eye was before something was opened, to give it back after. */
  const restore = useRef<Viewport | null>(null);
  const settle = useRef<number | null>(null);

  /** Move the view ourselves, with the animation a hand-pan must not have. */
  const glideTo = useCallback((next: Viewport) => {
    setGlide(true);
    setViewport(next);
    if (settle.current) window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => setGlide(false), 340);
  }, []);

  useEffect(
    () => () => {
      if (settle.current) window.clearTimeout(settle.current);
    },
    [],
  );

  /** Scale so every column is on screen, which is what "fit" has to mean
   *  on a board whose whole point is seeing the month at once. */
  const fit = useCallback(() => {
    const width = stage.current?.clientWidth ?? 0;
    const content = columnX((board?.zones.length ?? 5) + 1);
    const scale = width ? Math.min(1, Math.max(0.3, (width - 48) / content)) : 0.85;
    glideTo({ x: 24, y: 16, scale });
  }, [board, glideTo]);

  const load = useCallback(async () => {
    setError(null);
    try {
      // Two calls for the whole screen: the clients, and the firm's own
      // numbers. Both are aggregates the API already builds in one pass.
      const [next, centre, colleagues] = await Promise.all([
        apiGet<BoardData>("/v1/board"),
        apiGet<CommandCentre>("/v1/command-centre"),
        apiGet<TeamMember[]>("/v1/team"),
      ]);
      setBoard(next);
      setSummary(centre);
      setTeam(colleagues);
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

  useEffect(() => {
    if (board) fit();
    // Only when the board first arrives; re-fitting on every change would
    // yank the view out from under someone who had panned somewhere.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board?.generated_at]);

  const needle = query.trim().toLowerCase();
  const matches = useCallback(
    (card: BoardCard) => !needle || card.name.toLowerCase().includes(needle),
    [needle],
  );
  const found = board?.cards.filter(matches).length ?? 0;

  /* --- what the firm's own cards say --------------------------------- */

  const replied = useMemo(() => {
    const since = Date.now() - 2 * 24 * 60 * 60 * 1000;
    return (board?.cards ?? []).filter(
      (card) => card.last_response_at && Date.parse(card.last_response_at) > since,
    ).length;
  }, [board]);

  const chipLines: Record<PanelKey, { line: string; urgent: boolean }> = {
    clients: {
      line: summary
        ? `${summary.clients_total} on your books${summary.clients_blocked ? ` · ${summary.clients_blocked} blocked` : ""}`
        : "—",
      urgent: false,
    },
    firm: {
      line:
        team.length === 0
          ? "—"
          : `${team.length} ${team.length === 1 ? "login" : "logins"}${
              team.filter((member) => !member.is_active).length
                ? ` · ${team.filter((member) => !member.is_active).length} off`
                : ""
            }`,
      urgent: false,
    },
    plan: { line: "The allowance and what it costs", urgent: false },
    you: { line: "Signed in, and the older pages", urgent: false },
    data: { line: "Retention, and where files live", urgent: false },
    attention: {
      line: summary
        ? summary.exceptions_open === 0
          ? "Nothing waiting on you"
          : `${summary.exceptions_open} waiting on you`
        : "—",
      urgent: (summary?.exceptions_open ?? 0) > 0,
    },
    tasks: {
      line: summary
        ? `${summary.tasks_open} open${summary.tasks_overdue ? ` · ${summary.tasks_overdue} overdue` : ""}`
        : "—",
      urgent: (summary?.tasks_overdue ?? 0) > 0,
    },
    replies: {
      line: replied === 0 ? "No replies in two days" : `${replied} replied in two days`,
      urgent: false,
    },
    agent: {
      line: summary
        ? summary.agent_enabled
          ? `${summary.messages_sent_today} of ${summary.message_limit_per_day} sent today`
          : "Stopped"
        : "—",
      urgent: summary ? !summary.agent_enabled : false,
    },
    documents: {
      line: summary
        ? summary.documents_awaiting_review === 0
          ? "Nothing to look at"
          : `${summary.documents_awaiting_review} need a look`
        : "—",
      urgent: (summary?.documents_awaiting_review ?? 0) > 0,
    },
  };

  /* --- where everything sits, given what is open --------------------- */

  const layout = useMemo(() => {
    const zones = board?.zones ?? [];

    const panels = PANEL_ORDER.map((panel, row) => ({
      kind: "panel" as const,
      key: panel as string,
      panel,
      card: null,
      column: 0,
      row,
      baseLeft: 0,
      baseTop: HEADER + row * (FIRM_HEIGHT + FIRM_GAP),
      collapsedWidth: FIRM_WIDTH,
      collapsedHeight: FIRM_HEIGHT,
      openWidth: PANEL_WIDTH,
      openHeight: PANEL_HEIGHT,
    }));

    const rows: Record<string, number> = {};
    const clients = (board?.cards ?? []).map((card) => {
      const zoneIndex = Math.max(
        zones.findIndex((zone) => zone.key === card.zone),
        0,
      );
      const row = rows[card.zone] ?? 0;
      rows[card.zone] = row + 1;
      return {
        kind: "client" as const,
        key: card.client_id,
        panel: null,
        card,
        column: zoneIndex + 1,
        row,
        baseLeft: columnX(zoneIndex + 1),
        baseTop: HEADER + row * (CARD_HEIGHT + CARD_GAP),
        collapsedWidth: COLUMN_WIDTH,
        collapsedHeight: CARD_HEIGHT,
        openWidth: DETAIL_WIDTH,
        openHeight: DETAIL_HEIGHT,
      };
    });

    const all = [...panels, ...clients];
    const open =
      opened === null
        ? null
        : all.find((item) => item.kind === opened.kind && item.key === opened.key) ?? null;

    // The opened object keeps its own coordinates; only what it would cover
    // moves. That is what makes the growth read as this card, not a panel.
    const dx = open ? open.openWidth - open.collapsedWidth : 0;
    const dy = open ? open.openHeight - open.collapsedHeight : 0;

    const placed = all.map((item) => ({
      ...item,
      left: item.baseLeft + (open && item.column > open.column ? dx : 0),
      top:
        item.baseTop + (open && item.column === open.column && item.row > open.row ? dy : 0),
    }));

    const tallest = Math.max(...zones.map((zone) => zone.count), 1);
    return {
      open,
      placed,
      columns: zones.map(
        (_, index) => columnX(index + 1) + (open && index + 1 > open.column ? dx : 0),
      ),
      width: columnX(Math.max(zones.length, 1) + 1) + dx,
      height:
        Math.max(
          HEADER + tallest * (CARD_HEIGHT + CARD_GAP),
          HEADER + PANEL_ORDER.length * (FIRM_HEIGHT + FIRM_GAP),
        ) +
        dy +
        80,
    };
  }, [board, opened]);

  const close = useCallback(() => {
    if (!opened) return;
    setOpened(null);
    if (restore.current) {
      glideTo(restore.current);
      restore.current = null;
    }
  }, [opened, glideTo]);

  /**
   * Open something where it stands, and bring the eye to it.
   *
   * The coordinates are the object's own — the ones it has when nothing is
   * open — because an opened object never moves itself out of the way.
   */
  const openAt = useCallback(
    (next: Opened, left: number, top: number, width: number, height: number) => {
      const frame = stage.current;
      setOpened(next);
      if (!frame) return;
      if (!restore.current) restore.current = viewport;
      const stageWidth = frame.clientWidth;
      const stageHeight = frame.clientHeight;
      const scale = Math.min(
        1,
        Math.max(0.45, Math.min((stageWidth - 80) / width, (stageHeight - 80) / height)),
      );
      glideTo({
        scale,
        x: stageWidth / 2 - (left + width / 2) * scale,
        y: stageHeight / 2 - (top + height / 2) * scale,
      });
    },
    [viewport, glideTo],
  );

  /** Used by the firm's panels: "open their card" means this board's card. */
  const openClient = useCallback(
    (clientId: string) => {
      const target = layout.placed.find(
        (item) => item.kind === "client" && item.key === clientId,
      );
      if (!target) return;
      openAt(
        { kind: "client", key: clientId },
        target.baseLeft,
        target.baseTop,
        DETAIL_WIDTH,
        DETAIL_HEIGHT,
      );
    },
    [layout, openAt],
  );

  // "/" focuses search — the fastest way to find one client among four
  // hundred, which is the thing a board is otherwise worse at than a list.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA"].includes(
        (event.target as HTMLElement)?.tagName ?? "",
      );
      if (event.key === "/" && !typing) {
        event.preventDefault();
        search.current?.focus();
      }
      if (event.key === "Escape") {
        close();
        search.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  async function chase(dryRun: boolean) {
    setBusy(true);
    setRun(null);
    try {
      setRun(await apiSend<AgentRunResult>("/v1/agent/run", { dry_run: dryRun }));
      if (!dryRun) await load();
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

  return (
    <div className="fixed inset-0 flex flex-col">
      {/* --- the bar ---------------------------------------------------- */}
      <header className="z-20 flex flex-wrap items-center gap-3 border-b border-line bg-surface px-4 py-2.5">
        <Link href="/board" aria-label="DocuParse">
          <Wordmark />
        </Link>
        <h1 className="text-sm font-semibold text-ink">The month</h1>
        <input
          ref={search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a client    /"
          className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
        />
        {needle && <span className="text-xs text-muted">{found} matching</span>}

        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" disabled={busy} onClick={() => void chase(true)}>
            {busy ? "Working…" : "Show me what it would do"}
          </Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => void chase(false)}>
            Chase what needs chasing
          </Button>
        </div>
      </header>

      {run && (
        <div className="z-20 border-b border-line bg-accent/5 px-4 py-2 text-sm text-ink">
          {run.sent > 0
            ? `${run.sent} message${run.sent === 1 ? "" : "s"} sent.`
            : `${run.scheduled} would be chased. Nothing was sent.`}
          {run.skipped.length > 0 && (
            <span className="ml-2 text-ink-2">
              Skipped: {run.skipped.slice(0, 3).join("; ")}
              {run.skipped.length > 3 && ` and ${run.skipped.length - 3} more`}
            </span>
          )}
          <button className="ml-3 text-xs text-muted underline" onClick={() => setRun(null)}>
            dismiss
          </button>
        </div>
      )}

      {notice && (
        <div className="z-20 border-b border-line bg-surface-sunken px-4 py-2 text-sm text-ink">
          {notice}
          <button
            className="ml-3 text-xs text-muted underline"
            onClick={() => setNotice(null)}
          >
            dismiss
          </button>
        </div>
      )}

      {error && (
        <div className="z-20 px-4 py-2">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      {/* --- the board -------------------------------------------------- */}
      <div ref={stage} className="relative min-h-0 flex-1 bg-surface-sunken">
        <Canvas
          viewport={viewport}
          onViewportChange={(next) => {
            setGlide(false);
            setViewport(next);
          }}
          onBackgroundClick={close}
          glide={glide}
        >
          <div style={{ width: layout.width, height: layout.height }}>
            {/* the firm's own column */}
            <div className="absolute" style={{ left: 0, top: 0, width: FIRM_WIDTH }}>
              <div className="h-1 w-full rounded-full bg-ink-2" />
              <p className="mt-2 text-sm font-semibold text-ink">Your firm</p>
              <p className="mt-0.5 text-xs text-muted">
                Everything that is not one client&apos;s.
              </p>
            </div>

            {board?.zones.map((zone, index) => (
              <div
                key={zone.key}
                className="absolute transition-[left] duration-300 ease-out"
                style={{ left: layout.columns[index], top: 0, width: COLUMN_WIDTH }}
              >
                <div
                  className={`h-1 w-full rounded-full ${(ZONE_TONE[zone.key] ?? ZONE_TONE.clear).bar}`}
                />
                <p className="mt-2 text-sm font-semibold text-ink">
                  {zone.label} <span className="tnum font-normal text-muted">{zone.count}</span>
                </p>
                <p className="mt-0.5 text-xs text-muted">{zone.note}</p>
              </div>
            ))}

            {layout.placed.map((item) => {
              const isOpen =
                opened !== null && opened.kind === item.kind && opened.key === item.key;
              return (
                <div
                  key={`${item.kind}:${item.key}`}
                  className="absolute transition-[left,top] duration-300 ease-out"
                  style={{ left: item.left, top: item.top, zIndex: isOpen ? 10 : 1 }}
                >
                  {item.kind === "client" && item.card ? (
                    isOpen ? (
                      <div className="card-open">
                        <CardDetail
                          card={item.card}
                          width={DETAIL_WIDTH}
                          height={DETAIL_HEIGHT}
                          onClose={close}
                          onChanged={load}
                          onNotice={setNotice}
                        />
                      </div>
                    ) : (
                      <Card
                        card={item.card}
                        dimmed={!matches(item.card) || opened !== null}
                        onOpen={() =>
                          openAt(
                            { kind: "client", key: item.key },
                            item.baseLeft,
                            item.baseTop,
                            DETAIL_WIDTH,
                            DETAIL_HEIGHT,
                          )
                        }
                      />
                    )
                  ) : item.panel ? (
                    isOpen ? (
                      <div className="card-open">
                        <FirmPanel
                          panel={item.panel}
                          width={PANEL_WIDTH}
                          height={PANEL_HEIGHT}
                          summary={summary}
                          onClose={close}
                          onChanged={load}
                          onOpenClient={openClient}
                        />
                      </div>
                    ) : (
                      <FirmChip
                        panel={item.panel}
                        line={chipLines[item.panel].line}
                        urgent={chipLines[item.panel].urgent}
                        width={FIRM_WIDTH}
                        height={FIRM_HEIGHT}
                        dimmed={opened !== null}
                        onOpen={() =>
                          openAt(
                            { kind: "panel", key: item.panel as PanelKey },
                            item.baseLeft,
                            item.baseTop,
                            PANEL_WIDTH,
                            PANEL_HEIGHT,
                          )
                        }
                      />
                    )
                  ) : null}
                </div>
              );
            })}
          </div>
        </Canvas>

        {needle && found === 0 && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <p className="rounded-lg border border-line bg-surface px-4 py-3 text-sm text-ink-2 shadow-sm">
              No client here matches <span className="font-medium text-ink">{query}</span>.
            </p>
          </div>
        )}

        <p className="pointer-events-none absolute bottom-3 left-4 z-10 text-xs text-muted">
          {layout.open
            ? `${
                layout.open.kind === "client"
                  ? "Everything about them is here"
                  : PANEL_LABELS[layout.open.key as PanelKey]
              } · Esc, or click the paper, to close`
            : "Click anything to open it in place · drag to pan · ⌘/ctrl + scroll to zoom · / to find a client"}
        </p>
        <div className="absolute bottom-3 right-4 z-10 flex gap-1.5">
          {layout.open && (
            <Button size="sm" onClick={close}>
              Back to the board
            </Button>
          )}
          <Button size="sm" onClick={fit}>
            Fit
          </Button>
        </div>
      </div>
    </div>
  );
}
