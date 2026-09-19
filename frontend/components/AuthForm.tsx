"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ApiRequestError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, ErrorNotice, Field, Input } from "./ui";
import { Wordmark } from "./Wordmark";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const { user, loading, login, signup } = useAuth();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/command-centre");
  }, [user, loading, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") await login(email, password);
      else await signup(email, password, organization);
      router.push("/command-centre");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught
          : new ApiRequestError(
              0,
              { code: "unexpected_error", message: String(caught) },
              null,
            ),
      );
    } finally {
      setSubmitting(false);
    }
  }

  const isSignup = mode === "signup";

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <Wordmark className="justify-center" />
          <p className="mt-3 text-sm text-ink-2">
            {isSignup
              ? "Create an account to get an API key."
              : "Sign in to your dashboard."}
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-line bg-surface p-6">
          {error && (
            <ErrorNotice
              title={isSignup ? "Could not create the account" : "Could not sign in"}
              message={error.message}
              code={error.code}
              requestId={error.requestId}
            />
          )}

          <Field label="Email">
            <Input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </Field>

          <Field
            label="Password"
            hint={isSignup ? "At least 10 characters." : undefined}
          >
            <Input
              type="password"
              autoComplete={isSignup ? "new-password" : "current-password"}
              required
              minLength={isSignup ? 10 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {isSignup && (
            <Field label="Organization" hint="Optional. You can change it later.">
              <Input
                value={organization}
                onChange={(e) => setOrganization(e.target.value)}
                placeholder="Acme Accounting"
              />
            </Field>
          )}

          <Button type="submit" variant="primary" className="w-full" disabled={submitting}>
            {submitting
              ? isSignup
                ? "Creating account…"
                : "Signing in…"
              : isSignup
                ? "Create account"
                : "Sign in"}
          </Button>
        </form>

        <p className="mt-4 text-center text-sm text-ink-2">
          {isSignup ? "Already have an account? " : "No account yet? "}
          <Link
            href={isSignup ? "/login" : "/signup"}
            className="text-accent underline underline-offset-2"
          >
            {isSignup ? "Sign in" : "Create one"}
          </Link>
        </p>
      </div>
    </main>
  );
}
