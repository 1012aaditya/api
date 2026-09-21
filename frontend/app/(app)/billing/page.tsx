"use client";

import { useCallback, useEffect, useState } from "react";

import {
  Badge,
  Card,
  CardHeader,
  ErrorNotice,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { ApiRequestError, apiGet } from "@/lib/api";
import type { Plan, Statement } from "@/lib/types";

const rupees = (value: string | number) =>
  `₹${Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const whole = (value: number) => value.toLocaleString("en-IN");

/** How far through the allowance, and whether that is worth a colour. */
function Allowance({ used, included }: { used: number; included: number }) {
  const share = included > 0 ? Math.min(used / included, 1) : 0;
  const over = used > included;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-sm text-ink">
          <span className="tnum font-semibold">{whole(used)}</span> of{" "}
          <span className="tnum">{whole(included)}</span> included documents
        </span>
        {over && (
          <span className="text-sm text-ink-2">
            +{whole(used - included)} charged
          </span>
        )}
      </div>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-surface-sunken"
        role="img"
        aria-label={`${whole(used)} of ${whole(included)} included documents used`}
      >
        <div
          className={`h-full rounded-full ${over ? "bg-warning" : "bg-accent"}`}
          style={{ width: `${Math.max(share * 100, 2)}%` }}
        />
      </div>
    </div>
  );
}

function PlanCard({ plan }: { plan: Plan }) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        plan.is_current ? "border-accent bg-accent/5" : "border-line bg-surface"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink">{plan.name}</p>
          <p className="mt-0.5 text-xs text-ink-2">{plan.description}</p>
        </div>
        {plan.is_current && <Badge>Your plan</Badge>}
      </div>
      <p className="mt-3 text-lg font-semibold text-ink">
        {Number(plan.monthly_price) === 0 ? "No charge" : rupees(plan.monthly_price)}
        {Number(plan.monthly_price) > 0 && (
          <span className="text-sm font-normal text-muted"> a month</span>
        )}
      </p>
      <ul className="mt-3 space-y-1 text-xs text-ink-2">
        <li>{whole(plan.included_documents)} documents included</li>
        <li>
          {Number(plan.overage_per_document) === 0
            ? "No charge beyond that"
            : `${rupees(plan.overage_per_document)} per document after that — work never stops`}
        </li>
        <li>Up to {whole(plan.included_clients)} clients</li>
        <li>{plan.voice_available ? "Voice calls available" : "No voice calls"}</li>
      </ul>
    </div>
  );
}

export default function BillingPage() {
  const [statement, setStatement] = useState<Statement | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [current, catalogue] = await Promise.all([
        apiGet<Statement>("/v1/billing/statement"),
        apiGet<Plan[]>("/v1/billing/plans"),
      ]);
      setStatement(current);
      setPlans(catalogue);
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
        title="Plan and usage"
        description="What you are on, what you have used, and what it comes to."
      />

      {error && (
        <div className="mb-6">
          <ErrorNotice message={error.message} code={error.code} requestId={error.requestId} />
        </div>
      )}

      {statement?.warnings.map((warning) => (
        <div
          key={warning}
          className="mb-4 rounded-lg border border-warning/40 bg-warning/5 px-4 py-3 text-sm text-ink"
        >
          {warning}
        </div>
      ))}

      {statement && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader
              title={`This month · ${statement.period}`}
              description={
                statement.provisional
                  ? "The month is still running, so this is not a final bill."
                  : "A finished month."
              }
            />
            <div className="space-y-4 px-5 py-4">
              <Allowance
                used={statement.documents_used}
                included={statement.documents_included}
              />
              <p className="text-sm text-ink-2">
                <span className="tnum font-semibold text-ink">
                  {whole(statement.clients)}
                </span>{" "}
                clients, of {whole(statement.plan.included_clients)} your plan covers.
              </p>
            </div>
          </Card>

          <Card>
            <CardHeader
              title={statement.provisional ? "So far this month" : "Statement"}
            />
            {statement.lines.length === 0 ? (
              <p className="px-5 py-5 text-sm text-ink-2">
                Nothing to charge — you are on {statement.plan.name}.
              </p>
            ) : (
              <>
                <ul className="divide-y divide-line">
                  {statement.lines.map((line) => (
                    <li
                      key={line.label}
                      className="flex items-start justify-between gap-4 px-5 py-3"
                    >
                      <span>
                        <span className="block text-sm text-ink">{line.label}</span>
                        <span className="block text-xs text-muted">{line.detail}</span>
                      </span>
                      <span className="tnum shrink-0 text-sm text-ink">
                        {rupees(line.amount)}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="border-t border-line px-5 py-3">
                  {[
                    ["Subtotal", statement.subtotal],
                    [`Tax at ${Math.round(Number(statement.tax_rate) * 100)}%`, statement.tax],
                  ].map(([label, amount]) => (
                    <div key={label} className="flex justify-between py-0.5 text-sm">
                      <span className="text-ink-2">{label}</span>
                      <span className="tnum text-ink">{rupees(amount)}</span>
                    </div>
                  ))}
                  <div className="mt-1 flex justify-between border-t border-line pt-2 text-sm font-semibold">
                    <span className="text-ink">Total</span>
                    <span className="tnum text-ink">{rupees(statement.total)}</span>
                  </div>
                </div>
              </>
            )}
            <div className="space-y-1.5 border-t border-line px-5 py-3 text-xs text-muted">
              <p>{statement.note}</p>
              <p>{statement.payment_note}</p>
            </div>
          </Card>
        </div>
      )}

      <Card className="mt-6">
        <CardHeader
          title="Plans"
          description="Documents included is what the price covers, not a limit. Going over is charged; it never stops work mid-filing."
        />
        <div className="grid gap-4 px-5 py-5 sm:grid-cols-2 lg:grid-cols-3">
          {plans.map((plan) => (
            <PlanCard key={plan.key} plan={plan} />
          ))}
        </div>
        <p className="border-t border-line px-5 py-3 text-xs text-muted">
          Changing plan is not self-service yet — ask your provider.
        </p>
      </Card>
    </>
  );
}
