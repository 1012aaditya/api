"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatTile } from "@/components/StatTile";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import {
  caseState,
  deadlineNote,
  exceptionTitle,
  outstandingSummary,
  severity as severityLook,
} from "@/lib/practice";
import type {
  AgentEvent,
  AgentRunResult,
  CommandCentre,
  ComplianceCase,
  PracticeTask,
  ReviewException,
} from "@/lib/types";

export default function CommandCentrePage() {
  const [summary, setSummary] = useState<CommandCentre | null>(null);
  const [cases, setCases] = useState<ComplianceCase[]>([]);
  const [exceptions, setExceptions] = useState<ReviewException[]>([]);
  const [tasks, setTasks] = useState<PracticeTask[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<AgentRunResult | null>(null);
  const [previewed, setPreviewed] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [centre, blocked, open, todo, activity] = await Promise.all([
        apiGet<CommandCentre>("/v1/command-centre"),
        apiGet<ComplianceCase[]>("/v1/cases?status=blocked&limit=8"),
        apiGet<ReviewException[]>("/v1/exceptions?limit=5"),
        apiGet<PracticeTask[]>("/v1/tasks?limit=5"),
        apiGet<AgentEvent[]>("/v1/agent/activity?limit=8"),
      ]);
      setSummary(centre);
      setCases(blocked);
      setExceptions(open);
      setTasks(todo);
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

  async function runAgent(dryRun = false) {
    setRunning(true);
    setRunResult(null);
    setError(null);
    setPreviewed(dryRun);
    try {
      setRunResult(
        await apiSend<AgentRunResult>("/v1/agent/run", { dry_run: dryRun }),
      );
      // A preview changed nothing, so there is nothing to reload.
      if (!dryRun) await load();
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setRunning(false);
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
        title="Today"
        description="What is blocked, what needs a person, and what the agent has been doing."
        action={
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void runAgent(true)} disabled={running}>
              Show me what it would do
            </Button>
            <Button
              variant="primary"
              onClick={() => void runAgent(false)}
              disabled={running}
            >
              {running ? "Working…" : "Chase what needs chasing"}
            </Button>
          </div>
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

      {runResult && (
        <Card className="mb-6 px-5 py-4">
          <p className="text-sm text-ink">
            {runResult.scheduled === 0 && runResult.sent === 0
              ? "Nothing needs chasing."
              : previewed
                ? `It would chase ${runResult.scheduled} client${
                    runResult.scheduled === 1 ? "" : "s"
                  }.`
                : `${runResult.scheduled} follow-up${
                    runResult.scheduled === 1 ? "" : "s"
                  } queued.`}{" "}
            <span className="text-ink-2">
              {previewed
                ? "Nothing has been queued and nothing has been sent — this is only what it would do."
                : "The agent sends each one when it comes due, so pressing this does not message everybody at once."}
            </span>
          </p>
          {runResult.skipped.length > 0 && (
            <ul className="mt-2 space-y-1 text-sm text-ink-2">
              {runResult.skipped.map((reason) => (
                <li key={reason}>
                  {previewed ? reason : `Held back: ${reason}`}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Waiting on clients"
              value={summary.cases_blocked}
              caption={`${summary.clients_blocked} of ${summary.clients_total} clients`}
              accent={summary.cases_blocked > 0}
            />
            <StatTile
              label="Ready to file"
              value={summary.cases_ready}
              caption={`${summary.cases_completed} filed`}
            />
            <StatTile
              label="Needs a person"
              value={summary.exceptions_open}
              caption={`${summary.documents_awaiting_review} documents in review`}
            />
            <StatTile
              label="Your tasks"
              value={summary.tasks_open}
              caption={
                summary.tasks_overdue > 0
                  ? `${summary.tasks_overdue} overdue`
                  : "Nothing overdue"
              }
            />
          </div>

          <p className="mt-3 text-xs text-muted">
            {summary.agent_enabled
              ? `The agent has sent ${summary.messages_sent_today} of its ${summary.message_limit_per_day} messages today.`
              : "The agent is switched off. Nothing is being sent."}
            {summary.calls_required > 0 &&
              (summary.calls_required === 1
                ? " One case reached the end of the ladder and needs a call."
                : ` ${summary.calls_required} cases reached the end of the ladder and need a call.`)}
          </p>
        </>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Blocked on documents"
            description="Cases that cannot be filed until something arrives."
            action={
              <Link href="/cases">
                <Button size="sm">All cases</Button>
              </Link>
            }
          />
          {cases.length === 0 ? (
            <EmptyState
              title="Nothing is blocked"
              description="Every open case has what it needs."
            />
          ) : (
            <ul className="divide-y divide-line">
              {cases.map((item) => (
                <li key={item.id} className="px-5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link
                        href={`/clients/${item.client_id}`}
                        className="text-sm text-ink underline-offset-2 hover:underline"
                      >
                        {item.client_name ?? "Client"}
                      </Link>
                      <p className="mt-0.5 text-xs text-ink-2">
                        {item.label} · {outstandingSummary(item.outstanding)}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <StatusBadge
                        status={caseState(item.status).tone}
                        label={caseState(item.status).label}
                      />
                      <p className="mt-1 text-xs text-muted">
                        {deadlineNote(item.deadline)}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Needs a person"
            description="The agent stopped here on purpose."
            action={
              <Link href="/exceptions">
                <Button size="sm">All exceptions</Button>
              </Link>
            }
          />
          {exceptions.length === 0 ? (
            <EmptyState
              title="Nothing waiting on you"
              description="No exception is open right now."
            />
          ) : (
            <ul className="divide-y divide-line">
              {exceptions.map((item) => (
                <li key={item.id} className="px-5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm text-ink">{exceptionTitle(item.type)}</p>
                      <p className="mt-0.5 text-xs text-ink-2">{item.message}</p>
                    </div>
                    <StatusBadge
                      status={severityLook(item.severity).tone}
                      label={severityLook(item.severity).label}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Your tasks"
            description="Work the agent handed to a person."
            action={
              <Link href="/tasks">
                <Button size="sm">All tasks</Button>
              </Link>
            }
          />
          {tasks.length === 0 ? (
            <EmptyState title="Nothing to do" description="No open tasks." />
          ) : (
            <ul className="divide-y divide-line">
              {tasks.map((item) => (
                <li key={item.id} className="px-5 py-3">
                  <p className="text-sm text-ink">{item.title}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {item.client_name ?? "No client"} ·{" "}
                    {item.due_at ? `due ${relativeTime(item.due_at)}` : "no due date"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="What the agent did"
            description="Every message, call and decision, in order."
            action={
              <Link href="/agent">
                <Button size="sm">Agent</Button>
              </Link>
            }
          />
          {events.length === 0 ? (
            <EmptyState
              title="Nothing yet"
              description="The agent has not done anything for this firm."
            />
          ) : (
            <ul className="divide-y divide-line">
              {events.map((event) => (
                <li key={event.id} className="px-5 py-3">
                  <p className="text-sm text-ink">{event.summary}</p>
                  <p className="mt-0.5 font-mono text-xs text-muted">
                    {event.action} · {relativeTime(event.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
