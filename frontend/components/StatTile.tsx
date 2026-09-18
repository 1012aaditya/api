import type { ReactNode } from "react";

/**
 * A headline number. Deliberately not a one-bar bar chart — a single current
 * value reads faster as a figure than as a mark on an axis.
 *
 * `value` uses the font's proportional figures: tabular figures give every
 * digit the width of a zero, which looks loose at display sizes. Columns of
 * numbers (tables, axis ticks) use `.tnum` instead.
 */
export function StatTile({
  label,
  value,
  caption,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  caption?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3.5">
      <p className="text-xs font-medium text-ink-2">{label}</p>
      <p
        className={`mt-1.5 text-2xl font-semibold leading-none ${
          accent ? "text-accent" : "text-ink"
        }`}
      >
        {value}
      </p>
      {caption && <p className="mt-1.5 text-xs text-muted">{caption}</p>}
    </div>
  );
}
