// Data integrity, replay and recovery across roles. Every check reads the persisted state back through the API;
// a UI message alone is never taken as evidence. No sleeps: Playwright auto-waiting and explicit responses only.
import { apiContext, call, errorCode } from "./support/api";
import { seededCredentials } from "./support/env";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { fillCreateEmployeeForm, freeWorkday, leaveById, myLeaves, pickDate, remaining, runTag, searchEmployees,
         syntheticPassword } from "./support/workflow";

test.describe("data integrity and recovery", () => {
  test("INT-1 double-clicking Submit on the leave form stores exactly one request", async ({ browser, tokens }) => {
    const api = await apiContext();
    const reason = `E2E double submit ${runTag()}`;
    const date = await freeWorkday(api, tokens.EMPLOYEE, new Date(Date.UTC(2028, 0, 3)));
    const page = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
    await page.goto("/leaves");
    await expectPageTitle(page, "Leaves");
    await expect(page.getByText("Loading leave requests...")).toHaveCount(0);
    await pickDate(page, "Start date", date);
    await pickDate(page, "End date", date);
    await page.getByPlaceholder("Reason").fill(reason);
    await page.getByRole("button", { name: "Submit", exact: true }).dblclick();
    await expect(page.getByRole("row").filter({ hasText: reason })).toHaveCount(1);
    expect((await myLeaves(api, tokens.EMPLOYEE)).filter((row) => row.reason === reason)).toHaveLength(1);
    await api.dispose();
  });

  test("INT-2/INT-4 stale approval screen: ADMIN rejects meanwhile, the manager's Approve is refused, nothing changes",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const reason = `E2E stale ${runTag()}`;
      const date = await freeWorkday(api, tokens.EMPLOYEE, new Date(Date.UTC(2028, 2, 1)));
      const created = await call(api, tokens.EMPLOYEE, "POST", "/api/v1/leaves/requests",
        { data: { leave_type: "CASUAL", start_date: date, end_date: date, reason } });
      expect(created.status).toBe(200);
      const id = created.body.data.id;
      const balanceBefore = await remaining(api, tokens.EMPLOYEE, "CASUAL");

      const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await manager.goto("/leaves");
      const row = manager.getByRole("row").filter({ hasText: reason });
      await expect(row.getByRole("button", { name: "Approve" })).toBeVisible(); // screen now holds PENDING data

      const rejected = await call(api, tokens.ADMIN, "POST", `/api/v1/leaves/requests/${id}/reject`);
      expect(rejected.status).toBe(200);

      await row.getByRole("button", { name: "Approve" }).click(); // acting on stale data
      await expect(manager.getByText("Only pending leave request can be approved")).toBeVisible();
      const after = await leaveById(api, tokens.EMPLOYEE, id);
      expect(after.status).toBe("REJECTED");
      expect(await remaining(api, tokens.EMPLOYEE, "CASUAL")).toBe(balanceBefore);
      const repeated = await call(api, tokens.ADMIN, "POST", `/api/v1/leaves/requests/${id}/reject`); // repeated decision
      expect([repeated.status, errorCode(repeated)]).toEqual([400, "LEAVE_NOT_PENDING"]);
      await api.dispose();
    });

  test("INT-6 network failure while creating an employee: unknown-outcome message, no record; the retry creates one",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const tag = runTag();
      const hire = { name: `E2E Retry ${tag}`, email: `e2e.retry.${tag}@hrms-e2e.dev`, password: syntheticPassword() };
      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto("/employees/new");
      await expectPageTitle(admin, "Add Employee");
      await fillCreateEmployeeForm(admin, hire);
      let aborted = 0;
      await admin.route("**/api/v1/employees", async (route) => {
        if (route.request().method() === "POST" && aborted === 0) {
          aborted += 1;
          return route.abort("internetdisconnected");
        }
        return route.continue();
      });
      const confirm = async () => {
        const dialog = admin.getByRole("dialog", { name: "Create employee account" });
        await dialog.getByLabel("Your password").fill(seededCredentials("ADMIN")!.password);
        await dialog.getByRole("button", { name: "Confirm" }).click();
      };
      await admin.getByRole("button", { name: "Create Employee" }).click();
      await confirm();
      await expect(admin.getByText(/don't know whether this change was applied/)).toBeVisible();
      expect(aborted).toBe(1);
      expect(await searchEmployees(api, tokens.ADMIN, hire.email)).toEqual([]); // the request never reached the API

      await admin.getByRole("button", { name: "Create Employee" }).click(); // the user decides to try again
      await confirm();
      await expect(admin).toHaveURL(/\/employees$/);
      expect((await searchEmployees(api, tokens.ADMIN, hire.email)).map((row) => row.email)).toEqual([hire.email]);
      await api.dispose();
    });
});
