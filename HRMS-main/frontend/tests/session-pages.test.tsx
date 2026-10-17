// B-17 follow-up: pages and the Copilot end the session through lib/session on a revoked or expired token (HTTP 401),
// and never on a network error. UX only - the backend refuses the token (tests/rbac/test_token_revocation.py).
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TOKEN_KEY } from "@/lib/session";

import { fail, mockBackend } from "./helpers";

const router = vi.hoisted(() => ({ push: vi.fn(), pathname: "/tickets" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: router.push, replace: router.push }),
  usePathname: () => router.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/topbar", () => ({ Topbar: () => null }));

function at(pathname: string) {
  window.history.pushState({}, "", pathname);
  router.pathname = pathname;
}

function sessionCleared() {
  return localStorage.getItem(TOKEN_KEY) === null && !document.cookie.includes("hrms_auth=1")
    && !document.cookie.includes("hrms_role=ADMIN");
}

beforeEach(() => {
  router.push.mockReset();
  localStorage.setItem(TOKEN_KEY, "token");
  document.cookie = "hrms_auth=1; path=/";
  document.cookie = "hrms_role=ADMIN; path=/";
});

describe("revoked tokens on ordinary pages and the Copilot", () => {
  it("a page that receives TOKEN_REVOKED ends the session", async () => {
    at("/tickets");
    mockBackend({
      "GET /api/v1/employees/me": () => fail(401, "TOKEN_REVOKED"),
      "GET /api/v1/tickets": () => fail(401, "TOKEN_REVOKED"),
    });
    const { default: TicketsPage } = await import("@/app/tickets/page");
    render(<TicketsPage />);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=session-ended"));
    expect(sessionCleared()).toBe(true);
  });

  it("the Copilot page ends the session on a revoked token", async () => {
    at("/copilot");
    mockBackend({
      "GET /api/v1/employees/me": () => fail(401, "TOKEN_REVOKED"),
      "GET /api/v1/chat/permissions": () => fail(401, "TOKEN_REVOKED"),
      "GET /api/v1/chat/conversations": () => fail(401, "TOKEN_REVOKED"),
    });
    const { default: CopilotPage } = await import("@/app/copilot/[[...conversation]]/page");
    render(<CopilotPage />);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=session-ended"));
    expect(sessionCleared()).toBe(true);
  });

  it("a network error is not treated as a sign-out", async () => {
    at("/tickets");
    mockBackend({
      "GET /api/v1/employees/me": () => ({ networkError: true }),
      "GET /api/v1/tickets": () => ({ networkError: true }),
    });
    const { default: TicketsPage } = await import("@/app/tickets/page");
    render(<TicketsPage />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(router.push).not.toHaveBeenCalled();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("token");
  });
});
