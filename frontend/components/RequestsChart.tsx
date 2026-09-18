"use client";

import { useEffect, useRef, useState } from "react";

import { shortDay } from "@/lib/format";
import type { DailyPoint } from "@/lib/types";

/**
 * Requests per day, split succeeded / failed: part-to-whole over time, so a
 * stacked column chart.
 *
 * The two hues were validated against this surface rather than chosen by eye
 * (CVD ΔE 23.8, normal-vision ΔE 31.6, both ≥ 3:1 contrast). Segments are
 * separated by a 2px gap in the surface colour rather than a stroke — the gap
 * is the separator; a border would add ink that isn't data.
 */

const SUCCEEDED = "#2a78d6";
const FAILED = "#d03b3b";
const SURFACE = "#ffffff";

const DEFAULT_W = 720;
const VIEW_H = 240;
const PAD = { top: 16, right: 12, bottom: 30, left: 46 };
const PLOT_H = VIEW_H - PAD.top - PAD.bottom;
const MAX_BAR_W = 24;
const SEGMENT_GAP = 2;
const CORNER = 4;

/** Rounded at the data end, square at the baseline. */
function columnPath(x: number, y: number, w: number, h: number, round: boolean): string {
  if (h <= 0) return "";
  if (!round) return `M${x},${y}h${w}v${h}h${-w}Z`;
  const r = Math.min(CORNER, h, w / 2);
  return (
    `M${x},${y + h}L${x},${y + r}Q${x},${y} ${x + r},${y}` +
    `L${x + w - r},${y}Q${x + w},${y} ${x + w},${y + r}L${x + w},${y + h}Z`
  );
}

/**
 * A ceiling that divides into four whole steps.
 *
 * Rounding an arbitrary maximum produces ticks like 0 / 3 / 5 / 8 / 10, which
 * look like data rather than a scale. Snapping the top to a multiple of four
 * clean units keeps every tick a round number.
 */
function niceScale(value: number): { max: number; ticks: number[] } {
  const target = Math.max(value, 4);
  const magnitude = 10 ** Math.floor(Math.log10(target / 4));
  for (const unit of [1, 2, 2.5, 5, 10, 20, 25].map((m) => m * magnitude)) {
    const step = Math.ceil(target / 4 / unit) * unit;
    const max = step * 4;
    if (Number.isInteger(step) && max >= target) {
      return { max, ticks: [0, step, step * 2, step * 3, max] };
    }
  }
  const step = Math.ceil(target / 4);
  return { max: step * 4, ticks: [0, step, step * 2, step * 3, step * 4] };
}

/**
 * The rendered width, so labels keep their real pixel size on a phone.
 *
 * The element being measured must not be stretchable by what it contains.
 * Give the SVG a pixel width and it widens its own container, the observer
 * reads that widened value, and the measurement settles on a wrong number
 * that never shrinks — so the SVG below is sized in CSS percent and only its
 * viewBox uses the measurement.
 */
function useMeasuredWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(DEFAULT_W);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width);
      if (next > 0) setWidth(next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width] as const;
}

export function RequestsChart({ daily }: { daily: DailyPoint[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [plotRef, viewW] = useMeasuredWidth<HTMLDivElement>();

  if (daily.length === 0) {
    return (
      <div className="px-5 py-12 text-center text-sm text-ink-2">
        No requests in this period yet. Run an extraction from the{" "}
        <a href="/playground" className="text-accent underline underline-offset-2">
          playground
        </a>{" "}
        to see it here.
      </div>
    );
  }

  const plotW = Math.max(120, viewW - PAD.left - PAD.right);
  const { max, ticks } = niceScale(Math.max(...daily.map((d) => d.requests), 1));
  const band = plotW / daily.length;
  const barW = Math.min(MAX_BAR_W, Math.max(2, band - 4));
  const yOf = (value: number) => PAD.top + PLOT_H - (value / max) * PLOT_H;
  const uniqueTicks = [...new Set(ticks)];

  // Label roughly six x positions, never all of them.
  const labelEvery = Math.max(1, Math.ceil(daily.length / 6));
  const active = hovered !== null ? daily[hovered] : null;

  return (
    <div className="px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        {/* Two series, so a legend is always present — identity never rests
            on colour-matching alone. */}
        <div className="flex items-center gap-4">
          <LegendKey color={SUCCEEDED} label="Succeeded" />
          <LegendKey color={FAILED} label="Failed" />
        </div>
        <button
          type="button"
          onClick={() => setShowTable((open) => !open)}
          className="text-xs text-ink-2 underline underline-offset-2 hover:text-ink"
        >
          {showTable ? "Hide table" : "View as table"}
        </button>
      </div>

      <div className="relative w-full overflow-hidden" ref={plotRef}>
        <svg
          viewBox={`0 0 ${viewW} ${VIEW_H}`}
          height={VIEW_H}
          className="block w-full"
          role="img"
          aria-label={`Requests per day over ${daily.length} days, split into succeeded and failed.`}
          onMouseLeave={() => setHovered(null)}
        >
          {uniqueTicks.map((tick) => (
            <g key={tick}>
              <line
                x1={PAD.left}
                x2={viewW - PAD.right}
                y1={yOf(tick)}
                y2={yOf(tick)}
                stroke={tick === 0 ? "#c3c2b7" : "#e1e0d9"}
                strokeWidth="1"
              />
              <text
                x={PAD.left - 8}
                y={yOf(tick) + 4}
                textAnchor="end"
                className="tnum"
                fontSize="11"
                fill="#898781"
              >
                {tick.toLocaleString()}
              </text>
            </g>
          ))}

          {daily.map((point, index) => {
            const x = PAD.left + index * band + (band - barW) / 2;
            const baseline = PAD.top + PLOT_H;
            const failedH = (point.failed / max) * PLOT_H;
            const successH = (point.successful / max) * PLOT_H;
            const bothPresent = point.failed > 0 && point.successful > 0;
            const gap = bothPresent ? SEGMENT_GAP : 0;

            const successY = baseline - successH;
            const failedY = successY - gap - failedH;
            const isHovered = hovered === index;

            return (
              <g key={point.day}>
                {point.successful > 0 && (
                  <path
                    d={columnPath(
                      x,
                      successY,
                      barW,
                      successH,
                      point.failed === 0,
                    )}
                    fill={SUCCEEDED}
                    opacity={hovered === null || isHovered ? 1 : 0.45}
                  />
                )}
                {point.failed > 0 && (
                  <path
                    d={columnPath(x, failedY, barW, failedH, true)}
                    fill={FAILED}
                    opacity={hovered === null || isHovered ? 1 : 0.45}
                  />
                )}
                {index % labelEvery === 0 && (
                  <text
                    x={x + barW / 2}
                    y={baseline + 18}
                    textAnchor="middle"
                    className="tnum"
                    fontSize="11"
                    fill="#898781"
                  >
                    {shortDay(point.day)}
                  </text>
                )}
                {/* A hit target wider than the mark, so hovering is easy. */}
                <rect
                  x={PAD.left + index * band}
                  y={PAD.top}
                  width={band}
                  height={PLOT_H}
                  fill="transparent"
                  onMouseEnter={() => setHovered(index)}
                />
                {isHovered && (
                  <line
                    x1={x + barW / 2}
                    x2={x + barW / 2}
                    y1={PAD.top}
                    y2={baseline}
                    stroke="#c3c2b7"
                    strokeWidth="1"
                  />
                )}
                {/* The surface-coloured gap that separates the two segments. */}
                {bothPresent && (
                  <rect
                    x={x}
                    y={successY - gap}
                    width={barW}
                    height={gap}
                    fill={SURFACE}
                  />
                )}
              </g>
            );
          })}
        </svg>

        {active && hovered !== null && (
          <div
            className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm"
            style={{
              left: `${((PAD.left + hovered * band + band / 2) / viewW) * 100}%`,
            }}
          >
            <p className="font-medium text-ink">
              {new Date(active.day).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
            </p>
            <TooltipRow color={SUCCEEDED} label="Succeeded" value={active.successful} />
            <TooltipRow color={FAILED} label="Failed" value={active.failed} />
            <p className="tnum mt-1 border-t border-line pt-1 text-ink-2">
              {active.documents} document{active.documents === 1 ? "" : "s"}
            </p>
          </div>
        )}
      </div>

      {showTable && (
        <div className="mt-4 max-h-64 overflow-auto rounded-md border border-line">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-surface-sunken text-ink-2">
              <tr>
                <th className="px-3 py-2 font-medium">Day</th>
                <th className="px-3 py-2 text-right font-medium">Requests</th>
                <th className="px-3 py-2 text-right font-medium">Succeeded</th>
                <th className="px-3 py-2 text-right font-medium">Failed</th>
                <th className="px-3 py-2 text-right font-medium">Documents</th>
              </tr>
            </thead>
            <tbody className="tnum">
              {daily.map((point) => (
                <tr key={point.day} className="border-t border-line">
                  <td className="px-3 py-1.5 text-ink">{point.day}</td>
                  <td className="px-3 py-1.5 text-right text-ink-2">{point.requests}</td>
                  <td className="px-3 py-1.5 text-right text-ink-2">{point.successful}</td>
                  <td className="px-3 py-1.5 text-right text-ink-2">{point.failed}</td>
                  <td className="px-3 py-1.5 text-right text-ink-2">{point.documents}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LegendKey({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      <span
        className="h-2.5 w-2.5 rounded-sm"
        style={{ background: color }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function TooltipRow({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: number;
}) {
  return (
    <span className="mt-1 flex items-center gap-1.5">
      <span
        className="h-2 w-2 rounded-sm"
        style={{ background: color }}
        aria-hidden="true"
      />
      <span className="text-ink-2">{label}</span>
      <span className="tnum ml-auto pl-3 font-medium text-ink">{value}</span>
    </span>
  );
}
