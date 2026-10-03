/**
 * Typed client for the AI Copilot endpoints (Phase 9).
 *
 * The frontend is a client, never an authorization boundary: requests carry only the bearer token
 * and the documented body fields. Identity, role, scope and permissions are decided by the backend.
 * All AI endpoints are called from here only (no scattered fetch calls).
 */

import { API_BASE } from "@/lib/api";

// ---- backend contracts (backend/app/services/ai/*/models.py, schemas/chat.py) -------------------

export type ChatRequest = {
  message: string;
  conversation_id?: string; // only an id previously returned by the backend
};

export type PolicySource = {
  chunk_id: string;
  title: string;
  category: string;
  filename: string;
  section: string;
};

export type PolicyData = { answer: string; grounded: boolean; sources: PolicySource[] };

export type SqlCell = string | number | boolean | null;

export type SqlData = {
  answer: string;
  scope: string;
  columns: string[];
  rows: Array<Record<string, SqlCell>>;
  row_count: number;
  truncated: boolean;
};

export type ActionData = {
  status: "clarification_required" | "confirmation_required";
  message: string;
  action?: string;
  summary?: string;
  confirmation_id?: string;
  expires_at?: string;
};

export type PartRoute = "POLICY" | "SQL" | "ACTION" | "CLARIFICATION" | "REFUSAL";
export type PartStatus = "answered" | "clarification_required" | "confirmation_required" | "refused" | "failed";

export type ChatPart = {
  route: PartRoute;
  status: PartStatus;
  message: string;
  data?: PolicyData | SqlData | ActionData | Record<string, never>;
  error_code?: string;
};

export type ChatStatus = "answered" | "clarification_required" | "confirmation_required" | "refused" | "partial";

export type ChatResponse = {
  conversation_id: string;
  request_id: string;
  status: ChatStatus;
  code?: string;
  intents: string[];
  routes: string[];
  classification_source: string;
  message: string;
  parts: ChatPart[];
  follow_up: boolean;
};

export type Conversation = {
  conversation_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  sequence: number;
  role: "USER" | "ASSISTANT";
  content: string;
  route?: string;
  status?: string;
  error_code?: string;
  created_at: string;
};

export type ConversationDetail = Conversation & { messages: ConversationMessage[] };

export type ConfirmationResponse = {
  status: "completed" | "failed";
  message: string;
  action?: string;
  summary?: string;
  result?: Record<string, SqlCell>;
  error_code?: string;
};

export type ApiError = { code: string; message: string };

/** GET /api/v1/chat/permissions: the caller's AI capabilities as derived by the backend (UX hint for the client). */
export type AiPermissions = { employee_id: number; role: string; capabilities: string[]; data_scope: string };

export type AuditEvent = {
  event_id: string;
  request_id: string;
  sequence: number;
  conversation_id: string | null;
  employee_id: number | null;
  event_type: string;
  status: string;
  route: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type AuditPage = { items: AuditEvent[]; meta: { total: number; limit: number; offset: number } };

export type LatencySummary = { count: number; avg: number | null; p95: number | null; max: number | null };

export type AuditMetrics = {
  requests_total: number;
  requests_success: number;
  requests_failed: number;
  requests_denied: number;
  requests_refused: number;
  requests_clarification: number;
  authorization_denied: number;
  sql_security_rejections: number;
  action_proposals: number;
  action_confirmations: number;
  action_executions: number;
  action_rejections: number;
  action_expirations: number;
  provider_calls: number;
  provider_errors: number;
  ai_errors: number;
  refusals_by_reason: Record<string, number>;
  latency_ms: LatencySummary;
  provider_latency_ms: LatencySummary;
  estimated_cost_usd: number | null;
  // Phase 10 usage dashboard (operational metrics derived from audit events)
  requests_by_role: Record<string, number>;
  intents: Record<string, number>;
  tools: { policy_rag: number; sql_agent: number; action_agent: number };
  actions_proposed_by_type: Record<string, number>;
  policy_answers: number;
  rag_no_answer_rate: number | null;
  action_confirmation_rate: number | null;
  action_execution: { succeeded: number; failed: number; success_rate: number | null };
};

// ---- result type ----------------------------------------------------------------------------------

export type CopilotResult<T> = {
  ok: boolean;
  status: number; // 0 = network failure / no response
  data?: T;
  error?: ApiError;
  requestId?: string;
  conversationId?: string; // X-Conversation-Id on failed /chat turns
};

const NETWORK_ERROR: ApiError = { code: "NETWORK_ERROR", message: "" };

async function call<T>(token: string, path: string, init: RequestInit = {}): Promise<CopilotResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, ...(init.body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 0, error: NETWORK_ERROR };
  }
  const requestId = response.headers.get("X-Request-Id") ?? undefined;
  const conversationId = response.headers.get("X-Conversation-Id") ?? undefined;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const envelope = body as { success?: boolean; data?: T; error?: ApiError | null } | null;
  if (response.ok && envelope?.success) {
    return { ok: true, status: response.status, data: envelope.data, requestId, conversationId };
  }
  const error = envelope?.error && typeof envelope.error.code === "string"
    ? { code: envelope.error.code, message: String(envelope.error.message ?? "") }
    : { code: response.status === 422 ? "VALIDATION_ERROR" : `HTTP_${response.status}`, message: "" };
  return { ok: false, status: response.status, error, requestId, conversationId };
}

// ---- endpoints --------------------------------------------------------------------------------------

/** POST /api/v1/chat. The body is built here from exactly two fields; nothing else can be added. */
export function sendChat(token: string, message: string, conversationId?: string | null): Promise<CopilotResult<ChatResponse>> {
  const body: ChatRequest = conversationId ? { message, conversation_id: conversationId } : { message };
  return call<ChatResponse>(token, "/api/v1/chat", { method: "POST", body: JSON.stringify(body) });
}

export function listConversations(token: string, limit = 30): Promise<CopilotResult<Conversation[]>> {
  return call<Conversation[]>(token, `/api/v1/chat/conversations?limit=${limit}`);
}

export function getConversation(token: string, conversationId: string, limit = 100): Promise<CopilotResult<ConversationDetail>> {
  return call<ConversationDetail>(token, `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}?limit=${limit}`);
}

export function archiveConversation(token: string, conversationId: string): Promise<CopilotResult<{ conversation_id: string; status: string }>> {
  return call(token, `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

/** POST /api/v1/chat/actions/confirm with ONLY the backend-issued confirmation id. Never retried. */
export function confirmAction(token: string, confirmationId: string): Promise<CopilotResult<ConfirmationResponse>> {
  return call<ConfirmationResponse>(token, "/api/v1/chat/actions/confirm", {
    method: "POST",
    body: JSON.stringify({ confirmation_id: confirmationId }),
  });
}

export function fetchAiPermissions(token: string): Promise<CopilotResult<AiPermissions>> {
  return call<AiPermissions>(token, "/api/v1/chat/permissions");
}

export type AuditQuery = { limit?: number; offset?: number; event_type?: string; status?: string; route?: string };

export function fetchAuditEvents(token: string, query: AuditQuery = {}): Promise<CopilotResult<AuditPage>> {
  const params = new URLSearchParams();
  params.set("limit", String(query.limit ?? 50));
  params.set("offset", String(query.offset ?? 0));
  for (const key of ["event_type", "status", "route"] as const) {
    if (query[key]) params.set(key, String(query[key]));
  }
  return call<AuditPage>(token, `/api/v1/ai/audit?${params.toString()}`);
}

export function fetchAuditMetrics(token: string): Promise<CopilotResult<AuditMetrics>> {
  return call<AuditMetrics>(token, "/api/v1/ai/audit/metrics");
}

// ---- narrowing helpers (shape checks, not trust) ------------------------------------------------------

export function isPolicyData(data: ChatPart["data"]): data is PolicyData {
  return !!data && Array.isArray((data as PolicyData).sources);
}

export function isSqlData(data: ChatPart["data"]): data is SqlData {
  return !!data && Array.isArray((data as SqlData).columns) && Array.isArray((data as SqlData).rows);
}

export function isActionData(data: ChatPart["data"]): data is ActionData {
  return !!data && typeof (data as ActionData).status === "string" && "message" in data;
}
