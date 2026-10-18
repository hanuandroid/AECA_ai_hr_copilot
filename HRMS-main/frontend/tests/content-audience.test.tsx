// RBAC step 6 (decision 5): announcement/poll audience and project catalogue controls. UX only - the backend enforces
// every rule (backend tests/rbac/test_content_audience.py and test_project_scope.py).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnnouncementsPage from "@/app/announcements/page";
import EmployeeProjectsPage from "@/app/employees/projects/page";
import PollsPage from "@/app/polls/page";

import { fail, mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

const profile = (role: string) => () => ok({ id: 2, name: "Test User", role, has_profile_photo: false });
const NEWS = [
  { id: 1, title: "Company update", body: "For everyone.", author_id: 1, author_name: "Admin", audience: "ORG", created_at: "2026-10-01T09:00:00Z" },
  { id: 2, title: "Team update", body: "For the team.", author_id: 2, author_name: "Manager", audience: "TEAM", created_at: "2026-10-01T09:00:00Z" },
];

function announcements(role: string, createReply = ok(NEWS[1])) {
  return mockBackend({
    "GET /api/v1/employees/me": profile(role),
    "GET /api/v1/announcements": () => ok({ items: NEWS, meta: { total: 2, limit: 20, offset: 0 } }),
    "POST /api/v1/announcements": () => createReply,
  });
}

async function post(title = "Release notes") {
  await userEvent.type(screen.getByPlaceholderText("Title"), title);
  await userEvent.type(screen.getByPlaceholderText(/Announcement details/), "Details for the team.");
  await userEvent.click(screen.getByRole("button", { name: "Post Announcement" }));
}

describe("announcement audience (UX only)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("a MANAGER has no audience choice and always posts to their team", async () => {
    const { calls } = announcements("MANAGER");
    render(<AnnouncementsPage />);
    await screen.findByText("Company update");
    expect(screen.queryByRole("combobox", { name: "Audience" })).not.toBeInTheDocument();
    expect(screen.getByText("Visible to you and your direct reports.")).toBeInTheDocument();
    await post();
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ audience: "TEAM" });
  });

  it("an ADMIN posts to everyone by default and can choose their team", async () => {
    const { calls } = announcements("ADMIN");
    render(<AnnouncementsPage />);
    await screen.findByText("Company update");
    await post("First");
    // G-11: a post to everyone (ORG) is sent only after the administrator confirms with their password.
    await userEvent.type(await screen.findByLabelText("Your password"), "synthetic-admin-pass-1");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    expect(calls.filter((c) => c.method === "POST")[0].body).toMatchObject({ audience: "ORG" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Audience" }), "TEAM");
    await post("Second");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toMatchObject({ audience: "TEAM" });
  });

  it("a backend 403 is shown as a team-only message, and TEAM items carry a badge", async () => {
    announcements("MANAGER", fail(403, "AUDIENCE_FORBIDDEN"));
    render(<AnnouncementsPage />);
    await screen.findByText("Team update");
    expect(screen.getAllByText("Team")).toHaveLength(1);
    await post();
    expect(await screen.findByText("You can only post to your own team.")).toBeInTheDocument();
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
  });

  it("an EMPLOYEE sees no create form", async () => {
    announcements("EMPLOYEE");
    render(<AnnouncementsPage />);
    await screen.findByText("Company update");
    expect(screen.queryByRole("button", { name: "Post Announcement" })).not.toBeInTheDocument();
  });
});

describe("poll audience (UX only)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  const POLL = { id: 7, question: "Team lunch?", options: ["Yes", "No"], created_by: 2, audience: "TEAM", created_at: "2026-10-01T09:00:00Z", my_vote: null };

  it("a MANAGER creates TEAM polls; a hidden poll's 404 on vote reloads with a message", async () => {
    const { calls } = mockBackend({
      "GET /api/v1/employees/me": profile("MANAGER"),
      "GET /api/v1/polls": () => ok({ items: [POLL], meta: { total: 1, limit: 20, offset: 0 } }),
      "POST /api/v1/polls/7/vote": () => fail(404, "POLL_NOT_FOUND"),
      "POST /api/v1/polls": () => ok(POLL),
    });
    render(<PollsPage />);
    await screen.findByText("Team lunch?");
    expect(screen.getByText("Team")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Question"), "Offsite date?");
    await userEvent.type(screen.getByPlaceholderText("Option A, Option B"), "Mon, Tue");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/polls")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/polls")?.body).toMatchObject({ audience: "TEAM" });
    const loads = calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/v1/polls")).length;
    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(await screen.findByText("This poll is no longer available to you.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "GET" && c.path.startsWith("/api/v1/polls")).length).toBeGreaterThan(loads);
  });
});

describe("project catalogue controls (UX only)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  function projects(role: string) {
    return mockBackend({
      "GET /api/v1/employees/me": profile(role),
      "GET /api/v1/employees": () => ok({ items: [{ id: 3, name: "Report Person", email: "r@example.test" }], meta: { total: 1, limit: 100, offset: 0 } }),
      "GET /api/v1/employees/projects/catalog": () => ok([{ id: 1, name: "Copilot", description: null, status: "ONGOING" }]),
      "GET /api/v1/employees/3/projects": () => ok([{ project_id: 1, project_name: "Copilot", project_description: null, project_status: "ONGOING", role_on_project: "Dev" }]),
      "PUT /api/v1/employees/projects/catalog/1/status": () => fail(403, "FORBIDDEN"),
    });
  }

  it("only an ADMIN sees catalogue creation", async () => {
    projects("ADMIN");
    const { unmount } = render(<EmployeeProjectsPage />);
    expect(await screen.findByRole("button", { name: "Create Project" })).toBeInTheDocument();
    unmount();
    projects("MANAGER");
    render(<EmployeeProjectsPage />);
    await screen.findByText(/Assign New Project/);
    expect(screen.queryByRole("button", { name: "Create Project" })).not.toBeInTheDocument();
  });

  it("a refused status change explains the assignment rule", async () => {
    projects("MANAGER");
    render(<EmployeeProjectsPage />);
    const select = await screen.findByDisplayValue("ONGOING");
    await userEvent.selectOptions(select, "ON_HOLD");
    expect(await screen.findByText(/only change the status of projects you or your direct reports are assigned to/)).toBeInTheDocument();
  });
});
