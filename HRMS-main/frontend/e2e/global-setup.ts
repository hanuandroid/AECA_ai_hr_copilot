// Runs once before the suite, against the disposable stack only (docker-compose.e2e.yml). It checks readiness once (the
// stack is started with `up --wait`, which waits for the health checks; nothing here sleeps), then creates the
// synthetic people the scenarios need through the real admin API:
//   outsider      EMPLOYEE with no manager (outside Manager User's team), with one pending leave
//   inactive      EMPLOYEE created, signed in once (token kept), then deactivated
//   sessionUser   EMPLOYEE used for sign-out-everywhere (so the shared seeded sessions are never revoked)
//   editTarget / toggleTarget   EMPLOYEEs the step-up UI scenarios edit and deactivate/reactivate
// plus a ticket owned by Employee User. Generated passwords are random and live only in e2e/.runtime (git-ignored).
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { apiContext, call, login, type RuntimeFixtures } from "./support/api";
import { E2E, seededCredentials } from "./support/env";

function syntheticPassword(): string {
  return `E2e-${crypto.randomBytes(12).toString("base64url")}-9a`;
}

async function expectReachable(url: string, what: string): Promise<void> {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    throw new Error(`${what} at ${url} is not ready (${String(error)}). Start the disposable stack first: ` +
      "docker compose -f docker-compose.e2e.yml up -d --build --wait");
  }
}

export default async function globalSetup(): Promise<void> {
  fs.mkdirSync(E2E.runtimeDir, { recursive: true });
  const marker = path.join(E2E.runtimeDir, "fixtures.json");
  fs.rmSync(marker, { force: true });
  const admin = seededCredentials("ADMIN");
  if (!admin || !seededCredentials("MANAGER") || !seededCredentials("EMPLOYEE")) return; // specs skip: NOT_TESTED

  await expectReachable(`${E2E.apiUrl}/api/v1/health`, "E2E API");
  await expectReachable(`${E2E.webUrl}/login`, "E2E web app");

  const api = await apiContext();
  const tokens: Record<string, string> = {};
  for (const role of ["ADMIN", "MANAGER", "EMPLOYEE"] as const) {
    const credentials = seededCredentials(role)!;
    const result = await login(api, credentials.email, credentials.password);
    if (result.status !== 200 || !result.token) throw new Error(`seeded ${role} cannot sign in (HTTP ${result.status})`);
    tokens[role] = result.token;
  }
  const meId = async (token: string) => (await call(api, token, "GET", "/api/v1/employees/me")).body.data.id as number;
  const stepUp = { "X-Step-Up-Password": admin.password }; // G-11 (log mode): sent as the admin UI would

  const run = Date.now().toString(36);
  const people: RuntimeFixtures["people"] = {} as RuntimeFixtures["people"];
  const created: Record<string, number> = {};
  const emails: Record<string, string> = {};
  for (const key of ["outsider", "inactive", "sessionUser", "editTarget", "toggleTarget"]) {
    const email = `e2e.${key.toLowerCase()}.${run}@hrms-e2e.dev`;
    const password = syntheticPassword();
    // The edit form requires every profile and finance field, so the edit target gets complete SYNTHETIC values.
    const profile = key === "editTarget" ? {
      date_of_birth: "1994-04-04", phone: "+919000000123", occupancy: "Full-Time", job_title: "Engineer",
      current_salary_usd: 1000, bank_name: "Synthetic Bank", bank_account_number: "000000000001",
      bank_account_name: "E2E Edit Target", bank_branch: "Main", bank_ifsc: "SYNT0000001", pan_number: "SYNTH0001E",
      pan_name: "E2E Edit Target", pan_dob: "1994-04-04", pf_uan: "SYNPF0001", esi_no: "SYNESI0001",
    } : {};
    const result = await call(api, tokens.ADMIN, "POST", "/api/v1/employees", {
      data: { name: `E2E ${key} ${run}`, email, password, role: "EMPLOYEE", joining_date: "2026-01-05", ...profile },
      headers: stepUp,
    });
    if (result.status !== 200) throw new Error(`could not create synthetic ${key} (HTTP ${result.status})`);
    created[key] = result.body.data.id;
    emails[key] = email;
    if (key === "outsider" || key === "inactive" || key === "sessionUser") people[key] = { email, password };
  }

  const inactiveLogin = await login(api, people.inactive.email, people.inactive.password);
  if (!inactiveLogin.token) throw new Error("synthetic inactive user could not sign in before deactivation");
  const deactivated = await call(api, tokens.ADMIN, "DELETE", `/api/v1/employees/${created.inactive}`, { headers: stepUp });
  if (deactivated.status !== 200) throw new Error(`could not deactivate the synthetic inactive user (HTTP ${deactivated.status})`);

  const outsiderToken = (await login(api, people.outsider.email, people.outsider.password)).token!;
  const leave = await call(api, outsiderToken, "POST", "/api/v1/leaves/requests", {
    data: { leave_type: "CASUAL", start_date: "2027-02-10", end_date: "2027-02-10", reason: "E2E synthetic leave" },
  });
  if (leave.status !== 200) throw new Error(`could not create the outsider's pending leave (HTTP ${leave.status})`);
  const ticket = await call(api, tokens.EMPLOYEE, "POST", "/api/v1/tickets", {
    data: { title: `E2E ticket ${run}`, description: "E2E synthetic ticket", category: "IT", priority: "LOW" },
  });
  if (ticket.status !== 200) throw new Error(`could not create the employee's ticket (HTTP ${ticket.status})`);

  const fixtures: RuntimeFixtures = {
    ids: {
      admin: await meId(tokens.ADMIN), manager: await meId(tokens.MANAGER), employee: await meId(tokens.EMPLOYEE),
      outsider: created.outsider, inactive: created.inactive, sessionUser: created.sessionUser,
      editTarget: created.editTarget, toggleTarget: created.toggleTarget,
    },
    people,
    targetEmails: { editTarget: emails.editTarget, toggleTarget: emails.toggleTarget },
    inactiveTokenBeforeDeactivation: inactiveLogin.token,
    outsiderPendingLeaveId: leave.body.data.id,
    employeeTicketId: ticket.body.data.id,
  };
  fs.writeFileSync(marker, JSON.stringify(fixtures), { encoding: "utf-8", mode: 0o600 });
  await api.dispose();
}
