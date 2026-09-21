"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Wordmark } from "@/components/Wordmark";
import { Button, Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

/* Two jobs share this dashboard: running a practice, and running the API
 * that serves it. Splitting the nav keeps the daily work at the top rather
 * than mixed in with the developer pages. */
type NavItem = { href: string; label: string; privileged?: boolean };

const NAV: { heading: string; items: NavItem[] }[] = [
  {
    heading: "Practice",
    items: [
      { href: "/command-centre", label: "Today" },
      { href: "/clients", label: "Clients" },
      { href: "/cases", label: "Cases" },
      { href: "/exceptions", label: "Needs a person" },
      { href: "/tasks", label: "Tasks" },
      { href: "/conversations", label: "Conversations" },
      { href: "/agent", label: "Agent" },
      { href: "/team", label: "Your firm" },
      { href: "/privacy", label: "Client data" },
      { href: "/billing", label: "Plan and usage" },
    ],
  },
  {
    heading: "Developer",
    items: [
      { href: "/playground", label: "Playground" },
      { href: "/batches", label: "Bulk upload" },
      { href: "/documents", label: "Documents" },
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

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const mayAdminister = user?.role === "owner" || user?.role === "admin";

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    setMenuOpen(false);
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
        <Link href="/command-centre">
          <Wordmark />
        </Link>
        <Button size="sm" variant="ghost" onClick={() => setMenuOpen((o) => !o)}>
          {menuOpen ? "Close" : "Menu"}
        </Button>
      </header>

      <aside
        className={`${
          menuOpen ? "block" : "hidden"
        } border-b border-line bg-surface lg:sticky lg:top-0 lg:flex lg:h-screen lg:w-56 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r`}
      >
        <div className="hidden px-5 py-5 lg:block">
          <Link href="/command-centre">
            <Wordmark />
          </Link>
        </div>

        <nav className="px-3 py-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:py-0">
          {NAV.map((section) => {
            // A link that can only answer 403 is not a link. The API refuses a
            // staff login's key management outright, so it is left out rather
            // than shown and then apologised for.
            const items = section.items.filter((item) => !item.privileged || mayAdminister);
            if (items.length === 0) return null;
            return (
            <div key={section.heading} className="mb-4 last:mb-0">
              <p className="px-3 pb-1.5 text-xs font-medium uppercase tracking-wide text-muted">
                {section.heading}
              </p>
              <ul className="space-y-0.5">
                {items.map((item) => {
                  const active =
                    pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={`block rounded-md px-3 py-2 text-sm transition-colors ${
                          active
                            ? "bg-surface-sunken font-medium text-ink"
                            : "text-ink-2 hover:bg-surface-sunken hover:text-ink"
                        }`}
                      >
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
            );
          })}
        </nav>

        <div className="mt-4 border-t border-line px-5 py-4 lg:mt-0">
          <p className="truncate text-sm font-medium text-ink">
            {user.organization.name}
          </p>
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
