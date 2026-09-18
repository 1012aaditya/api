/** Small display helpers. Nothing here changes a value's meaning. */

export function compactNumber(value: number): string {
  if (Math.abs(value) < 1000) return value.toLocaleString();
  if (Math.abs(value) < 1_000_000)
    return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}K`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

export function percent(value: number | null, digits = 1): string {
  // Null means "not measurable", which is not the same as zero.
  if (value === null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function duration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function usd(value: number | null): string {
  // Null is "we have no configured rates", not "free".
  if (value === null) return "—";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(2)}`;
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "Never";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 2_592_000) return `${Math.floor(seconds / 86_400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function shortDay(day: string): string {
  const [, month, date] = day.split("-");
  return `${date}/${month}`;
}
