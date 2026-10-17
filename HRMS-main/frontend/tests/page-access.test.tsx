// RBAC remediation step 9: one permission map, role-filtered sidebar, direct-URL guards, access-denied view.
// UX only: every page's API calls are refused by the backend for roles not listed (backend route-permission suite).
import { readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessGuard } from "@/components/access/access-guard";
import { Sidebar } from "@/components/layout/sidebar";
import { PAGE_ACCESS, canAccess, requiredRoles } from "@/lib/permissions";
import { middleware } from "@/middleware";

import { fail, mockBackend, ok } from "./helpers";

const navigation = vi.hoisted(() => ({ pathname: "/dashboard" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/image", () => ({ default: () => null }));

const APP_DIR = join(__dirname, "..", "app");

function pageRoutes(dir = APP_DIR): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return pageRoutes(full);
    if (name !== "page.tsx") return [];
    const segments = relative(APP_DIR, dir).split(sep).filter(Boolean)
      .filter((segment) => !segment.startsWith("(") && !segment.startsWith("[["));  // route groups, optional catch-all
    return [`/${segments.join("/")}`];
  });
}

function asRole(role: string | null, status = 200) {
  return mockBackend({
    "GET /api/v1/employees/me": () => (status === 200 ? ok({ id: 9, name: "Test User", role }) : fail(status, "ERR")),
  });
}

const EXPECTED: Record<string, [boolean, boolean, boolean]> = {  // [EMPLOYEE, MANAGER, ADMIN]
  "/dashboard": [true, true, true], "/copilot": [true, true, true], "/copilot/11111111-1111-4111-8111-111111111111": [true, true, true],
  "/me": [true, true, true], "/me/documents": [true, true, true], "/finance": [true, true, true],
  "/attendance": [true, true, true], "/leaves": [true, true, true], "/announcements": [true, true, true],
  "/hr-policies": [true, true, true], "/polls": [true, true, true], "/team-calendar": [true, true, true],
  "/tickets": [true, true, true], "/organization": [true, true, true],
  "/employees": [false, true, true], "/employees/projects": [false, true, true], "/employees/payslips": [false, true, true],
  "/employees/new": [false, false, true], "/admin/ai-audit": [false, false, true], "/admin/ai-usage": [false, false, true],
};

describe("permission map", () => {
  it("covers every page in app/ (a new page without a row fails here)", () => {
    const routes = pageRoutes();
    expect(routes.length).toBeGreaterThan(15);
    for (const route of routes) expect(requiredRoles(route), route).not.toBeNull();
  });

  it.each(Object.entries(EXPECTED))("%s", (path, [employee, manager, admin]) => {
    expect([canAccess("EMPLOYEE", path), canAccess("MANAGER", path), canAccess("ADMIN", path)]).toEqual([employee, manager, admin]);
  });

  it("fails closed for unknown, missing or differently cased roles and unmapped paths", () => {
    for (const role of [null, undefined, "", "admin", "SUPERUSER", "ADMIN "]) expect(canAccess(role, "/employees")).toBe(false);
    for (const path of ["/employeesX", "/administrator", "/admin-tools", "/unknown"]) {
      expect(canAccess("ADMIN", path), path).toBe(false);
    }
    expect(canAccess("EMPLOYEE", "/employees/new/../projects")).toBe(false);
    expect(canAccess(null, "/login")).toBe(true);
  });

  it("the most specific row wins", () => {
    expect(PAGE_ACCESS.find((row) => row.path === "/employees/new")?.roles).toEqual(["ADMIN"]);
    expect(canAccess("MANAGER", "/employees/new")).toBe(false);
    expect(canAccess("MANAGER", "/employees")).toBe(true);
  });
});

describe("direct-URL guard", () => {
  beforeEach(() => {
    localStorage.setItem("hrms_access_token", "token");
    document.cookie = "hrms_role=; path=/; max-age=0";
  });

  it.each([
    ["EMPLOYEE", "/employees/projects"], ["EMPLOYEE", "/employees"], ["MANAGER", "/employees/new"],
    ["MANAGER", "/admin/ai-audit"], ["EMPLOYEE", "/admin/ai-usage"],
  ])("a %s opening %s gets the access-denied view and the page never mounts", async (role, path) => {
    navigation.pathname = path;
    asRole(role);
    render(<AccessGuard><div>restricted page</div></AccessGuard>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(screen.queryByText("restricted page")).not.toBeInTheDocument();
  });

  it.each([["MANAGER", "/employees/projects"], ["ADMIN", "/admin/ai-audit"], ["ADMIN", "/employees/new"]])(
    "a %s may open %s", async (role, path) => {
      navigation.pathname = path;
      asRole(role);
      render(<AccessGuard><div>restricted page</div></AccessGuard>);
      expect(await screen.findByText("restricted page")).toBeInTheDocument();
    });

  it("shows nothing restricted while the role is still loading", () => {
    navigation.pathname = "/admin/ai-audit";
    mockBackend({ "GET /api/v1/employees/me": () => new Promise(() => undefined) });
    render(<AccessGuard><div>restricted page</div></AccessGuard>);
    expect(screen.queryByText("restricted page")).not.toBeInTheDocument();
  });

  it("pages open to every role render without waiting for the profile", () => {
    navigation.pathname = "/tickets";
    mockBackend({ "GET /api/v1/employees/me": () => new Promise(() => undefined) });
    render(<AccessGuard><div>tickets page</div></AccessGuard>);
    expect(screen.getByText("tickets page")).toBeInTheDocument();
  });

  it("fails closed when the role cannot be loaded", async () => {
    navigation.pathname = "/employees/projects";
    asRole(null, 500);
    render(<AccessGuard><div>restricted page</div></AccessGuard>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
  });

  it("a forged hrms_role cookie does not open admin pages; only the server profile counts", async () => {
    navigation.pathname = "/admin/ai-usage";
    document.cookie = "hrms_role=ADMIN; path=/";
    const { calls } = asRole("EMPLOYEE");
    render(<AccessGuard><div>restricted page</div></AccessGuard>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(calls.some((call) => call.path === "/api/v1/employees/me" && call.headers.Authorization === "Bearer token")).toBe(true);
  });

  it("an expired session is left to the page, which redirects to /login", async () => {
    navigation.pathname = "/employees";
    asRole(null, 401);
    render(<AccessGuard><div>page handles 401</div></AccessGuard>);
    expect(await screen.findByText("page handles 401")).toBeInTheDocument();
  });
});

describe("role-filtered sidebar", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  async function linksFor(role: string): Promise<string[]> {
    navigation.pathname = "/dashboard";
    asRole(role);
    render(<AccessGuard><Sidebar /></AccessGuard>);
    if (role !== "EMPLOYEE") await screen.findByRole("link", { name: "Employees" });
    else await waitFor(() => expect(screen.getByRole("link", { name: "Tickets" })).toBeInTheDocument());
    return screen.getAllByRole("link").map((link) => link.getAttribute("href") ?? "");
  }

  it("an EMPLOYEE sees no Employees link", async () => {
    const links = await linksFor("EMPLOYEE");
    expect(links).not.toContain("/employees");
    expect(links).toContain("/tickets");
  });

  it.each(["MANAGER", "ADMIN"])("a %s sees the Employees link", async (role) => {
    expect(await linksFor(role)).toContain("/employees");
  });

  it("every sidebar link is allowed for the role that sees it", async () => {
    for (const role of ["EMPLOYEE", "MANAGER", "ADMIN"]) {
      for (const href of await linksFor(role)) expect(canAccess(role, href), `${role} ${href}`).toBe(true);
      cleanup();
    }
  });
});

describe("middleware login check", () => {
  it.each(["/organization", "/admin/ai-audit", "/employees/new"])("%s without a session redirects to /login", (path) => {
    const response = middleware(new NextRequest(`http://localhost${path}`));
    expect(response.headers.get("location")).toBe("http://localhost/login");
  });

  it("a signed-in request passes through (roles are checked by the page guard and the backend)", () => {
    const request = new NextRequest("http://localhost/organization", { headers: { cookie: "hrms_auth=1" } });
    expect(middleware(request).headers.get("location")).toBeNull();
  });
});
