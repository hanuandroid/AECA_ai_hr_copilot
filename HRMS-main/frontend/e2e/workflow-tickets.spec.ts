// Ticket lifecycle and ownership end to end (endpoints/tickets.py): OPEN / IN_PROGRESS / RESOLVED; assign = ADMIN
// anyone, MANAGER self or direct reports; status = ADMIN, MANAGER in scope, or the assignee; the EMPLOYEE owner may not
// change status. After every UI step the persisted state is read back through the API.
// Documented Copilot/API differences are kept and tested separately (E1, E2 in docs/role-access-matrix.md §4).
import type { Page } from "@playwright/test";

import { apiContext, call, classify, errorCode } from "./support/api";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { createPerson, runTag, ticketById } from "./support/workflow";

async function raiseTicketInUi(page: Page, title: string, category = "IT"): Promise<void> {
  await page.goto("/tickets");
  await expectPageTitle(page, "Tickets");
  await expect(page.getByText("Loading tickets...")).toHaveCount(0);
  await page.getByPlaceholder("Title").fill(title);
  await page.getByPlaceholder("Description").fill(`Synthetic description for ${title}`);
  await page.getByRole("combobox").filter({ hasText: "ONBOARDING" }).selectOption(category);
  await page.getByRole("combobox").filter({ hasText: "MEDIUM" }).selectOption("HIGH");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
}

const card = (page: Page, title: string) => page.locator("article").filter({ hasText: title });

test.describe("ticket lifecycle", () => {
  test("TK-A team ticket: employee raises, manager assigns to self, starts and resolves, employee sees RESOLVED",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const title = `E2E ticket team ${runTag()}`;
      const employee = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await raiseTicketInUi(employee, title);
      await expect(card(employee, title)).toContainText("OPEN");
      await expect(card(employee, title).getByRole("button", { name: "Start" })).toHaveCount(0); // owner: no status control
      const id = Number((await card(employee, title).getByRole("heading").textContent())!.match(/#(\d+)/)![1]);
      expect(await ticketById(api, tokens.EMPLOYEE, id)).toMatchObject(
        { employee_id: data.ids.employee, assignee_id: null, status: "OPEN", category: "IT", priority: "HIGH" });

      const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await manager.goto("/tickets");
      await expectPageTitle(manager, "Tickets");
      await card(manager, title).getByPlaceholder("Assignee ID").fill(String(data.ids.manager));
      await card(manager, title).getByRole("button", { name: "Assign" }).click();
      await expect(card(manager, title)).toContainText(`Assignee: ${data.ids.manager}`);
      expect((await ticketById(api, tokens.MANAGER, id)).assignee_id).toBe(data.ids.manager);
      await card(manager, title).getByRole("button", { name: "Start" }).click();
      await expect(card(manager, title)).toContainText("IN_PROGRESS");
      expect((await ticketById(api, tokens.MANAGER, id)).status).toBe("IN_PROGRESS");
      await card(manager, title).getByRole("button", { name: "Resolve" }).click();
      await expect(card(manager, title)).toContainText("RESOLVED");
      expect((await ticketById(api, tokens.MANAGER, id)).status).toBe("RESOLVED");

      await employee.reload();
      await expect(card(employee, title)).toContainText("RESOLVED");
      const ownerChange = await call(api, tokens.EMPLOYEE, "POST", `/api/v1/tickets/${id}/status`, { data: { status: "OPEN" } });
      expect(classify(ownerChange.status)).toBe("deny");

      // Someone outside the team neither sees nor changes it.
      const outsider = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "TicketOutsider");
      expect(await ticketById(api, outsider.token, id)).toBeUndefined();
      expect(["deny", "hidden"]).toContain(classify((await call(api, outsider.token, "POST", `/api/v1/tickets/${id}/status`,
                                                               { data: { status: "OPEN" } })).status));
      expect(["deny", "hidden"]).toContain(classify((await call(api, outsider.token, "POST", `/api/v1/tickets/${id}/assign`,
                                                               { data: { assignee_id: outsider.id } })).status));
      expect(await ticketById(api, tokens.MANAGER, id)).toMatchObject({ status: "RESOLVED", assignee_id: data.ids.manager });
      await api.dispose();
    });

  test("TK-B assignee handoff across teams: ADMIN assigns an outsider's ticket to Employee User, who starts it",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const owner = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "TicketOwner");
      const title = `E2E ticket handoff ${runTag()}`;
      const created = await call(api, owner.token, "POST", "/api/v1/tickets",
        { data: { title, description: "Synthetic cross-team ticket", category: "IT", priority: "LOW" } });
      expect(created.status).toBe(200);
      const id = created.body.data.id;

      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto("/tickets");
      await expectPageTitle(admin, "Tickets");
      await card(admin, title).getByPlaceholder("Assignee ID").fill(String(data.ids.employee));
      await card(admin, title).getByRole("button", { name: "Assign" }).click();
      await expect(card(admin, title)).toContainText(`Assignee: ${data.ids.employee}`);

      const assignee = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await assignee.goto("/tickets");
      await card(assignee, title).getByRole("button", { name: "Start" }).click(); // the assignee may change status
      await expect(card(assignee, title)).toContainText("IN_PROGRESS");
      expect((await ticketById(api, tokens.ADMIN, id)).status).toBe("IN_PROGRESS");

      const ownerChange = await call(api, owner.token, "POST", `/api/v1/tickets/${id}/status`, { data: { status: "RESOLVED" } });
      expect(classify(ownerChange.status)).toBe("deny");
      expect(await ticketById(api, owner.token, id)).toMatchObject({ status: "IN_PROGRESS" }); // owner still sees it
      // Manager scope = tickets owned by self or direct reports, or assigned to self: this one is none of those.
      expect(await ticketById(api, tokens.MANAGER, id)).toBeUndefined();
      expect(classify((await call(api, tokens.MANAGER, "POST", `/api/v1/tickets/${id}/status`, { data: { status: "RESOLVED" } })).status))
        .toBe("hidden");
      // A MANAGER may assign only to self or direct reports.
      const otherTicket = await call(api, tokens.EMPLOYEE, "POST", "/api/v1/tickets",
        { data: { title: `E2E ticket assign limit ${runTag()}`, description: "Synthetic", category: "HR", priority: "LOW" } });
      const tooFar = await call(api, tokens.MANAGER, "POST", `/api/v1/tickets/${otherTicket.body.data.id}/assign`,
                                { data: { assignee_id: owner.id } });
      expect([tooFar.status, errorCode(tooFar)]).toEqual([403, "FORBIDDEN"]);
      expect((await ticketById(api, tokens.MANAGER, otherTicket.body.data.id)).assignee_id).toBeNull();
      await api.dispose();
    });

  test("TK-B2 documented differences: the business API lets an assignee act; the Copilot never grants EMPLOYEE status changes",
    async ({ tokens, data }) => {
      const api = await apiContext();
      const owner = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "TicketOwnerE2");
      const created = await call(api, owner.token, "POST", "/api/v1/tickets",
        { data: { title: `E2E ticket E2 ${runTag()}`, description: "Synthetic", category: "IT", priority: "LOW" } });
      const id = created.body.data.id;
      // API side of E2: a ticket merely ASSIGNED to the manager (owner outside the team) may be updated by the manager.
      expect((await call(api, tokens.ADMIN, "POST", `/api/v1/tickets/${id}/assign`, { data: { assignee_id: data.ids.manager } })).status).toBe(200);
      expect((await call(api, tokens.MANAGER, "POST", `/api/v1/tickets/${id}/status`, { data: { status: "IN_PROGRESS" } })).status).toBe(200);
      expect((await ticketById(api, tokens.ADMIN, id)).status).toBe("IN_PROGRESS");
      // Copilot side of E1: an EMPLOYEE never has the ticket-status capability, even while assignee of a ticket.
      const employeeCaps = (await call(api, tokens.EMPLOYEE, "GET", "/api/v1/chat/permissions")).body.data.capabilities;
      expect(employeeCaps).not.toContain("TICKET_STATUS_UPDATE");
      expect(employeeCaps).not.toContain("TICKET_ASSIGN");
      // The Copilot side of E2 (manager limited to tickets OWNED by the team) needs a model-made action proposal:
      // NOT_TESTED here; covered by backend tests/ai/actions/test_action_parity.py.
      await api.dispose();
    });

  test("TK-C onboarding checklist: manager adds a task in the UI, the owner marks it done", async ({ browser, tokens }) => {
    const api = await apiContext();
    const title = `E2E onboarding ${runTag()}`;
    const employee = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
    await raiseTicketInUi(employee, title, "ONBOARDING");
    await expect(card(employee, title)).toContainText("Onboarding Checklist");
    const id = Number((await card(employee, title).getByRole("heading").textContent())!.match(/#(\d+)/)![1]);

    const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
    await manager.goto("/tickets");
    await card(manager, title).getByPlaceholder("Add onboarding task").fill("Issue synthetic laptop");
    await card(manager, title).getByRole("button", { name: "Add Task" }).click();
    await expect(card(manager, title)).toContainText("Issue synthetic laptop");

    await employee.reload();
    await card(employee, title).getByRole("button", { name: "Mark Done" }).click();
    await expect(card(employee, title)).toContainText("Issue synthetic laptop (Done)");
    const tasks = (await ticketById(api, tokens.EMPLOYEE, id)).onboarding_tasks;
    expect(tasks.map((task: { task_name: string; is_completed: boolean }) => [task.task_name, task.is_completed]))
      .toEqual([["Issue synthetic laptop", true]]);

    const itTicket = await call(api, tokens.EMPLOYEE, "POST", "/api/v1/tickets",
      { data: { title: `E2E it ${runTag()}`, description: "Synthetic", category: "IT", priority: "LOW" } });
    const wrongKind = await call(api, tokens.MANAGER, "POST", `/api/v1/tickets/${itTicket.body.data.id}/onboarding-tasks`,
                                 { data: { task_name: "Not allowed" } });
    expect([wrongKind.status, errorCode(wrongKind)]).toEqual([400, "INVALID_TICKET_CATEGORY"]);
    const employeeAdds = await call(api, tokens.EMPLOYEE, "POST", `/api/v1/tickets/${id}/onboarding-tasks`, { data: { task_name: "Employee-added task" } });
    expect(classify(employeeAdds.status)).toBe("deny");
    await api.dispose();
  });
});
