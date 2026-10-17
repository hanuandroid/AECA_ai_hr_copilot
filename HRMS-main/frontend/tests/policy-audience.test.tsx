// RBAC step 7 (decision 3): HR policy audiences. UX only - the backend filters lists, downloads and Copilot retrieval
// and refuses an audience the uploader is not part of (backend tests/rbac/test_policy_audience.py).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HRPoliciesPage from "@/app/hr-policies/page";

import { mockBackend, ok } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

const ITEMS = [
  { id: 1, title: "Leave Policy", category: "LEAVE", original_filename: "leave.md", status: "PUBLISHED", audience: "ALL", version: 1, created_at: "2026-10-01T00:00:00" },
  { id: 3, title: "Calibration Guide", category: "PERFORMANCE", original_filename: "calibration.md", status: "PUBLISHED", audience: "MANAGERS", version: 1, created_at: "2026-10-01T00:00:00" },
];

function setup(role: string, uploadStatus = 200) {
  const { fetchMock } = mockBackend({
    "GET /api/v1/employees/me": () => ok({ id: role === "ADMIN" ? 1 : 2, name: "Test User", role }),
    "GET /api/v1/hr-policies": () => ok({ items: ITEMS, meta: { total: ITEMS.length, limit: 100, offset: 0 } }),
  });
  const inner = fetchMock.getMockImplementation()!;
  const uploads: FormData[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    if (init.body instanceof FormData) {
      uploads.push(init.body);
      const body = uploadStatus === 200
        ? { success: true, data: { ...ITEMS[0], id: 9, status: "SUBMITTED", audience: init.body.get("audience") }, error: null }
        : { success: false, data: null, error: { code: "POLICY_AUDIENCE_FORBIDDEN", message: "server message must not be shown" } };
      return new Response(JSON.stringify(body), { status: uploadStatus, headers: { "Content-Type": "application/json" } });
    }
    return inner(input, init);
  }));
  return uploads;
}

async function upload(container: HTMLElement, audience?: string) {
  await userEvent.type(screen.getByPlaceholderText("Policy title"), "New policy");
  if (audience) await userEvent.selectOptions(screen.getByRole("combobox", { name: "Readers" }), audience);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, new File(["# Policy"], "policy.md", { type: "text/markdown" }));
  await userEvent.click(screen.getByRole("button", { name: "Upload Policy" }));
}

describe("HR policy audience (UX only)", () => {
  beforeEach(() => localStorage.setItem("hrms_access_token", "token"));

  it("a MANAGER may choose Everyone or Managers, never Admins only", async () => {
    setup("MANAGER");
    render(<HRPoliciesPage />);
    const select = await screen.findByRole("combobox", { name: "Readers" });
    const options = within(select).getAllByRole("option").map((option) => option.getAttribute("value"));
    expect(options).toEqual(["ALL", "MANAGERS"]);
  });

  it("an ADMIN may choose any audience and it is sent with the upload", async () => {
    const uploads = setup("ADMIN");
    const { container } = render(<HRPoliciesPage />);
    const select = await screen.findByRole("combobox", { name: "Readers" });
    expect(within(select).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual(["ALL", "MANAGERS", "ADMINS"]);
    await upload(container, "ADMINS");
    await waitFor(() => expect(uploads).toHaveLength(1));
    expect(uploads[0].get("audience")).toBe("ADMINS");
  });

  it("restricted policies are labelled and a backend 403 shows a safe message", async () => {
    const uploads = setup("MANAGER", 403);
    const { container } = render(<HRPoliciesPage />);
    expect(await screen.findByText("Managers")).toBeInTheDocument();
    await upload(container);
    expect(await screen.findByText("You can only choose an audience that includes you.")).toBeInTheDocument();
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
    expect(uploads[0].get("audience")).toBe("ALL");
  });

  it("an EMPLOYEE has no upload form", async () => {
    setup("EMPLOYEE");
    render(<HRPoliciesPage />);
    await screen.findByText("leave.md");
    expect(screen.queryByRole("combobox", { name: "Readers" })).not.toBeInTheDocument();
  });
});
