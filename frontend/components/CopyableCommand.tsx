"use client";

import { useState } from "react";

import { Button } from "./ui";

/** A copy-to-clipboard code block, for commands and one-time secrets. */
export function CopyableCommand({
  command,
  label = "Copy",
}: {
  command: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Clipboard blocked (insecure origin, denied permission): the text is
         still selectable, so nothing is actually lost. */
    }
  }

  return (
    <div className="relative">
      <Button size="sm" className="absolute right-3 top-3 z-10" onClick={copy}>
        {copied ? "Copied" : label}
      </Button>
      <pre className="overflow-x-auto rounded-md border border-line bg-surface-sunken p-4 pr-20 font-mono text-xs leading-relaxed text-ink">
        <code>{command}</code>
      </pre>
    </div>
  );
}
