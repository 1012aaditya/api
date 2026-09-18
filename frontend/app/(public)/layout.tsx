"use client";

import Link from "next/link";

import { Wordmark } from "@/components/Wordmark";
import { useAuth } from "@/lib/auth";

/**
 * Chrome for pages that do not need an account.
 *
 * The API reference is the thing a developer reads *before* deciding to sign
 * up, so putting it behind the dashboard's auth gate would hide it from
 * exactly the people it is written for. It lives here instead, at the same
 * `/docs` URL the dashboard's sidebar points at — one page, one address,
 * whether or not anyone is signed in.
 */
export default function PublicLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  return (
    <div className="flex min-h-screen flex-col bg-surface-sunken">
      <header className="sticky top-0 z-10 border-b border-line bg-surface">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link href="/" className="shrink-0">
            <Wordmark />
          </Link>

          <nav className="flex items-center gap-1 text-sm">
            <Link
              href="/docs"
              className="rounded-md px-3 py-1.5 text-ink-2 transition-colors hover:bg-surface-sunken hover:text-ink"
            >
              Docs
            </Link>

            {/* Rendered only once auth has resolved, so the header does not
                flip from "Sign in" to "Dashboard" a moment after paint. */}
            {loading ? (
              <span className="px-3 py-1.5 text-transparent" aria-hidden>
                Sign in
              </span>
            ) : user ? (
              <Link
                href="/dashboard"
                className="rounded-md bg-accent px-3 py-1.5 font-medium text-white transition-opacity hover:opacity-90"
              >
                Dashboard
              </Link>
            ) : (
              <>
                <Link
                  href="/login"
                  className="rounded-md px-3 py-1.5 text-ink-2 transition-colors hover:bg-surface-sunken hover:text-ink"
                >
                  Sign in
                </Link>
                <Link
                  href="/signup"
                  className="rounded-md bg-accent px-3 py-1.5 font-medium text-white transition-opacity hover:opacity-90"
                >
                  Create account
                </Link>
              </>
            )}
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">{children}</main>

      <footer className="border-t border-line bg-surface">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-muted sm:px-6">
          <p>DocuParse — GST invoices as structured, validated JSON.</p>
          <p>
            No accuracy figure is published, because none has been measured.
          </p>
        </div>
      </footer>
    </div>
  );
}
