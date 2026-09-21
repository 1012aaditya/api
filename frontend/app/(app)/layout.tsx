"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Wordmark } from "@/components/Wordmark";
import { Button, Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

/* The board is the product; this list is a fire escape.
 *
 * What needs a person, your tasks, what clients have said, the agent and
 * the documents coming in are all worked from the board itself now, so
 * their pages are kept — a long list reads better than a board when you
 * want to sort or export — but folded away rather than shown at all times.
 * Twenty links on the left made the important three impossible to see. */
type NavItem = { href: string; label: string; privileged?: boolean };

const PRIMARY: NavItem[] = [{ href: "/board", label: "The month" }];

const FOLDED: { heading: string; note: string; items: NavItem[] }[] = [
  {
    heading: "As lists",
    note: "The same work the board holds, in rows.",
    items: [
      { href: "/command-centre", label: "Today" },
      { href: "/clients", label: "Clients" },
      { href: "/cases", label: "Cases" },
      { href: "/exceptions", label: "Needs a person" },
      { href: "/tasks", label: "Tasks" },
      { href: "/conversations", label: "Conversations" },
      { href: "/agent", label: "Agent" },
      { href: "/documents", label: "Documents" },
      { href: "/team", label: "Your firm" },
      { href: "/privacy", label: "Client data" },
      { href: "/billing", label: "Plan and usage" },
    ],
  },
  {
    heading: "Developer",
    note: "The API behind all of it.",
    items: [
      { href: "/playground", label: "Playground" },
      { href: "/batches", label: "Bulk upload" },
      { href: "/tally", label: "Post to Tally" },
      { href: "/dashboard", label: "API usage" },
      { href: "/usage", label: "Usage" },
      { href: "/api-keys", label: "API keys", privileged: true },
      { href: "/webhooks", label: "Webhooks" },
      { href: "/docs", label: "Docs" },
      { href: "/settings", label: "Settings" },
    ],
  },
];

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`block rounded-md px-3 py-1.5 text-sm transition-colors ${
        active
          ? "bg-surface-sunken font-medium text-ink"
          : "text-ink-2 hover:bg-surface-sunken hover:text-ink"
      }`}
    >
      {item.label}
    </Link>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [unfolded, setUnfolded] = useState<string | null>(null);
  const mayAdminister = user?.role === "owner" || user?.role === "admin";

  const isHere = (item: NavItem) =>
    pathname === item.href || pathname.startsWith(`${item.href}/`);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    setMenuOpen(false);
    // A folded group that holds the page you are on opens itself, so you
    // are never on a page the navigation cannot show you.
    const holding = FOLDED.find((group) => group.items.some(isHere));
    setUnfolded(holding?.heading ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="min-h-screen lg:flex">
      <header className="flex items-center justify-between border-b border-line bg-surface px-4 py-3 lg:hidden">
        <Link href="/board">
          <Wordmark />
        </Link>
        <Button size="sm" variant="ghost" onClick={() => setMenuOpen((o) => !o)}>
          {menuOpen ? "Close" : "Menu"}
        </Button>
      </header>

      <aside
        className={`${
          menuOpen ? "block" : "hidden"
        } border-b border-line bg-surface lg:sticky lg:top-0 lg:flex lg:h-screen lg:w-44 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r`}
      >
        <div className="hidden px-4 py-5 lg:block">
          <Link href="/board">
            <Wordmark />
          </Link>
        </div>

        <nav className="px-2 py-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:py-0">
          <ul className="space-y-0.5">
            {PRIMARY.map((item) => (
              <li key={item.href}>
                <NavLink item={item} active={isHere(item)} />
              </li>
            ))}
          </ul>

          <p className="mt-3 px-3 text-xs leading-relaxed text-muted">
            Clients, chasing, replies, documents, tasks, your colleagues, the
            plan — all of it is on the board.
          </p>

          {FOLDED.map((group) => {
            // A link that can only answer 403 is not a link. The API refuses a
            // staff login's key management outright, so it is left out rather
            // than shown and then apologised for.
            const items = group.items.filter((item) => !item.privileged || mayAdminister);
            if (items.length === 0) return null;
            const open = unfolded === group.heading;
            return (
              <div key={group.heading} className="mt-3">
                <button
                  aria-expanded={open}
                  onClick={() => setUnfolded(open ? null : group.heading)}
                  className="flex w-full items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted hover:text-ink"
                >
                  <span
                    aria-hidden
                    className={`text-[10px] transition-transform ${open ? "rotate-90" : ""}`}
                  >
                    ▶
                  </span>
                  {group.heading}
                </button>
                {open && (
                  <>
                    <p className="px-3 pb-1 text-xs text-muted">{group.note}</p>
                    <ul className="space-y-0.5">
                      {items.map((item) => (
                        <li key={item.href}>
                          <NavLink item={item} active={isHere(item)} />
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            );
          })}
        </nav>

        <div className="mt-4 border-t border-line px-4 py-4 lg:mt-0">
          <p className="truncate text-sm font-medium text-ink">{user.organization.name}</p>
          <p className="truncate text-xs text-muted">{user.email}</p>
          <Button size="sm" variant="ghost" onClick={logout} className="mt-2 -ml-2.5">
            Sign out
          </Button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
