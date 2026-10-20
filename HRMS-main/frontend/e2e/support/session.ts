// Browser sessions without the login form: the same state the login page writes (token in localStorage, the
// hrms_auth / hrms_role cookies), built from a real token. The login form itself is covered by auth.spec.ts.
import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { E2E, type SeededRole } from "./env";

export async function contextWithToken(browser: Browser, token: string | null, role?: SeededRole): Promise<BrowserContext> {
  const host = new URL(E2E.webUrl).hostname;
  const context = await browser.newContext({
    storageState: token
      ? {
          cookies: [
            { name: "hrms_auth", value: "1", domain: host, path: "/", expires: -1, httpOnly: false, secure: false, sameSite: "Lax" },
            ...(role ? [{ name: "hrms_role", value: role, domain: host, path: "/", expires: -1, httpOnly: false, secure: false,
                          sameSite: "Lax" as const }] : []),
          ],
          origins: [{ origin: E2E.webUrl, localStorage: [{ name: "hrms_access_token", value: token }] }],
        }
      : { cookies: [], origins: [] },
  });
  return context;
}

/** Fill the real login form; credentials are typed into the page only. */
export async function loginThroughForm(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function expectPageTitle(page: Page, title: string | RegExp): Promise<void> {
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
}

export async function expectAccessDenied(page: Page): Promise<void> {
  await expect(page.getByRole("alert").filter({ hasText: "Access denied" })).toBeVisible();
}
