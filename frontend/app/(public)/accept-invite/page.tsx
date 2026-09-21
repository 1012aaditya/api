"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, Card, CardHeader, ErrorNotice, Field, Input, Spinner } from "@/components/ui";
import { ApiRequestError, apiSend, setToken } from "@/lib/api";
import type { TokenResponse } from "@/lib/types";

function AcceptInviteForm() {
  const params = useSearchParams();
  const router = useRouter();
  const [token, setTokenValue] = useState(params.get("token") ?? "");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const joined = await apiSend<TokenResponse>(
        "/v1/auth/accept-invite",
        { token: token.trim(), password, full_name: fullName.trim() || null },
        "POST",
        true,
      );
      setToken(joined.access_token);
      // A full load rather than a client-side push: the auth provider reads
      // the stored token when it mounts.
      window.location.href = "/board";
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md py-10">
      <Card>
        <CardHeader
          title="Join your firm"
          description="Someone at the firm invited you. Pick a password and you are in."
        />
        <form onSubmit={submit} className="space-y-4 px-5 py-5">
          {error && (
            <ErrorNotice
              title="Could not join"
              message={error.message}
              code={error.code}
              requestId={error.requestId}
            />
          )}

          {!params.get("token") && (
            <Field label="Invitation code" hint="From the link you were sent.">
              <Input
                required
                value={token}
                onChange={(e) => setTokenValue(e.target.value)}
                placeholder="Paste it here"
              />
            </Field>
          )}

          <Field label="Your name">
            <Input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Ravi Kumar"
            />
          </Field>

          <Field label="Choose a password" hint="At least ten characters.">
            <Input
              required
              type="password"
              minLength={10}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          <Button type="submit" variant="primary" disabled={busy} className="w-full">
            {busy ? "Joining…" : "Join the firm"}
          </Button>

          <p className="text-xs text-muted">
            Your email address comes from the invitation, so it cannot be
            changed here. If it is wrong, ask for a new invitation.
          </p>
        </form>
      </Card>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="py-20 text-center">
          <Spinner />
        </div>
      }
    >
      <AcceptInviteForm />
    </Suspense>
  );
}
