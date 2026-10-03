"use client";

import { useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AuditMetrics, fetchAuditMetrics } from "@/lib/copilot-api";
import { friendlyError } from "@/lib/copilot-errors";

const percent = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`);
const ms = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${Math.round(value)} ms`);

const TOOL_LABELS: Record<string, string> = { policy_rag: "Policy RAG", sql_agent: "SQL Agent", action_agent: "Action Agent" };

function BarList({ title, data, label = (key: string) => key }: { title: string; data: Record<string, number>; label?: (key: string) => string }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? <p className="text-sm text-slate-500">No data yet.</p> : (
          <ul aria-label={title} className="space-y-2">
            {entries.map(([key, value]) => (
              <li key={key} className="text-sm">
                <div className="flex justify-between text-slate-700">
                  <span>{label(key)}</span>
                  <span className="font-medium">{value}</span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-slate-100" aria-hidden="true">
                  <div className="h-2 rounded-full bg-indigo-500" style={{ width: `${(value / max) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** Admin AI usage dashboard. Operational metrics from the audit trail; access is decided by the backend. */
export function UsageDashboard({ token, onUnauthorized }: { token: string; onUnauthorized: () => void }) {
  const [metrics, setMetrics] = useState<AuditMetrics | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    void fetchAuditMetrics(token).then((result) => {
      if (result.status === 401) return onUnauthorized();
      if (result.status === 403) return setMessage("You don't have access to AI usage metrics.");
      if (!result.ok || !result.data) return setMessage(friendlyError(result.error, result.status));
      setMetrics(result.data);
    });
  }, [token, onUnauthorized]);

  if (message) return <p role="alert" className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700">{message}</p>;
  if (!metrics) return <p role="status" className="text-sm text-slate-500">Loading…</p>;

  const tiles: Array<[string, string]> = [
    ["Total AI requests", String(metrics.requests_total)],
    ["Permission-denied attempts", String(metrics.authorization_denied)],
    ["SQL blocked queries", String(metrics.sql_security_rejections)],
    ["RAG no-answer rate", percent(metrics.rag_no_answer_rate)],
    ["Refusals", String(metrics.requests_refused)],
    ["Action confirmation rate", percent(metrics.action_confirmation_rate)],
    ["Action execution success", percent(metrics.action_execution.success_rate)],
    ["Average response latency", ms(metrics.latency_ms.avg)],
    ["P95 response latency", ms(metrics.latency_ms.p95)],
    ["Provider errors", String(metrics.provider_errors)],
  ];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Operational AI Metrics</h2>
        <p className="text-sm text-slate-500">
          Counts and rates from recorded AI requests. These are operational metrics, not model-quality scores. Estimated
          cost appears only when pricing and real token usage exist.
        </p>
      </div>
      <section aria-label="Operational AI metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {tiles.map(([label, value]) => (
          <Card key={label}>
            <CardContent className="p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
              <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
            </CardContent>
          </Card>
        ))}
      </section>
      <div className="grid gap-3 md:grid-cols-2">
        <BarList title="Requests by intent" data={metrics.intents} />
        <BarList title="Most used tools" data={metrics.tools} label={(key) => TOOL_LABELS[key] ?? key} />
        <BarList title="Actions proposed by type" data={metrics.actions_proposed_by_type} />
        <BarList title="Requests by role" data={metrics.requests_by_role} />
        <BarList title="Refusals by reason" data={metrics.refusals_by_reason} />
        <BarList title="Action execution" data={{ Succeeded: metrics.action_execution.succeeded, Failed: metrics.action_execution.failed }} />
      </div>
      {metrics.estimated_cost_usd !== null ? (
        <p className="text-xs text-slate-500">Estimated model cost (not billing): ${metrics.estimated_cost_usd}</p>
      ) : null}
    </div>
  );
}
