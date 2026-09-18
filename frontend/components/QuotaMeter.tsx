/**
 * One ratio against a limit, so: a meter, not a two-slice pie.
 *
 * The unfilled track is a lighter step of the same blue ramp, so the state
 * reads across the whole bar. The fill shifts to a status colour as it
 * approaches the limit — and the numbers beside it say the same thing, so the
 * colour is never carrying the meaning alone.
 */
export function QuotaMeter({
  used,
  quota,
  periodStart,
}: {
  used: number;
  quota: number;
  periodStart: string;
}) {
  const ratio = quota > 0 ? Math.min(used / quota, 1) : 0;
  const percentUsed = Math.round(ratio * 100);

  const fill =
    ratio >= 1 ? "bg-critical" : ratio >= 0.8 ? "bg-warning" : "bg-accent";
  const note =
    ratio >= 1
      ? "Quota exhausted — further extractions return 403 quota_exceeded."
      : ratio >= 0.8
        ? "Approaching the monthly limit."
        : `Resets on the 1st. Period started ${new Date(periodStart).toLocaleDateString(
            undefined,
            { day: "numeric", month: "short", year: "numeric" },
          )}.`;

  return (
    <div className="px-5 py-4">
      <div className="flex items-baseline justify-between gap-4">
        <p className="text-sm text-ink-2">Documents this month</p>
        <p className="tnum text-sm text-ink">
          {/* Both sides in full: "8 / 1K" mixes two number formats in one
              ratio and reads as a typo. */}
          <span className="font-semibold">{used.toLocaleString()}</span>
          <span className="text-muted"> / {quota.toLocaleString()}</span>
        </p>
      </div>
      <div
        className="mt-2.5 h-2 w-full overflow-hidden rounded-full bg-accent-100"
        role="meter"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={quota}
        aria-label="Monthly document quota used"
      >
        <div
          className={`h-full rounded-full transition-[width] ${fill}`}
          style={{ width: `${Math.max(ratio * 100, used > 0 ? 1.5 : 0)}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-muted">
        {percentUsed}% used. {note}
      </p>
    </div>
  );
}
