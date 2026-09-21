"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Wordmark } from "@/components/Wordmark";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

/**
 * No navigation.
 *
 * The board is the product: every client, and everything the firm does
 * about them, is worked from there. A column of twenty links beside it was
 * a second way to reach the same things, and two ways to do one job is how
 * an interface starts lying about where the work happens.
 *
 * The pages are still here — a long list beats a board when you want to
 * sort or export — but they are reached from the board (the "You" card) and
 * they carry one link back to it, which is all the navigation left.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  // The board draws its own chrome edge to edge; anything the layout added
  // around it would be a second header.
  if (pathname === "/board") return <>{children}</>;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 flex items-center gap-4 border-b border-line bg-surface px-4 py-2.5">
        <Link href="/board" aria-label="The board">
          <Wordmark />
        </Link>
        <Link
          href="/board"
          className="rounded-md px-2 py-1 text-sm text-ink-2 transition-colors hover:bg-surface-sunken hover:text-ink"
        >
          ← Back to the board
        </Link>
        <span className="ml-auto truncate text-xs text-muted">
          {user.organization.name} · {user.email}
        </span>
      </header>

      <main className="px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
