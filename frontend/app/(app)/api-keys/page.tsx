"use client";

import { useCallback, useEffect, useState } from "react";

import { CopyableCommand } from "@/components/CopyableCommand";
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
import { ApiRequestError, apiDelete, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { ApiKeySummary, CreatedApiKey } from "@/lib/types";

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeySummary[]>([]);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setKeys(await apiGet<ApiKeySummary[]>("/v1/api-keys"));
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

  async function act<T>(action: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    const key = await act(() =>
      apiSend<CreatedApiKey>("/v1/api-keys", {
        name: name.trim() || "Default key",
        environment: "live",
      }),
    );
    if (key) {
      setCreated(key);
      setName("");
      await load();
    }
  }

  async function revoke(id: string) {
    if (!window.confirm("Revoke this key? Anything using it stops working immediately.")) {
      return;
    }
    if (await act(() => apiDelete(`/v1/api-keys/${id}`))) await load();
  }

  async function rotate(id: string) {
    if (
      !window.confirm(
        "Rotate this key? A replacement is issued and the current key is revoked immediately.",
      )
    ) {
      return;
    }
    const key = await act(() => apiSend<CreatedApiKey>(`/v1/api-keys/${id}/rotate`, {}));
    if (key) {
      setCreated(key);
      await load();
    }
  }

  return (
    <>
      <PageHeader
        title="API keys"
        description="Keys authenticate your server-to-server traffic. They are stored hashed — the secret is shown once, at creation."
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

      {created && (
        <Card className="mb-6 border-accent/40">
          <CardHeader
            title="Copy your key now"
            description="This is the only time it will be shown. We store a SHA-256 hash, so we cannot show it again."
            action={
              <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>
                Done
              </Button>
            }
          />
          <div className="p-5">
            <CopyableCommand command={created.key} />
          </div>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader title="Create a key" />
        <div className="flex flex-wrap items-end gap-3 p-5">
          <div className="min-w-56 flex-1">
            <Field label="Name" hint="For your own reference, e.g. “Production” or “CI”.">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Production"
                maxLength={120}
              />
            </Field>
          </div>
          <Button variant="primary" onClick={() => void create()} disabled={busy}>
            Create key
          </Button>
        </div>
      </Card>

      <Card>
        <CardHeader title="Your keys" />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : keys.length === 0 ? (
          <EmptyState
            title="No keys yet"
            description="Create one above to start calling the API."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Key</th>
                  <th className="px-3 py-2 font-medium">Created</th>
                  <th className="px-3 py-2 font-medium">Last used</th>
                  <th className="px-5 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {keys.map((key) => (
                  <tr key={key.id} className={key.active ? "" : "opacity-60"}>
                    <td className="px-5 py-3">
                      <span className="text-ink">{key.name}</span>
                      {!key.active && (
                        <span className="ml-2">
                          <Badge>Revoked</Badge>
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3 font-mono text-xs text-ink-2">
                      {key.masked_key}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">
                      {relativeTime(key.created_at)}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">
                      {relativeTime(key.last_used_at)}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {key.active && (
                        <span className="inline-flex gap-1.5">
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => void rotate(key.id)}
                          >
                            Rotate
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={busy}
                            onClick={() => void revoke(key.id)}
                          >
                            Revoke
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
