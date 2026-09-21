"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { contactState } from "@/lib/practice";
import type { PracticeClient } from "@/lib/types";

const BLANK = {
  name: "",
  business_name: "",
  client_code: "",
  whatsapp_phone: "",
  gstin: "",
};

/** A firm with five hundred clients must not silently see two hundred. */
const PAGE = 100;

export default function ClientsPage() {
  const [clients, setClients] = useState<PracticeClient[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState(BLANK);

  const load = useCallback(async (term: string, offset = 0) => {
    setError(null);
    try {
      const query = term.trim() ? `&search=${encodeURIComponent(term.trim())}` : "";
      const page = await apiGet<PracticeClient[]>(
        `/v1/clients?limit=${PAGE}&offset=${offset}${query}`,
      );
      setClients((current) => (offset === 0 ? page : [...current, ...page]));
      // A full page means there may be another. One extra request at the end
      // beats a count query on every keystroke.
      setMore(page.length === PAGE);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Debounced so typing a name is one request, not one per keystroke.
    const timer = setTimeout(() => void load(search), 250);
    return () => clearTimeout(timer);
  }, [search, load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      // Empty strings are "not given", not a value the API should store.
      const body = Object.fromEntries(
        Object.entries(draft).filter(([, value]) => value.trim() !== ""),
      );
      await apiSend<PracticeClient>("/v1/clients", body);
      setDraft(BLANK);
      setAdding(false);
      await load(search);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Clients"
        description="Everyone whose filings you handle, and where each one stands."
        action={
          <div className="flex items-center gap-2">
            <Link href="/clients/import">
              <Button variant="secondary">Import a list</Button>
            </Link>
            <Button variant="primary" onClick={() => setAdding((open) => !open)}>
              {adding ? "Cancel" : "Add a client"}
            </Button>
          </div>
        }
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice
            message={error.message}
            code={error.code}
            requestId={error.requestId}
          />
        </div>
      )}

      {adding && (
        <Card className="mb-6">
          <CardHeader
            title="New client"
            description="The WhatsApp number is how the agent reaches them. Without it, nothing is sent."
          />
          <form onSubmit={create} className="grid gap-4 px-5 py-4 sm:grid-cols-2">
            <Field label="Name">
              <Input
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="ABC Traders"
              />
            </Field>
            <Field label="Registered name">
              <Input
                value={draft.business_name}
                onChange={(e) => setDraft({ ...draft, business_name: e.target.value })}
                placeholder="ABC Traders Pvt Ltd"
              />
            </Field>
            <Field label="Your code for them">
              <Input
                value={draft.client_code}
                onChange={(e) => setDraft({ ...draft, client_code: e.target.value })}
                placeholder="C001"
              />
            </Field>
            <Field label="WhatsApp number">
              <Input
                value={draft.whatsapp_phone}
                onChange={(e) => setDraft({ ...draft, whatsapp_phone: e.target.value })}
                placeholder="+9198…"
              />
            </Field>
            <Field label="GSTIN" hint="Used to tell their invoices from somebody else's.">
              <Input
                value={draft.gstin}
                onChange={(e) =>
                  setDraft({ ...draft, gstin: e.target.value.toUpperCase() })
                }
                placeholder="29AABCU9603R1ZJ"
              />
            </Field>
            <div className="flex items-end">
              <Button type="submit" variant="primary" disabled={saving}>
                {saving ? "Saving…" : "Add client"}
              </Button>
            </div>
          </form>
        </Card>
      )}

      <Card>
        <CardHeader
          title="All clients"
          description={`${clients.length} shown`}
          action={
            // Input is w-full by design; the width belongs to the wrapper,
            // not to a second width class fighting it.
            <div className="w-56 shrink-0">
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name or code"
                aria-label="Search clients"
              />
            </div>
          }
        />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : clients.length === 0 ? (
          <EmptyState
            title={search ? "No client matches that" : "No clients yet"}
            description={
              search
                ? "Try a different name or code."
                : "Add your first client and the agent can start collecting their documents."
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Client</th>
                  <th className="px-3 py-2 font-medium">Where we stand</th>
                  <th className="px-3 py-2 text-right font-medium">Open</th>
                  <th className="px-3 py-2 text-right font-medium">Blocked</th>
                  <th className="px-5 py-2 font-medium">Last contacted</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {clients.map((client) => {
                  const look = contactState(client.contact_state);
                  return (
                    <tr key={client.id}>
                      <td className="px-5 py-3">
                        <Link
                          href={`/clients/${client.id}`}
                          className="text-ink underline-offset-2 hover:underline"
                        >
                          {client.business_name ?? client.name}
                        </Link>
                        <p className="text-xs text-muted">
                          {client.client_code ?? "No code"}
                          {client.gstin ? ` · ${client.gstin}` : ""}
                        </p>
                      </td>
                      <td className="px-3 py-3">
                        <StatusBadge status={look.tone} label={look.label} />
                        {!client.allow_automated_contact && (
                          <p className="mt-1 text-xs text-muted">Automation off</p>
                        )}
                      </td>
                      <td className="tnum px-3 py-3 text-right text-ink-2">
                        {client.open_cases}
                      </td>
                      <td className="tnum px-3 py-3 text-right text-ink-2">
                        {client.blocked_cases}
                      </td>
                      <td className="px-5 py-3 text-xs text-muted">
                        {relativeTime(client.last_contacted_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {more && (
          <div className="border-t border-line px-5 py-4 text-center">
            <Button onClick={() => void load(search, clients.length)}>
              Load more
            </Button>
          </div>
        )}
      </Card>
    </>
  );
}
