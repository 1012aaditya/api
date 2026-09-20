"use client";

import { useCallback, useEffect, useState } from "react";

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
  StatusBadge,
} from "@/components/ui";
import { CopyableCommand } from "@/components/CopyableCommand";
import { ApiRequestError, apiDelete, apiGet, apiSend } from "@/lib/api";
import { dateTime, relativeTime } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { Invitation, InvitationCreated, TeamMember } from "@/lib/types";

const ROLES = [
  { value: "staff", label: "Staff", hint: "The daily work: clients, cases, documents, exceptions." },
  {
    value: "admin",
    label: "Administrator",
    hint: "All of that, plus inviting colleagues, the agent's settings and API keys.",
  },
];

export default function TeamPage() {
  const { user } = useAuth();
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [issued, setIssued] = useState<InvitationCreated | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("staff");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // The API refuses what a staff login may not do; this only decides what is
  // worth showing them.
  const privileged = user?.role === "owner" || user?.role === "admin";

  const load = useCallback(async () => {
    setError(null);
    try {
      setMembers(await apiGet<TeamMember[]>("/v1/team"));
      if (privileged) setInvites(await apiGet<Invitation[]>("/v1/team/invites"));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, [privileged]);

  useEffect(() => {
    void load();
  }, [load]);

  async function invite(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setIssued(
        await apiSend<InvitationCreated>("/v1/team/invites", { email: email.trim(), role }),
      );
      setEmail("");
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  async function change(member: TeamMember, body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await apiSend<TeamMember>(`/v1/team/${member.id}`, body, "PATCH");
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(invitation: Invitation) {
    setBusy(true);
    try {
      await apiDelete(`/v1/team/invites/${invitation.id}`);
      await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Your firm"
        description="Everyone who can see your clients' documents, and what each of them may do."
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      {issued && (
        <Card className="mb-6">
          <CardHeader
            title={`Invitation for ${issued.email}`}
            description="Shown once. Send it to them yourself — WhatsApp, email, however you already talk."
            action={
              <Button size="sm" onClick={() => setIssued(null)}>
                Done
              </Button>
            }
          />
          <div className="space-y-3 px-5 py-4">
            <CopyableCommand command={issued.accept_url} />
            <p className="text-xs text-muted">
              Anyone holding this link can join your firm until it is used or it
              expires on {dateTime(issued.expires_at)}. If it goes astray,
              withdraw it below and send a new one.
            </p>
          </div>
        </Card>
      )}

      {privileged && (
        <Card className="mb-6">
          <CardHeader title="Invite a colleague" />
          <form onSubmit={invite} className="flex flex-wrap items-end gap-4 px-5 py-4">
            <div className="min-w-64 flex-1">
              <Field label="Their email">
                <Input
                  required
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="ravi@sharma-associates.example"
                />
              </Field>
            </div>
            <div>
              <Field label="They may">
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-56 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                >
                  {ROLES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Button type="submit" variant="primary" disabled={busy}>
              Create invitation
            </Button>
          </form>
          <p className="px-5 pb-4 text-xs text-muted">
            {ROLES.find((option) => option.value === role)?.hint}
          </p>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader title="People" description={`${members.length} with a login`} />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Person</th>
                  <th className="px-3 py-2 font-medium">May do</th>
                  <th className="px-3 py-2 font-medium">Last signed in</th>
                  {privileged && <th className="px-5 py-2 text-right font-medium">Change</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {members.map((member) => (
                  <tr key={member.id} className={member.is_active ? "" : "opacity-60"}>
                    <td className="px-5 py-3">
                      <p className="text-ink">
                        {member.full_name ?? member.email}
                        {member.is_you && <span className="text-muted"> — you</span>}
                      </p>
                      <p className="text-xs text-muted">{member.email}</p>
                    </td>
                    <td className="px-3 py-3">
                      <Badge>{member.role_label}</Badge>
                      {!member.is_active && (
                        <span className="ml-2">
                          <StatusBadge status="failed" label="Switched off" />
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">
                      {relativeTime(member.last_login_at)}
                    </td>
                    {privileged && (
                      <td className="px-5 py-3 text-right">
                        {member.is_you ? (
                          <span className="text-xs text-muted">
                            Ask a colleague to change yours
                          </span>
                        ) : (
                          <div className="flex justify-end gap-2">
                            {member.role !== "owner" && (
                              <Button
                                size="sm"
                                disabled={busy}
                                onClick={() =>
                                  void change(member, {
                                    role: member.role === "admin" ? "staff" : "admin",
                                  })
                                }
                              >
                                Make {member.role === "admin" ? "staff" : "an administrator"}
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant={member.is_active ? "danger" : "secondary"}
                              disabled={busy}
                              onClick={() =>
                                void change(member, { is_active: !member.is_active })
                              }
                            >
                              {member.is_active ? "Switch off" : "Switch back on"}
                            </Button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {privileged && (
        <Card>
          <CardHeader
            title="Invitations nobody has used yet"
            description="Each one is a live way into your firm until it is used, withdrawn, or expires."
          />
          {invites.length === 0 ? (
            <EmptyState title="None outstanding" description="Nothing is waiting to be accepted." />
          ) : (
            <ul className="divide-y divide-line">
              {invites.map((invitation) => (
                <li
                  key={invitation.id}
                  className="flex items-center justify-between gap-4 px-5 py-3"
                >
                  <div>
                    <p className="text-sm text-ink">{invitation.email}</p>
                    <p className="text-xs text-muted">
                      {invitation.role_label} · expires {dateTime(invitation.expires_at)}
                    </p>
                  </div>
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => void withdraw(invitation)}>
                    Withdraw
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </>
  );
}
