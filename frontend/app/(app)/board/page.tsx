"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CardDetail } from "@/components/CardDetail";
import { Canvas, type Viewport } from "@/components/Canvas";
import { Button, ErrorNotice, Spinner } from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { AgentRunResult, BoardCard, BoardData } from "@/lib/types";

/* --- layout ------------------------------------------------------------
 * Cards are laid out, never placed by hand. Position means state, so a
 * card somebody dragged would be a lie the board tells about a client.
 *
 * Opening a card does not dock a panel on the right: the card grows where
 * it stands, and the cards below and to the right of it move out of the
 * way. You never lose the place of the client you are working on.       */

const COLUMN_WIDTH = 300;
const COLUMN_GAP = 40;
const CARD_HEIGHT = 128;
const CARD_GAP = 14;
const HEADER = 92;

const DETAIL_WIDTH = 660;
const DETAIL_HEIGHT = 470;

const ZONE_TONE: Record<string, { bar: string; text: string }> = {
  needs_you: { bar: "bg-critical", text: "text-critical" },
  waiting: { bar: "bg-warning", text: "text-ink-2" },
  reading: { bar: "bg-accent", text: "text-ink-2" },
  ready: { bar: "bg-good", text: "text-good-ink" },
  clear: { bar: "bg-axis", text: "text-muted" },
};

/* --- a card, closed --------------------------------------------------- */

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
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [run, setRun] = useState<AgentRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewport, setViewport] = useState<Viewport>({ x: 48, y: 24, scale: 0.85 });
  const [glide, setGlide] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  /** Where the eye was before a card was opened, to give it back after. */
  const restore = useRef<Viewport | null>(null);
  const settle = useRef<number | null>(null);

  /** Move the view ourselves, with the animation a hand-pan must not have. */
  const glideTo = useCallback((next: Viewport) => {
    setGlide(true);
    setViewport(next);
    if (settle.current) window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => setGlide(false), 340);
  }, []);

  useEffect(() => () => {
    if (settle.current) window.clearTimeout(settle.current);
  }, []);

  /** Scale so every column is on screen, which is what "fit" has to mean
   *  on a board whose whole point is seeing the month at once. */
  const fit = useCallback(() => {
    const width = stage.current?.clientWidth ?? 0;
    const columns = board?.zones.length ?? 5;
    const content = columns * (COLUMN_WIDTH + COLUMN_GAP);
    const scale = width ? Math.min(1, Math.max(0.3, (width - 48) / content)) : 0.85;
    glideTo({ x: 24, y: 16, scale });
  }, [board, glideTo]);

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

  const needle = query.trim().toLowerCase();
  const matches = useCallback(
    (card: BoardCard) => !needle || card.name.toLowerCase().includes(needle),
    [needle],
  );
  const found = board?.cards.filter(matches).length ?? 0;

  /* --- where everything sits, given what is open --------------------- */

  const layout = useMemo(() => {
    const zones = board?.zones ?? [];
    const rows: Record<string, number> = {};
    const base = (board?.cards ?? []).map((card) => {
      const zoneIndex = Math.max(
        zones.findIndex((zone) => zone.key === card.zone),
        0,
      );
      const row = rows[card.zone] ?? 0;
      rows[card.zone] = row + 1;
      return { card, zoneIndex, row };
    });

    const open = base.find((entry) => entry.card.client_id === openId) ?? null;
    const dx = DETAIL_WIDTH - COLUMN_WIDTH;
    const dy = DETAIL_HEIGHT - CARD_HEIGHT;

    // The opened card keeps its own coordinates; only what it would cover
    // moves. That is what makes the growth read as this card, not a panel.
    const placed = base.map((entry) => ({
      ...entry,
      left:
        entry.zoneIndex * (COLUMN_WIDTH + COLUMN_GAP) +
        (open && entry.zoneIndex > open.zoneIndex ? dx : 0),
      top:
        HEADER +
        entry.row * (CARD_HEIGHT + CARD_GAP) +
        (open && entry.zoneIndex === open.zoneIndex && entry.row > open.row ? dy : 0),
    }));

    const tallest = Math.max(...zones.map((zone) => zone.count), 1);
    return {
      open,
      placed,
      columns: zones.map(
        (_, index) =>
          index * (COLUMN_WIDTH + COLUMN_GAP) + (open && index > open.zoneIndex ? dx : 0),
      ),
      width: Math.max(zones.length, 1) * (COLUMN_WIDTH + COLUMN_GAP) + (open ? dx : 0),
      height: HEADER + tallest * (CARD_HEIGHT + CARD_GAP) + (open ? dy : 0) + 80,
    };
  }, [board, openId]);

  const opened = layout.open;

  const close = useCallback(() => {
    if (!openId) return;
    setOpenId(null);
    if (restore.current) {
      glideTo(restore.current);
      restore.current = null;
    }
  }, [openId, glideTo]);

  /** Open a card where it stands, and bring the eye to it. */
  const open = useCallback(
    (card: BoardCard, left: number, top: number) => {
      const frame = stage.current;
      setOpenId(card.client_id);
      if (!frame) return;
      if (!restore.current) restore.current = viewport;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      const scale = Math.min(
        1,
        Math.max(
          0.45,
          Math.min((width - 80) / DETAIL_WIDTH, (height - 80) / DETAIL_HEIGHT),
        ),
      );
      glideTo({
        scale,
        x: width / 2 - (left + DETAIL_WIDTH / 2) * scale,
        y: height / 2 - (top + DETAIL_HEIGHT / 2) * scale,
      });
    },
    [viewport, glideTo],
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
        {needle && <span className="text-xs text-muted">{found} matching</span>}

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

            {layout.placed.map(({ card, left, top }) => {
              const isOpen = card.client_id === openId;
              return (
                <div
                  key={card.client_id}
                  className="absolute transition-[left,top] duration-300 ease-out"
                  style={{ left, top, zIndex: isOpen ? 10 : 1 }}
                >
                  {isOpen ? (
                    <div className="card-open">
                      <CardDetail
                        card={card}
                        width={DETAIL_WIDTH}
                        height={DETAIL_HEIGHT}
                        onClose={close}
                        onChanged={load}
                      />
                    </div>
                  ) : (
                    <Card
                      card={card}
                      dimmed={!matches(card) || (opened !== null && !isOpen)}
                      onOpen={() => open(card, left, top)}
                    />
                  )}
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
          {opened
            ? "Everything about them is here · Esc, or click the paper, to close"
            : "Click a card to open it in place · drag to pan · ⌘/ctrl + scroll to zoom · / to find a client"}
        </p>
        <div className="absolute bottom-3 right-4 z-10 flex gap-1.5">
          {opened && (
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
