/**
 * How the practice's states are shown.
 *
 * The API owns the states; this file owns the words and the colour. Keeping
 * the mapping in one place is what stops "blocked" reading as a warning on
 * one page and a failure on the next — and every colour here is paired with
 * its label, never used alone.
 */

import type { CheckStatus } from "./types";

export interface StateLook {
  label: string;
  tone: CheckStatus;
}

function look(label: string, tone: CheckStatus): StateLook {
  return { label, tone };
}

/** A fallback for a state added to the API but not yet to this map. */
function unknown(value: string): StateLook {
  return look(value.replace(/_/g, " "), "not_checked");
}

const CASE_STATE: Record<string, StateLook> = {
  not_started: look("Not started", "not_checked"),
  in_progress: look("In progress", "warning"),
  blocked: look("Waiting on the client", "warning"),
  ready: look("Ready to file", "passed"),
  completed: look("Completed", "passed"),
  escalated: look("Needs a person", "failed"),
};

const REQUIREMENT_STATE: Record<string, StateLook> = {
  missing: look("Not asked for yet", "not_checked"),
  requested: look("Asked for", "warning"),
  received: look("Received", "warning"),
  processing: look("Reading", "warning"),
  valid: look("Done", "passed"),
  invalid: look("Rejected", "failed"),
  needs_review: look("Needs review", "failed"),
  waived: look("Not needed", "passed"),
};

const CONTACT_STATE: Record<string, StateLook> = {
  not_contacted: look("Not contacted", "not_checked"),
  contacted: look("Waiting on them", "warning"),
  responded: look("Replied", "passed"),
  committed: look("Promised to send", "warning"),
  follow_up_required: look("Needs a follow-up", "warning"),
  escalated: look("Needs a person", "failed"),
  opted_out: look("Opted out", "failed"),
};

const SEVERITY: Record<string, StateLook> = {
  info: look("Info", "not_checked"),
  warning: look("Warning", "warning"),
  high: look("High", "failed"),
  critical: look("Critical", "failed"),
};

const PRIORITY: Record<string, StateLook> = {
  low: look("Low", "not_checked"),
  normal: look("Normal", "not_checked"),
  high: look("High", "warning"),
  urgent: look("Urgent", "failed"),
};

export const caseState = (value: string): StateLook => CASE_STATE[value] ?? unknown(value);
export const requirementState = (value: string): StateLook =>
  REQUIREMENT_STATE[value] ?? unknown(value);
export const contactState = (value: string): StateLook =>
  CONTACT_STATE[value] ?? unknown(value);
export const severity = (value: string): StateLook => SEVERITY[value] ?? unknown(value);
export const priority = (value: string): StateLook => PRIORITY[value] ?? unknown(value);
export const exceptionState = (value: string): StateLook =>
  EXCEPTION_STATE[value] ?? unknown(value);
export const conversationState = (value: string): StateLook =>
  CONVERSATION_STATE[value] ?? unknown(value);
export const taskState = (value: string): StateLook => TASK_STATE[value] ?? unknown(value);

/** What an exception is, in the firm's language rather than the schema's. */
const EXCEPTION_TITLE: Record<string, string> = {
  gstin_mismatch: "GSTIN does not match the client",
  classification_uncertain: "Not sure what this document is",
  validation_failed: "The document failed a check",
  arithmetic_mismatch: "The totals do not add up",
  duplicate_document: "Sent twice",
  period_mismatch: "Wrong period",
  unreadable_document: "Could not read the document",
  wrong_document_type: "Wrong kind of document",
  client_requested_human: "The client asked for a person",
  client_opted_out: "The client asked not to be contacted",
  wrong_number: "Wrong number",
  max_followups_reached: "Chased as far as the agent may go",
  other: "Needs a look",
};

const EXCEPTION_STATE: Record<string, StateLook> = {
  open: look("Open", "failed"),
  in_review: look("With a person", "warning"),
  resolved: look("Resolved", "passed"),
  dismissed: look("Dismissed", "not_checked"),
};

const CONVERSATION_STATE: Record<string, StateLook> = {
  open: look("Open", "not_checked"),
  awaiting_client: look("Waiting on them", "warning"),
  awaiting_firm: look("Waiting on you", "failed"),
  closed: look("Closed", "passed"),
};

const TASK_STATE: Record<string, StateLook> = {
  open: look("Open", "warning"),
  in_progress: look("In progress", "warning"),
  done: look("Done", "passed"),
  cancelled: look("Cancelled", "not_checked"),
};

export function exceptionTitle(type: string): string {
  return EXCEPTION_TITLE[type] ?? type.replace(/_/g, " ");
}

/** "3 things missing" reads better than a list of slugs on a dense row. */
export function outstandingSummary(labels: string[]): string {
  if (labels.length === 0) return "Nothing outstanding";
  if (labels.length <= 2) return labels.join(" and ");
  return `${labels.slice(0, 2).join(", ")} and ${labels.length - 2} more`;
}

/** Days until a deadline. Negative means it has passed. */
export function daysUntil(date: string | null): number | null {
  if (!date) return null;
  const then = new Date(`${date}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((then.getTime() - today.getTime()) / 86_400_000);
}

export function deadlineNote(date: string | null): string {
  const days = daysUntil(date);
  if (days === null) return "No deadline set";
  if (days < 0) return `${Math.abs(days)} d overdue`;
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  return `${days} d left`;
}
