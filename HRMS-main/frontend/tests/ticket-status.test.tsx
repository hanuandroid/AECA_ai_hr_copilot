// RBAC Decision 4 (step 5): employees raise tickets and check status but cannot change it; only the assignee, a manager
// in scope or an admin can. UX only: the backend refuses the owner with 403 (backend tests/rbac/test_ticket_status.py).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TicketsPage from "@/app/tickets/page";

import { fail, mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/tickets" }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

const VIEWER = 9;

function ticket(id: number, employeeId: number, assigneeId: number | null) {
  return { id, employee_id: employeeId, assignee_id: assigneeId, title: `Ticket ${id}`, description: "Something broke.",
           category: "IT", priority: "MEDIUM", status: "OPEN", created_at: "2026-10-01T09:00:00Z" };
}

function backend(role: string, statusReply = ok(ticket(1, VIEWER, null))) {
  return mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: VIEWER, name: "Test User", role, has_profile_photo: false }),
    "GET /api/v1/tickets": () => ok({ items: [ticket(1, VIEWER, null), ticket(2, 4, VIEWER)], meta: { total: 2, limit: 100, offset: 0 } }),
    "POST /api/v1/tickets": () => statusReply,
  });
}

function card(id: number) {
  return screen.getByRole("heading", { name: `#${id} Ticket ${id}` }).closest("article") as HTMLElement;
}

describe("ticket status controls (UX only)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("an EMPLOYEE sees status controls only on tickets assigned to them, not on tickets they raised", async () => {
    backend("EMPLOYEE");
    render(<TicketsPage />);
    await screen.findByRole("heading", { name: "#1 Ticket 1" });
    expect(screen.getAllByRole("button", { name: "Resolve" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Start" })).toHaveLength(1);
    expect(within(card(1)).queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
    expect(within(card(2)).getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  });

  it.each(["MANAGER", "ADMIN"])("a %s sees status controls on every listed ticket", async (role) => {
    backend(role);
    render(<TicketsPage />);
    await screen.findByRole("heading", { name: "#1 Ticket 1" });
    expect(screen.getAllByRole("button", { name: "Resolve" })).toHaveLength(2);
  });

  it("a backend 403 is shown as a permission message and the list is reloaded", async () => {
    const { calls } = backend("MANAGER", fail(403, "FORBIDDEN"));
    render(<TicketsPage />);
    await screen.findByRole("heading", { name: "#1 Ticket 1" });
    const loadsBefore = calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/v1/tickets")).length;
    await userEvent.click(screen.getAllByRole("button", { name: "Resolve" })[0]);
    expect(await screen.findByText("You are not allowed to change this ticket's status.")).toBeInTheDocument();
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/v1/tickets")).length).toBeGreaterThan(loadsBefore));
    const statusCall = calls.find((c) => c.method === "POST" && c.path.endsWith("/status"));
    expect(statusCall?.body).toEqual({ status: "RESOLVED" });
  });
});
