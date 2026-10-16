// RBAC Decision 1 (step 2): the employee directory search is offered only to MANAGER and ADMIN. UX only: the backend
// returns 403 to EMPLOYEE (backend tests/rbac/test_directory_scope.py). The role comes from the server profile.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Topbar } from "@/components/layout/topbar";

import { mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/layout/notification-bell", () => ({ NotificationBell: () => null }));

function backend(role: string) {
  return mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: 9, name: "Test User", role, has_profile_photo: false }),
    "GET /api/v1/employees": () => ok({ items: [{ id: 3, name: "Report Person", email: "report@example.test" }], meta: { total: 1, limit: 6, offset: 0 } }),
  });
}

describe("topbar employee search (directory scope)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("is not shown to an EMPLOYEE and never calls the directory", async () => {
    const { calls } = backend("EMPLOYEE");
    render(<Topbar name="Test User" />);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/employees/me")).toBe(true));
    expect(screen.queryByPlaceholderText(/search employees/i)).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/employees?"))).toBe(false);
  });

  it.each(["MANAGER", "ADMIN"])("is shown to a %s and searches the scoped directory", async (role) => {
    const { calls } = backend(role);
    render(<Topbar name="Test User" />);
    const input = await screen.findByPlaceholderText(/search employees/i);
    await userEvent.type(input, "Re");
    expect(await screen.findByText("Report Person")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/employees?"))).toBe(true);
  });
});
