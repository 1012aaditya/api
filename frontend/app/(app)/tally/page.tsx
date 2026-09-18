"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { ApiRequestError, apiDownload, apiGet, apiSend, apiUpload } from "@/lib/api";
import type {
  Ledger,
  LedgerImportResult,
  TallyPreview,
  TallySettings,
  UnmatchedSupplier,
} from "@/lib/types";

/** The ledgers a purchase voucher needs, in the order a bookkeeper fills them. */
const LEDGER_FIELDS: { key: keyof TallySettings; label: string; hint: string }[] = [
  { key: "purchase_ledger", label: "Purchase", hint: "Required — the expense side." },
  { key: "cgst_ledger", label: "Input CGST", hint: "Needed for intrastate invoices." },
  { key: "sgst_ledger", label: "Input SGST", hint: "Needed for intrastate invoices." },
  { key: "igst_ledger", label: "Input IGST", hint: "Needed for interstate invoices." },
  { key: "cess_ledger", label: "Cess", hint: "Only if your suppliers charge it." },
  { key: "round_off_ledger", label: "Round off", hint: "Where rounding differences go." },
  { key: "other_charges_ledger", label: "Other charges", hint: "Freight, packing and the like." },
];

export default function TallyPage() {
  const [settings, setSettings] = useState<TallySettings | null>(null);
  const [preview, setPreview] = useState<TallyPreview | null>(null);
  const [ledgers, setLedgers] = useState<Ledger[]>([]);
  const [importResult, setImportResult] = useState<LedgerImportResult | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [nextSettings, nextPreview, nextLedgers] = await Promise.all([
        apiGet<TallySettings>("/v1/tally/settings"),
        apiGet<TallyPreview>("/v1/tally/preview?limit=100"),
        apiGet<Ledger[]>("/v1/tally/ledgers?limit=2000"),
      ]);
      setSettings(nextSettings);
      setPreview(nextPreview);
      setLedgers(nextLedgers);
      setError(null);
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

  async function importMaster(file: File) {
    setBusy(true);
    setError(null);
    try {
      setImportResult(await apiUpload<{ data: LedgerImportResult }>("/v1/tally/ledgers", file).then((r) => (r as unknown as { data: LedgerImportResult }).data));
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function saveSettings() {
    if (!settings) return;
    setBusy(true);
    setError(null);
    try {
      const { configured: _c, ledger_count: _l, unknown_ledgers: _u, ...body } = settings;
      setSettings(await apiSend<TallySettings>("/v1/tally/settings", body, "PUT"));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  async function confirmMatch(supplier: UnmatchedSupplier, ledgerId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiSend("/v1/tally/matches", {
        ledger_id: ledgerId,
        supplier_name: supplier.name,
        supplier_gstin: supplier.gstin,
      });
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    setError(null);
    try {
      await apiDownload("/v1/tally/vouchers.xml", "tally-vouchers.xml");
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    }
  }

  function update(key: keyof TallySettings, value: string) {
    setSettings((current) =>
      current ? { ...current, [key]: value || null } : current,
    );
  }

  if (loading) {
    return (
      <div className="flex min-h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const ready = (preview?.postable ?? 0) > 0;
  // A long run of identical blockers is noise: the actionable version of
  // it is already one line in 'suppliers we could not place'.
  const VISIBLE_ROWS = 25;
  const rows = preview?.vouchers.slice(0, VISIBLE_ROWS) ?? [];
  const hidden = (preview?.vouchers.length ?? 0) - rows.length;

  return (
    <>
      <PageHeader
        title="Post to Tally"
        description="Match your suppliers once, then download a month of purchase vouchers."
        action={
          <Button onClick={() => void download()} disabled={!ready}>
            Download vouchers
          </Button>
        }
      />

      {error ? (
        <ErrorNotice
          message={error.message}
          code={error.code}
          requestId={error.requestId}
        />
      ) : null}

      <div className="space-y-6">
        {/* Untested against a real Tally installation. Saying so here is the
            honest thing: the file is well-formed and balanced, but nobody has
            watched it import. */}
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <strong className="font-medium">Check one voucher first.</strong> This
          file has not yet been imported into a real Tally installation by us.
          Import it into a test company and confirm one voucher before you trust
          it with a month of purchases.
        </div>

        <Card>
          <CardHeader
            title="1. Your ledger master"
            description="In Tally: Gateway → Display → List of Accounts → Export as XML."
          />
          <div className="space-y-3 p-5">
            <div className="flex flex-wrap items-center gap-3">
              <input
                ref={fileRef}
                type="file"
                accept=".xml,.csv"
                className="text-sm text-ink-2 file:mr-3 file:rounded-md file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-sm file:text-ink hover:file:bg-surface-sunken"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void importMaster(file);
                }}
              />
              {settings?.ledger_count ? (
                <Badge>{settings.ledger_count} ledgers</Badge>
              ) : null}
            </div>
            {importResult ? (
              <p className="text-sm text-ink-2">
                Imported {importResult.imported} ledgers
                {importResult.replaced > 0
                  ? `, replacing ${importResult.replaced}`
                  : ""}
                .{" "}
                {importResult.aliases_kept > 0 ? (
                  <span className="text-ink">
                    {importResult.aliases_kept} confirmed supplier match
                    {importResult.aliases_kept === 1 ? "" : "es"} kept.
                  </span>
                ) : null}
              </p>
            ) : (
              <p className="text-sm text-muted">
                A CSV with a <code className="font-mono text-xs">name</code>{" "}
                column works too. We only ever post to a ledger that is in this
                list — never one we invented.
              </p>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="2. Where the legs post"
            description="Names exactly as they appear in Tally."
          />
          <div className="space-y-4 p-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Company name in Tally" hint="Must match the open company.">
                <Input
                  value={settings?.company_name ?? ""}
                  onChange={(e) => update("company_name", e.target.value)}
                  placeholder="Acme Traders Pvt Ltd"
                />
              </Field>
              <Field label="Voucher type" hint="Usually “Purchase”.">
                <Input
                  value={settings?.voucher_type ?? "Purchase"}
                  onChange={(e) => update("voucher_type", e.target.value)}
                />
              </Field>
              {LEDGER_FIELDS.map((entry) => (
                <Field key={entry.key} label={entry.label} hint={entry.hint}>
                  <Input
                    list="ledger-names"
                    value={(settings?.[entry.key] as string | null) ?? ""}
                    onChange={(e) => update(entry.key, e.target.value)}
                    placeholder={entry.label}
                  />
                </Field>
              ))}
            </div>

            <datalist id="ledger-names">
              {ledgers.map((ledger) => (
                <option key={ledger.id} value={ledger.name} />
              ))}
            </datalist>

            {settings?.unknown_ledgers.length ? (
              <p className="rounded-md bg-surface-sunken px-3 py-2 text-sm text-ink-2">
                Not in your ledger master:{" "}
                <strong className="text-ink">
                  {settings.unknown_ledgers.join(", ")}
                </strong>
                . Tally will reject a voucher naming a ledger that does not
                exist, so create them there or fix the spelling here.
              </p>
            ) : null}

            <div className="flex items-center gap-3">
              <Button onClick={() => void saveSettings()} disabled={busy}>
                Save
              </Button>
              {saved ? <span className="text-sm text-ink-2">Saved.</span> : null}
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="3. Suppliers we could not place"
            description="Answer once. We remember it for next month."
          />
          {preview?.unmatched_suppliers.length ? (
            <ul className="divide-y divide-line">
              {preview.unmatched_suppliers.map((supplier) => (
                <li key={supplier.name} className="space-y-2 p-5">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div>
                      <p className="font-medium text-ink">{supplier.name}</p>
                      <p className="text-xs text-muted">
                        {supplier.gstin ? `${supplier.gstin} · ` : ""}
                        {supplier.documents} invoice
                        {supplier.documents === 1 ? "" : "s"}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {supplier.suggestions.map((suggestion) => (
                      <Button
                        key={suggestion.ledger_id}
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() =>
                          void confirmMatch(supplier, suggestion.ledger_id)
                        }
                      >
                        {suggestion.ledger_name}
                      </Button>
                    ))}
                    <select
                      className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink"
                      defaultValue=""
                      disabled={busy}
                      onChange={(event) => {
                        if (event.target.value) {
                          void confirmMatch(supplier, event.target.value);
                        }
                      }}
                      aria-label={`Choose a ledger for ${supplier.name}`}
                    >
                      <option value="">Choose a ledger…</option>
                      {ledgers.map((ledger) => (
                        <option key={ledger.id} value={ledger.id}>
                          {ledger.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="p-5">
              <EmptyState
                title="Every supplier is matched"
                description="Nothing here needs your attention."
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="4. What will be posted"
            description={
              preview
                ? `${preview.postable} ready · ${preview.blocked} held back`
                : undefined
            }
          />
          {preview?.vouchers.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-5 py-2 font-medium">Invoice</th>
                    <th className="px-3 py-2 font-medium">Supplier</th>
                    <th className="px-3 py-2 font-medium">Ledger</th>
                    <th className="px-3 py-2 text-right font-medium">Total</th>
                    <th className="px-5 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((voucher) => (
                    <tr key={voucher.document_id}>
                      <td className="px-5 py-2.5 text-ink">
                        {voucher.invoice_number ?? (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-ink-2">
                        {voucher.supplier_name ?? "—"}
                      </td>
                      <td className="px-3 py-2.5 text-ink-2">
                        {voucher.ledger_name ?? (
                          <span className="text-muted">not matched</span>
                        )}
                      </td>
                      <td className="tnum px-3 py-2.5 text-right text-ink">
                        {voucher.total ?? "—"}
                      </td>
                      <td className="px-5 py-2.5">
                        {voucher.postable ? (
                          <span className="text-ink-2">Ready</span>
                        ) : (
                          <span
                            className="line-clamp-2 text-[#d03b3b]"
                            title={voucher.blockers.join(" ")}
                          >
                            {voucher.blockers[0] ?? "Held back"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-5">
              <EmptyState
                title="Nothing extracted yet"
                description="Upload invoices first — they appear here once they have been read."
              />
            </div>
          )}
          {hidden > 0 ? (
            <p className="border-t border-line px-5 py-2.5 text-xs text-muted">
              Showing {rows.length} of {preview?.vouchers.length}. The other{" "}
              {hidden} are counted above.
            </p>
          ) : null}
          <p className="border-t border-line px-5 py-3 text-xs text-muted">
            A held-back invoice is left out of the file rather than guessed at. A
            missing entry is something you notice; a wrong one quietly
            reconciles.{" "}
            <Link href="/batches" className="text-accent underline underline-offset-2">
              Bulk upload
            </Link>{" "}
            adds more invoices.
          </p>
        </Card>
      </div>
    </>
  );
}
