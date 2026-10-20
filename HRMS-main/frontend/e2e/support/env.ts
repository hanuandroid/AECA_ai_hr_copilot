// Settings for the role-access suite. Credentials come only from the environment (see e2e.env.example); nothing
// here holds a credential. The suite refuses to run against anything but loopback addresses, so it can never be pointed
// at a shared or production deployment by mistake.
import fs from "node:fs";
import path from "node:path";

function loadLocalEnvFile(): void {
  // Optional, git-ignored frontend/.env.e2e with E2E_* values (synthetic demo accounts only). Never the backend .env.
  const file = path.resolve(__dirname, "../../.env.e2e");
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf-8").split(/\r?\n/)) {
    const match = /^\s*(E2E_[A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match && process.env[match[1]] === undefined) process.env[match[1]] = match[2];
  }
}
loadLocalEnvFile();

function loopback(url: string, name: string): string {
  const host = new URL(url).hostname;
  if (host !== "127.0.0.1" && host !== "localhost") {
    throw new Error(`${name} must point at the local disposable E2E stack (127.0.0.1), got host ${host}`);
  }
  return url.replace(/\/$/, "");
}

export type SeededRole = "ADMIN" | "MANAGER" | "EMPLOYEE";

export const E2E = {
  webUrl: loopback(process.env.E2E_WEB_URL ?? "http://127.0.0.1:13000", "E2E_WEB_URL"),
  apiUrl: loopback(process.env.E2E_API_URL ?? "http://127.0.0.1:18000", "E2E_API_URL"),
  composeFile: path.resolve(__dirname, "../../../docker-compose.e2e.yml"),
  composeProject: "hrms-e2e",
  runtimeDir: path.resolve(__dirname, "../.runtime"),
};

/** Credentials of a seeded account, or null when the environment does not provide them (tests then skip: NOT_TESTED). */
export function seededCredentials(role: SeededRole): { email: string; password: string } | null {
  const email = process.env[`E2E_${role}_EMAIL`];
  const password = process.env[`E2E_${role}_PASSWORD`];
  return email && password ? { email, password } : null;
}

export const MISSING_CREDENTIALS =
  "NOT_TESTED: E2E_ADMIN/MANAGER/EMPLOYEE_EMAIL and _PASSWORD are not set (see frontend/e2e/e2e.env.example)";
