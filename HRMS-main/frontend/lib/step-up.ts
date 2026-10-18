/**
 * G-11 password step-up for high-impact admin operations. UX only: the backend (app/services/step_up.py) decides,
 * logs or blocks. This module only decides when to ask, so administrators are not asked when nothing protected
 * changes, and maps the backend's step-up refusals to clear messages.
 */
import type { ContentAudience, EmployeeUpdatePayload } from "@/lib/api";

/** The values loaded into the edit form, as the backend returned them (the comparison baseline). */
export type EmployeeSensitiveSnapshot = {
  email: string | null;
  role: string | null;
  bank_name: string | null;
  bank_account_number: string | null;
  bank_account_name: string | null;
  bank_branch: string | null;
  bank_ifsc: string | null;
  pan_number: string | null;
  pan_name: string | null;
  pan_dob: string | null;
};

const TEXT_FIELDS = ["bank_name", "bank_account_number", "bank_account_name", "bank_branch", "pan_name"] as const;
const UPPER_FIELDS = ["bank_ifsc", "pan_number"] as const;

/**
 * Mirrors the backend's `_update_needs_step_up`: a new password, a different role, a different email
 * (case-insensitive), or changed bank/PAN details. Values are normalised the way the backend stores them, so
 * re-submitting what was loaded asks for nothing. Fields the payload does not send are not changes.
 */
export function employeeUpdateNeedsStepUp(original: EmployeeSensitiveSnapshot, payload: EmployeeUpdatePayload): boolean {
  if (payload.password && payload.password.trim()) return true;
  if (payload.role !== undefined && payload.role !== original.role) return true;
  if (payload.email !== undefined && payload.email.trim().toLowerCase() !== (original.email ?? "").toLowerCase()) {
    return true;
  }
  for (const field of TEXT_FIELDS) {
    const value = payload[field];
    if (value !== undefined && value.trim() !== (original[field] ?? "")) return true;
  }
  for (const field of UPPER_FIELDS) {
    const value = payload[field];
    if (value !== undefined && value.trim().toUpperCase() !== (original[field] ?? "")) return true;
  }
  return payload.pan_dob !== undefined && payload.pan_dob !== "" && payload.pan_dob !== original.pan_dob;
}

export function audienceNeedsStepUp(audience: ContentAudience): boolean {
  return audience === "ORG";
}

export type StepUpFailure = "STEP_UP_REQUIRED" | "STEP_UP_FAILED" | "STEP_UP_THROTTLED";

const MESSAGES: Record<StepUpFailure, string> = {
  STEP_UP_REQUIRED: "Enter your password to confirm this change. Nothing was changed.",
  STEP_UP_FAILED: "Your password could not be verified. Nothing was changed.",
  STEP_UP_THROTTLED: "Too many password attempts. Wait a minute and try again. Nothing was changed.",
};

/** The step-up refusal in a response body, in either envelope shape ({detail: {error}} or {error}); else null. */
export function stepUpFailure(body: unknown): StepUpFailure | null {
  if (!body || typeof body !== "object") return null;
  const record = body as Record<string, unknown>;
  const envelope = (record.detail && typeof record.detail === "object" ? record.detail : record) as Record<string, unknown>;
  const error = envelope.error as Record<string, unknown> | undefined;
  const code = error && typeof error.code === "string" ? error.code : null;
  return code && code in MESSAGES ? (code as StepUpFailure) : null;
}

export function stepUpMessage(failure: StepUpFailure): string {
  return MESSAGES[failure];
}

/** No automatic retry after a network failure: the server may have applied the change. */
export const STEP_UP_NETWORK_MESSAGE =
  "We couldn't reach the server, so we don't know whether this change was applied. Refresh the page and check before trying again.";

/** What one attempt from the dialog led to: close the dialog, or keep it open with a message for a new attempt. */
export type StepUpAttempt = { kind: "done" } | { kind: "retry"; message: string };
