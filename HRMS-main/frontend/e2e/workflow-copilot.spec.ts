// Copilot workflows in the real UI against the E2E stack's FAKE model provider (no rules, no keys). Deterministic
// paths: rule-based refusals, the MANAGER skill-search query, the no-answer policy refusal. Every path that needs a
// model fails with AI_INVALID_OUTPUT here, which is used as the "malformed model output" failure case.
// Nothing here measures live-model answer quality.
import type { Page, Response } from "@playwright/test";

import { apiContext, call, classify, errorCode } from "./support/api";
import { E2E } from "./support/env";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { createPerson, items, myLeaves } from "./support/workflow";

async function openCopilot(page: Page): Promise<void> {
  await page.goto("/copilot");
  await expectPageTitle(page, "AI Copilot");
}

async function ask(page: Page, question: string): Promise<Response> {
  const answered = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/v1/chat"));
  await page.getByPlaceholder("Ask PeopleOps Copilot…").fill(question);
  await page.getByRole("button", { name: "Send" }).click();
  return answered;
}

const conversation = (page: Page) => page.getByRole("list", { name: "Conversation messages" });

test.describe("Copilot workflows (fake provider)", () => {
  test("AI-A MANAGER asks an authorized question: answered from data, persisted, reloadable, private to the owner",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const page = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await openCopilot(page);
      const response = await ask(page, "Which employees know Python?");
      expect(response.status()).toBe(200);
      const body = await response.json();
      expect([body.data.status, body.data.routes]).toEqual(["answered", ["SQL"]]);
      const id: string = body.data.conversation_id;
      await expect(conversation(page)).toContainText("skill matches for Python");
      await expect(conversation(page).getByRole("table")).toBeVisible();
      await expect(page).toHaveURL(`${E2E.webUrl}/copilot/${id}`);
      // Approved skill-search fields only; nothing sensitive in the rows.
      const columns = body.data.parts[0].data.columns as string[];
      expect(columns.filter((column) => /salary|bank|pan|password|dob|birth|phone|email/i.test(column))).toEqual([]);

      const stored = await call(api, tokens.MANAGER, "GET", `/api/v1/chat/conversations/${id}`);
      expect(stored.status).toBe(200);
      const roles = (stored.body.data.messages as { role: string }[]).map((message) => message.role.toLowerCase());
      expect(roles).toEqual(["user", "assistant"]); // one question, one answer, stored for the owner
      await page.reload();
      await expect(conversation(page)).toContainText("Which employees know Python?");
      for (const other of ["EMPLOYEE", "ADMIN"] as const) {
        expect(classify((await call(api, tokens[other], "GET", `/api/v1/chat/conversations/${id}`)).status), other).toBe("hidden");
      }
      await api.dispose();
    });

  test("AI-A EMPLOYEE and ADMIN: a policy question with no matching policy gets the model-free no-answer reply",
    async ({ browser, tokens }) => {
      const page = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const response = await ask(page, "Can I bring my pet to the office?");
      const body = await response.json();
      expect([response.status(), body.data.status, body.data.parts[0].route, body.data.parts[0].data.grounded])
        .toEqual([200, "answered", "POLICY", false]);
      await expect(conversation(page)).toContainText("couldn't find enough information");
      const api = await apiContext();
      const admin = await call(api, tokens.ADMIN, "POST", "/api/v1/chat", { data: { message: "Can I bring my pet to the office?" } });
      expect([admin.status, admin.body.data.parts[0].data.grounded]).toEqual([200, false]);
      await api.dispose();
    });

  test("AI-B restricted question: refused in the UI, no data, the refusal is audited for that employee only",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const page = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const response = await ask(page, "What is Rahul's bank account number?");
      const body = await response.json();
      expect([response.status(), body.data.status]).toEqual([200, "refused"]);
      expect(JSON.stringify(body.data.parts)).not.toMatch(/\d{6,}/); // no account-like numbers in the answer
      await expect(conversation(page)).toContainText("I can't help with that request");
      const requestId = response.headers()["x-request-id"];
      expect(requestId).toBeTruthy();
      // Audit events are written right after the response; poll (no sleeps) until they are visible.
      await expect.poll(async () => items(await call(api, tokens.ADMIN, "GET", `/api/v1/ai/audit?request_id=${requestId}&limit=50`))
        .filter((event) => event.event_type === "REFUSAL" && event.status === "REFUSED" && event.employee_id === data.ids.employee).length)
        .toBe(1);
      const ownView = items(await call(api, tokens.EMPLOYEE, "GET", `/api/v1/ai/audit?request_id=${requestId}&limit=50`));
      expect(ownView.length).toBeGreaterThan(0);
      const stranger = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "AuditStranger");
      expect(items(await call(api, stranger.token, "GET", `/api/v1/ai/audit?request_id=${requestId}&limit=50`))).toEqual([]);
      await api.dispose();
    });

  test("AI-C an action request the scripted model does not cover fails safely: no confirmation, no business change",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const before = (await myLeaves(api, tokens.EMPLOYEE)).length;
      const page = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const response = await ask(page, "Apply casual leave for tomorrow because of personal work.");
      expect(response.status()).toBe(502);
      expect((await response.json()).error.code).toBe("AI_INVALID_OUTPUT");
      await expect(page.getByRole("alert", { name: "Copilot error" })).toContainText("produced an unusable answer");
      await expect(page.getByRole("button", { name: /^Confirm/ })).toHaveCount(0);
      expect((await myLeaves(api, tokens.EMPLOYEE)).length).toBe(before);
      // The full propose -> confirm -> execute lifecycle (scripted proposals) is in workflow-copilot-actions.spec.ts.
      await api.dispose();
    });

  test("AI-D provider outage and recovery, malformed model output, archived conversation", async ({ browser, tokens }) => {
    const api = await apiContext();
    const page = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
    await openCopilot(page);
    // Simulated outage: the browser's /chat call gets a 503 (intercepted); the user sees a friendly error.
    await page.route("**/api/v1/chat", (route) => route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ success: false, data: null, error: { code: "AI_UNAVAILABLE", message: "unavailable" } }) }));
    await ask(page, "Which employees know Python?");
    await expect(page.getByRole("alert", { name: "Copilot error" })).toContainText("temporarily unavailable");
    await page.unroute("**/api/v1/chat");
    // Recovery: the user retries; the real backend answers.
    const retried = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/v1/chat"));
    await page.getByRole("button", { name: "Try again" }).click();
    expect((await retried).status()).toBe(200);
    await expect(conversation(page)).toContainText("skill matches for Python");

    // Malformed model output (the fake provider has no rule for this): friendly error, nothing executed.
    const malformed = await ask(page, "How many sick leaves can I take?");
    expect([malformed.status(), (await malformed.json()).error.code]).toEqual([502, "AI_INVALID_OUTPUT"]);
    await expect(page.getByRole("alert", { name: "Copilot error" }).last()).toContainText("unusable answer");

    // Archived conversation: gone for its owner; a new message to it is refused like an unknown one.
    const id = page.url().split("/copilot/")[1];
    expect(id).toBeTruthy();
    expect((await call(api, tokens.MANAGER, "DELETE", `/api/v1/chat/conversations/${id}`)).status).toBe(200);
    expect(classify((await call(api, tokens.MANAGER, "GET", `/api/v1/chat/conversations/${id}`)).status)).toBe("hidden");
    const toArchived = await call(api, tokens.MANAGER, "POST", "/api/v1/chat", { data: { message: "Which employees know Python?", conversation_id: id } });
    expect([toArchived.status, errorCode(toArchived)]).toEqual([404, "AI_CONVERSATION_NOT_FOUND"]);
    await api.dispose();
  });
});
