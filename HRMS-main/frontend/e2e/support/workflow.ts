// Helpers for the business-workflow specs (workflow-*.spec.ts): synthetic people, persisted-state reads and UI
// shortcuts. Every helper talks to the disposable E2E stack only; nothing prints tokens or passwords.
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { call, errorCode, login, type ApiResult } from "./api";
import { E2E, seededCredentials } from "./env";

export type Person = { id: number; email: string; password: string; name: string; token: string };

export function runTag(): string {
  return `${Date.now().toString(36)}${crypto.randomBytes(2).toString("hex")}`;
}

export function syntheticPassword(): string {
  return `E2e-${crypto.randomBytes(10).toString("base64url")}-7b`;
}

/** ADMIN creates a synthetic person (no manager: the API cannot set one) and signs them in. */
/** Complete SYNTHETIC profile + finance values: the admin edit form requires every one of them. */
export const FULL_PROFILE = {
  date_of_birth: "1994-04-04", phone: "+919000000123", occupancy: "Full-Time", job_title: "Engineer",
  current_salary_usd: 1000, bank_name: "Synthetic Bank", bank_account_number: "000000000001", bank_account_name: "E2E Person",
  bank_branch: "Main", bank_ifsc: "SYNT0000001", pan_number: "SYNTH0001E", pan_name: "E2E Person", pan_dob: "1994-04-04",
  pf_uan: "SYNPF0001", esi_no: "SYNESI0001",
};

export async function createPerson(api: APIRequestContext, adminToken: string, role: "EMPLOYEE" | "MANAGER" | "ADMIN",
                                   label: string, extra: Record<string, unknown> = {}): Promise<Person> {
  const tag = runTag();
  const email = `e2e.${label.toLowerCase()}.${tag}@hrms-e2e.dev`;
  const password = syntheticPassword();
  const name = `E2E ${label} ${tag}`;
  const created = await call(api, adminToken, "POST", "/api/v1/employees", {
    data: { name, email, password, role, joining_date: "2026-01-05", ...extra },
    headers: { "X-Step-Up-Password": seededCredentials("ADMIN")!.password },
  });
  expect(created.status, `create synthetic ${label}`).toBe(200);
  const signedIn = await login(api, email, password);
  expect(signedIn.status, `sign in synthetic ${label}`).toBe(200);
  return { id: created.body.data.id, email, password, name, token: signedIn.token! };
}

export function items(result: ApiResult): any[] {
  const data = result.body?.data;
  return Array.isArray(data) ? data : data?.items ?? [];
}

export async function employeeRecord(api: APIRequestContext, adminToken: string, id: number): Promise<any> {
  const result = await call(api, adminToken, "GET", `/api/v1/employees/${id}`);
  expect(result.status).toBe(200);
  return result.body.data;
}

export async function myLeaves(api: APIRequestContext, token: string): Promise<any[]> {
  return items(await call(api, token, "GET", "/api/v1/leaves/requests/me?limit=100"));
}

export async function leaveById(api: APIRequestContext, ownerToken: string, id: number): Promise<any> {
  const leave = (await myLeaves(api, ownerToken)).find((row) => row.id === id);
  expect(leave, `leave ${id} is visible to its owner`).toBeTruthy();
  return leave;
}

export async function remaining(api: APIRequestContext, token: string, leaveType: string): Promise<number> {
  const balances = items(await call(api, token, "GET", "/api/v1/leaves/balances/me"));
  return balances.find((row) => row.leave_type === leaveType).remaining;
}

/** A working day after `from` on which the owner holds no PENDING/APPROVED leave (repeat runs on one stack). */
export async function freeWorkday(api: APIRequestContext, token: string, from = new Date(Date.UTC(2027, 5, 1))): Promise<string> {
  const taken = (await myLeaves(api, token)).filter((row) => row.status !== "REJECTED");
  for (let offset = 0; offset < 400; offset += 1) {
    const day = new Date(from.getTime() + offset * 86_400_000);
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue;
    const iso = day.toISOString().slice(0, 10);
    if (!taken.some((row) => row.start_date <= iso && row.end_date >= iso)) return iso;
  }
  throw new Error("no free working day found");
}

export async function ticketById(api: APIRequestContext, token: string, id: number): Promise<any | undefined> {
  return items(await call(api, token, "GET", "/api/v1/tickets?limit=100")).find((row) => row.id === id);
}

/** Pick a date in the app's calendar widget (the field's visible name opens it). */
export async function pickDate(page: Page, buttonName: string, iso: string): Promise<void> {
  const [year, month, day] = iso.split("-").map(Number);
  await page.getByRole("button", { name: buttonName, exact: true }).click();
  const calendar = page.locator("div.absolute.z-50").filter({ has: page.locator("select") });
  await calendar.locator("select").nth(1).selectOption(String(year));
  await calendar.locator("select").nth(0).selectOption(String(month - 1));
  await calendar.getByRole("button", { name: String(day), exact: true }).click();
}

/** Re-index HR policies inside the E2E api container (the application's own mechanism). null = could not run. */
export function reindexPolicies(): string | null {
  try {
    return execFileSync("docker", ["compose", "-f", E2E.composeFile, "-p", E2E.composeProject, "exec", "-T", "api",
                                    "python", "scripts/ingest_policies.py"],
                        { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"], timeout: 120_000 });
  } catch {
    return null;
  }
}

export { call, errorCode };

/** Fill the ADMIN "Add Employee" form (both steps) with synthetic values; does not submit. */
export async function fillCreateEmployeeForm(page: Page, person: { name: string; email: string; password: string }): Promise<void> {
  const fill = (placeholder: string, value: string) => page.getByPlaceholder(placeholder).fill(value);
  await fill("e.g. John Miller", person.name);
  await fill("e.g. john@company.com", person.email);
  await fill("Minimum 8 characters", person.password);
  await pickDate(page, "Date of Joining", "2026-10-01");
  await pickDate(page, "Date of Birth", "1996-06-06");
  await fill("e.g. Software Engineer", "Engineer");
  await fill("Local number", "9000000777");
  await page.getByRole("combobox").filter({ hasText: "Select employment type" }).selectOption("Full-Time");
  await page.getByRole("button", { name: "Continue" }).click();
  await fill("e.g. 65000", "1000");
  await fill("e.g. HDFC Bank", "Synthetic Bank");
  await fill("e.g. 123456789012", "000000000003");
  await fill("As per bank records", person.name);
  await fill("e.g. City Center", "Main");
  await fill("e.g. HDFC0001234", "SYNT0000003");
  await fill("e.g. ABCDE1234F", "SYNTH0003E");
  await fill("As per PAN card", person.name);
  await pickDate(page, "PAN Date of Birth", "1996-06-06");
  await fill("Provident Fund UAN", "SYNPF0003");
  await fill("Employee State Insurance Number", "SYNESI0003");
}

/** ADMIN directory search; returns the matching records' ids. */
export async function searchEmployees(api: APIRequestContext, adminToken: string, q: string): Promise<any[]> {
  return items(await call(api, adminToken, "GET", `/api/v1/employees?q=${encodeURIComponent(q)}`));
}
