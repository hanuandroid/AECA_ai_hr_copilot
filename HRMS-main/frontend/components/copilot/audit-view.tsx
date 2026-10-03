"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AuditEvent, AuditMetrics, fetchAuditEvents, fetchAuditMetrics } from "@/lib/copilot-api";
import { friendlyError } from "@/lib/copilot-errors";

const PAGE = 25;
const EVENT_TYPES = ["", "AI_HTTP_REQUEST", "CHAT_RESPONSE", "REFUSAL", "CLARIFICATION_REQUESTED", "AUTHORIZATION_DENIED",
  "SQL_SECURITY_REJECTION", "ACTION_PROPOSED", "ACTION_CONFIRMED", "ACTION_EXECUTED", "ACTION_REJECTED", "ACTION_EXPIRED",
  "AI_PROVIDER_ERROR", "AI_ERROR"];
const STATUSES = ["", "SUCCESS", "FAILED", "DENIED", "REFUSED", "CLARIFICATION_REQUIRED", "CONFIRMATION_REQUIRED"];

/** Shows only safe audit metadata codes; the API never returns prompts, answers, SQL or personal data. */
function detail(event: AuditEvent): string {
  const meta = event.metadata;
  const value = meta.reason_code ?? meta.error_code ?? meta.action ?? meta.endpoint;
  return typeof value === "string" ? value : "";
}

function ms(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value)} ms`;
}

export function AuditView({ token, onUnauthorized }: { token: string; onUnauthorized: () => void }) {
  const [metrics, setMetrics] = useState<AuditMetrics | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [eventType, setEventType] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const [metricsResult, eventsResult] = await Promise.all([
      fetchAuditMetrics(token),
      fetchAuditEvents(token, { limit: PAGE, offset, event_type: eventType || undefined, status: status || undefined }),
    ]);
    setLoading(false);
    if (metricsResult.status === 401 || eventsResult.status === 401) return onUnauthorized();
    if (metricsResult.status === 403) {
      // The backend decides; the UI never tries another way to get the data.
      setDenied("You don't have access to the AI audit log.");
      return;
    }
    if (metricsResult.ok && metricsResult.data) setMetrics(metricsResult.data);
    if (eventsResult.ok && eventsResult.data) {
      setEvents(eventsResult.data.items);
      setTotal(eventsResult.data.meta.total);
    } else {
      setError(friendlyError(eventsResult.error, eventsResult.status));
    }
  }, [token, offset, eventType, status, onUnauthorized]);

  useEffect(() => {
    void load();
  }, [load]);

  if (denied) return <p role="alert" className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700">{denied}</p>;

  const successRate = metrics && metrics.requests_total ? Math.round((metrics.requests_success / metrics.requests_total) * 100) : null;
  const tiles: Array<[string, string]> = metrics
    ? [
        ["AI requests", String(metrics.requests_total)],
        ["Success rate", successRate === null ? "—" : `${successRate}%`],
        ["Average latency", ms(metrics.latency_ms.avg)],
        ["P95 latency", ms(metrics.latency_ms.p95)],
        ["Refusals", String(metrics.requests_refused)],
        ["Authorization denials", String(metrics.authorization_denied)],
        ["Action proposals", String(metrics.action_proposals)],
        ["Action executions", String(metrics.action_executions)],
        ["Provider errors", String(metrics.provider_errors)],
      ]
    : [];

  return (
    <div className="space-y-4">
      <section aria-label="AI metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {tiles.map(([label, value]) => (
          <Card key={label}>
            <CardContent className="p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
              <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
            </CardContent>
          </Card>
        ))}
      </section>
      <p className="text-xs text-slate-500">
        Operational metrics from recorded AI requests. They are not model-quality scores; estimated cost is shown only when
        pricing and real token usage exist{metrics?.estimated_cost_usd != null ? ` (estimate: $${metrics.estimated_cost_usd})` : ""}.
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Audit events</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <label className="text-sm text-slate-600">
              Event{" "}
              <select className="ml-1 h-9 rounded-md border border-border px-2 text-sm" value={eventType}
                      onChange={(e) => { setOffset(0); setEventType(e.target.value); }}>
                {EVENT_TYPES.map((value) => <option key={value} value={value}>{value || "All"}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-600">
              Status{" "}
              <select className="ml-1 h-9 rounded-md border border-border px-2 text-sm" value={status}
                      onChange={(e) => { setOffset(0); setStatus(e.target.value); }}>
                {STATUSES.map((value) => <option key={value} value={value}>{value || "All"}</option>)}
              </select>
            </label>
          </div>
          {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
          {loading ? <p role="status" className="text-sm text-slate-500">Loading…</p> : null}
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <caption className="sr-only">AI audit events</caption>
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {["Time", "Request", "Conversation", "Route", "Status", "Event", "Detail", "Duration"].map((heading) => (
                    <th key={heading} scope="col" className="px-3 py-2 font-semibold">{heading}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {events.map((event) => (
                  <tr key={event.event_id}>
                    <td className="whitespace-nowrap px-3 py-2">{new Date(`${event.created_at}Z`).toLocaleString()}</td>
                    <td className="px-3 py-2 font-mono text-xs">RQ-{event.request_id.slice(0, 8)}</td>
                    <td className="px-3 py-2 font-mono text-xs">{event.conversation_id ? event.conversation_id.slice(0, 8) : "—"}</td>
                    <td className="px-3 py-2">{event.route ?? "—"}</td>
                    <td className="px-3 py-2">{event.status}</td>
                    <td className="px-3 py-2">{event.event_type}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">{detail(event)}</td>
                    <td className="whitespace-nowrap px-3 py-2">{ms(event.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-sm text-slate-600">
            <span>{total ? `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}` : "No events"}</span>
            <span className="flex gap-2">
              <Button type="button" variant="outline" size="sm" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button type="button" variant="outline" size="sm" disabled={offset + PAGE >= total || loading} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
