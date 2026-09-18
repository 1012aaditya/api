"use client";

import { useState } from "react";

import type { ConfidenceBand, FieldConfidence } from "@/lib/types";
import { ConfidenceBadge } from "./ui";

const BAND_ORDER: Record<ConfidenceBand, number> = { low: 0, medium: 1, high: 2 };

/**
 * The fields the overall score is weighted on — the ones someone actually
 * posts to a ledger. Mirrors the backend's weighting.
 *
 * Without this, the default view is "the twelve lowest scores", which on a
 * clean invoice is twelve uncited address lines and none of the numbers the
 * reader came to check.
 */
const KEY_FIELDS = new Set([
  "invoice_number",
  "invoice_date",
  "supplier.name",
  "supplier.gstin",
  "buyer.name",
  "buyer.gstin",
  "tax.taxable_amount",
  "tax.cgst",
  "tax.sgst",
  "tax.igst",
  "total",
]);

export function ConfidenceTable({
  fields,
  lowConfidenceFields,
  overall,
  band,
}: {
  fields: Record<string, FieldConfidence>;
  lowConfidenceFields: string[];
  overall: number | null;
  band: ConfidenceBand | null;
}) {
  const [showAll, setShowAll] = useState(false);
  const entries = Object.entries(fields).sort(
    (a, b) =>
      BAND_ORDER[a[1].band] - BAND_ORDER[b[1].band] ||
      a[1].confidence - b[1].confidence,
  );
  // Worst first, then the headline fields — anything that needs attention,
  // plus everything a reader would check before posting the invoice.
  const summary = entries.filter(
    ([path, value]) => value.band === "low" || KEY_FIELDS.has(path),
  );
  const visible = showAll ? entries : summary;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <p className="text-sm text-ink-2">
          {lowConfidenceFields.length === 0
            ? "No fields flagged for review."
            : `${lowConfidenceFields.length} field${
                lowConfidenceFields.length === 1 ? "" : "s"
              } worth reviewing.`}
        </p>
        {overall !== null && band !== null && (
          <span className="text-xs text-ink-2">
            Overall <ConfidenceBadge band={band} value={overall} />
          </span>
        )}
      </div>

      <table className="w-full text-left text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-5 py-2 font-medium">Field</th>
            <th className="px-3 py-2 font-medium">Read from the page</th>
            <th className="px-5 py-2 text-right font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {visible.map(([path, value]) => (
            <tr key={path}>
              <td className="px-5 py-2 font-mono text-xs text-ink">{path}</td>
              <td className="px-3 py-2 text-xs text-ink-2">
                {value.source_text ? (
                  <>
                    <span className="font-mono">{value.source_text}</span>
                    {value.page !== undefined && (
                      <span className="text-muted"> · p{value.page}</span>
                    )}
                  </>
                ) : (
                  <span className="text-muted">Not cited</span>
                )}
              </td>
              <td className="px-5 py-2 text-right">
                <ConfidenceBadge band={value.band} value={value.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {entries.length > summary.length && (
        <button
          type="button"
          onClick={() => setShowAll((open) => !open)}
          className="w-full border-t border-line px-5 py-2.5 text-xs text-ink-2 hover:bg-surface-sunken hover:text-ink"
        >
          {showAll
            ? `Show key fields only`
            : `Show all ${entries.length} extracted fields`}
        </button>
      )}

      <p className="border-t border-line px-5 py-3 text-xs text-muted">
        Scores come from checkable signals — whether the model cited the
        characters it read, whether those characters appear in the PDF&apos;s text
        layer, field format, and agreement with the arithmetic checks. Nothing
        scores 1.00.
      </p>
    </div>
  );
}
