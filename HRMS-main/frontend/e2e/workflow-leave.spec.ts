// Leave lifecycle end to end: EMPLOYEE submits in the UI, MANAGER decides in the UI, EMPLOYEE sees the decision.
// Real state machine (endpoints/leaves.py): PENDING -> APPROVED | REJECTED; a second decision is 400 LEAVE_NOT_PENDING;
// approval deducts the balance; managers decide for direct reports only (others: unknown-id 404).
// The seeded Employee User is used because a manager relationship cannot be created through the API.
import type { Page } from "@playwright/test";

import { apiContext, call, classify, errorCode } from "./support/api";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { createPerson, freeWorkday, leaveById, myLeaves, pickDate, remaining, runTag } from "./support/workflow";

async function submitLeaveInUi(page: Page, type: string, date: string, reason: string): Promise<void> {
  await page.goto("/leaves");
  await expectPageTitle(page, "Leaves");
  await expect(page.getByText("Loading leave requests...")).toHaveCount(0);
  await page.getByRole("combobox").filter({ hasText: "CASUAL" }).selectOption(type);
  await pickDate(page, "Start date", date);
  await pickDate(page, "End date", date);
  await page.getByPlaceholder("Reason").fill(reason);
  await page.getByRole("button", { name: "Submit", exact: true }).click();
}

const myRow = (page: Page, reason: string) => page.getByRole("row").filter({ hasText: reason });

test.describe("leave lifecycle", () => {
  test("LV-A approve handoff: employee submits, unrelated manager refused, manager approves, employee sees it, balance once",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const reason = `E2E leave approve ${runTag()}`;
      const date = await freeWorkday(api, tokens.EMPLOYEE);
      const balanceBefore = await remaining(api, tokens.EMPLOYEE, "EARNED");

      const employee = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await submitLeaveInUi(employee, "EARNED", date, reason);
      await expect(myRow(employee, reason)).toContainText("PENDING");
      const leaveId = (await myLeaves(api, tokens.EMPLOYEE)).find((row) => row.reason === reason).id;
      const stored = await leaveById(api, tokens.EMPLOYEE, leaveId);
      expect({ owner: stored.employee_id, type: stored.leave_type, start: stored.start_date, end: stored.end_date,
               status: stored.status, approver: stored.approver_id })
        .toEqual({ owner: data.ids.employee, type: "EARNED", start: date, end: date, status: "PENDING", approver: null });

      // An unrelated manager (synthetic, no reports) cannot see or decide it; nothing changes.
      const otherManager = await createPerson(api, tokens.ADMIN, "MANAGER", "OtherManager");
      expect(classify((await call(api, otherManager.token, "POST", `/api/v1/leaves/requests/${leaveId}/approve`)).status)).toBe("hidden");
      expect(classify((await call(api, otherManager.token, "POST", `/api/v1/leaves/requests/${leaveId}/reject`)).status)).toBe("hidden");
      expect((await leaveById(api, tokens.EMPLOYEE, leaveId)).status).toBe("PENDING");

      // The team manager sees it in Pending Approvals and approves it in the UI.
      const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await manager.goto("/leaves");
      await expectPageTitle(manager, "Leaves");
      const pending = manager.getByRole("row").filter({ hasText: reason });
      await expect(pending).toContainText(String(data.ids.employee));
      await pending.getByRole("button", { name: "Approve" }).click();
      await expect(pending).toHaveCount(0);
      const decided = await leaveById(api, tokens.EMPLOYEE, leaveId);
      expect([decided.status, decided.approver_id]).toEqual(["APPROVED", data.ids.manager]);
      expect(await remaining(api, tokens.EMPLOYEE, "EARNED")).toBe(balanceBefore - 1);

      // The employee sees the decision after a reload.
      await employee.reload();
      await expect(myRow(employee, reason)).toContainText("APPROVED");

      // A second, incompatible decision is refused and changes nothing.
      for (const action of ["approve", "reject"]) {
        const again = await call(api, tokens.MANAGER, "POST", `/api/v1/leaves/requests/${leaveId}/${action}`);
        expect([again.status, errorCode(again)]).toEqual([400, "LEAVE_NOT_PENDING"]);
      }
      expect((await leaveById(api, tokens.EMPLOYEE, leaveId)).status).toBe("APPROVED");
      expect(await remaining(api, tokens.EMPLOYEE, "EARNED")).toBe(balanceBefore - 1);
      await api.dispose();
    });

  test("LV-B reject handoff: manager rejects in the UI, balance untouched, employee sees REJECTED, re-approval refused",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const reason = `E2E leave reject ${runTag()}`;
      const date = await freeWorkday(api, tokens.EMPLOYEE, new Date(Date.UTC(2027, 8, 1)));
      const balanceBefore = await remaining(api, tokens.EMPLOYEE, "SICK");
      const employee = await (await contextWithToken(browser, tokens.EMPLOYEE, "EMPLOYEE")).newPage();
      await submitLeaveInUi(employee, "SICK", date, reason);
      await expect(myRow(employee, reason)).toContainText("PENDING");

      const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await manager.goto("/leaves");
      const pending = manager.getByRole("row").filter({ hasText: reason });
      await pending.getByRole("button", { name: "Reject" }).click();
      await expect(pending).toHaveCount(0);

      const leaveId = (await myLeaves(api, tokens.EMPLOYEE)).find((row) => row.reason === reason).id;
      const decided = await leaveById(api, tokens.EMPLOYEE, leaveId);
      expect([decided.status, decided.approver_id]).toEqual(["REJECTED", data.ids.manager]);
      expect(await remaining(api, tokens.EMPLOYEE, "SICK")).toBe(balanceBefore);
      await employee.reload();
      await expect(myRow(employee, reason)).toContainText("REJECTED");
      const reapprove = await call(api, tokens.MANAGER, "POST", `/api/v1/leaves/requests/${leaveId}/approve`);
      expect([reapprove.status, errorCode(reapprove)]).toEqual([400, "LEAVE_NOT_PENDING"]);
      expect(await remaining(api, tokens.EMPLOYEE, "SICK")).toBe(balanceBefore);
      await api.dispose();
    });

  test("LV-C validation, correction, overlap and refused decisions leave nothing behind", async ({ tokens, data }) => {
    const api = await apiContext();
    const employee = tokens.EMPLOYEE;
    const day = await freeWorkday(api, employee, new Date(Date.UTC(2027, 10, 1)));
    const next = new Date(Date.parse(`${day}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    const before = (await myLeaves(api, employee)).length;

    const backwards = await call(api, employee, "POST", "/api/v1/leaves/requests",
      { data: { leave_type: "CASUAL", start_date: next, end_date: day, reason: "E2E backwards" } });
    expect([backwards.status, errorCode(backwards)]).toEqual([400, "INVALID_DATE_RANGE"]);
    const halfDay = await call(api, employee, "POST", "/api/v1/leaves/requests",
      { data: { leave_type: "CASUAL", start_date: day, end_date: next, is_half_day: true, half_day_period: "FIRST_HALF", reason: "E2E half" } });
    expect([halfDay.status, errorCode(halfDay)]).toEqual([400, "INVALID_HALF_DAY_RANGE"]);
    const badType = await call(api, employee, "POST", "/api/v1/leaves/requests",
      { data: { leave_type: "VACATION", start_date: day, end_date: day, reason: "E2E bad type" } });
    expect(classify(badType.status)).toBe("rejected");
    expect((await myLeaves(api, employee)).length).toBe(before); // nothing stored

    const corrected = await call(api, employee, "POST", "/api/v1/leaves/requests",
      { data: { leave_type: "CASUAL", start_date: day, end_date: day, reason: `E2E corrected ${runTag()}` } });
    expect(corrected.status).toBe(200);
    const overlap = await call(api, employee, "POST", "/api/v1/leaves/requests",
      { data: { leave_type: "SICK", start_date: day, end_date: day, reason: "E2E overlap" } });
    expect([overlap.status, errorCode(overlap)]).toEqual([409, "LEAVE_OVERLAP"]);
    expect((await myLeaves(api, employee)).length).toBe(before + 1);

    // Refused decisions on the stored request: the owner (EMPLOYEE) and an outside employee cannot decide it.
    const id = corrected.body.data.id;
    expect(classify((await call(api, employee, "POST", `/api/v1/leaves/requests/${id}/approve`)).status)).toBe("deny");
    const outsider = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "LeaveOutsider");
    expect(classify((await call(api, outsider.token, "POST", `/api/v1/leaves/requests/${id}/reject`)).status)).toBe("deny");
    expect((await leaveById(api, employee, id)).status).toBe("PENDING");
    void data;
    await api.dispose();
  });
});
