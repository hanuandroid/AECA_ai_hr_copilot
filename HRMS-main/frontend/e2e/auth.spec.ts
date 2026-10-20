// Authentication and session lifecycle through the real login form and the real API.
import { apiContext, call, errorCode, login } from "./support/api";
import { E2E, seededCredentials } from "./support/env";
import { contextWithToken, expectAccessDenied, expectPageTitle, loginThroughForm } from "./support/session";
import { expect, test } from "./support/suite";

for (const role of ["EMPLOYEE", "MANAGER", "ADMIN"] as const) {
  test(`${role} signs in through the form and lands on the dashboard`, async ({ page, data }) => {
    void data;
    const credentials = seededCredentials(role)!;
    await loginThroughForm(page, credentials.email, credentials.password);
    await expect(page).toHaveURL(`${E2E.webUrl}/dashboard`);
    await expectPageTitle(page, "Dashboard");
    expect(await page.evaluate(() => Boolean(localStorage.getItem("hrms_access_token")))).toBe(true);
  });
}

test("a wrong password is refused: no session is created", async ({ page, data }) => {
  void data;
  await loginThroughForm(page, seededCredentials("EMPLOYEE")!.email, "Not-The-Password-1");
  // The page shows its generic fallback: it reads `error.message`, while the API nests the message under
  // `detail.error` (UX finding F-UX-1 in docs/role-access-test-report.md; generic either way, no account disclosure).
  await expect(page.getByText("Login failed")).toBeVisible();
  await expect(page).toHaveURL(`${E2E.webUrl}/login`);
  expect(await page.evaluate(() => localStorage.getItem("hrms_access_token"))).toBeNull();
});

test("an inactive account cannot sign in (same generic answer as bad credentials) and its old token is refused",
  async ({ page, data }) => {
    const api = await apiContext();
    const attempt = await login(api, data.people.inactive.email, data.people.inactive.password);
    expect(attempt.status).toBe(401);
    expect(attempt.token).toBeUndefined();
    expect(errorCode(attempt)).toBe("INVALID_CREDENTIALS");
    // A token issued before the deactivation is refused on every protected endpoint.
    const me = await call(api, data.inactiveTokenBeforeDeactivation, "GET", "/api/v1/employees/me");
    expect(me.status).toBe(403);
    expect(errorCode(me)).toBe("USER_INACTIVE");
    const copilot = await call(api, data.inactiveTokenBeforeDeactivation, "GET", "/api/v1/chat/permissions");
    expect(copilot.status).toBe(403);
    await loginThroughForm(page, data.people.inactive.email, data.people.inactive.password);
    await expect(page.getByText("Login failed")).toBeVisible(); // identical to a wrong password
    await expect(page).toHaveURL(`${E2E.webUrl}/login`);
    await api.dispose();
  });

test("anonymous API calls to protected endpoint groups get 401", async ({ data }) => {
  void data;
  const api = await apiContext();
  const protectedCalls: [string, string][] = [
    ["GET", "/api/v1/employees/me"], ["GET", "/api/v1/employees"], ["POST", "/api/v1/employees"],
    ["GET", "/api/v1/leaves/requests/me"], ["GET", "/api/v1/tickets"], ["GET", "/api/v1/announcements"],
    ["GET", "/api/v1/polls"], ["GET", "/api/v1/hr-policies"], ["GET", "/api/v1/attendance/me"],
    ["GET", "/api/v1/finance/payroll/me"], ["GET", "/api/v1/calendar/team"], ["GET", "/api/v1/org/tree"],
    ["POST", "/api/v1/chat"], ["GET", "/api/v1/chat/permissions"], ["GET", "/api/v1/ai/audit"],
    ["GET", "/api/v1/ai/audit/metrics"], ["POST", "/api/v1/auth/logout"],
  ];
  const outcomes = [];
  for (const [method, url] of protectedCalls) outcomes.push(`${method} ${url} ${(await call(api, null, method, url)).status}`);
  expect(outcomes).toEqual(protectedCalls.map(([method, url]) => `${method} ${url} 401`));
  expect((await call(api, null, "GET", "/api/v1/health")).status).toBe(200);
  await api.dispose();
});

test("a forged hrms_role cookie does not open an admin page: the role comes from the server profile",
  async ({ browser, tokens }) => {
    const context = await contextWithToken(browser, tokens.EMPLOYEE, "ADMIN"); // employee token, cookie claims ADMIN
    const page = await context.newPage();
    await page.goto("/employees/new");
    await expectAccessDenied(page);
    await page.goto("/dashboard");
    await expect(page.locator("aside nav").getByRole("link", { name: "Employees", exact: true })).toHaveCount(0);
    await context.close();
  });

test("sign out everywhere ends the session and revokes the old token", async ({ page, data }) => {
  await loginThroughForm(page, data.people.sessionUser.email, data.people.sessionUser.password);
  await expectPageTitle(page, "Dashboard");
  const token = await page.evaluate(() => localStorage.getItem("hrms_access_token"));
  expect(token).toBeTruthy();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Sign out everywhere" }).click();
  await expect(page).toHaveURL(new RegExp(`${E2E.webUrl}/login`));
  expect(await page.evaluate(() => localStorage.getItem("hrms_access_token"))).toBeNull();
  const api = await apiContext();
  const reuse = await call(api, token, "GET", "/api/v1/employees/me");
  expect(reuse.status).toBe(401); // token_version was bumped (B-17)
  await api.dispose();
});
