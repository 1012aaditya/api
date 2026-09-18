import type { CheckStatus, ValidationCheck } from "@/lib/types";
import { StatusBadge, StatusDot } from "./ui";

const HUMAN_NAME: Record<string, string> = {
  required_fields_present: "Required fields present",
  supplier_gstin_format: "Supplier GSTIN format",
  buyer_gstin_format: "Buyer GSTIN format",
  supply_type_consistency: "Supply type (CGST+SGST vs IGST)",
  cgst_sgst_split: "CGST / SGST split",
  invoice_total: "Invoice total reconciles",
  taxable_amount_consistency: "Taxable amount consistency",
  line_item_calculation: "Line item arithmetic",
  line_item_sum: "Line items sum to the invoice",
};

const OVERALL_COPY: Record<CheckStatus, string> = {
  passed: "Every check that could run passed.",
  warning: "Extracted, with something worth a look.",
  failed: "The document is internally inconsistent.",
  not_checked: "Nothing could be checked on this document.",
};

export function ValidationReport({
  overall,
  checks,
}: {
  overall: CheckStatus;
  checks: ValidationCheck[];
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <p className="text-sm text-ink-2">{OVERALL_COPY[overall]}</p>
        <StatusBadge status={overall} />
      </div>
      <ul className="divide-y divide-line">
        {checks.map((check) => (
          <li key={check.name} className="flex items-start gap-2.5 px-5 py-3">
            <StatusDot status={check.status} />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-ink">
                {HUMAN_NAME[check.name] ?? check.name}
              </p>
              {check.message && (
                <p className="mt-0.5 text-sm text-ink-2">{check.message}</p>
              )}
            </div>
            <span className="shrink-0 text-xs text-muted">
              {check.status.replace("_", " ")}
            </span>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-5 py-3 text-xs text-muted">
        “Not checked” means the document did not carry what the check needs — it
        is not a pass. GSTIN checks are format only, not a GSTN lookup.
      </p>
    </div>
  );
}
