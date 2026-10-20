// Employee lifecycle end to end: ADMIN creates (UI) -> the new person signs in -> ADMIN edits -> self-service edit ->
// ADMIN deactivates / reactivates (UI) with the session consequences; admin self-actions with synthetic admins only.
// Real rules: endpoints/employees.py (ADMIN only, G-11 step-up in log mode, self-action guards, field visibility).
import { apiContext, call, classify, errorCode, login } from "./support/api";
import { seededCredentials } from "./support/env";
import { contextWithToken, expectPageTitle, loginThroughForm } from "./support/session";
import { expect, test } from "./support/suite";
import { FULL_PROFILE, createPerson, employeeRecord, fillCreateEmployeeForm, runTag, searchEmployees,
         syntheticPassword } from "./support/workflow";

const adminPassword = () => seededCredentials("ADMIN")!.password;

async function confirmStepUp(page: import("@playwright/test").Page, dialog: string): Promise<void> {
  const box = page.getByRole("dialog", { name: dialog });
  await expect(box).toBeVisible();
  await box.getByLabel("Your password").fill(adminPassword());
  await box.getByRole("button", { name: "Confirm" }).click();
}

test.describe("employee lifecycle", () => {
  test("EMP-A create in the UI -> persisted ACTIVE EMPLOYEE -> the new person signs in and sees only employee features",
    async ({ browser, page, tokens }) => {
      const api = await apiContext();
      const tag = runTag();
      const hire = { name: `E2E Hire ${tag}`, email: `e2e.hire.${tag}@hrms-e2e.dev`, password: syntheticPassword() };
      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto("/employees/new");
      await expectPageTitle(admin, "Add Employee");
      await admin.getByRole("button", { name: "Continue" }).click(); // required fields first: nothing is sent
      await expect(admin.getByText(/are required/)).toBeVisible();
      await fillCreateEmployeeForm(admin, hire);
      await admin.getByRole("button", { name: "Create Employee" }).click();
      await confirmStepUp(admin, "Create employee account");
      await expect(admin).toHaveURL(/\/employees$/);

      const found = await searchEmployees(api, tokens.ADMIN, hire.email);
      expect(found.map((row) => row.email)).toEqual([hire.email]);
      const record = await employeeRecord(api, tokens.ADMIN, found[0].id);
      expect({ name: record.name, email: record.email, role: record.role, status: record.status, manager: record.manager_id ?? null })
        .toEqual({ name: hire.name, email: hire.email, role: "EMPLOYEE", status: "ACTIVE", manager: null });

      await loginThroughForm(page, hire.email, hire.password);
      await expectPageTitle(page, "Dashboard");
      await expect(page.locator("aside nav").getByRole("link", { name: "Employees", exact: true })).toHaveCount(0);
      await page.goto("/me");
      await expectPageTitle(page, "My Profile");
      await expect(page.getByRole("textbox").filter({ has: page.locator(`[value="${hire.email}"]`) }).or(
        page.locator(`input[value="${hire.email}"]`))).toHaveCount(1);
      await page.goto("/employees");
      await expect(page.getByRole("alert").filter({ hasText: "Access denied" })).toBeVisible();
      await api.dispose();
    });

  test("EMP-A negatives: duplicate email in the UI, invalid role/department via API, non-admin create: no partial records",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const existing = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "Existing");
      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto("/employees/new");
      await expectPageTitle(admin, "Add Employee");
      await fillCreateEmployeeForm(admin, { name: "E2E Duplicate", email: existing.email, password: syntheticPassword() });
      await admin.getByRole("button", { name: "Create Employee" }).click();
      await confirmStepUp(admin, "Create employee account");
      await expect(admin.getByText("An employee with this email already exists")).toBeVisible();
      await expect(admin).toHaveURL(/\/employees\/new/);
      expect((await searchEmployees(api, tokens.ADMIN, existing.email)).length).toBe(1);
      expect((await employeeRecord(api, tokens.ADMIN, existing.id)).name).toBe(existing.name); // not overwritten

      const stepUp = { "X-Step-Up-Password": adminPassword() };
      const base = { name: "E2E Invalid", password: syntheticPassword(), joining_date: "2026-01-05" };
      const badRole = await call(api, tokens.ADMIN, "POST", "/api/v1/employees",
        { data: { ...base, email: `e2e.badrole.${runTag()}@hrms-e2e.dev`, role: "SUPERUSER" }, headers: stepUp });
      expect([badRole.status, errorCode(badRole)]).toEqual([400, "INVALID_ROLE"]);
      const badDept = await call(api, tokens.ADMIN, "POST", "/api/v1/employees",
        { data: { ...base, email: `e2e.baddept.${runTag()}@hrms-e2e.dev`, department_id: 987654 }, headers: stepUp });
      expect([badDept.status, errorCode(badDept)]).toEqual([400, "INVALID_DEPARTMENT"]);
      const missing = await call(api, tokens.ADMIN, "POST", "/api/v1/employees",
        { data: { name: "E2E Missing", email: `e2e.missing.${runTag()}@hrms-e2e.dev` }, headers: stepUp });
      expect(classify(missing.status)).toBe("rejected");
      for (const email of [badRole, badDept].map((_r, i) => i)) void email;
      expect((await searchEmployees(api, tokens.ADMIN, "e2e.badrole.")).length).toBe(0);
      expect((await searchEmployees(api, tokens.ADMIN, "e2e.baddept.")).length).toBe(0);
      expect((await searchEmployees(api, tokens.ADMIN, "e2e.missing.")).length).toBe(0);
      const byManager = await call(api, tokens.MANAGER, "POST", "/api/v1/employees",
        { data: { ...base, email: `e2e.bymanager.${runTag()}@hrms-e2e.dev` } });
      expect(classify(byManager.status)).toBe("deny");
      expect((await searchEmployees(api, tokens.ADMIN, "e2e.bymanager.")).length).toBe(0);
      await api.dispose();
    });

  test("EMP-B update: ADMIN edits the job title (no step-up), self-service phone, sensitive fields stay protected",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "Editable", FULL_PROFILE);
      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      let stepUpHeaderSent = false;
      admin.on("request", (request) => {
        if (request.method() === "PUT" && request.url().endsWith(`/api/v1/employees/${person.id}`)
            && request.headers()["x-step-up-password"] !== undefined) stepUpHeaderSent = true;
      });
      await admin.goto(`/employees/new?employee_id=${person.id}`);
      await expectPageTitle(admin, "Edit Employee");
      await expect(admin.getByPlaceholder("e.g. john@company.com")).toHaveValue(person.email);
      await admin.getByPlaceholder("e.g. Software Engineer").fill("Staff Engineer");
      // The phone field is left untouched here; phone edits are covered by the F-BUG-1 regression tests below.
      await admin.getByRole("button", { name: "Continue" }).click();
      await admin.getByRole("button", { name: "Save Changes" }).click();
      await expect(admin).toHaveURL(/\/employees$/);
      expect(stepUpHeaderSent).toBe(false);
      const edited = await employeeRecord(api, tokens.ADMIN, person.id);
      expect([edited.job_title, edited.phone]).toEqual(["Staff Engineer", FULL_PROFILE.phone]); // phone survives a save
      expect([edited.bank_name, edited.pan_number]).toEqual([FULL_PROFILE.bank_name, FULL_PROFILE.pan_number]); // untouched

      // Self-service: the person changes their own phone on /me.
      const self = await (await contextWithToken(browser, person.token, "EMPLOYEE")).newPage();
      await self.goto("/me");
      await expectPageTitle(self, "My Profile");
      const phone = self.locator("div.space-y-1").filter({ has: self.getByText("Phone", { exact: true }) }).getByRole("textbox");
      await expect(phone).toHaveValue(FULL_PROFILE.phone);
      await phone.fill("+919000000888");
      await self.getByRole("button", { name: "Save Changes" }).click();
      await expect(self.getByText("Profile updated")).toBeVisible();
      expect((await call(api, person.token, "GET", "/api/v1/employees/me")).body.data.phone).toBe("+919000000888");

      // Sensitive fields: a manager's view of a direct report hides the financial fields the ADMIN sees.
      const adminView = await employeeRecord(api, tokens.ADMIN, data.ids.employee);
      const managerView = (await call(api, tokens.MANAGER, "GET", `/api/v1/employees/${data.ids.employee}`)).body.data;
      for (const field of ["bank_account_number", "pan_number", "current_salary_usd", "date_of_birth"]) {
        expect(field in adminView, `${field} in the ADMIN view`).toBe(true);
        expect(managerView[field] ?? null, `${field} hidden from the manager`).toBeNull();
      }
      // Non-admin attempts on someone else's record are refused and change nothing.
      expect(classify((await call(api, tokens.MANAGER, "PUT", `/api/v1/employees/${person.id}`, { data: { role: "MANAGER" } })).status)).toBe("deny");
      expect(classify((await call(api, person.token, "PUT", `/api/v1/employees/${person.id}`, { data: { role: "ADMIN" } })).status)).toBe("deny");
      const after = await employeeRecord(api, tokens.ADMIN, person.id);
      expect([after.role, after.job_title]).toEqual(["EMPLOYEE", "Staff Engineer"]);
      await api.dispose();
    });

  // F-BUG-1 regression (fixed 2026-10-20 in app/employees/new/page.tsx + lib/phone.ts): the edit form used to split a
  // stored phone with /^\+\d{1,3}/, so "+919000000123" loaded as "+919" / "000000123" and an edited number saved as
  // "+9199000000999". It now uses the longest supported country code (unit and form tests: tests/phone.test.ts,
  // tests/employee-phone.test.tsx). This test was an expected failure until the fix and reported an unexpected pass
  // with the fixed build before its marker was removed.
  test("F-BUG-1 regression: editing an Indian phone number keeps +91, persists, and reloads correctly", async ({ browser, tokens }) => {
    const api = await apiContext();
    const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "PhoneBug", FULL_PROFILE);
    const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
    const open = async () => {
      await admin.goto(`/employees/new?employee_id=${person.id}`);
      await expectPageTitle(admin, "Edit Employee");
      await expect(admin.getByPlaceholder("e.g. john@company.com")).toHaveValue(person.email);
    };
    const countryCode = () => admin.getByRole("combobox", { name: "Phone Number" });
    await open();
    await expect(countryCode()).toHaveValue("+91");
    await expect(admin.getByPlaceholder("Local number")).toHaveValue("9000000123");
    await admin.getByPlaceholder("Local number").fill("9000000999");
    await admin.getByRole("button", { name: "Continue" }).click();
    await admin.getByRole("button", { name: "Save Changes" }).click();
    await expect(admin).toHaveURL(/\/employees$/);
    expect((await employeeRecord(api, tokens.ADMIN, person.id)).phone).toBe("+919000000999");
    // Reopen the edited employee: the persisted number loads back as +91 / 9000000999.
    await open();
    await expect(countryCode()).toHaveValue("+91");
    await expect(admin.getByPlaceholder("Local number")).toHaveValue("9000000999");
    await api.dispose();
  });

  test("F-BUG-1 regression: an unsupported stored number is flagged and kept exactly as saved", async ({ browser, tokens }) => {
    const api = await apiContext();
    const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "ForeignPhone", { ...FULL_PROFILE, phone: "+33123456789" });
    const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
    await admin.goto(`/employees/new?employee_id=${person.id}`);
    await expectPageTitle(admin, "Edit Employee");
    await expect(admin.getByPlaceholder("Local number")).toHaveValue("+33123456789");
    await expect(admin.getByText(/no supported country code; it is kept as saved unless you edit it/)).toBeVisible();
    await admin.getByPlaceholder("e.g. Software Engineer").fill("Analyst"); // an unrelated edit
    await admin.getByRole("button", { name: "Continue" }).click();
    await admin.getByRole("button", { name: "Save Changes" }).click();
    await expect(admin).toHaveURL(/\/employees$/);
    const saved = await employeeRecord(api, tokens.ADMIN, person.id);
    expect([saved.phone, saved.job_title]).toEqual(["+33123456789", "Analyst"]); // no invented "+91"
    await api.dispose();
  });

  // Session lifecycle (fixed 2026-10-20): deactivation and reactivation both increment token_version, so a session
  // that existed before the deactivation never comes back (backend: tests/rbac/test_deactivation_sessions.py).
  test("EMP-C deactivation ends the session for good: refused while inactive, still refused after reactivation, fresh sign-in works",
    async ({ browser, tokens }) => {
      const api = await apiContext();
      const person = await createPerson(api, tokens.ADMIN, "EMPLOYEE", "Lifecycle");
      const oldToken = person.token;

      // 1-2. The employee's existing browser session works and holds the token.
      const employeeContext = await contextWithToken(browser, oldToken, "EMPLOYEE");
      const employeePage = await employeeContext.newPage();
      await employeePage.goto("/me");
      await expectPageTitle(employeePage, "My Profile");
      await expect(employeePage.locator(`input[value="${person.email}"]`)).toHaveCount(1);
      expect((await call(api, oldToken, "GET", "/api/v1/employees/me")).status).toBe(200);

      // 3. ADMIN deactivates in the UI.
      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto(`/employees?q=${encodeURIComponent(person.email)}`);
      await expectPageTitle(admin, "Employee Directory");
      await admin.getByRole("cell", { name: person.email }).click();
      await admin.getByRole("button", { name: "Deactivate Employee" }).click();
      await confirmStepUp(admin, "Deactivate employee");
      await expect(admin.getByText("Status: INACTIVE")).toBeVisible();
      expect((await employeeRecord(api, tokens.ADMIN, person.id)).status).toBe("INACTIVE");

      // 4-5. The old session is refused by the backend, and the browser no longer shows the person's data.
      const whileInactive = await call(api, oldToken, "GET", "/api/v1/employees/me");
      expect([whileInactive.status, errorCode(whileInactive)]).toEqual([403, "USER_INACTIVE"]);
      await employeePage.goto("/me");
      await expect(employeePage.locator(`input[value="${person.email}"]`)).toHaveCount(0);
      const refused = await login(api, person.email, person.password);
      expect([refused.status, errorCode(refused)]).toEqual([401, "INVALID_CREDENTIALS"]); // same as a bad password

      // 6. ADMIN reactivates in the UI.
      await admin.getByRole("button", { name: "Reactivate Employee" }).click();
      await confirmStepUp(admin, "Reactivate employee");
      await expect(admin.getByText("Status: ACTIVE")).toBeVisible();
      expect((await employeeRecord(api, tokens.ADMIN, person.id)).status).toBe("ACTIVE");

      // 7-8. The same old session stays dead: the API refuses it and the browser is sent to sign in again.
      for (const path of ["/api/v1/employees/me", "/api/v1/tickets", "/api/v1/chat/permissions"]) {
        const revived = await call(api, oldToken, "GET", path);
        expect([revived.status, errorCode(revived)], path).toEqual([401, "TOKEN_REVOKED"]);
      }
      await employeePage.goto("/me");
      await expect(employeePage).toHaveURL(/\/login\?reason=session-ended$/); // lib/session.ts ends a revoked session
      expect(await employeePage.evaluate(() => localStorage.getItem("hrms_access_token"))).toBeNull();
      await employeeContext.close();

      // 9-10. A fresh sign-in works, with employee rights only (UI and API).
      const fresh = await (await browser.newContext()).newPage();
      await loginThroughForm(fresh, person.email, person.password);
      await expectPageTitle(fresh, "Dashboard");
      await expect(fresh.locator("aside nav").getByRole("link", { name: "Employees", exact: true })).toHaveCount(0);
      await fresh.goto("/employees");
      await expect(fresh.getByRole("alert").filter({ hasText: "Access denied" })).toBeVisible();
      const freshToken = await fresh.evaluate(() => localStorage.getItem("hrms_access_token"));
      expect(freshToken).toBeTruthy();
      expect(freshToken).not.toBe(oldToken);
      const me = await call(api, freshToken, "GET", "/api/v1/employees/me");
      expect([me.status, me.body.data.role]).toEqual([200, "EMPLOYEE"]);
      expect((await call(api, freshToken, "GET", "/api/v1/employees")).status).toBe(403);

      // Unrelated sessions are untouched throughout.
      expect((await call(api, tokens.ADMIN, "GET", "/api/v1/employees/me")).status).toBe(200);
      expect((await call(api, tokens.EMPLOYEE, "GET", "/api/v1/employees/me")).status).toBe(200);
      await api.dispose();
    });

  test("EMP-D admin self-actions with synthetic admins; the seeded administrator is never touched", async ({ tokens, data }) => {
    const api = await apiContext();
    const a2 = await createPerson(api, tokens.ADMIN, "ADMIN", "AdminTwo");
    const a3 = await createPerson(api, tokens.ADMIN, "ADMIN", "AdminThree");
    const a2StepUp = { "X-Step-Up-Password": a2.password };
    const removeOther = await call(api, a2.token, "DELETE", `/api/v1/employees/${a3.id}`, { headers: a2StepUp });
    expect(removeOther.status).toBe(200); // another admin remains active: allowed
    expect((await login(api, a3.email, a3.password)).status).toBe(401);
    const selfDeactivate = await call(api, a2.token, "DELETE", `/api/v1/employees/${a2.id}`, { headers: a2StepUp });
    expect([selfDeactivate.status, errorCode(selfDeactivate)]).toEqual([400, "INVALID_OPERATION"]);
    const selfDemote = await call(api, a2.token, "PUT", `/api/v1/employees/${a2.id}`, { data: { role: "EMPLOYEE" }, headers: a2StepUp });
    expect([selfDemote.status, errorCode(selfDemote)]).toEqual([403, "SELF_ROLE_CHANGE_FORBIDDEN"]);
    expect((await employeeRecord(api, tokens.ADMIN, a2.id))).toMatchObject({ role: "ADMIN", status: "ACTIVE" });
    expect((await employeeRecord(api, tokens.ADMIN, data.ids.admin))).toMatchObject({ role: "ADMIN", status: "ACTIVE" });
    // The concurrent last-active-admin race is covered by backend tests/rbac/test_last_active_admin.py.
    await api.dispose();
  });
});
