import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { confirmAction, fetchAuditEvents, getConversation, listConversations, sendChat } from "@/lib/copilot-api";
import { friendlyError } from "@/lib/copilot-errors";
import { cellText, conversationGroup, copilotSuggestions, maskSensitive, visibleColumns } from "@/lib/copilot-display";

import { CONVERSATION_A, chatResponse, fail, mockBackend, ok, policyPart } from "./helpers";

const FORBIDDEN_FIELDS = ["user_id", "employee_id", "role", "permissions", "organization_id", "capability", "scope",
  "authorization_override", "owner_id"];

describe("client payloads carry no identity or authorization", () => {
  it("sends only the message for a new conversation", async () => {
    const { calls } = mockBackend({ "POST /api/v1/chat": () => ok(chatResponse([policyPart()])) });
    await sendChat("token-1", "What is the sick leave policy?");
    expect(calls[0].body).toEqual({ message: "What is the sick leave policy?" });
    expect(calls[0].headers.Authorization).toBe("Bearer token-1");
  });

  it("adds only the backend-issued conversation id for follow-ups", async () => {
    const { calls } = mockBackend({ "POST /api/v1/chat": () => ok(chatResponse([policyPart()])) });
    await sendChat("t", "Does that apply to managers?", CONVERSATION_A);
    expect(calls[0].body).toEqual({ message: "Does that apply to managers?", conversation_id: CONVERSATION_A });
    for (const field of FORBIDDEN_FIELDS) expect(calls[0].body).not.toHaveProperty(field);
    expect(Object.keys(calls[0].headers).map((h) => h.toLowerCase()).sort()).toEqual(["authorization", "content-type"]);
  });

  it("confirms with exactly the confirmation id and nothing else", async () => {
    const { calls } = mockBackend({ "POST /api/v1/chat/actions/confirm": () => ok({ status: "completed", message: "Leave approved." }) });
    await confirmAction("t", "tok-backend-issued-123");
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ confirmation_id: "tok-backend-issued-123" });
  });

  it("encodes path ids so a crafted id cannot change the endpoint", async () => {
    const { calls } = mockBackend({ "GET /api/v1/chat/conversations": () => fail(404, "AI_CONVERSATION_NOT_FOUND") });
    await getConversation("t", "../../ai/audit");
    expect(calls[0].path.startsWith("/api/v1/chat/conversations/..%2F..%2Fai%2Faudit")).toBe(true);
  });
});

describe("response handling", () => {
  it("returns data and the correlation ids", async () => {
    mockBackend({ "POST /api/v1/chat": () => ok(chatResponse([policyPart()])) });
    const result = await sendChat("t", "hi");
    expect(result.ok).toBe(true);
    expect(result.data?.conversation_id).toBe(CONVERSATION_A);
    expect(result.requestId).toBe("a1b2c3d4e5f60718293a4b5c6d7e8f90");
  });

  it("parses the error envelope and the conversation header of a failed turn", async () => {
    mockBackend({ "POST /api/v1/chat": () => fail(403, "SQL_SCOPE_VIOLATION", "raw", { "X-Conversation-Id": CONVERSATION_A }) });
    const result = await sendChat("t", "hi");
    expect(result).toMatchObject({ ok: false, status: 403, error: { code: "SQL_SCOPE_VIOLATION" }, conversationId: CONVERSATION_A });
  });

  it("maps FastAPI validation errors and non-JSON bodies safely", async () => {
    mockBackend({
      "GET /api/v1/chat/conversations": () => ({ status: 422, body: { detail: [{ loc: ["query", "limit"], msg: "internal detail" }] } }),
      "GET /api/v1/ai/audit": () => ({ status: 502, body: undefined }),
    });
    expect((await listConversations("t")).error).toEqual({ code: "VALIDATION_ERROR", message: "" });
    expect((await fetchAuditEvents("t")).error).toEqual({ code: "HTTP_502", message: "" });
  });

  it("reports network failures as status 0 without throwing", async () => {
    mockBackend({ "POST /api/v1/chat/actions/confirm": () => ({ networkError: true }) });
    const result = await confirmAction("t", "tok");
    expect(result).toMatchObject({ ok: false, status: 0, error: { code: "NETWORK_ERROR" } });
  });

  it("builds audit queries only from typed parameters", async () => {
    const { calls } = mockBackend({ "GET /api/v1/ai/audit": () => ok({ items: [], meta: { total: 0, limit: 25, offset: 50 } }) });
    await fetchAuditEvents("t", { limit: 25, offset: 50, event_type: "REFUSAL" });
    expect(calls[0].path).toBe("/api/v1/ai/audit?limit=25&offset=50&event_type=REFUSAL");
  });
});

describe("error copy uses real backend codes only", () => {
  const backendErrors = readFileSync(path.resolve(__dirname, "../../backend/app/services/ai/errors.py"), "utf8");
  const mapped = readFileSync(path.resolve(__dirname, "../lib/copilot-errors.ts"), "utf8").match(/\b(AI|SQL)_[A-Z_]+(?=:)/g) ?? [];

  it("every mapped AI_/SQL_ code exists in the backend", () => {
    expect(mapped.length).toBeGreaterThan(20);
    for (const code of mapped) expect(backendErrors, code).toContain(`"${code}"`);
  });

  it("never echoes the server message and falls back by status", () => {
    expect(friendlyError({ code: "AI_CONVERSATION_NOT_FOUND", message: "x" }, 404)).toBe("Conversation unavailable.");
    expect(friendlyError({ code: "SQL_SCOPE_VIOLATION", message: "Traceback ..." }, 403)).toBe("You don't have access to that information.");
    expect(friendlyError({ code: "SOMETHING_NEW", message: "stack trace" }, 500)).toBe("Something went wrong. Please try again.");
    for (const status of [400, 401, 403, 404, 409, 422, 500, 502, 503]) {
      expect(friendlyError({ code: "UNKNOWN", message: "secret" }, status)).not.toContain("secret");
    }
  });
});

describe("display helpers", () => {
  it.each([
    ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIzIn0.c2lnbmF0dXJlc2ln"], ["sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"], ["CFJNR1002V"],
    ["MOCK0000003"], ["00031000000002"], ["100200300003"], ["Bearer abc.def.ghi"],
  ])("masks sensitive-looking value %s", (value) => {
    expect(maskSensitive(`value: ${value}`)).toBe("value: [hidden]");
  });

  it("keeps ordinary numbers and text", () => {
    expect(maskSensitive("You have 9 sick leaves left out of 10 (2026).")).toBe("You have 9 sick leaves left out of 10 (2026).");
    expect(cellText(null)).toBe("—");
    expect(cellText(true)).toBe("Yes");
  });

  it("drops sensitive columns", () => {
    expect(visibleColumns(["name", "pan_number", "bank_account_number", "current_salary_usd", "pf_uan", "esi_no", "remaining"]))
      .toEqual(["name", "remaining"]);
  });

  it("groups conversations by day", () => {
    const now = new Date(2026, 9, 3, 12, 0, 0);
    expect(conversationGroup(new Date(2026, 9, 3, 8).toISOString(), now)).toBe("Today");
    expect(conversationGroup(new Date(2026, 9, 2, 8).toISOString(), now)).toBe("Yesterday");
    expect(conversationGroup(new Date(2026, 8, 20).toISOString(), now)).toBe("Earlier");
  });

  it("suggestions are hints that only name supported capabilities", () => {
    expect(copilotSuggestions("EMPLOYEE")).toContain("Show my leave balance.");
    expect(copilotSuggestions("MANAGER")).toContain("How many people report to me?");
    expect(copilotSuggestions("EMPLOYEE").join(" ")).not.toMatch(/salary|payroll|deactivate|approve/i);
  });
});
