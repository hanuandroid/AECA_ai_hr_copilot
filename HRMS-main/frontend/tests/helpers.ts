import { vi } from "vitest";

import type { ChatPart, ChatResponse, Conversation, ConversationMessage } from "@/lib/copilot-api";

export type RecordedCall = { method: string; path: string; body: Record<string, unknown> | null; headers: Record<string, string> };

type Reply = { status?: number; body?: unknown; headers?: Record<string, string>; networkError?: boolean };
type Handler = (call: RecordedCall) => Reply | Promise<Reply>;

/** Stubs global fetch with route handlers ("METHOD /path-prefix") and records every request. */
export function mockBackend(routes: Record<string, Handler>) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    const method = (init.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>));
    const call: RecordedCall = { method, path: url.pathname + url.search, body: init.body ? JSON.parse(String(init.body)) : null, headers };
    calls.push(call);
    const key = Object.keys(routes)
      .filter((route) => {
        const [routeMethod, routePath] = route.split(" ");
        return routeMethod === method && (url.pathname === routePath || url.pathname.startsWith(`${routePath}/`) || (url.pathname + url.search).startsWith(routePath));
      })
      .sort((a, b) => b.length - a.length)[0];
    if (!key) throw new Error(`Unexpected request ${method} ${url.pathname}`);
    const reply = await routes[key](call);
    if (reply.networkError) throw new TypeError("Failed to fetch");
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json", ...(reply.headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export const ok = <T,>(data: T, headers: Record<string, string> = {}): Reply => ({
  status: 200,
  body: { success: true, data, error: null },
  headers: { "X-Request-Id": "a1b2c3d4e5f60718293a4b5c6d7e8f90", ...headers },
});

export const fail = (status: number, code: string, message = "server message must not be shown", headers: Record<string, string> = {}): Reply => ({
  status,
  body: { success: false, data: null, error: { code, message } },
  headers: { "X-Request-Id": "ffeeddccbbaa99887766554433221100", ...headers },
});

export const CONVERSATION_A = "11111111-1111-4111-8111-111111111111";
export const CONVERSATION_B = "22222222-2222-4222-8222-222222222222";
export const FOREIGN = "99999999-9999-4999-8999-999999999999";

export function policyPart(message = "Employees get 10 sick leave days per year."): ChatPart {
  return {
    route: "POLICY", status: "answered", message,
    data: { answer: message, grounded: true, sources: [
      { chunk_id: "internal-chunk-7", title: "Leave Policy", category: "LEAVE", filename: "leave_policy.md", section: "Sick Leave" },
      { chunk_id: "internal-chunk-8", title: "Leave Policy", category: "LEAVE", filename: "leave_policy.md", section: "Sick Leave" },
    ] },
  };
}

export function sqlPart(rows: Array<Record<string, string | number | null>>, columns: string[], message = "Here are your results."): ChatPart {
  return { route: "SQL", status: "answered", message, data: { answer: message, scope: "SELF", columns, rows, row_count: rows.length, truncated: false } };
}

export function actionPart(confirmationId = "tok-backend-issued-123", summary = "Approve leave request #20 for Rahul Mehta (2026-11-01)"): ChatPart {
  return {
    route: "ACTION", status: "confirmation_required", message: "Please confirm this action.",
    data: { status: "confirmation_required", message: "Please confirm this action.", action: "LEAVE_APPROVE", summary,
            confirmation_id: confirmationId, expires_at: "2026-10-03T10:05:00Z" },
  };
}

export function chatResponse(parts: ChatPart[], conversationId = CONVERSATION_A, extra: Partial<ChatResponse> = {}): ChatResponse {
  const status = parts.some((p) => p.status === "confirmation_required") ? "confirmation_required"
    : parts.some((p) => p.route === "REFUSAL") ? "refused"
    : parts.some((p) => p.status === "clarification_required") ? "clarification_required" : "answered";
  return {
    conversation_id: conversationId, request_id: "a1b2c3d4e5f60718293a4b5c6d7e8f90", status, intents: [],
    routes: parts.map((p) => p.route), classification_source: "rules", message: parts.map((p) => p.message).join("\n\n"),
    parts, follow_up: false, ...extra,
  };
}

export function conversation(id: string, title: string, updatedAt = new Date().toISOString().replace("Z", "")): Conversation {
  return { conversation_id: id, title, message_count: 2, created_at: updatedAt, updated_at: updatedAt };
}

export function historyMessage(sequence: number, role: "USER" | "ASSISTANT", content: string, extra: Partial<ConversationMessage> = {}): ConversationMessage {
  return { sequence, role, content, created_at: "2026-10-03T09:00:00", ...extra };
}
