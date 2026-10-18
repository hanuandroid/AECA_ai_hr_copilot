/**
 * Central mapping of backend error codes to user-facing copy. Only real backend codes are listed
 * (backend/app/services/ai/errors.py). Unknown codes fall back to a generic message by HTTP status.
 * Raw server messages, stack traces and internal details are never shown.
 */

import type { ApiError } from "@/lib/copilot-api";

const MESSAGES: Record<string, string> = {
  // availability / provider
  AI_UNAVAILABLE: "The Copilot is temporarily unavailable. Please try again later.",
  AI_PROVIDER_ERROR: "The Copilot couldn't get a response right now. Please try again.",
  AI_INVALID_OUTPUT: "The Copilot produced an unusable answer. Please rephrase and try again.",
  AI_EMBEDDING_FAILED: "The Copilot couldn't process that question right now. Please try again.",
  AI_RETRIEVAL_FAILED: "The Copilot couldn't search the HR policies right now. Please try again.",
  AI_BACKEND_UNAVAILABLE: "The HR system couldn't complete this right now. Please try again later.",
  // usage limits (server-side; production hardening B-09)
  AI_RATE_LIMITED: "You're sending requests too quickly. Please wait a minute and try again.",
  AI_BUDGET_EXCEEDED: "You've reached today's Copilot usage limit. Please try again tomorrow.",
  AI_BUSY: "The Copilot is busy right now. Please try again shortly.",
  AI_REQUEST_LIMIT_EXCEEDED: "That request needed too much work. Please ask a shorter or simpler question.",
  AI_REQUEST_TIMEOUT: "The Copilot took too long to answer. Please try again.",
  AI_PROVIDER_POLICY_BLOCKED: "The Copilot could not process this request safely. Please rephrase it.",
  // understanding / routing
  AI_INVALID_REQUEST: "Please enter a question (up to 1000 characters).",
  AI_CLASSIFICATION_FAILED: "I couldn't understand that request. Please rephrase it.",
  AI_ROUTING_FAILED: "Something went wrong while handling that request. Please try again.",
  AI_MAX_ITERATIONS: "That request needs too many steps. Please ask one thing at a time.",
  // authorization
  AI_PERMISSION_DENIED: "You don't have permission to do that.",
  AI_USER_INACTIVE: "Your account is not active, so the Copilot is unavailable.",
  AI_TARGET_NOT_FOUND: "I couldn't find a matching item that you're allowed to act on.",
  // actions
  AI_CONFIRMATION_INVALID: "This confirmation is no longer valid (it may have expired or already been used). Please ask again.",
  AI_AUDIT_UNAVAILABLE: "The action could not be completed right now. Nothing was changed. Please try again.",
  AI_ACTION_OUTCOME_UNKNOWN: "We couldn't confirm whether this action completed. Please check the related page before trying again.",
  AI_STEP_UP_FAILED: "Your password could not be verified. Nothing was changed. Please try again.",
  AI_STEP_UP_THROTTLED: "Too many password attempts. Please wait a minute and try again. Nothing was changed.",
  // conversations (a generic message: a foreign conversation looks exactly like a missing one)
  AI_CONVERSATION_NOT_FOUND: "Conversation unavailable.",
  AI_CONVERSATION_LIMIT_REACHED: "This conversation is full. Please start a new conversation.",
  // data questions
  SQL_SCOPE_VIOLATION: "You don't have access to that information.",
  SQL_TABLE_NOT_ALLOWED: "That information isn't available through the Copilot.",
  SQL_COLUMN_NOT_ALLOWED: "That information isn't available through the Copilot.",
  SQL_NOT_ALLOWED: "That information isn't available through the Copilot.",
  SQL_MUTATION_REJECTED: "The Copilot can only look data up. It can't change records that way.",
  SQL_PARSE_ERROR: "I couldn't turn that into a data lookup. Please rephrase it.",
  SQL_QUERY_TOO_COMPLEX: "That question is too complex. Please ask something more specific.",
  SQL_EXECUTION_ERROR: "The data lookup failed. Please try again.",
  // client-side
  NETWORK_ERROR: "Can't reach the server. Check your connection and try again.",
  VALIDATION_ERROR: "That request wasn't valid.",
};

const BY_STATUS: Record<number, string> = {
  400: "That request wasn't valid.",
  401: "Your session has expired. Please sign in again.",
  403: "You don't have permission to do that.",
  404: "That item is unavailable.",
  409: "That couldn't be completed because something changed. Please try again.",
  422: "That request wasn't valid.",
  500: "Something went wrong. Please try again.",
  502: "The Copilot couldn't get a response right now. Please try again.",
  503: "The Copilot is temporarily unavailable. Please try again later.",
};

export function friendlyError(error: ApiError | undefined, status: number): string {
  if (error && MESSAGES[error.code]) return MESSAGES[error.code];
  return BY_STATUS[status] ?? "Something went wrong. Please try again.";
}

/** Short reference for support: derived from the backend request id, never shown for success. */
export function errorReference(requestId: string | undefined): string | undefined {
  return requestId ? `RQ-${requestId.slice(0, 8)}` : undefined;
}
