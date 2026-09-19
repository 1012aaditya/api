"use client";

import { useCallback, useEffect, useState } from "react";

import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AgentEvent, AgentPolicy } from "@/lib/types";

/** A labelled switch. The label is the control, so the hit target is the row. */
function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 rounded-md px-3 py-2.5 hover:bg-surface-sunken">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
      />
      <span>
        <span className="block text-sm text-ink">{label}</span>
        <span className="block text-xs text-muted">{hint}</span>
      </span>
    </label>
  );
}

function Number_({
  label,
  hint,
  value,
  onChange,
  min = 0,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
}) {
  return (
    <label className="block px-3 py-2.5">
      <span className="block text-sm text-ink">{label}</span>
      <span className="mt-1.5 block w-32">
        <Input
          type="number"
          min={min}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </span>
      <span className="mt-1 block text-xs text-muted">{hint}</span>
    </label>
  );
}

export default function AgentPage() {
  const [policy, setPolicy] = useState<AgentPolicy | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [current, activity] = await Promise.all([
        apiGet<AgentPolicy>("/v1/agent/policy"),
        apiGet<AgentEvent[]>("/v1/agent/activity?limit=50"),
      ]);
      setPolicy(current);
      setEvents(activity);
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

  async function save() {
    if (!policy) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      setPolicy(await apiSend<AgentPolicy>("/v1/agent/policy", policy, "PUT"));
      setSaved(true);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="py-16 text-center">
        <Spinner />
      </div>
    );
  }

  return (
    <>
      <PageHeader
        title="Agent"
        description="What the agent may do on your behalf, and everything it has done."
        action={
          <Button variant="primary" onClick={() => void save()} disabled={saving || !policy}>
            {saving ? "Saving…" : "Save settings"}
          </Button>
        }
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

      {saved && !error && (
        <p className="mb-6 text-sm text-good-ink">Saved. The agent follows these now.</p>
      )}

      {policy && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="What it may do"
              description="Off means off. Nothing here is a suggestion the agent can overrule."
            />
            <div className="px-2 py-2">
              <Toggle
                label="Run at all"
                hint="Turn this off and the agent sends nothing, to anybody."
                checked={policy.enabled}
                onChange={(enabled) => setPolicy({ ...policy, enabled })}
              />
              <Toggle
                label="Send WhatsApp messages"
                hint="The first ask and the reminders."
                checked={policy.allow_whatsapp}
                onChange={(allow_whatsapp) => setPolicy({ ...policy, allow_whatsapp })}
              />
              <Toggle
                label="Place voice calls"
                hint="The last rung of the ladder. Off by default — a call is the most intrusive thing the agent can do."
                checked={policy.allow_voice_calls}
                onChange={(allow_voice_calls) =>
                  setPolicy({ ...policy, allow_voice_calls })
                }
              />
              <Toggle
                label="Follow up on its own"
                hint="Without this it asks once and waits for you."
                checked={policy.allow_auto_followup}
                onChange={(allow_auto_followup) =>
                  setPolicy({ ...policy, allow_auto_followup })
                }
              />
              <Toggle
                label="Escalate on its own"
                hint="Hand a case to a person when the ladder runs out."
                checked={policy.allow_auto_escalation}
                onChange={(allow_auto_escalation) =>
                  setPolicy({ ...policy, allow_auto_escalation })
                }
              />
              <Toggle
                label="Keep documents on your own hardware"
                hint="Refuse any AI provider that is not local. Extraction fails loudly rather than sending a client's papers outside."
                checked={policy.local_ai_only}
                onChange={(local_ai_only) => setPolicy({ ...policy, local_ai_only })}
              />
            </div>
          </Card>

          <Card>
            <CardHeader
              title="How hard it may push"
              description="Caps the agent cannot exceed, whatever it decides."
            />
            <div className="grid grid-cols-2 px-2 py-2">
              <Number_
                label="Messages a day"
                hint="Across the whole firm."
                value={policy.max_messages_per_day}
                onChange={(max_messages_per_day) =>
                  setPolicy({ ...policy, max_messages_per_day })
                }
              />
              <Number_
                label="Calls a day"
                hint="Across the whole firm."
                value={policy.max_calls_per_day}
                onChange={(max_calls_per_day) =>
                  setPolicy({ ...policy, max_calls_per_day })
                }
              />
              <Number_
                label="Reminders per case"
                hint="Then it stops and tells a person."
                value={policy.max_followups_per_case}
                onChange={(max_followups_per_case) =>
                  setPolicy({ ...policy, max_followups_per_case })
                }
              />
              <Number_
                label="First reminder"
                hint="Hours after the first ask."
                min={1}
                value={policy.first_reminder_hours}
                onChange={(first_reminder_hours) =>
                  setPolicy({ ...policy, first_reminder_hours })
                }
              />
              <Number_
                label="Second reminder"
                hint="Hours after the first reminder."
                min={1}
                value={policy.second_reminder_hours}
                onChange={(second_reminder_hours) =>
                  setPolicy({ ...policy, second_reminder_hours })
                }
              />
              <Number_
                label="Call after"
                hint="Hours of silence before a call, if calls are on."
                min={1}
                value={policy.voice_call_after_hours}
                onChange={(voice_call_after_hours) =>
                  setPolicy({ ...policy, voice_call_after_hours })
                }
              />
              <Number_
                label="Quiet from"
                hint="Hour of the day, 24h. Nothing is sent after this."
                value={policy.quiet_hours_start}
                onChange={(quiet_hours_start) =>
                  setPolicy({ ...policy, quiet_hours_start })
                }
              />
              <Number_
                label="Quiet until"
                hint="Hour of the day, 24h."
                value={policy.quiet_hours_end}
                onChange={(quiet_hours_end) => setPolicy({ ...policy, quiet_hours_end })}
              />
            </div>
            <div className="border-t border-line px-5 py-4">
              <label className="block">
                <span className="block text-sm text-ink">
                  Accept a document when the classifier is at least
                </span>
                <span className="mt-1.5 block w-32">
                  <Input
                    value={policy.classification_threshold}
                    onChange={(e) =>
                      setPolicy({ ...policy, classification_threshold: e.target.value })
                    }
                  />
                </span>
                <span className="mt-1 block text-xs text-muted">
                  Below this the document waits for a person instead of being filed
                  under a guess.
                </span>
              </label>
            </div>
          </Card>
        </div>
      )}

      <Card className="mt-6">
        <CardHeader
          title="Everything it has done"
          description="Every message, call, refusal and hand-over, in order. Nothing is written here that did not happen."
        />
        {events.length === 0 ? (
          <EmptyState
            title="Nothing yet"
            description="The agent has not acted for this firm."
          />
        ) : (
          <ul className="divide-y divide-line">
            {events.map((event) => (
              <li key={event.id} className="px-5 py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm text-ink">{event.summary}</p>
                  <p className="font-mono text-xs text-muted">
                    {event.action} · {event.actor_type} · {dateTime(event.created_at)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
