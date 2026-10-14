// Policy publication workflow UI (approved 2026-10-14). UX only: the backend enforces that only ADMINs publish and that
// drafts are hidden from everyone but ADMINs and the submitter (backend tests/characterization/test_hr_policy_publication.py).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HRPoliciesPage from "@/app/hr-policies/page";

import { mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

const PUBLISHED = { id: 1, title: "Leave Policy", category: "LEAVE", original_filename: "leave.md", status: "PUBLISHED", version: 1, created_at: "2026-10-01T00:00:00" };
const DRAFT = { id: 2, title: "Remote Work", category: "WORKPLACE", original_filename: "remote.md", status: "SUBMITTED", version: 1, uploaded_by: 2, created_at: "2026-10-14T00:00:00" };

function backend(role: string, items: unknown[]) {
  return mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: role === "ADMIN" ? 1 : 2, name: "Test User", role }),
    "GET /api/v1/hr-policies": () => ok({ items, meta: { total: items.length, limit: 100, offset: 0 } }),
    "POST /api/v1/hr-policies/2/publish": () => ok({ ...DRAFT, status: "PUBLISHED", index_refresh_required: true }),
  });
}

describe("HR policy review UI", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("shows a manager their submission as awaiting review, without publish controls", async () => {
    backend("MANAGER", [PUBLISHED, DRAFT]);
    render(<HRPoliciesPage />);
    expect(await screen.findByText(/Awaiting admin review/)).toBeInTheDocument();
    expect(screen.getByText(/submitted for admin review before they are published/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
  });

  it("lets an admin publish a submission through the backend review endpoint", async () => {
    const { calls } = backend("ADMIN", [PUBLISHED, DRAFT]);
    render(<HRPoliciesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/v1/hr-policies/2/publish")).toBe(true));
    expect(await screen.findByText(/Run the policy re-index/)).toBeInTheDocument();
    // Only the backend-reported submission gets review controls; a published policy has none.
    expect(screen.getAllByRole("button", { name: "Publish" })).toHaveLength(1);
  });
});
