// Every page (21 app/**/page.tsx files) x {anonymous, EMPLOYEE, MANAGER, ADMIN}, by direct navigation.
//
// Expected access is written out here independently (from docs/role-access-matrix.md: lib/permissions.ts PAGE_ACCESS,
// middleware.ts and the backend route suite) and pinned against PAGE_ACCESS below, so drift in either place fails.
// For a denied page the test also calls the page's primary API with that role's real token: the BACKEND must refuse
// it, because the "Access denied" view is only the client-side guard.
import { PAGE_ACCESS } from "../lib/permissions";

import { call, apiContext, classify } from "./support/api";
import { E2E, type SeededRole } from "./support/env";
import { contextWithToken, expectAccessDenied, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";

type Page = {
  file: string;
  path: string;
  title: string; // the Topbar heading (level 1)
  roles: SeededRole[] | "public";
  // A request the page depends on; the backend must answer it with `deniedClass` for every role that is not allowed.
  primaryApi?: { method: string; url: (ids: { outsider: number }) => string; data?: unknown; deniedClass: "deny" };
};

const EVERYONE: SeededRole[] = ["ADMIN", "MANAGER", "EMPLOYEE"];
const MANAGERS: SeededRole[] = ["ADMIN", "MANAGER"];

export const PAGES: Page[] = [
  { file: "app/page.tsx", path: "/", title: "", roles: "public" },
  { file: "app/login/page.tsx", path: "/login", title: "", roles: "public" },
  { file: "app/dashboard/page.tsx", path: "/dashboard", title: "Dashboard", roles: EVERYONE },
  { file: "app/copilot/[[...conversation]]/page.tsx", path: "/copilot", title: "AI Copilot", roles: EVERYONE },
  { file: "app/me/page.tsx", path: "/me", title: "My Profile", roles: EVERYONE },
  { file: "app/me/documents/page.tsx", path: "/me/documents", title: "My Documents", roles: EVERYONE },
  { file: "app/finance/page.tsx", path: "/finance", title: "Finance", roles: EVERYONE },
  { file: "app/attendance/page.tsx", path: "/attendance", title: "Attendance", roles: EVERYONE },
  { file: "app/leaves/page.tsx", path: "/leaves", title: "Leaves", roles: EVERYONE },
  { file: "app/announcements/page.tsx", path: "/announcements", title: "Announcements", roles: EVERYONE },
  { file: "app/hr-policies/page.tsx", path: "/hr-policies", title: "HR Policies", roles: EVERYONE },
  { file: "app/polls/page.tsx", path: "/polls", title: "Polls", roles: EVERYONE },
  { file: "app/team-calendar/page.tsx", path: "/team-calendar", title: "Team Calendar", roles: EVERYONE },
  { file: "app/tickets/page.tsx", path: "/tickets", title: "Tickets", roles: EVERYONE },
  { file: "app/organization/page.tsx", path: "/organization", title: "Organization", roles: EVERYONE },
  { file: "app/employees/page.tsx", path: "/employees", title: "Employee Directory", roles: MANAGERS,
    primaryApi: { method: "GET", url: () => "/api/v1/employees", deniedClass: "deny" } },
  { file: "app/employees/projects/page.tsx", path: "/employees/projects", title: "Project Management", roles: MANAGERS,
    primaryApi: { method: "GET", url: () => "/api/v1/employees/projects/catalog", deniedClass: "deny" } },
  { file: "app/employees/payslips/page.tsx", path: "/employees/payslips", title: "Employee Documents Upload", roles: MANAGERS,
    primaryApi: { method: "GET", url: ({ outsider }) => `/api/v1/employees/${outsider}/projects`, deniedClass: "deny" } },
  { file: "app/employees/new/page.tsx", path: "/employees/new", title: "Add Employee", roles: ["ADMIN"],
    primaryApi: { method: "POST", url: () => "/api/v1/employees", deniedClass: "deny",
                  data: { name: "E2E Denied Hire", email: "e2e.denied.hire@hrms-e2e.dev", password: `E2e-denied-${Date.now().toString(36)}`,
                          joining_date: "2026-01-05" } } },
  { file: "app/admin/ai-audit/page.tsx", path: "/admin/ai-audit", title: "AI Audit Log", roles: ["ADMIN"],
    primaryApi: { method: "GET", url: () => "/api/v1/ai/audit/metrics", deniedClass: "deny" } },
  { file: "app/admin/ai-usage/page.tsx", path: "/admin/ai-usage", title: "AI Usage", roles: ["ADMIN"],
    primaryApi: { method: "GET", url: () => "/api/v1/ai/audit/metrics", deniedClass: "deny" } },
];

test("the page inventory covers all 21 page files and matches the frontend permission map", () => {
  expect(PAGES).toHaveLength(21);
  for (const page of PAGES.filter((p) => p.roles !== "public")) {
    const row = [...PAGE_ACCESS].filter((r) => page.path === r.path || page.path.startsWith(`${r.path}/`))
      .sort((a, b) => b.path.length - a.path.length)[0];
    expect(row, `${page.path} has a PAGE_ACCESS row`).toBeTruthy();
    expect([...row!.roles].sort(), `${page.path} roles`).toEqual([...(page.roles as SeededRole[])].sort());
  }
});

test.describe("anonymous", () => {
  for (const page of PAGES) {
    test(`anonymous ${page.path}`, async ({ browser, data }) => {
      void data; // suite readiness
      const context = await contextWithToken(browser, null);
      const tab = await context.newPage();
      const response = await tab.goto(page.path);
      // Every non-login page sends an anonymous visitor to /login with a SERVER-side redirect (middleware.ts, or
      // redirect() in app/page.tsx), so the protected page is never rendered.
      await expect(tab).toHaveURL(`${E2E.webUrl}/login`);
      if (page.path !== "/login") expect(response?.request().redirectedFrom(), "server-side redirect").toBeTruthy();
      await expect(tab.getByRole("button", { name: "Sign in" })).toBeVisible();
      await context.close();
    });
  }
});

for (const role of ["EMPLOYEE", "MANAGER", "ADMIN"] as const) {
  test.describe(role, () => {
    for (const page of PAGES) {
      const allowed = page.roles === "public" || page.roles.includes(role);
      test(`${role} ${page.path} -> ${page.roles === "public" ? "redirect to dashboard" : allowed ? "allowed" : "denied"}`,
        async ({ browser, data, tokens }) => {
          const context = await contextWithToken(browser, tokens[role], role);
          const tab = await context.newPage();
          await tab.goto(page.path);
          if (page.roles === "public") {
            // "/" -> /login -> (signed in) /dashboard: middleware.ts
            await expect(tab).toHaveURL(`${E2E.webUrl}/dashboard`);
            await expectPageTitle(tab, "Dashboard");
          } else if (allowed) {
            await expectPageTitle(tab, page.title);
            await expect(tab.getByRole("alert").filter({ hasText: "Access denied" })).toHaveCount(0);
          } else {
            await expectAccessDenied(tab); // client-side guard; the URL is unchanged (no server 403 for pages)
            await expect(tab).toHaveURL(`${E2E.webUrl}${page.path}`);
            await expect(tab.getByRole("heading", { level: 1, name: page.title })).toHaveCount(0);
            const api = await apiContext();
            const result = await call(api, tokens[role], page.primaryApi!.method, page.primaryApi!.url(data.ids),
                                      { data: page.primaryApi!.data });
            expect(classify(result.status), `backend refuses ${page.primaryApi!.method} for ${role}`)
              .toBe(page.primaryApi!.deniedClass);
            await api.dispose();
          }
          await context.close();
        });
    }
  });
}
