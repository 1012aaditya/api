"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BoardInspector } from "@/components/BoardInspector";
import { Canvas, type Viewport } from "@/components/Canvas";
import { Badge, Button, ErrorNotice, Spinner } from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { AgentRunResult, BoardCard, BoardData } from "@/lib/types";

/* --- layout ------------------------------------------------------------
 * Cards are laid out, never placed by hand. Position means state, so a
 * card somebody dragged would be a lie the board tells about a client.  */

const COLUMN_WIDTH = 300;
const COLUMN_GAP = 40;
const CARD_HEIGHT = 128;
const CARD_GAP = 14;
const HEADER = 92;

const ZONE_TONE: Record<string, { bar: string; text: string }> = {
  needs_you: { bar: "bg-critical", text: "text-critical" },
  waiting: { bar: "bg-warning", text: "text-ink-2" },
  reading: { bar: "bg-accent", text: "text-ink-2" },
  ready: { bar: "bg-good", text: "text-good-ink" },
  clear: { bar: "bg-axis", text: "text-muted" },
};

function position(zoneIndex: number, row: number) {
  return {
    left: zoneIndex * (COLUMN_WIDTH + COLUMN_GAP),
    top: HEADER + row * (CARD_HEIGHT + CARD_GAP),
  };
}

/* --- a card ----------------------------------------------------------- */

function Card({
  card,
  dimmed,
  selected,
  onOpen,
}: {
  card: BoardCard;
  dimmed: boolean;
  selected: boolean;
  onOpen: () => void;
}) {
  const tone = ZONE_TONE[card.zone] ?? ZONE_TONE.clear;
  const urgent = card.days_left !== null && card.days_left <= 3;

  return (
    <button
      data-card
      onClick={onOpen}
      style={{ width: COLUMN_WIDTH, height: CARD_HEIGHT }}
      className={`flex flex-col items-start rounded-lg border bg-surface p-3 text-left transition-all ${
        selected ? "border-accent ring-2 ring-accent/30" : "border-line hover:border-ink-2"
      } ${dimmed ? "opacity-25" : "opacity-100"}`}
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
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<BoardCard | null>(null);
  const [run, setRun] = useState<AgentRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewport, setViewport] = useState<Viewport>({ x: 48, y: 24, scale: 0.85 });
  const search = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  /** Scale so every column is on screen, which is what "fit" has to mean
   *  on a board whose whole point is seeing the month at once. */
  const fit = useCallback(() => {
    const width = stage.current?.clientWidth ?? 0;
    const columns = board?.zones.length ?? 5;
    const content = columns * (COLUMN_WIDTH + COLUMN_GAP);
    const scale = width ? Math.min(1, Math.max(0.3, (width - 48) / content)) : 0.85;
    setViewport({ x: 24, y: 16, scale });
  }, [board]);

  const load = useCallback(async () => {
    setError(null);
    try {
      setBoard(await apiGet<BoardData>("/v1/board"));
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
        setSelected(null);
        search.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const needle = query.trim().toLowerCase();
  const matches = useCallback(
    (card: BoardCard) => !needle || card.name.toLowerCase().includes(needle),
    [needle],
  );
  const found = board?.cards.filter(matches).length ?? 0;

  const placed = useMemo(() => {
    if (!board) return [];
    const rows: Record<string, number> = {};
    return board.cards.map((card) => {
      const zoneIndex = board.zones.findIndex((z) => z.key === card.zone);
      const row = rows[card.zone] ?? 0;
      rows[card.zone] = row + 1;
      return { card, ...position(Math.max(zoneIndex, 0), row) };
    });
  }, [board]);

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

  const tallest = Math.max(...(board?.zones.map((z) => z.count) ?? [0]), 1);

  return (
    <div className="fixed inset-0 flex flex-col lg:left-56">
      {/* --- the bar ---------------------------------------------------- */}
      <header className="z-20 flex flex-wrap items-center gap-3 border-b border-line bg-surface px-4 py-2.5">
        <h1 className="text-sm font-semibold text-ink">The month</h1>
        <input
          ref={search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a client    /"
          className="w-56 rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
        />
        {needle && (
          <span className="text-xs text-muted">
            {found} matching
          </span>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" disabled={busy} onClick={() => void chase(true)}>
            {busy ? "Working…" : "Show me what it would do"}
          </Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => void chase(false)}>
            Chase what needs chasing
          </Button>
          <Link href="/command-centre">
            <Button size="sm" variant="ghost">
              List view
            </Button>
          </Link>
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

      {error && (
        <div className="z-20 px-4 py-2">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      {/* --- the board -------------------------------------------------- */}
      <div className="flex min-h-0 flex-1">
      <div ref={stage} className="relative min-h-0 flex-1 bg-surface-sunken">
        <Canvas viewport={viewport} onViewportChange={setViewport}>
          <div
            style={{
              width: (board?.zones.length ?? 5) * (COLUMN_WIDTH + COLUMN_GAP),
              height: HEADER + tallest * (CARD_HEIGHT + CARD_GAP) + 80,
            }}
          >
            {board?.zones.map((zone, index) => (
              <div
                key={zone.key}
                className="absolute"
                style={{ left: index * (COLUMN_WIDTH + COLUMN_GAP), top: 0, width: COLUMN_WIDTH }}
              >
                <div
                  className={`h-1 w-full rounded-full ${(ZONE_TONE[zone.key] ?? ZONE_TONE.clear).bar}`}
                />
                <p className="mt-2 text-sm font-semibold text-ink">
                  {zone.label}{" "}
                  <span className="tnum font-normal text-muted">{zone.count}</span>
                </p>
                <p className="mt-0.5 text-xs text-muted">{zone.note}</p>
              </div>
            ))}

            {placed.map(({ card, left, top }) => (
              <div key={card.client_id} className="absolute" style={{ left, top }}>
                <Card
                  card={card}
                  dimmed={!matches(card)}
                  selected={selected?.client_id === card.client_id}
                  onOpen={() => setSelected(card)}
                />
              </div>
            ))}
          </div>
        </Canvas>

        {needle && found === 0 && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <p className="rounded-lg border border-line bg-surface px-4 py-3 text-sm text-ink-2 shadow-sm">
              No client here matches <span className="font-medium text-ink">{query}</span>.
            </p>
          </div>
        )}

        {/* --- the inspector -------------------------------------------- */}
        {/* closes the stage; the inspector is a sibling so it takes space
            rather than covering the columns on the right. */}
        <p className="pointer-events-none absolute bottom-3 left-4 z-10 text-xs text-muted">
          Drag to pan · ⌘/ctrl + scroll to zoom · / to find a client · Esc to close
        </p>
        <div className="absolute bottom-3 right-4 z-10 flex gap-1.5">
          <Button size="sm" onClick={fit}>
            Fit
          </Button>
        </div>
      </div>

        {selected && (
          <BoardInspector
            card={selected}
            onClose={() => setSelected(null)}
            onChanged={load}
          />
        )}
      </div>
    </div>
  );
}
