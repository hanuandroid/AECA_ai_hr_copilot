/**
 * Display helpers for untrusted AI content (defence in depth; the backend already redacts).
 *
 * - All AI text is rendered as React text nodes (never as HTML), so markup such as <script> is inert.
 * - maskSensitive() hides values that look like tokens, API keys, PAN, IFSC or account numbers even if
 *   a malformed response contained them.
 * - visibleColumns() drops columns whose names denote sensitive HR fields.
 * - copilotSuggestions() is a UI HINT ONLY: it chooses example prompts by role for convenience. It grants
 *   nothing; the backend authorizes every request regardless of what the UI shows.
 */

const MASKS: Array<[RegExp, string]> = [
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[hidden]"], // JWT
  [/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, "[hidden]"],
  [/\bsk-[A-Za-z0-9_-]{16,}\b/g, "[hidden]"], // API keys
  [/\b[A-Z]{5}[0-9]{4}[A-Z]\b/g, "[hidden]"], // PAN
  [/\b[A-Z]{4}0[A-Z0-9]{6}\b/g, "[hidden]"], // IFSC
  [/\b\d{9,18}\b/g, "[hidden]"], // account / UAN-like long numbers
];

export function maskSensitive(text: string): string {
  return MASKS.reduce((value, [pattern, replacement]) => value.replace(pattern, replacement), text);
}

const SENSITIVE_COLUMN = /(pan|bank|ifsc|account_number|salary|password|hashed|uan|esi|date_of_birth|dob|token|secret)/i;

export function visibleColumns(columns: string[]): string[] {
  return columns.filter((column) => !SENSITIVE_COLUMN.test(column));
}

export function columnLabel(column: string): string {
  return column.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function cellText(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return maskSensitive(String(value));
}

// ---- role-specific Copilot modes (UX only) ------------------------------------------------------------

export type CopilotMode = { key: "EMPLOYEE" | "MANAGER" | "ADMIN"; title: string; focus: string[]; prompts: string[] };

type PromptHint = { text: string; capability: string };

const POLICY: PromptHint[] = [
  { text: "What is the sick leave policy?", capability: "POLICY_READ" },
  { text: "Can I work from home?", capability: "POLICY_READ" },
];

const MODES: Record<CopilotMode["key"], { title: string; focus: string[]; prompts: PromptHint[] }> = {
  EMPLOYEE: {
    title: "Employee Copilot",
    focus: ["Policy questions", "Your leave", "Your tickets", "Your projects"],
    prompts: [
      ...POLICY,
      { text: "Show my leave balance.", capability: "SQL_READ_SELF" },
      { text: "Show my open tickets.", capability: "SQL_READ_SELF" },
      { text: "Show my current project assignments.", capability: "SQL_READ_SELF" },
      { text: "Create a high-priority IT ticket for VPN not working.", capability: "TICKET_CREATE" },
    ],
  },
  MANAGER: {
    title: "Manager Copilot",
    focus: ["Team insights", "Leave approvals", "Ticket management", "Project staffing"],
    prompts: [
      ...POLICY,
      { text: "Show my leave balance.", capability: "SQL_READ_SELF" },
      { text: "How many people report to me?", capability: "SQL_READ_TEAM" },
      { text: "How many pending leave requests does my team have?", capability: "SQL_READ_TEAM" },
      { text: "Approve Employee Test's pending leave request.", capability: "LEAVE_APPROVE" },
      { text: "Which projects are assigned to my team?", capability: "SQL_READ_TEAM" },
    ],
  },
  ADMIN: {
    title: "Admin Copilot",
    focus: ["HR operations", "Announcements", "Employee lifecycle", "Organisation insights", "AI audit"],
    prompts: [
      ...POLICY,
      { text: "Show my leave balance.", capability: "SQL_READ_SELF" },
      { text: "How many open tickets are there?", capability: "SQL_READ_ORG" },
      { text: "How many employees are in Engineering?", capability: "SQL_READ_ORG" },
      { text: "Create an announcement that Friday's townhall is moved to 5 PM.", capability: "ANNOUNCEMENT_CREATE" },
    ],
  },
};

/**
 * UI HINT ONLY. Picks the Copilot mode for the role reported by the backend and, when the backend's capability
 * list is known (GET /api/v1/chat/permissions), shows only prompts the user can actually use. It grants nothing:
 * the backend authorizes every request whatever the UI shows.
 */
export function copilotMode(roleHint: string | null, capabilities?: string[] | null): CopilotMode {
  const key: CopilotMode["key"] = roleHint === "MANAGER" || roleHint === "ADMIN" ? roleHint : "EMPLOYEE";
  const mode = MODES[key];
  const prompts = mode.prompts
    .filter((prompt) => !capabilities || capabilities.includes(prompt.capability))
    .map((prompt) => prompt.text);
  return { key, title: mode.title, focus: mode.focus, prompts };
}

/** UI hint only (see copilotMode). */
export function copilotSuggestions(roleHint: string | null): string[] {
  return copilotMode(roleHint).prompts;
}

export function conversationGroup(updatedAt: string, now: Date = new Date()): "Today" | "Yesterday" | "Earlier" {
  const date = new Date(updatedAt.endsWith("Z") ? updatedAt : `${updatedAt}Z`);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (date.getTime() >= startOfToday) return "Today";
  if (date.getTime() >= startOfToday - 86_400_000) return "Yesterday";
  return "Earlier";
}

const ACTION_LABELS: Record<string, string> = {
  LEAVE_CREATE: "Leave request",
  LEAVE_APPROVE: "Leave approval",
  LEAVE_REJECT: "Leave rejection",
  TICKET_CREATE: "New ticket",
  TICKET_ASSIGN: "Ticket assignment",
  TICKET_STATUS_UPDATE: "Ticket status update",
  ANNOUNCEMENT_CREATE: "Announcement",
  PROJECT_ASSIGN: "Project assignment",
  PROJECT_CREATE: "New project",
  EMPLOYEE_DEACTIVATE: "Employee deactivation",
};

export function actionLabel(action: string | undefined): string {
  return (action && ACTION_LABELS[action]) || "HR action";
}
