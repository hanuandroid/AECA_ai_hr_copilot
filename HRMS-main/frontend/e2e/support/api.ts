// Direct API access with real tokens issued by the disposable E2E backend. Helpers never print tokens, passwords or
// response bodies in assertion messages: failures show the method, path and status only.
import fs from "node:fs";
import path from "node:path";

import { expect, request as playwrightRequest, type APIRequestContext } from "@playwright/test";

import { E2E, seededCredentials, type SeededRole } from "./env";

export type Identity = "anon" | "employee" | "manager" | "admin";
export type ApiResult = { status: number; body: any };

/** Fixtures created by global-setup.ts (ids and generated synthetic passwords), git-ignored under e2e/.runtime/. */
export type RuntimeFixtures = {
  ids: { admin: number; manager: number; employee: number; outsider: number; inactive: number; sessionUser: number;
         editTarget: number; toggleTarget: number };
  people: Record<"outsider" | "inactive" | "sessionUser", { email: string; password: string }>;
  targetEmails: { editTarget: string; toggleTarget: string };
  inactiveTokenBeforeDeactivation: string;
  outsiderPendingLeaveId: number;
  employeeTicketId: number;
};

export function fixtures(): RuntimeFixtures {
  return JSON.parse(fs.readFileSync(path.join(E2E.runtimeDir, "fixtures.json"), "utf-8"));
}

export async function apiContext(): Promise<APIRequestContext> {
  return playwrightRequest.newContext({ baseURL: E2E.apiUrl });
}

export async function login(api: APIRequestContext, email: string, password: string): Promise<ApiResult & { token?: string }> {
  const response = await api.post("/api/v1/auth/login", { data: { email, password } });
  const body = await response.json().catch(() => null);
  return { status: response.status(), body, token: body?.data?.access_token };
}

const tokenCache = new Map<string, string>();

/** A token for a seeded role (logged in once per worker and cached). */
export async function tokenFor(api: APIRequestContext, role: SeededRole): Promise<string> {
  const cached = tokenCache.get(role);
  if (cached) return cached;
  const credentials = seededCredentials(role);
  if (!credentials) throw new Error(`credentials for ${role} are not configured`);
  const result = await login(api, credentials.email, credentials.password);
  expect(result.status, `login as ${role}`).toBe(200);
  tokenCache.set(role, result.token!);
  return result.token!;
}

export async function call(
  api: APIRequestContext,
  token: string | null,
  method: string,
  url: string,
  options: { data?: unknown; multipart?: Record<string, any>; headers?: Record<string, string> } = {},
): Promise<ApiResult> {
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await api.fetch(url, { method, headers, data: options.data, multipart: options.multipart });
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null; // binary downloads
  }
  return { status: response.status(), body };
}

/** Business-endpoint error code in either envelope shape ({detail: {error}} or {error}). */
export function errorCode(result: ApiResult): string | undefined {
  return result.body?.detail?.error?.code ?? result.body?.error?.code;
}

export function classify(status: number): string {
  if (status === 401) return "auth";
  if (status === 403) return "deny";
  if (status === 404) return "hidden";
  if (status >= 200 && status < 300) return "ok";
  if ([400, 409, 422].includes(status)) return "rejected";
  return String(status);
}
