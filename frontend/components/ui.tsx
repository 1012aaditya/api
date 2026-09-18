"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

import type { CheckStatus, ConfidenceBand } from "@/lib/types";

/* --- layout ---------------------------------------------------------- */

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-line bg-surface ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && (
          <p className="mt-0.5 text-sm text-ink-2">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-ink-2">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/* --- controls -------------------------------------------------------- */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const sizes = { sm: "px-2.5 py-1.5 text-xs", md: "px-3.5 py-2 text-sm" };
  const variants = {
    primary: "bg-accent text-white hover:bg-accent-600",
    secondary: "border border-line bg-surface text-ink hover:bg-surface-sunken",
    ghost: "text-ink-2 hover:bg-surface-sunken hover:text-ink",
    danger: "border border-line bg-surface text-critical hover:bg-surface-sunken",
  };
  return (
    <button
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
      {...props}
    />
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-ink">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none ${className}`}
      {...props}
    />
  );
}

/* --- feedback -------------------------------------------------------- */

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-2" role="status">
      <svg
        className="h-3.5 w-3.5 animate-spin text-muted"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="2" opacity="0.25" />
        <path
          d="M8 1.5a6.5 6.5 0 0 1 6.5 6.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      {label}
    </span>
  );
}

export function ErrorNotice({
  title = "Something went wrong",
  message,
  code,
  requestId,
}: {
  title?: string;
  message: string;
  code?: string;
  requestId?: string | null;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-critical/30 bg-critical/5 px-4 py-3"
    >
      <div className="flex items-start gap-2">
        <StatusDot status="failed" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{title}</p>
          <p className="mt-0.5 text-sm text-ink-2">{message}</p>
          {(code || requestId) && (
            <p className="mt-1.5 font-mono text-xs text-muted">
              {code}
              {code && requestId ? " · " : ""}
              {requestId}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="px-5 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-2">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* --- status ---------------------------------------------------------- */

const STATUS_STYLE: Record<CheckStatus, { dot: string; text: string; label: string }> = {
  passed: { dot: "bg-good", text: "text-good-ink", label: "Passed" },
  warning: { dot: "bg-warning", text: "text-ink-2", label: "Warning" },
  failed: { dot: "bg-critical", text: "text-critical", label: "Failed" },
  not_checked: { dot: "bg-axis", text: "text-muted", label: "Not checked" },
};

/** A status colour never carries meaning alone — every use pairs it with text. */
export function StatusDot({ status }: { status: CheckStatus }) {
  return (
    <span
      className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${STATUS_STYLE[status].dot}`}
      aria-hidden="true"
    />
  );
}

export function StatusBadge({
  status,
  label,
}: {
  status: CheckStatus;
  /** Override the wording. The colours carry state; the words carry meaning,
   *  and "Passed" is wrong for a delivered webhook or an active endpoint. */
  label?: string;
}) {
  const style = STATUS_STYLE[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium ${style.text}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {label ?? style.label}
    </span>
  );
}

const BAND_STYLE: Record<ConfidenceBand, string> = {
  high: "text-good-ink",
  medium: "text-ink-2",
  low: "text-critical",
};

export function ConfidenceBadge({
  band,
  value,
}: {
  band: ConfidenceBand;
  value: number;
}) {
  return (
    <span className={`tnum text-xs font-medium ${BAND_STYLE[band]}`}>
      {value.toFixed(2)} <span className="text-muted">{band}</span>
    </span>
  );
}

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-line bg-surface-sunken px-2 py-0.5 text-xs font-medium text-ink-2">
      {children}
    </span>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-xs text-ink-2">{children}</span>;
}
