// G-11 password step-up in the real admin UI, under the stack's STEP_UP_ENFORCEMENT=log (the approved setting).
// For each of the six protected operations: the dialog appears, nothing is sent before Confirm, the confirmed request
// carries X-Step-Up-Password with the administrator's password, and the password is not in the URL or body.
// In log mode a wrong or missing password does NOT block: the test shows the operation succeeds and the API logs
// `auth.step_up.not_satisfied`. Blocking (enforce) is covered by backend/tests/rbac/test_step_up_api.py.
// Header values are compared in code and never printed.
import { execFileSync } from "node:child_process";

import type { Page, Request } from "@playwright/test";

import { apiContext, call } from "./support/api";
import { E2E, seededCredentials } from "./support/env";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";

const HEADER = "x-step-up-password";

function adminPassword(): string {
  return seededCredentials("ADMIN")!.password;
}

/** Facts about one request, without exposing the secret. */
function stepUpFacts(request: Request, password: string) {
  const header = request.headers()[HEADER];
  return {
    headerPresent: header !== undefined,
    headerIsAdminPassword: header === password,
    passwordInUrl: request.url().includes(password),
    passwordInBody: (request.postData() ?? "").includes(password),
  };
}

async function confirmInDialog(page: Page, dialogName: string, password: string): Promise<void> {
  const dialog = page.getByRole("dialog", { name: dialogName });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Your password").fill(password);
  await dialog.getByRole("button", { name: "Confirm" }).click();
}

async function pickDate(page: Page, label: string, iso: string): Promise<void> {
  const [year, month, day] = iso.split("-").map(Number);
  await page.getByRole("button", { name: label, exact: true }).click(); // the field label names the picker button
  const calendar = page.locator("div.absolute.z-50").filter({ has: page.locator("select") });
  await calendar.locator("select").nth(1).selectOption(String(year));
  await calendar.locator("select").nth(0).selectOption(String(month - 1));
  await calendar.getByRole("button", { name: String(day), exact: true }).click();
}

function apiLogsSince(since: string): string {
  try {
    return execFileSync("docker", ["compose", "-f", E2E.composeFile, "-p", E2E.composeProject, "logs", "api", "--no-log-prefix",
                                    "--since", since], { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return "";
  }
}

const EXPECTED = { headerPresent: true, headerIsAdminPassword: true, passwordInUrl: false, passwordInBody: false };

test.describe.serial("admin step-up dialogs (log mode)", () => {
  test("ORG announcement: dialog, header, posted; a TEAM post needs no password", async ({ browser, tokens }) => {
    const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
    const page = await context.newPage();
    await page.goto("/announcements");
    await expectPageTitle(page, "Announcements");
    const title = `E2E org post ${Date.now().toString(36)}`;
    await page.getByPlaceholder("Title").fill(title);
    await page.getByPlaceholder(/Announcement details/).fill("Synthetic announcement for the role-access suite.");
    let sent = false;
    page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/api/v1/announcements")) sent = true; });
    await page.getByRole("button", { name: "Post Announcement" }).click();
    await expect(page.getByRole("dialog", { name: "Post to everyone" })).toBeVisible();
    expect(sent, "nothing is sent before the password is confirmed").toBe(false);
    const posted = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/announcements"));
    await confirmInDialog(page, "Post to everyone", adminPassword());
    expect(stepUpFacts(await posted, adminPassword())).toEqual(EXPECTED);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.getByRole("combobox", { name: "Audience" }).selectOption("TEAM");
    await page.getByPlaceholder("Title").fill(`E2E team post ${Date.now().toString(36)}`);
    await page.getByPlaceholder(/Announcement details/).fill("Synthetic team announcement.");
    const teamPost = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/announcements"));
    await page.getByRole("button", { name: "Post Announcement" }).click();
    expect((await teamPost).headers()[HEADER]).toBeUndefined();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await context.close();
  });

  test("ORG poll: dialog and header", async ({ browser, tokens }) => {
    const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
    const page = await context.newPage();
    await page.goto("/polls");
    await expectPageTitle(page, "Polls");
    const question = `E2E poll ${Date.now().toString(36)}?`;
    await page.getByPlaceholder("Question").fill(question);
    await page.getByPlaceholder("Option A, Option B").fill("Yes, No");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const posted = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/polls"));
    await confirmInDialog(page, "Create a poll for everyone", adminPassword());
    expect(stepUpFacts(await posted, adminPassword())).toEqual(EXPECTED);
    await expect(page.getByText(question)).toBeVisible();
    await context.close();
  });

  test("deactivate and reactivate an employee: dialog and header each time", async ({ browser, tokens, data }) => {
    const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
    const page = await context.newPage();
    const api = await apiContext();
    const status = async () => (await call(api, tokens.ADMIN, "GET", `/api/v1/employees/${data.ids.toggleTarget}`)).body.data.status;
    await page.goto(`/employees?q=${encodeURIComponent(data.targetEmails.toggleTarget)}`);
    await expectPageTitle(page, "Employee Directory");
    await page.getByRole("cell", { name: data.targetEmails.toggleTarget }).click();

    const deactivate = page.waitForRequest((request) => request.method() === "DELETE" && request.url().endsWith(`/api/v1/employees/${data.ids.toggleTarget}`));
    await page.getByRole("button", { name: "Deactivate Employee" }).click();
    await confirmInDialog(page, "Deactivate employee", adminPassword());
    expect(stepUpFacts(await deactivate, adminPassword())).toEqual(EXPECTED);
    // The success note is cleared by the list reload that follows (UX finding F-UX-2); the panel shows the new status.
    await expect(page.getByText("Status: INACTIVE")).toBeVisible();
    expect(await status()).toBe("INACTIVE");

    const reactivate = page.waitForRequest((request) => request.method() === "PATCH" && request.url().endsWith(`/api/v1/employees/${data.ids.toggleTarget}/reactivate`));
    await page.getByRole("button", { name: "Reactivate Employee" }).click();
    await confirmInDialog(page, "Reactivate employee", adminPassword());
    expect(stepUpFacts(await reactivate, adminPassword())).toEqual(EXPECTED);
    await expect(page.getByText("Status: ACTIVE")).toBeVisible();
    expect(await status()).toBe("ACTIVE");
    await api.dispose();
    await context.close();
  });

  test("edit employee: a non-sensitive change needs no password; an email change asks and sends the header",
    async ({ browser, tokens, data }) => {
      const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
      const page = await context.newPage();
      const isPut = (request: Request) => request.method() === "PUT" && request.url().endsWith(`/api/v1/employees/${data.ids.editTarget}`);
      const open = async () => {
        await page.goto(`/employees/new?employee_id=${data.ids.editTarget}`);
        await expectPageTitle(page, "Edit Employee");
        await expect(page.getByPlaceholder("e.g. john@company.com")).not.toHaveValue("");
      };
      await open();
      await page.getByPlaceholder("e.g. Software Engineer").fill("Senior Engineer");
      await page.getByRole("button", { name: "Continue" }).click();
      const plain = page.waitForRequest(isPut);
      await page.getByRole("button", { name: "Save Changes" }).click();
      expect((await plain).headers()[HEADER]).toBeUndefined();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page).toHaveURL(/\/employees$/);

      await open();
      const newEmail = `e2e.edited.${Date.now().toString(36)}@hrms-e2e.dev`;
      await page.getByPlaceholder("e.g. john@company.com").fill(newEmail);
      await page.getByRole("button", { name: "Continue" }).click();
      await page.getByRole("button", { name: "Save Changes" }).click();
      const sensitive = page.waitForRequest(isPut);
      await confirmInDialog(page, "Confirm sensitive changes", adminPassword());
      expect(stepUpFacts(await sensitive, adminPassword())).toEqual(EXPECTED);
      await expect(page).toHaveURL(/\/employees$/);
      const api = await apiContext();
      expect((await call(api, tokens.ADMIN, "GET", `/api/v1/employees/${data.ids.editTarget}`)).body.data.email).toBe(newEmail);
      await api.dispose();
      await context.close();
    });

  test("create employee: form validation first, then the dialog and the header", async ({ browser, tokens }) => {
    const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
    const page = await context.newPage();
    await page.goto("/employees/new");
    await expectPageTitle(page, "Add Employee");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText(/are required/)).toBeVisible();
    const email = `e2e.ui.hire.${Date.now().toString(36)}@hrms-e2e.dev`;
    const tempPassword = `E2e-ui-hire-${Date.now().toString(36)}`; // synthetic, for the new account only
    const fill = (placeholder: string, value: string) => page.getByPlaceholder(placeholder).fill(value);
    await fill("e.g. John Miller", "E2E UI Hire");
    await fill("e.g. john@company.com", email);
    await fill("Minimum 8 characters", tempPassword);
    await pickDate(page, "Date of Joining", "2026-10-01");
    await pickDate(page, "Date of Birth", "1996-06-06");
    await fill("e.g. Software Engineer", "Engineer");
    await fill("Local number", "9000000555");
    await page.getByRole("combobox").filter({ hasText: "Select employment type" }).selectOption("Full-Time");
    await page.getByRole("button", { name: "Continue" }).click();
    await fill("e.g. 65000", "1000");
    await fill("e.g. HDFC Bank", "Synthetic Bank");
    await fill("e.g. 123456789012", "000000000002");
    await fill("As per bank records", "E2E UI Hire");
    await fill("e.g. City Center", "Main");
    await fill("e.g. HDFC0001234", "SYNT0000002");
    await fill("e.g. ABCDE1234F", "SYNTH0002E");
    await fill("As per PAN card", "E2E UI Hire");
    await pickDate(page, "PAN Date of Birth", "1996-06-06");
    await fill("Provident Fund UAN", "SYNPF0002");
    await fill("Employee State Insurance Number", "SYNESI0002");
    let sent = false;
    page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/api/v1/employees")) sent = true; });
    await page.getByRole("button", { name: "Create Employee" }).click();
    await expect(page.getByRole("dialog", { name: "Create employee account" })).toBeVisible();
    expect(sent).toBe(false);
    const created = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/employees"));
    await confirmInDialog(page, "Create employee account", adminPassword());
    const request = await created;
    expect(stepUpFacts(request, adminPassword())).toEqual(EXPECTED);
    expect((await request.response())?.status()).toBe(200);
    await expect(page).toHaveURL(/\/employees$/);
    await context.close();
  });

  test("log mode: a wrong or missing password does not block, and the API logs the unsatisfied step-up",
    async ({ browser, tokens }) => {
      const since = new Date(Date.now() - 1000).toISOString();
      const context = await contextWithToken(browser, tokens.ADMIN, "ADMIN");
      const page = await context.newPage();
      await page.goto("/announcements");
      await expectPageTitle(page, "Announcements");
      const title = `E2E wrong password ${Date.now().toString(36)}`;
      await page.getByPlaceholder("Title").fill(title);
      await page.getByPlaceholder(/Announcement details/).fill("Posted with a wrong password in log mode.");
      await page.getByRole("button", { name: "Post Announcement" }).click();
      const posted = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/v1/announcements"));
      await confirmInDialog(page, "Post to everyone", "Not-The-Admin-Password-1");
      expect((await (await posted).response())?.status(), "log mode never blocks").toBe(200);
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
      await context.close();

      const api = await apiContext();
      const missing = await call(api, tokens.ADMIN, "POST", "/api/v1/polls",
                                 { data: { question: `E2E no password ${Date.now().toString(36)}?`, options: ["a", "b"] } });
      expect(missing.status, "no header at all: still allowed in log mode").toBe(200);
      await api.dispose();

      const logs = apiLogsSince(since);
      test.skip(!logs, "NOT_TESTED: docker compose logs for the E2E api are not readable from this runner");
      const events = logs.split(/\r?\n/).filter((line) => line.includes("auth.step_up.not_satisfied"));
      expect(events.some((line) => line.includes("announcement.create_org") && line.includes("INVALID") && line.includes("log"))).toBe(true);
      expect(events.some((line) => line.includes("poll.create_org") && line.includes("MISSING"))).toBe(true);
      // Neither the wrong nor the real password appears anywhere in the API log (compared, never printed).
      expect(logs.includes("Not-The-Admin-Password-1")).toBe(false);
      expect(logs.includes(adminPassword())).toBe(false);
    });
});
