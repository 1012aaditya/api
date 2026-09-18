"use client";

import Link from "next/link";

import { CopyableCommand } from "@/components/CopyableCommand";
import { Card, CardHeader, PageHeader } from "@/components/ui";
import { API_URL } from "@/lib/api";

const ERRORS: [string, string, string][] = [
  ["400", "invalid_request / invalid_file", "The request or the file could not be read."],
  ["401", "authentication_required / invalid_api_key", "Missing, malformed, revoked or expired key."],
  ["403", "quota_exceeded", "The monthly document allowance is used up."],
  ["413", "file_too_large / too_many_pages", "Over the size or page limit."],
  ["415", "unsupported_file_type", "Not a PDF, PNG, JPG or JPEG."],
  ["422", "extraction_failed", "The document could not be turned into structured data."],
  ["429", "rate_limit_exceeded", "Too many requests. Honour the Retry-After header."],
  ["500", "internal_error", "Something went wrong on our side. Quote the request id."],
  ["503", "extraction_provider_unavailable", "No extraction provider is configured or reachable."],
];

export default function DocsPage() {
  return (
    <>
      <PageHeader
        title="API reference"
        description="Everything you need for a first integration. The full interactive schema is served by the API itself."
      />

      <div className="space-y-6">
        <Card>
          <CardHeader title="Authenticate" description="One header, on every request." />
          <div className="space-y-3 p-5">
            <CopyableCommand command={`Authorization: Bearer dp_live_xxxxxxxx`} />
            <p className="text-sm text-ink-2">
              Create a key on the{" "}
              <Link href="/api-keys" className="text-accent underline underline-offset-2">
                API keys
              </Link>{" "}
              page. Keys are stored as a SHA-256 hash, so the secret is shown once
              and cannot be recovered — rotate instead of hunting for it.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Extract an invoice"
            description="POST /v1/invoices/extract · multipart/form-data"
          />
          <div className="space-y-4 p-5">
            <CopyableCommand
              command={[
                `curl -X POST ${API_URL}/v1/invoices/extract \\`,
                `  -H "Authorization: Bearer dp_live_xxxxxxxx" \\`,
                `  -F "file=@invoice.pdf"`,
              ].join("\n")}
            />
            <CopyableCommand
              command={[
                `# Python`,
                `import httpx`,
                ``,
                `with open("invoice.pdf", "rb") as handle:`,
                `    response = httpx.post(`,
                `        "${API_URL}/v1/invoices/extract",`,
                `        headers={"Authorization": "Bearer dp_live_xxxxxxxx"},`,
                `        files={"file": handle},`,
                `        timeout=120,`,
                `    )`,
                `response.raise_for_status()`,
                `print(response.json()["data"]["invoice_number"])`,
              ].join("\n")}
            />
            <CopyableCommand
              command={[
                `// JavaScript`,
                `const form = new FormData();`,
                `form.append("file", file);`,
                ``,
                `const response = await fetch(`,
                `  "${API_URL}/v1/invoices/extract",`,
                `  { method: "POST", headers: { Authorization: "Bearer dp_live_xxxxxxxx" }, body: form },`,
                `);`,
                `const result = await response.json();`,
                `console.log(result.data.invoice_number);`,
              ].join("\n")}
            />
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Reading the response"
            description="Three blocks, and the difference between them matters."
          />
          <div className="space-y-4 p-5 text-sm text-ink-2">
            <p>
              <strong className="text-ink">data</strong> — the invoice. A field
              that was not on the document is{" "}
              <code className="font-mono text-xs text-ink">null</code>. It is
              never a guess, so a non-null value means it was printed on the page.
            </p>
            <p>
              <strong className="text-ink">validation</strong> — each check is{" "}
              <code className="font-mono text-xs text-ink">passed</code>,{" "}
              <code className="font-mono text-xs text-ink">warning</code>,{" "}
              <code className="font-mono text-xs text-ink">failed</code> or{" "}
              <code className="font-mono text-xs text-ink">not_checked</code>.
              The last one means the document did not carry what the check needs
              — it is not a pass. An invoice that fails validation still returns
              200: a bad invoice is a successful extraction of a bad invoice.
            </p>
            <p>
              <strong className="text-ink">confidence</strong> — per field, from
              checkable signals rather than a number the model asserted. Read{" "}
              <code className="font-mono text-xs text-ink">low_confidence_fields</code>{" "}
              as a review queue. Nothing ever scores 1.00.
            </p>
            <p>
              Every response, success or error, carries a{" "}
              <code className="font-mono text-xs text-ink">request_id</code>, also
              returned as the{" "}
              <code className="font-mono text-xs text-ink">X-Request-Id</code>{" "}
              header. Quote it and we can reconstruct the request.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Errors" description="Consistent envelope, stable codes." />
          <div className="p-5">
            <CopyableCommand
              command={JSON.stringify(
                {
                  success: false,
                  request_id: "req_01M2…",
                  error: { code: "rate_limit_exceeded", message: "Rate limit exceeded." },
                },
                null,
                2,
              )}
            />
          </div>
          <div className="overflow-x-auto border-t border-line">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-5 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Code</th>
                  <th className="px-5 py-2 font-medium">Meaning</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {ERRORS.map(([status, code, meaning]) => (
                  <tr key={code}>
                    <td className="tnum px-5 py-2.5 text-ink">{status}</td>
                    <td className="px-3 py-2.5 font-mono text-xs text-ink-2">{code}</td>
                    <td className="px-5 py-2.5 text-ink-2">{meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title="Limits" />
          <ul className="space-y-2 p-5 text-sm text-ink-2">
            <li>PDF, PNG, JPG and JPEG. The file&apos;s own bytes decide, not its extension.</li>
            <li>Up to 20 MB and 25 pages per document, by default.</li>
            <li>
              Rate limits and the monthly document quota are per organization —
              see{" "}
              <Link href="/usage" className="text-accent underline underline-offset-2">
                Usage
              </Link>
              .
            </li>
            <li>
              Uploaded files are deleted when their retention window expires, or
              immediately when you delete them.
            </li>
          </ul>
        </Card>

        <Card>
          <CardHeader
            title="Full schema"
            description="Generated from the running API, so it is never out of date."
          />
          <div className="p-5 text-sm text-ink-2">
            <a
              href={`${API_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              className="text-accent underline underline-offset-2"
            >
              Open the interactive API docs
            </a>{" "}
            or fetch{" "}
            <a
              href={`${API_URL}/openapi.json`}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-xs text-accent underline underline-offset-2"
            >
              /openapi.json
            </a>
            .
          </div>
        </Card>

        <Card>
          <CardHeader title="Not available yet" />
          <ul className="space-y-2 p-5 text-sm text-ink-2">
            <li>
              Asynchronous processing (<code className="font-mono text-xs">POST /v1/documents</code>,{" "}
              <code className="font-mono text-xs">GET /v1/jobs/&#123;id&#125;</code>)
            </li>
            <li>Webhooks</li>
            <li>The Python SDK</li>
            <li>Billing and subscriptions</li>
          </ul>
          <p className="border-t border-line px-5 py-3 text-xs text-muted">
            Listed here rather than shown as greyed-out menu items, so nothing in
            this dashboard looks like it works when it does not.
          </p>
        </Card>
      </div>
    </>
  );
}
