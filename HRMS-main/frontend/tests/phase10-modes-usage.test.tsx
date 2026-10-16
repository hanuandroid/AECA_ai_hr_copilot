import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CopilotShell } from "@/components/copilot/copilot-shell";
import { UsageDashboard } from "@/components/copilot/usage-dashboard";
import { fetchAiPermissions, type AuditMetrics } from "@/lib/copilot-api";
import { copilotMode } from "@/lib/copilot-display";

import { fail, mockBackend, ok } from "./helpers";

const EMPLOYEE_CAPS = ["POLICY_READ", "HR_READ_SELF", "SQL_READ_SELF", "LEAVE_CREATE", "TICKET_CREATE", "AUDIT_READ_OWN"];
const MANAGER_CAPS = [...EMPLOYEE_CAPS, "SKILL_SEARCH_ORG", "HR_READ_TEAM", "SQL_READ_TEAM", "LEAVE_APPROVE", "LEAVE_REJECT", "TICKET_ASSIGN"];
const ADMIN_CAPS = [...MANAGER_CAPS, "SQL_READ_ORG", "ANNOUNCEMENT_CREATE", "AUDIT_READ_ORG", "EMPLOYEE_DEACTIVATE"];

describe("role-specific Copilot modes (UX only)", () => {
  it.each([
    ["EMPLOYEE", EMPLOYEE_CAPS, "Employee Copilot", ["Policy questions", "Your leave", "Your tickets", "Your projects"]],
    ["MANAGER", MANAGER_CAPS, "Manager Copilot", ["Team insights", "Leave approvals", "Ticket management", "Project staffing"]],
    ["ADMIN", ADMIN_CAPS, "Admin Copilot", ["HR operations", "Announcements", "Employee lifecycle", "Organisation insights", "AI audit"]],
  ])("%s mode", (role, caps, title, focus) => {
    const mode = copilotMode(role, caps);
    expect(mode.title).toBe(title);
    expect(mode.focus).toEqual(focus);
    expect(mode.prompts.length).toBeGreaterThanOrEqual(4);
  });

  it("only suggests prompts for capabilities the backend reports", () => {
    expect(copilotMode("EMPLOYEE", EMPLOYEE_CAPS).prompts.join(" ")).not.toMatch(/approve|report to me|announcement|Engineering/i);
    expect(copilotMode("MANAGER", MANAGER_CAPS).prompts).toContain("Approve Employee Test's pending leave request.");
    // A manager whose LEAVE_APPROVE was revoked server-side gets no approval suggestion.
    expect(copilotMode("MANAGER", MANAGER_CAPS.filter((c) => c !== "LEAVE_APPROVE")).prompts.join(" ")).not.toMatch(/Approve/);
    expect(copilotMode("ADMIN", ["POLICY_READ"]).prompts).toEqual(["What is the sick leave policy?", "Can I work from home?"]);
    expect(copilotMode("ADMIN", []).prompts).toEqual([]);
  });

  it("an unknown or spoofed role falls back to the employee mode", () => {
    expect(copilotMode("SUPERUSER", EMPLOYEE_CAPS).key).toBe("EMPLOYEE");
    expect(copilotMode(null, null).key).toBe("EMPLOYEE");
  });

  it("the shell shows the mode, its focus areas and only permitted prompts", async () => {
    mockBackend({ "GET /api/v1/chat/conversations": () => ok([]) });
    render(<CopilotShell token="t" roleHint="MANAGER" capabilities={MANAGER_CAPS.filter((c) => c !== "LEAVE_APPROVE")}
                         initialConversationId={null} onUnauthorized={vi.fn()} onConversationChange={vi.fn()} />);
    expect(screen.getByText("Manager Copilot")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Manager Copilot focus areas" })).toHaveTextContent("Project staffing");
    expect(screen.getByRole("button", { name: "How many people report to me?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("reads the role and capabilities from the backend permissions endpoint", async () => {
    const { calls } = mockBackend({ "GET /api/v1/chat/permissions": () => ok({ employee_id: 3, role: "EMPLOYEE", capabilities: EMPLOYEE_CAPS, data_scope: "SELF" }) });
    const result = await fetchAiPermissions("t");
    expect(result.data?.role).toBe("EMPLOYEE");
    expect(calls[0]).toMatchObject({ method: "GET", path: "/api/v1/chat/permissions", body: null });
  });
});

const METRICS: AuditMetrics = {
  requests_total: 7, requests_success: 6, requests_failed: 0, requests_denied: 1, requests_refused: 1, requests_clarification: 0,
  authorization_denied: 2, sql_security_rejections: 1, action_proposals: 2, action_confirmations: 1, action_executions: 1,
  action_rejections: 0, action_expirations: 0, provider_calls: 4, provider_errors: 0, ai_errors: 1,
  refusals_by_reason: { DESTRUCTIVE_REQUEST: 1 }, latency_ms: { count: 7, avg: 88.2, p95: 140, max: 150 },
  provider_latency_ms: { count: 4, avg: 20, p95: 30, max: 31 }, estimated_cost_usd: null,
  requests_by_role: { ADMIN: 1, EMPLOYEE: 3, MANAGER: 3 }, intents: { ACTION: 2, SQL: 2, POLICY: 1, UNSUPPORTED: 1 },
  tools: { policy_rag: 1, sql_agent: 2, action_agent: 2 }, actions_proposed_by_type: { LEAVE_APPROVE: 2 },
  policy_answers: 1, rag_no_answer_rate: 1, action_confirmation_rate: 0.5,
  action_execution: { succeeded: 1, failed: 0, success_rate: 1 },
};

describe("AI usage dashboard", () => {
  it("shows operational metrics, clearly labelled as not model quality", async () => {
    mockBackend({ "GET /api/v1/ai/audit/metrics": () => ok(METRICS) });
    render(<UsageDashboard token="t" onUnauthorized={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Operational AI Metrics" })).toBeInTheDocument();
    expect(screen.getByText(/not model-quality scores/)).toBeInTheDocument();
    const tiles = screen.getByRole("region", { name: "Operational AI metrics" });
    expect(within(tiles).getByText("Total AI requests").nextSibling).toHaveTextContent("7");
    expect(within(tiles).getByText("RAG no-answer rate").nextSibling).toHaveTextContent("100%");
    expect(within(tiles).getByText("Action confirmation rate").nextSibling).toHaveTextContent("50%");
    expect(within(tiles).getByText("SQL blocked queries").nextSibling).toHaveTextContent("1");
    expect(within(tiles).getByText("Average response latency").nextSibling).toHaveTextContent("88 ms");
    expect(screen.getByRole("list", { name: "Requests by intent" })).toHaveTextContent("ACTION");
    expect(screen.getByRole("list", { name: "Most used tools" })).toHaveTextContent("SQL Agent");
    expect(screen.queryByText(/Estimated model cost/)).not.toBeInTheDocument();
  });

  it("a 403 is shown as no access and nothing else is requested", async () => {
    const { calls } = mockBackend({ "GET /api/v1/ai/audit/metrics": () => fail(403, "AI_PERMISSION_DENIED") });
    render(<UsageDashboard token="t" onUnauthorized={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to AI usage metrics.");
    expect(calls).toHaveLength(1);
  });

  it("401 uses the sign-in flow", async () => {
    mockBackend({ "GET /api/v1/ai/audit/metrics": () => fail(401, "UNAUTHORIZED") });
    const onUnauthorized = vi.fn();
    render(<UsageDashboard token="t" onUnauthorized={onUnauthorized} />);
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalled());
  });

  it("shows an estimated cost only when the backend reports one", async () => {
    mockBackend({ "GET /api/v1/ai/audit/metrics": () => ok({ ...METRICS, estimated_cost_usd: 0.0123 }) });
    render(<UsageDashboard token="t" onUnauthorized={vi.fn()} />);
    expect(await screen.findByText("Estimated model cost (not billing): $0.0123")).toBeInTheDocument();
  });
});
