"use client";

import { Card, CardHeader, PageHeader } from "@/components/ui";
import { API_URL } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useAuth } from "@/lib/auth";

export default function SettingsPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account and this organization."
      />

      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <Card>
          <CardHeader title="Organization" />
          <dl className="divide-y divide-line text-sm">
            <Row label="Name" value={user.organization.name} />
            <Row label="Slug" value={user.organization.slug} mono />
            <Row label="Id" value={user.organization.id} mono />
            <Row label="Plan" value={user.organization.plan} />
          </dl>
          <p className="border-t border-line px-5 py-3 text-xs text-muted">
            Rate limits, the monthly quota and the retention window are set per
            organization on the backend. Editing them from here is not built yet.
          </p>
        </Card>

        <Card>
          <CardHeader title="Account" />
          <dl className="divide-y divide-line text-sm">
            <Row label="Email" value={user.email} />
            <Row label="Name" value={user.full_name ?? "—"} />
            <Row label="Role" value={user.role} />
            <Row label="Joined" value={dateTime(user.created_at)} />
            <Row label="User id" value={user.id} mono />
          </dl>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Connection" />
          <dl className="divide-y divide-line text-sm">
            <Row label="API base URL" value={API_URL} mono />
          </dl>
          <p className="border-t border-line px-5 py-3 text-xs text-muted">
            Set with <code className="font-mono">NEXT_PUBLIC_API_URL</code>. It is
            public by design — never put a secret in a{" "}
            <code className="font-mono">NEXT_PUBLIC_</code> variable.
          </p>
        </Card>
      </div>
    </>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-5 py-2.5">
      <dt className="shrink-0 text-ink-2">{label}</dt>
      <dd
        className={`min-w-0 text-right text-ink ${
          mono ? "break-all font-mono text-xs" : "break-words"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
