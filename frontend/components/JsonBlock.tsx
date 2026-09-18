"use client";

import { useState } from "react";

import { Button } from "./ui";

/** Syntax-highlighted JSON with a copy button. Read-only — never an editor. */
export function JsonBlock({
  value,
  maxHeight = "28rem",
}: {
  value: unknown;
  maxHeight?: string;
}) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(value, null, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Clipboard blocked: the text is selectable, so nothing is lost. */
    }
  }

  return (
    <div className="relative">
      <Button
        size="sm"
        onClick={copy}
        className="absolute right-3 top-3 z-10"
        aria-label="Copy JSON"
      >
        {copied ? "Copied" : "Copy"}
      </Button>
      <pre
        className="overflow-auto rounded-md border border-line bg-surface-sunken p-4 pr-20 font-mono text-xs leading-relaxed text-ink"
        style={{ maxHeight }}
      >
        <code>{highlight(text)}</code>
      </pre>
    </div>
  );
}

/**
 * Minimal tokenizer. It renders the same characters it was given — nulls stay
 * visible as `null` rather than being hidden, because a null is the answer to
 * "was this on the document", not a blank.
 */
function highlight(json: string) {
  const pattern =
    /("(\\.|[^"\\])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;

  for (const match of json.matchAll(pattern)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) nodes.push(json.slice(lastIndex, index));

    let className = "text-accent-600"; // numbers
    if (token.startsWith('"')) {
      className = token.trimEnd().endsWith(":")
        ? "text-ink-2"
        : "text-good-ink";
    } else if (token === "null") {
      className = "text-muted italic";
    } else if (token === "true" || token === "false") {
      className = "text-accent-600";
    }

    nodes.push(
      <span key={key++} className={className}>
        {token}
      </span>,
    );
    lastIndex = index + token.length;
  }
  if (lastIndex < json.length) nodes.push(json.slice(lastIndex));
  return nodes;
}
