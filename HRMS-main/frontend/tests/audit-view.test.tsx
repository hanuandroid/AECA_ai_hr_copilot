import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AuditView } from "@/components/copilot/audit-view";
import type { AuditEvent, AuditMetrics } from "@/lib/copilot-api";

import { fail, mockBackend, ok } from "./helpers";

const METRICS: AuditMetrics = {
  requests_total: 8, requests_success: 6, requests_failed: 1, requests_denied: 1, requests_refused: 2, requests_clarification: 1,
  authorization_denied: 3, sql_security_rejections: 1, action_proposals: 2, action_confirmations: 1, action_executions: 1,
  action_rejections: 0, action_expirations: 0, provider_calls: 5, provider_errors: 1, ai_errors: 2,
  refusals_by_reason: { PROMPT_INJECTION: 2 }, latency_ms: { count: 8, avg: 120.4, p95: 310, max: 400 },
  provider_latency_ms: { count: 5, avg: 50, p95: 90, max: 95 }, estimated_cost_usd: null,
  requests_by_role: {}, intents: {}, tools: { policy_rag: 0, sql_agent: 0, action_agent: 0 }, actions_proposed_by_type: {},
  policy_answers: 0, rag_no_answer_rate: null, action_confirmation_rate: null,
  action_execution: { succeeded: 0, failed: 0, success_rate: null },
};

function event(index: number, extra: Partial<AuditEvent> = {}): AuditEvent {
  return {
    event_id: `e${index}`, request_id: `abcdef12${String(index).padStart(24, "0")}`, sequence: 1,
    conversation_id: "11111111-1111-4111-8111-111111111111", employee_id: 3, event_type: "REFUSAL", status: "REFUSED",
    route: "REFUSAL", duration_ms: 12.6, metadata: { reason_code: "PROMPT_INJECTION" }, created_at: "2026-10-03T09:00:00", ...extra,
  };
}

describe("AI audit view", () => {
  it("shows safe metrics and events with backend pagination", async () => {
    const { calls } = mockBackend({
      "GET /api/v1/ai/audit/metrics": () => ok(METRICS),
      "GET /api/v1/ai/audit": (call) => ok({ items: [event(call.path.includes("offset=25") ? 2 : 1)], meta: { total: 30, limit: 25, offset: 0 } }),
    });
    const user = userEvent.setup();
    render(<AuditView token="t" onUnauthorized={vi.fn()} />);
    const metrics = await screen.findByRole("region", { name: "AI metrics" });
    expect(within(metrics).getByText("75%")).toBeInTheDocument(); // 6 / 8
    expect(within(metrics).getByText("310 ms")).toBeInTheDocument();
    expect(await screen.findByText("PROMPT_INJECTION")).toBeInTheDocument();
    expect(screen.getByText("RQ-abcdef12")).toBeInTheDocument();
    expect(screen.getByText(/not model-quality scores/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(calls.some((c) => c.path.includes("offset=25"))).toBe(true));
  });

  it("filters are sent as typed query parameters", async () => {
    const { calls } = mockBackend({
      "GET /api/v1/ai/audit/metrics": () => ok(METRICS),
      "GET /api/v1/ai/audit": () => ok({ items: [], meta: { total: 0, limit: 25, offset: 0 } }),
    });
    const user = userEvent.setup();
    render(<AuditView token="t" onUnauthorized={vi.fn()} />);
    await user.selectOptions(await screen.findByLabelText(/Event/), "ACTION_EXECUTED");
    await waitFor(() => expect(calls.some((c) => c.path.includes("event_type=ACTION_EXECUTED"))).toBe(true));
  });

  it("a 403 is shown as no access and no other route is tried", async () => {
    const { calls } = mockBackend({
      "GET /api/v1/ai/audit/metrics": () => fail(403, "AI_PERMISSION_DENIED"),
      "GET /api/v1/ai/audit": () => ok({ items: [event(1)], meta: { total: 1, limit: 25, offset: 0 } }),
    });
    render(<AuditView token="t" onUnauthorized={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to the AI audit log.");
    expect(screen.queryByText("PROMPT_INJECTION")).not.toBeInTheDocument();
    expect(calls.every((c) => c.path.startsWith("/api/v1/ai/audit"))).toBe(true);
    expect(calls).toHaveLength(2);
  });

  it("401 uses the sign-in flow", async () => {
    mockBackend({ "GET /api/v1/ai/audit/metrics": () => fail(401, "UNAUTHORIZED"), "GET /api/v1/ai/audit": () => fail(401, "UNAUTHORIZED") });
    const onUnauthorized = vi.fn();
    render(<AuditView token="t" onUnauthorized={onUnauthorized} />);
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalled());
  });

  it("never renders metadata beyond safe codes", async () => {
    mockBackend({
      "GET /api/v1/ai/audit/metrics": () => ok(METRICS),
      "GET /api/v1/ai/audit": () => ok({ items: [event(1, { metadata: { message_hash: "deadbeefdeadbeef", tables: ["employees"] } })],
                                          meta: { total: 1, limit: 25, offset: 0 } }),
    });
    render(<AuditView token="t" onUnauthorized={vi.fn()} />);
    await screen.findByText("RQ-abcdef12");
    expect(document.body.textContent).not.toMatch(/deadbeef|employees/);
  });
});
