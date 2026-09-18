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
          <CardHeader
            title="Asynchronous processing"
            description="POST /v1/documents — for large or multi-page documents"
          />
          <div className="space-y-4 p-5">
            <p className="text-sm text-ink-2">
              Returns as soon as the upload is stored, so a slow extraction does
              not hold an HTTP connection open. Poll the job, or register a
              webhook and skip the polling entirely.
            </p>
            <CopyableCommand
              command={[
                `curl -X POST ${API_URL}/v1/documents \\`,
                `  -H "Authorization: Bearer dp_live_xxxxxxxx" \\`,
                `  -F "file=@invoice.pdf"`,
                ``,
                `# → {"success":true,"job_id":"job_01M2...","document_id":"doc_01M2...",`,
                `#    "status":"queued"}`,
                ``,
                `curl ${API_URL}/v1/jobs/job_01M2... \\`,
                `  -H "Authorization: Bearer dp_live_xxxxxxxx"`,
                ``,
                `# status is queued | processing | completed | failed.`,
                `# Once completed, read the result:`,
                `curl ${API_URL}/v1/documents/doc_01M2.../extraction \\`,
                `  -H "Authorization: Bearer dp_live_xxxxxxxx"`,
              ].join("\n")}
            />
            <p className="text-xs text-muted">
              A job that fails because the provider was briefly unreachable is
              retried with backoff. A document the model could not parse is not
              — the same bytes and the same prompt produce the same answer, so a
              retry would only cost you money.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Webhooks"
            description="Be told when a document finishes, instead of polling"
          />
          <div className="space-y-4 p-5">
            <p className="text-sm text-ink-2">
              Register an endpoint on the{" "}
              <Link href="/webhooks" className="text-accent underline underline-offset-2">
                Webhooks
              </Link>{" "}
              page. We POST a signed JSON body for{" "}
              <code className="font-mono text-xs">document.processing</code>,{" "}
              <code className="font-mono text-xs">document.completed</code> and{" "}
              <code className="font-mono text-xs">document.failed</code>.
            </p>
            <CopyableCommand
              command={JSON.stringify(
                {
                  id: "whd_01M2...",
                  event: "document.completed",
                  job_id: "job_01M2...",
                  document_id: "doc_01M2...",
                  extraction_id: "ext_01M2...",
                  status: "completed",
                  validation: { overall: "passed" },
                  occurred_at: "2026-09-18T19:54:36.919878+00:00",
                },
                null,
                2,
              )}
            />
            <p className="text-sm text-ink-2">
              <strong className="text-ink">Verify every delivery</strong> before
              trusting it. The{" "}
              <code className="font-mono text-xs">X-DocuParse-Signature</code>{" "}
              header is <code className="font-mono text-xs">t=&lt;unix&gt;,v1=&lt;hmac&gt;</code>,
              where the HMAC-SHA256 is computed over{" "}
              <code className="font-mono text-xs">&quot;&lt;t&gt;.&quot; + raw request body</code>{" "}
              with your endpoint&apos;s secret.
            </p>
            <CopyableCommand
              command={[
                `# Python — verify a delivery`,
                `import hashlib, hmac, time`,
                ``,
                `def verify(body: bytes, header: str, secret: str) -> bool:`,
                `    parts = dict(p.split("=", 1) for p in header.split(","))`,
                `    issued_at, received = int(parts["t"]), parts["v1"]`,
                `    if abs(time.time() - issued_at) > 300:`,
                `        return False  # too old — reject the replay`,
                `    expected = hmac.new(`,
                `        secret.encode(), f"{issued_at}.".encode() + body, hashlib.sha256`,
                `    ).hexdigest()`,
                `    return hmac.compare_digest(expected, received)`,
              ].join("\n")}
            />
            <ul className="space-y-1.5 text-sm text-ink-2">
              <li>
                Reply <code className="font-mono text-xs">2xx</code> as soon as you
                have stored the event. Do the work afterwards.
              </li>
              <li>
                A <code className="font-mono text-xs">5xx</code>, a timeout or a
                refused connection is retried with exponential backoff: 30s, 1m,
                2m, 4m, and so on. A <code className="font-mono text-xs">4xx</code>{" "}
                is not — that means you understood and refused.
              </li>
              <li>
                Deliveries carry a stable <code className="font-mono text-xs">id</code>.
                Use it to recognise a retry rather than double-posting.
              </li>
              <li>
                An endpoint that fails repeatedly is disabled, and we stop calling
                it until you re-enable it.
              </li>
              <li>
                Endpoints must be https and publicly reachable. Private, loopback
                and link-local addresses are refused.
              </li>
            </ul>
          </div>
        </Card>

        <Card>
          <CardHeader
            title="How a document is read"
            description="Cheapest source first — the model is the last resort, not the first"
          />
          <div className="space-y-4 p-5">
            <p className="text-sm text-ink-2">
              Extraction runs in tiers and escalates only when what it already
              has is not enough. The response tells you which ran, in{" "}
              <code className="font-mono text-xs text-ink">processing.tiers</code>.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="py-2 pr-4 font-medium">Tier</th>
                    <th className="py-2 pr-4 font-medium">Reads</th>
                    <th className="py-2 font-medium">Covers</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  <tr>
                    <td className="py-2 pr-4 font-mono text-xs text-ink">qr</td>
                    <td className="py-2 pr-4 text-ink-2">
                      The e-invoice QR — the IRP&apos;s own record
                    </td>
                    <td className="py-2 text-ink-2">Registered e-invoices</td>
                  </tr>
                  <tr>
                    <td className="py-2 pr-4 font-mono text-xs text-ink">text_layer</td>
                    <td className="py-2 pr-4 text-ink-2">
                      The characters already in the PDF
                    </td>
                    <td className="py-2 text-ink-2">Digital B2B invoices</td>
                  </tr>
                  <tr>
                    <td className="py-2 pr-4 font-mono text-xs text-ink">model</td>
                    <td className="py-2 pr-4 text-ink-2">
                      A vision model on the page images
                    </td>
                    <td className="py-2 text-ink-2">Scans, photos, odd layouts</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-sm text-ink-2">
              When more than one tier reads the same field, the more direct
              source wins and any disagreement is reported as a{" "}
              <code className="font-mono text-xs text-ink">source_agreement</code>{" "}
              check rather than resolved behind your back.
            </p>
            <p className="text-xs text-muted">
              The QR tier does not verify the QR&apos;s signature — that needs the
              IRP&apos;s public key. A decoded QR is treated as a very strong
              reading, not as proof, and the response says so in{" "}
              <code className="font-mono">processing.notes</code>.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Not available yet" />
          <ul className="space-y-2 p-5 text-sm text-ink-2">
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
