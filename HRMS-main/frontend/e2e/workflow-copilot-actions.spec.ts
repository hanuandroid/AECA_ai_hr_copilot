// Copilot action lifecycle end to end: PROPOSE -> REVIEW -> CONFIRM -> RE-CHECK -> EXECUTE -> VERIFY PERSISTED STATE.
// The only scripted part is the model's ActionProposal (AI_FAKE_LLM_SCRIPT in docker-compose.e2e.yml, schema-aware,
// refused in production); routing, validation, authorization, confirmation records, re-checks, execution through the
// existing business API with the user's own token, persistence and audit are the real application.
// Requests the script recognises: "... ticket titled <title>", "... urgent ticket titled <title>",
// "Approve leave request #<id>", "... announcement titled <title>". No live model is called.
import type { APIRequestContext, Page, Response } from "@playwright/test";

import { apiContext, call, errorCode } from "./support/api";
import { seededCredentials } from "./support/env";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { createPerson, freeWorkday, items, leaveById, runTag } from "./support/workflow";

const CONFIRM = "/api/v1/chat/actions/confirm";

async function openCopilot(page: Page): Promise<void> {
  await page.goto("/copilot");
  await expectPageTitle(page, "AI Copilot");
}

async function ask(page: Page, text: string): Promise<Response> {
  const answered = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/v1/chat"));
  await page.getByPlaceholder("Ask PeopleOps Copilot…").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  return answered;
}

const card = (page: Page) => page.getByRole("region", { name: /confirmation required/ }).last();

async function ticketsTitled(api: APIRequestContext, token: string, title: string): Promise<any[]> {
  return items(await call(api, token, "GET", "/api/v1/tickets?limit=100")).filter((ticket) => ticket.title === title);
}

async function propose(api: APIRequestContext, token: string, question: string) {
  const result = await call(api, token, "POST", "/api/v1/chat/actions", { data: { question } });
  return result;
}

async function auditFor(api: APIRequestContext, adminToken: string, query: string): Promise<any[]> {
  return items(await call(api, adminToken, "GET", `/api/v1/ai/audit?${query}&limit=100`));
}

async function pendingLeaveOfEmployee(api: APIRequestContext, employeeToken: string, from: Date): Promise<number> {
  const date = await freeWorkday(api, employeeToken, from);
  const created = await call(api, employeeToken, "POST", "/api/v1/leaves/requests",
    { data: { leave_type: "CASUAL", start_date: date, end_date: date, reason: `E2E copilot action ${runTag()}` } });
  expect(created.status).toBe(200);
  return created.body.data.id;
}

test.describe("Copilot action confirmation", () => {
  test("ACT-1 propose in the UI, nothing before Confirm, confirm in the UI, executed as the user, persisted, audited; ACT-4 replay refused",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "ActionOwner");
      const title = `E2E VPN outage ${runTag()}`;
      const page = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await openCopilot(page);

      const proposed = await ask(page, `Create a high-priority IT ticket titled ${title}`);
      const body = await proposed.json();
      expect([proposed.status(), body.data.status, body.data.routes]).toEqual([200, "confirmation_required", ["ACTION"]]);
      const part = body.data.parts[0].data;
      expect([part.action, part.summary]).toEqual(["TICKET_CREATE", `Create a HIGH priority IT ticket: '${title}'.`]);
      const confirmationId: string = part.confirmation_id;
      await expect(card(page)).toContainText(title);
      await expect(card(page)).toContainText("Nothing has been changed yet");
      expect(await ticketsTitled(api, person.token, title)).toEqual([]); // proposal never mutates

      let confirmRequests = 0;
      page.on("request", (request) => { if (request.url().endsWith(CONFIRM)) confirmRequests += 1; });
      const confirmed = page.waitForResponse((response) => response.url().endsWith(CONFIRM));
      await card(page).getByRole("button", { name: "Confirm" }).click();
      const confirmResponse = await confirmed;
      const outcome = await confirmResponse.json();
      expect([confirmResponse.status(), outcome.data.status, outcome.data.result.title]).toEqual([200, "completed", title]);
      await expect(card(page).getByRole("status")).toContainText("Done:");

      // Persisted through the business API with the user's own identity (owner = the confirming employee).
      const tickets = await ticketsTitled(api, person.token, title);
      expect(tickets.map((ticket) => [ticket.employee_id, ticket.priority, ticket.category, ticket.status]))
        .toEqual([[person.id, "HIGH", "IT", "OPEN"]]);
      expect(tickets[0].id).toBe(outcome.data.result.id);
      expect(confirmRequests).toBe(1);

      // Audit evidence for this confirmation request, recorded for this employee.
      const requestId = confirmResponse.headers()["x-request-id"];
      await expect.poll(async () => (await auditFor(api, tokens.ADMIN, `request_id=${requestId}`))
        .filter((event) => event.employee_id === person.id).map((event) => event.event_type).sort())
        .toEqual(expect.arrayContaining(["ACTION_CONFIRMED", "ACTION_EXECUTED"]));

      // ACT-4: the same confirmation id cannot run again.
      const replay = await call(api, person.token, "POST", CONFIRM, { data: { confirmation_id: confirmationId } });
      expect([replay.status, errorCode(replay)]).toEqual([400, "AI_CONFIRMATION_INVALID"]);
      expect(await ticketsTitled(api, person.token, title)).toHaveLength(1);
      await api.dispose();
    });

  test("ACT-2 an EMPLOYEE asking to approve a leave is refused before any confirmation; the leave is unchanged and the denial audited",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const leaveId = await pendingLeaveOfEmployee(api, tokens.EMPLOYEE, new Date(Date.UTC(2028, 4, 1)));
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "NotApprover");
      const page = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const response = await ask(page, `Approve leave request #${leaveId}`);
      expect([response.status(), (await response.json()).error.code]).toEqual([403, "AI_PERMISSION_DENIED"]);
      await expect(page.getByRole("alert", { name: "Copilot error" })).toBeVisible();
      await expect(page.getByRole("region", { name: /confirmation required/ })).toHaveCount(0);
      expect((await leaveById(api, tokens.EMPLOYEE, leaveId)).status).toBe("PENDING");
      const requestId = response.headers()["x-request-id"];
      await expect.poll(async () => (await auditFor(api, tokens.ADMIN, `request_id=${requestId}`))
        .some((event) => event.event_type === "AUTHORIZATION_DENIED" && event.employee_id === person.id)).toBe(true);
      await api.dispose();
    });

  test("ACT-3 another user's confirmation id is refused like an unknown one; the owner can still confirm it",
    async ({ tokens }) => {
      const api = await apiContext();
      const owner = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "ConfirmOwner");
      const stranger = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "ConfirmStranger");
      const title = `E2E foreign ${runTag()}`;
      const proposal = await propose(api, owner.token, `Create a ticket titled ${title}`);
      expect(proposal.body.data.status).toBe("confirmation_required");
      const id = proposal.body.data.confirmation_id;
      for (const token of [stranger.token, tokens.MANAGER, tokens.ADMIN]) {
        const foreign = await call(api, token, "POST", CONFIRM, { data: { confirmation_id: id } });
        expect([foreign.status, errorCode(foreign)]).toEqual([400, "AI_CONFIRMATION_INVALID"]);
      }
      expect(await ticketsTitled(api, tokens.ADMIN, title)).toEqual([]);
      const own = await call(api, owner.token, "POST", CONFIRM, { data: { confirmation_id: id } });
      expect([own.status, own.body.data.status]).toEqual([200, "completed"]);
      expect((await ticketsTitled(api, owner.token, title)).map((ticket) => ticket.employee_id)).toEqual([owner.id]);
      await api.dispose();
    });

  test("ACT-5 duplicate confirmation: concurrent API confirms execute once; a double-click in the UI sends one request",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "DoubleConfirm");
      const apiTitle = `E2E concurrent ${runTag()}`;
      const proposal = await propose(api, person.token, `Create a ticket titled ${apiTitle}`);
      const id = proposal.body.data.confirmation_id;
      const results = await Promise.all([1, 2].map(() => call(api, person.token, "POST", CONFIRM, { data: { confirmation_id: id } })));
      expect(results.map((result) => result.status).sort()).toEqual([200, 400]);
      expect(results.find((result) => result.status === 400)!.body.error.code).toBe("AI_CONFIRMATION_INVALID");
      expect(await ticketsTitled(api, person.token, apiTitle)).toHaveLength(1);

      const uiTitle = `E2E double click ${runTag()}`;
      const page = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await openCopilot(page);
      await ask(page, `Create a ticket titled ${uiTitle}`);
      let confirmRequests = 0;
      page.on("request", (request) => { if (request.url().endsWith(CONFIRM)) confirmRequests += 1; });
      await card(page).getByRole("button", { name: "Confirm" }).dblclick();
      await expect(card(page).getByRole("status")).toContainText("Done:");
      expect(confirmRequests).toBe(1);
      expect(await ticketsTitled(api, person.token, uiTitle)).toHaveLength(1);
      await api.dispose();
    });

  test("ACT-7 permission removed between proposal and confirmation: refused at confirmation, nothing changes, the id is spent",
    async ({ tokens }) => {
      const api = await apiContext();
      const leaveId = await pendingLeaveOfEmployee(api, tokens.EMPLOYEE, new Date(Date.UTC(2028, 6, 3)));
      const approver = await createPerson(api, tokens.ADMIN, "ADMIN", "DemotedApprover");
      const proposal = await propose(api, approver.token, `Approve leave request #${leaveId}`);
      expect([proposal.status, proposal.body.data.status, proposal.body.data.action]).toEqual([200, "confirmation_required", "LEAVE_APPROVE"]);
      const stepUp = { "X-Step-Up-Password": seededCredentials("ADMIN")!.password };
      expect((await call(api, tokens.ADMIN, "PUT", `/api/v1/employees/${approver.id}`, { data: { role: "EMPLOYEE" }, headers: stepUp })).status).toBe(200);

      const denied = await call(api, approver.token, "POST", CONFIRM, { data: { confirmation_id: proposal.body.data.confirmation_id } });
      expect([denied.status, errorCode(denied)]).toEqual([403, "AI_PERMISSION_DENIED"]);
      expect((await leaveById(api, tokens.EMPLOYEE, leaveId)).status).toBe("PENDING");

      // Restoring the role does not resurrect the refused confirmation.
      expect((await call(api, tokens.ADMIN, "PUT", `/api/v1/employees/${approver.id}`, { data: { role: "ADMIN" }, headers: stepUp })).status).toBe(200);
      const again = await call(api, approver.token, "POST", CONFIRM, { data: { confirmation_id: proposal.body.data.confirmation_id } });
      expect([again.status, errorCode(again)]).toEqual([400, "AI_CONFIRMATION_INVALID"]);
      expect((await leaveById(api, tokens.EMPLOYEE, leaveId)).status).toBe("PENDING");
      await api.dispose();
    });

  test("ACT-8 the record changes before confirmation: the business API refuses, the UI says Not completed, state is the manager's",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const leaveId = await pendingLeaveOfEmployee(api, tokens.EMPLOYEE, new Date(Date.UTC(2028, 8, 4)));
      const approver = await createPerson(api, tokens.ADMIN, "ADMIN", "LateApprover");
      const page = await (await contextWithToken(browser, approver.token, "ADMIN")).newPage();
      await openCopilot(page);
      const proposed = await ask(page, `Approve leave request #${leaveId}`);
      expect((await proposed.json()).data.status).toBe("confirmation_required");
      await expect(card(page)).toContainText(`#${leaveId}`);

      expect((await call(api, tokens.MANAGER, "POST", `/api/v1/leaves/requests/${leaveId}/approve`)).status).toBe(200); // meanwhile
      const confirmed = page.waitForResponse((response) => response.url().endsWith(CONFIRM));
      await card(page).getByRole("button", { name: "Confirm" }).click();
      const outcome = await (await confirmed).json();
      expect([outcome.data.status, outcome.data.error_code]).toEqual(["failed", "LEAVE_NOT_PENDING"]);
      await expect(card(page).getByRole("status")).toContainText("Not completed:");
      const leave = await leaveById(api, tokens.EMPLOYEE, leaveId);
      expect([leave.status, leave.approver_id]).toEqual(["APPROVED", data.ids.manager]); // the manager's decision stands
      await api.dispose();
    });

  test("ACT-9 invalid action parameters: the real validator asks for clarification; no confirmation, nothing created",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "BadParams");
      const title = `E2E urgent ${runTag()}`;
      const page = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const response = await ask(page, `Create an urgent ticket titled ${title}`);
      const body = await response.json();
      expect([response.status(), body.data.status]).toEqual([200, "clarification_required"]);
      await expect(page.getByRole("list", { name: "Conversation messages" })).toContainText("valid priority");
      await expect(page.getByRole("region", { name: /confirmation required/ })).toHaveCount(0);
      expect(await ticketsTitled(api, person.token, title)).toEqual([]);
      await api.dispose();
    });

  test("ACT-10 password-protected action: a wrong password leaves it pending and posts nothing; the right one posts a TEAM announcement",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const title = `E2E copilot townhall ${runTag()}`;
      const page = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await openCopilot(page);
      const proposed = await ask(page, `Create an announcement titled ${title}`);
      expect((await proposed.json()).data.parts[0].data.step_up_required).toBe(true);
      const password = card(page).getByLabel("This is a high-impact action. Enter your password to confirm it.");
      await expect(card(page).getByRole("button", { name: "Confirm" })).toBeDisabled();
      const listed = async () => items(await call(api, tokens.MANAGER, "GET", "/api/v1/announcements?limit=100"))
        .filter((item) => item.title === title);

      await password.fill("Not-The-Manager-Password-1");
      const wrong = page.waitForResponse((response) => response.url().endsWith(CONFIRM));
      await card(page).getByRole("button", { name: "Confirm" }).click();
      expect([(await wrong).status(), (await (await wrong).json()).error.code]).toEqual([403, "AI_STEP_UP_FAILED"]);
      await expect(card(page).getByRole("alert")).toBeVisible();
      expect(await listed()).toEqual([]);

      await password.fill(seededCredentials("MANAGER")!.password);
      const right = page.waitForResponse((response) => response.url().endsWith(CONFIRM));
      await card(page).getByRole("button", { name: "Confirm" }).click();
      expect((await (await right).json()).data.status).toBe("completed");
      await expect(card(page).getByRole("status")).toContainText("Done:");
      const posted = await listed();
      expect(posted.map((item) => [item.audience, item.author_id])).toEqual([["TEAM", data.ids.manager]]);
      await api.dispose();
    });

  test("ACT-11 the confirmation request is lost on the network: the UI says the outcome is unknown and never retries",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "LostConfirm");
      const title = `E2E lost confirm ${runTag()}`;
      const page = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await openCopilot(page);
      const proposed = await ask(page, `Create a ticket titled ${title}`);
      const confirmationId = (await proposed.json()).data.parts[0].data.confirmation_id;
      let attempts = 0;
      await page.route(`**${CONFIRM}`, (route) => { attempts += 1; return route.abort("internetdisconnected"); });
      await card(page).getByRole("button", { name: "Confirm" }).click();
      await expect(card(page).getByRole("status")).toContainText("couldn't confirm whether this action completed");
      expect(attempts).toBe(1);
      expect(await ticketsTitled(api, person.token, title)).toEqual([]); // the request never reached the server
      // The server-side confirmation is still valid; the user can decide to confirm it deliberately.
      const later = await call(api, person.token, "POST", CONFIRM, { data: { confirmation_id: confirmationId } });
      expect(later.body.data.status).toBe("completed");
      expect(await ticketsTitled(api, person.token, title)).toHaveLength(1);
      await api.dispose();
    });
});
