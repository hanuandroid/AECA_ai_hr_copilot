// B-17 follow-up: the ONE place that ends a browser session. Storage stays localStorage (decision S1); the
// hrms_auth / hrms_role cookies are navigation and display hints only - the backend authorizes every request.
//
// - clearSession(): removes the access token and both hint cookies.
// - endSession(router, reason): clears, then sends the user to /login?reason=<reason> (no tokens or details in the URL).
//   On public routes it only clears, so the login page can never redirect to itself in a loop.
// - Only an HTTP 401 means "authentication failed" (expired, invalid or revoked token, e.g. TOKEN_REVOKED after a
//   password change or a sign-out elsewhere). Network errors and other statuses are NOT session endings.

export const TOKEN_KEY = "hrms_access_token";
const HINT_COOKIES = ["hrms_auth", "hrms_role"] as const;
const PUBLIC_PATHS = new Set(["/", "/login"]);

export type SessionEndReason = "signed-out" | "session-ended" | "password-changed";

export const SESSION_END_MESSAGES: Record<SessionEndReason, string> = {
  "signed-out": "You have been signed out on all your devices.",
  "session-ended": "Your session has ended. Please sign in again.",
  "password-changed": "Your password was changed. Sign in with your new password.",
};

type Navigator = { push: (url: string) => void };

export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable: nothing to remove
  }
  if (typeof document !== "undefined") {
    for (const name of HINT_COOKIES) document.cookie = `${name}=; path=/; max-age=0; samesite=lax`;
  }
}

export function loginUrl(reason?: SessionEndReason): string {
  return reason ? `/login?reason=${reason}` : "/login";
}

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname);
}

export function endSession(router: Navigator, reason: SessionEndReason = "session-ended"): void {
  clearSession();
  const pathname = typeof window === "undefined" ? "" : window.location.pathname;
  if (!isPublicPath(pathname)) router.push(loginUrl(reason));
}

export function isAuthFailure(status: number | undefined | null): boolean {
  return status === 401;
}

/** The message for a ?reason= value on the login page; unknown values show nothing. */
export function messageForReason(reason: string | null): string | null {
  return reason && reason in SESSION_END_MESSAGES ? SESSION_END_MESSAGES[reason as SessionEndReason] : null;
}
