"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Wordmark } from "@/components/Wordmark";
import { Button, Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/playground", label: "Playground" },
  { href: "/documents", label: "Documents" },
  { href: "/usage", label: "Usage" },
  { href: "/api-keys", label: "API keys" },
  { href: "/webhooks", label: "Webhooks" },
  { href: "/docs", label: "Docs" },
  { href: "/settings", label: "Settings" },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

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
        <Link href="/dashboard">
          <Wordmark />
        </Link>
        <Button size="sm" variant="ghost" onClick={() => setMenuOpen((o) => !o)}>
          {menuOpen ? "Close" : "Menu"}
        </Button>
      </header>

      <aside
        className={`${
          menuOpen ? "block" : "hidden"
        } border-b border-line bg-surface lg:sticky lg:top-0 lg:block lg:h-screen lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r`}
      >
        <div className="hidden px-5 py-5 lg:block">
          <Link href="/dashboard">
            <Wordmark />
          </Link>
        </div>

        <nav className="px-3 py-3 lg:py-0">
          <ul className="space-y-0.5">
            {NAV.map((item) => {
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
        </nav>

        <div className="mt-4 border-t border-line px-5 py-4 lg:absolute lg:bottom-0 lg:w-56">
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
