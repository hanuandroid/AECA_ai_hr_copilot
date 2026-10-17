// B-17 follow-up: centralized session ending, global sign-out, password change on /me, and the security headers.
// UX only - the backend revokes tokens (tests/rbac/test_logout.py, test_token_revocation.py).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { securityHeaders } from "@/next.config";
import { clearSession, endSession, isAuthFailure, loginUrl, messageForReason, TOKEN_KEY } from "@/lib/session";

import { fail, mockBackend, ok } from "./helpers";

const router = vi.hoisted(() => ({ push: vi.fn(), pathname: "/me" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: router.push, replace: router.push }),
  usePathname: () => router.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/notification-bell", () => ({ NotificationBell: () => null }));

const PASSWORD = "current-pass-1";
const NEW = "brand-new-pass-2";

function signedIn() {
  localStorage.setItem(TOKEN_KEY, "token");
  document.cookie = "hrms_auth=1; path=/";
  document.cookie = "hrms_role=ADMIN; path=/";
}

function sessionCleared() {
  return localStorage.getItem(TOKEN_KEY) === null && !document.cookie.includes("hrms_auth=1")
    && !document.cookie.includes("hrms_role=ADMIN");
}

function at(pathname: string) {
  window.history.pushState({}, "", pathname);
  router.pathname = pathname;
}

beforeEach(() => {
  router.push.mockReset();
  signedIn();
  at("/me");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("session module", () => {
  it("clears the token and both hint cookies", () => {
    clearSession();
    expect(sessionCleared()).toBe(true);
  });

  it("ends a session with a reason in the URL and nothing sensitive", () => {
    endSession(router, "password-changed");
    expect(router.push).toHaveBeenCalledWith("/login?reason=password-changed");
    expect(sessionCleared()).toBe(true);
  });

  it("never redirects from public routes (no login loop)", () => {
    for (const path of ["/login", "/"]) {
      at(path);
      signedIn();
      endSession(router);
      expect(sessionCleared()).toBe(true);
    }
    expect(router.push).not.toHaveBeenCalled();
  });

  it("treats only HTTP 401 as an authentication failure", () => {
    expect([401, 403, 404, 500, 0, undefined, null].map(isAuthFailure)).toEqual([true, false, false, false, false, false, false]);
  });

  it("maps only known reasons to messages", () => {
    expect(messageForReason("signed-out")).toMatch(/all your devices/);
    expect(messageForReason("session-ended")).toMatch(/sign in again/);
    expect(messageForReason("<script>")).toBeNull();
    expect(messageForReason(null)).toBeNull();
    expect(loginUrl()).toBe("/login");
  });
});

describe("global sign-out", () => {
  async function signOut() {
    const { Topbar } = await import("@/components/layout/topbar");
    render(<Topbar name="Test User" />);
    await userEvent.click(screen.getByRole("button", { name: "Open profile menu" }));
    await userEvent.click(await screen.findByRole("button", { name: /Sign out everywhere/ }));
  }

  it("calls the backend logout and clears all three session indicators", async () => {
    const { calls } = mockBackend({
      "GET /api/v1/employees/me": () => ok({ id: 1, name: "Test User", role: "ADMIN", has_profile_photo: false }),
      "POST /api/v1/auth/logout": () => ok({ signed_out: true, scope: "all_sessions" }),
    });
    await signOut();
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=signed-out"));
    expect(calls.find((c) => c.path === "/api/v1/auth/logout")?.headers.Authorization).toBe("Bearer token");
    expect(sessionCleared()).toBe(true);
  });

  it("still signs out locally when the request fails", async () => {
    mockBackend({
      "GET /api/v1/employees/me": () => ok({ id: 1, name: "Test User", role: "ADMIN", has_profile_photo: false }),
      "POST /api/v1/auth/logout": () => ({ networkError: true }),
    });
    await signOut();
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=signed-out"));
    expect(sessionCleared()).toBe(true);
  });
});

describe("password change on /me", () => {
  async function submit(current: string, next: string, confirm = next) {
    const { ChangePasswordCard } = await import("@/components/account/change-password-card");
    render(<ChangePasswordCard />);
    await userEvent.type(screen.getByLabelText("Current password"), current);
    await userEvent.type(screen.getByLabelText("New password"), next);
    await userEvent.type(screen.getByLabelText("Confirm new password"), confirm);
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
  }

  it("on success ends the session and asks to sign in again, without passwords in URLs or logs", async () => {
    const logs = [vi.spyOn(console, "log"), vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "info")];
    const { calls } = mockBackend({ "POST /api/v1/auth/change-password": () => ok({ password_changed: true }) });
    await submit(PASSWORD, NEW);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=password-changed"));
    expect(calls[0].body).toEqual({ current_password: PASSWORD, new_password: NEW });
    expect(calls[0].path).toBe("/api/v1/auth/change-password");
    expect(sessionCleared()).toBe(true);
    for (const url of [...router.push.mock.calls.flat(), ...calls.map((c) => c.path), window.location.href]) {
      expect(String(url)).not.toContain(PASSWORD);
      expect(String(url)).not.toContain(NEW);
    }
    for (const spy of logs) expect(JSON.stringify(spy.mock.calls)).not.toContain(NEW);
  });

  it.each([
    [fail(400, "INVALID_CURRENT_PASSWORD"), "The current password is incorrect."],
    [fail(400, "PASSWORD_UNCHANGED"), "The new password must be different from the current one."],
    [fail(400, "INVALID_NEW_PASSWORD"), "The new password must not be blank."],
    [{ status: 422, body: { detail: [{ msg: "too short" }] } }, "The new password must be 8 to 128 characters."],
    [fail(429, "TOO_MANY_ATTEMPTS"), "Too many attempts. Wait a minute and try again."],
    [fail(500, "INTERNAL"), "Your password could not be changed. Please try again."],
  ])("shows a safe message and keeps the session on %#", async (reply, message) => {
    mockBackend({ "POST /api/v1/auth/change-password": () => reply });
    await submit(PASSWORD, NEW);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("server message must not be shown")).not.toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalled();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("token");
    for (const label of ["Current password", "New password", "Confirm new password"]) {
      expect(screen.getByLabelText(label)).toHaveValue("");  // cleared after the attempt
    }
  });

  it("reports a network failure without claiming success", async () => {
    mockBackend({ "POST /api/v1/auth/change-password": () => ({ networkError: true }) });
    await submit(PASSWORD, NEW);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to reach the server. Your password was not changed.");
    expect(localStorage.getItem(TOKEN_KEY)).toBe("token");
  });

  it("validates locally without sending anything", async () => {
    const { calls } = mockBackend({ "POST /api/v1/auth/change-password": () => ok({}) });
    await submit(PASSWORD, NEW, "something-else-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("The new passwords do not match.");
    expect(calls).toHaveLength(0);
  });

  it("an expired or revoked session ends through the shared handler", async () => {
    mockBackend({ "POST /api/v1/auth/change-password": () => fail(401, "TOKEN_REVOKED") });
    await submit(PASSWORD, NEW);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/login?reason=session-ended"));
    expect(sessionCleared()).toBe(true);
  });
});

describe("login page", () => {
  it.each([["signed-out", /all your devices/], ["password-changed", /new password/], ["session-ended", /sign in again/]])(
    "explains ?reason=%s without redirecting", async (reason, text) => {
      at(`/login?reason=${reason}`);
      router.pathname = "/login";
      const { default: LoginPage } = await import("@/app/login/page");
      render(<LoginPage />);
      expect(await screen.findByRole("status")).toHaveTextContent(text);
      expect(router.push).not.toHaveBeenCalled();
    });

  it("shows nothing for unknown reasons", async () => {
    at("/login?reason=%3Cscript%3E");
    const { default: LoginPage } = await import("@/app/login/page");
    render(<LoginPage />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("security headers", () => {
  const production = securityHeaders({ apiBaseUrl: "https://api.example.internal/", development: false });
  const csp = production.find((h) => h.key === "Content-Security-Policy")!.value;

  it("never allows unsafe-inline or unsafe-eval", () => {
    expect(csp).not.toMatch(/unsafe-inline|unsafe-eval/);
  });

  it("restricts connections to the API origin and forbids framing, plugins and base hijacking", () => {
    expect(csp).toContain("connect-src 'self' https://api.example.internal;");
    expect(csp).not.toContain("ws:");
    for (const directive of ["frame-ancestors 'none'", "object-src 'none'", "base-uri 'self'", "form-action 'self'"]) {
      expect(csp).toContain(directive);
    }
  });

  it("sets nosniff, a referrer policy and anti-framing", () => {
    const byKey = Object.fromEntries(production.map((h) => [h.key, h.value]));
    expect(byKey["X-Content-Type-Options"]).toBe("nosniff");
    expect(byKey["Referrer-Policy"]).toBe("strict-origin-when-cross-origin");
    expect(byKey["X-Frame-Options"]).toBe("DENY");
  });

  it("only development adds the hot-reload socket", () => {
    const dev = securityHeaders({ apiBaseUrl: "http://localhost:8000", development: true });
    expect(dev.find((h) => h.key === "Content-Security-Policy")!.value).toContain("connect-src 'self' http://localhost:8000 ws: wss:");
  });
});
