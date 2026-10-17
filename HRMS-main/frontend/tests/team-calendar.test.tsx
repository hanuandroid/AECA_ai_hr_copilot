// S-06: the team calendar API returns `employee_role` to ADMIN viewers only and scopes rows on the backend
// (backend tests/rbac/test_team_calendar_scope.py). The page renders whatever the backend returns, with or without role.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TeamCalendarPage from "@/app/team-calendar/page";

import { mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/team-calendar" }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

function backend(items: unknown[]) {
  return mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: 2, name: "Test User", role: "MANAGER" }),
    "GET /api/v1/calendar/team": () => ok({ year: 2031, month: 3, days: [{ date: "2031-03-02", day: 2, weekday: "Sun" }], items }),
    "GET /api/v1/calendar/holidays": () => ok([]),
  });
}

describe("team calendar (S-06)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("renders rows without a role field (manager and employee responses)", async () => {
    backend([{ employee_id: 3, employee_name: "Employee Test", markers: { "2031-03-02": "LEAVE" } }]);
    render(<TeamCalendarPage />);
    expect(await screen.findByText("Employee Test")).toBeInTheDocument();
    expect(screen.getByText("ID: 3")).toBeInTheDocument();
    expect(screen.queryByText(/\| EMPLOYEE/)).not.toBeInTheDocument();
  });

  it("shows the role when the backend returns it (admin responses)", async () => {
    backend([{ employee_id: 3, employee_name: "Employee Test", employee_role: "EMPLOYEE", markers: {} }]);
    render(<TeamCalendarPage />);
    expect(await screen.findByText("Employee Test")).toBeInTheDocument();
    expect(screen.getByText(/ID: 3\s*\| EMPLOYEE/)).toBeInTheDocument();
  });
});
