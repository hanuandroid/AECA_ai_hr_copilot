// Sidebar navigation and role-dependent controls (UX only; the backend checks behind each control are in api.spec.ts).
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";

const SIDEBAR: { label: string; href: string; title: string; employee: boolean }[] = [
  { label: "Dashboard", href: "/dashboard", title: "Dashboard", employee: true },
  { label: "AI Copilot", href: "/copilot", title: "AI Copilot", employee: true },
  { label: "Employees", href: "/employees", title: "Employee Directory", employee: false },
  { label: "My Profile", href: "/me", title: "My Profile", employee: true },
  { label: "My Documents", href: "/me/documents", title: "My Documents", employee: true },
  { label: "Finance", href: "/finance", title: "Finance", employee: true },
  { label: "Attendance", href: "/attendance", title: "Attendance", employee: true },
  { label: "Leaves", href: "/leaves", title: "Leaves", employee: true },
  { label: "Announcements", href: "/announcements", title: "Announcements", employee: true },
  { label: "HR Policies", href: "/hr-policies", title: "HR Policies", employee: true },
  { label: "Polls", href: "/polls", title: "Polls", employee: true },
  { label: "Team Calendar", href: "/team-calendar", title: "Team Calendar", employee: true },
  { label: "Tickets", href: "/tickets", title: "Tickets", employee: true },
];

for (const role of ["EMPLOYEE", "MANAGER", "ADMIN"] as const) {
  test(`${role}: sidebar lists exactly the permitted pages and every link opens its page`, async ({ browser, tokens }) => {
    const context = await contextWithToken(browser, tokens[role], role);
    const page = await context.newPage();
    await page.goto("/dashboard");
    await expectPageTitle(page, "Dashboard");
    const expected = SIDEBAR.filter((item) => role !== "EMPLOYEE" || item.employee);
    const nav = page.locator("aside nav");
    await expect(nav.getByRole("link")).toHaveText(expected.map((item) => item.label));
    for (const item of expected) {
      await nav.getByRole("link", { name: item.label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${item.href}$`));
      await expectPageTitle(page, item.title);
    }
    await context.close();
  });
}

test("restricted controls are shown only to the roles that may use them", async ({ browser, tokens, data }) => {
  const seen: Record<string, Record<string, boolean>> = {};
  for (const role of ["EMPLOYEE", "MANAGER", "ADMIN"] as const) {
    const context = await contextWithToken(browser, tokens[role], role);
    const page = await context.newPage();
    const visible = async (locator: ReturnType<typeof page.getByRole>) => (await locator.count()) > 0 && locator.first().isVisible();
    seen[role] = {};

    // The admin links depend on GET /chat/permissions: check only after that response arrived (no false "hidden").
    const permissions = page.waitForResponse((response) => response.url().endsWith("/api/v1/chat/permissions"));
    await page.goto("/copilot");
    expect((await permissions).status()).toBe(200);
    await expectPageTitle(page, "AI Copilot");
    seen[role].aiAdminLinks = await visible(page.getByRole("link", { name: "AI audit log" }));

    await page.goto("/announcements");
    await expectPageTitle(page, "Announcements");
    await expect(page.getByText("Loading announcements...")).toHaveCount(0);
    seen[role].postAnnouncement = await visible(page.getByRole("button", { name: "Post Announcement" }));
    seen[role].audienceChoice = await visible(page.getByRole("combobox", { name: "Audience" }));

    await page.goto("/polls");
    await expectPageTitle(page, "Polls");
    await expect(page.getByText("Loading polls...")).toHaveCount(0);
    seen[role].createPoll = await visible(page.getByRole("button", { name: "Create", exact: true }));

    if (role !== "EMPLOYEE") {
      await page.goto("/employees/projects");
      await expectPageTitle(page, "Project Management");
      await expect(page.getByText(/Assign New Project/)).toBeVisible();
      seen[role].createProject = await visible(page.getByRole("button", { name: "Create Project" }));

      await page.goto(`/employees?q=${encodeURIComponent("employee@mock-hrms.dev")}`);
      await expectPageTitle(page, "Employee Directory");
      await page.getByRole("cell", { name: "Employee User", exact: true }).click();
      await expect(page.getByText("Email:")).toBeVisible();
      seen[role].addEmployee = await visible(page.getByRole("button", { name: "Add New Employee" }));
      seen[role].deactivate = await visible(page.getByRole("button", { name: "Deactivate Employee" }));
    }
    await context.close();
  }
  void data;
  expect(seen).toEqual({
    EMPLOYEE: { aiAdminLinks: false, postAnnouncement: false, audienceChoice: false, createPoll: false },
    MANAGER: { aiAdminLinks: false, postAnnouncement: true, audienceChoice: false, createPoll: true, createProject: false,
               addEmployee: false, deactivate: false },
    ADMIN: { aiAdminLinks: true, postAnnouncement: true, audienceChoice: true, createPoll: true, createProject: true,
             addEmployee: true, deactivate: true },
  });
});
