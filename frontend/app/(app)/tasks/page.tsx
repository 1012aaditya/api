"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
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
import { ApiRequestError, apiGet, apiSend } from "@/lib/api";
import { dateTime, relativeTime } from "@/lib/format";
import { priority as priorityLook, taskState } from "@/lib/practice";
import type { PracticeTask } from "@/lib/types";

const FILTERS = [
  { value: "open", label: "Open" },
  { value: "done", label: "Done" },
];

export default function TasksPage() {
  const [tasks, setTasks] = useState<PracticeTask[]>([]);
  const [filter, setFilter] = useState("open");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (status: string) => {
    setLoading(true);
    setError(null);
    try {
      setTasks(await apiGet<PracticeTask[]>(`/v1/tasks?status=${status}&limit=200`));
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filter);
  }, [filter, load]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy("new");
    setError(null);
    try {
      await apiSend<PracticeTask>("/v1/tasks", { title: title.trim() });
      setTitle("");
      await load(filter);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(null);
    }
  }

  async function complete(id: string) {
    setBusy(id);
    setError(null);
    try {
      await apiSend<PracticeTask>(`/v1/tasks/${id}/complete`, {});
      await load(filter);
    } catch (caught) {
      if (caught instanceof ApiRequestError) setError(caught);
      else throw caught;
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Tasks"
        description="Work the agent handed over, plus anything you add yourself."
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

      <Card className="mb-6">
        <form onSubmit={add} className="flex flex-wrap items-end gap-3 px-5 py-4">
          <div className="min-w-64 flex-1">
            <Field label="Add a task">
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Call Verma Hardware about the bank statement"
              />
            </Field>
          </div>
          <Button type="submit" variant="primary" disabled={busy === "new"}>
            Add
          </Button>
        </form>
      </Card>

      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <Button
            key={option.value}
            size="sm"
            variant={filter === option.value ? "primary" : "secondary"}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <Card>
        <CardHeader title="Your list" description={`${tasks.length} shown`} />
        {loading ? (
          <div className="px-5 py-12 text-center">
            <Spinner />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            title={filter === "open" ? "Nothing to do" : "Nothing finished yet"}
            description={
              filter === "open"
                ? "The agent has not handed anything over, and you have not added anything."
                : "Completed tasks show up here."
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {tasks.map((task) => {
              const overdue =
                task.status === "open" &&
                task.due_at !== null &&
                new Date(task.due_at).getTime() < Date.now();
              return (
                <li key={task.id} className="flex items-start gap-4 px-5 py-3.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink">{task.title}</p>
                    {task.description && (
                      <p className="mt-0.5 text-sm text-ink-2">{task.description}</p>
                    )}
                    <p className="mt-1 text-xs text-muted">
                      {task.client_id ? (
                        <Link
                          href={`/clients/${task.client_id}`}
                          className="underline-offset-2 hover:underline"
                        >
                          {task.client_name ?? "Client"}
                        </Link>
                      ) : (
                        "No client"
                      )}{" "}
                      · added by {task.created_by} {relativeTime(task.created_at)}
                      {task.due_at && ` · due ${dateTime(task.due_at)}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {overdue && <StatusBadge status="failed" label="Overdue" />}
                    <StatusBadge
                      status={priorityLook(task.priority).tone}
                      label={priorityLook(task.priority).label}
                    />
                    {task.status === "open" ? (
                      <Button
                        size="sm"
                        disabled={busy === task.id}
                        onClick={() => void complete(task.id)}
                      >
                        Done
                      </Button>
                    ) : (
                      <StatusBadge
                        status={taskState(task.status).tone}
                        label={taskState(task.status).label}
                      />
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
